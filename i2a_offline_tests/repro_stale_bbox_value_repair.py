"""Regression guard (was: value repaired without a new bbox never
crop-back-queued -> new value survives anchored to the OLD rectangle).

Scenario: use_crop_back_check=1, use_verify_crops=0. Item freight_amount has
value 554 with a bbox (from extraction). Verifier flags value_disagreement
(image shows 654). Free-form repair returns value=654 with bbox=null.
The engine now treats a new value under the old box as a changed claim and
crop-back-re-proves it; the verifier disproves the box, so it is stripped
and re-surfaces next round as bbox_missing.
"""

import sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fake_frappe

FRAPPE, REQUESTS = fake_frappe.install()

sys.path.insert(0, "/mnt/storage/dev/frappe-v15/apps/frappe_tools")

from frappe_tools.i2a import engine

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + ("  " + str(detail) if detail and not cond else ""))


class StubState:
    def __init__(self, scripted):
        self.rounds = 1
        self.mode = "Automated"
        self.steps = []
        self.scripted = scripted
        self.call_log = []
        # _live_verifier / _mark_verifier_dead read these off the state
        self.dead_verifiers = set()
        self.executor_doc = None

    def call(self, model, messages, purpose):
        self.call_log.append((purpose, messages))
        return self.scripted[purpose]

    def chat(self, model, purpose, *, session=None, content=None, seed=None):
        # engine now drives repair via a session chat; stub delegates to call,
        # preserving the turn content so tests can introspect the crops sent
        return self.call(model, [{"role": "user", "content": content or []}], purpose)

    def step(self, kind, **data):
        self.steps.append({"step": kind, **data})


class StubAction:
    name = "test"
    rules = ""
    request_notes = None

    def parsed_schema(self):
        return [{"key": "freight_amount", "label": "Freight Amount", "required": True, "bbox_required": True}]


class StubVerifier:
    # _live_verifier only needs a `.name`; the stub state ignores the model
    name = "verifier-model"


from PIL import Image

pil = Image.new("RGB", (1000, 1000), "white")

OLD_BBOX = {"x": 0.7, "y": 0.3, "w": 0.05, "h": 0.03}  # rectangle around printed '554'
item = {"value": 554, "raw_text": "554", "confidence": 0.9, "bbox": dict(OLD_BBOX)}
fields = {"freight_amount": item}

# ONLY a value_disagreement — no bbox deficiency, so the OCR-anchored path
# is bypassed entirely and the deficiency goes straight to _freeform_repair.
deficiencies = [
    {"field": "freight_amount", "index": None, "kind": "value_disagreement",
     "detail": "verifier: image shows '654'"},
]

gctx = {
    "pil": pil, "words": None, "words_tried": False,
    "ocr_repair": 0, "crop_back": 1, "verify_crops": 0,
    "rejected": {},
}

# Repair fixes the value but returns bbox null (untrusted model output —
# the engine's own `if bbox:` guard at line 548 exists for exactly this).
scripted = {
    "repair": {"repairs": [{
        "field": "freight_amount", "index": None, "value": 654, "raw_text": "654",
        "confidence": 0.95, "bbox": None,
    }]},
    # crop_check answer scripted but should never be requested:
    "crop_check": {"checks": [{"crop": 1, "contains": False, "read_text": "554"}]},
}

state = StubState(scripted)
engine._repair(state, StubAction(), executor=None, verifier=StubVerifier(), image_parts=[],
               fields=fields, deficiencies=deficiencies, gctx=gctx)

check("repair applied the new value", item["value"] == 654, item)
# a new value under the OLD box is a changed claim — the engine now re-proves
# it via crop-back; the verifier disproves it, so the stale box is stripped.
check("stale box NOT left unchanged — it is re-proven and stripped on disproof",
      item["bbox"] is None, item)

crop_calls = [p for p, m in state.call_log if p == "crop_check"]
check("crop_check WAS called to re-prove the changed claim",
      len(crop_calls) == 1, state.call_log)

crop_back_steps = [s for s in state.steps if s["step"] == "crop_back"]
check("crop_back step logged with the box rejected",
      len(crop_back_steps) == 1
      and any(r["field"] == "freight_amount"
              for r in (crop_back_steps[0].get("rejected") if crop_back_steps else [])),
      state.steps)

check("item marked repaired=1 and its box was re-proven (stripped on disproof)",
      item.get("repaired") == 1 and item["bbox"] is None, item)

# --- next-round check: the stripped box re-surfaces as bbox_missing --------
from frappe_tools.i2a import verify as vmod

schema = StubAction().parsed_schema()
next_round = []
next_round += vmod.apply_formats(fields, schema)
next_round += vmod.deterministic_check(fields, schema)
check("next-round deterministic check re-raises bbox_missing (box stripped)",
      any(d["kind"] == "bbox_missing" for d in next_round), next_round)

# verify prompt next round (use_verify_crops=0): claim text has value/raw_text
# only — no coordinates, no crop.
msgs = vmod.build_verify_messages(StubAction(), [], fields, crops=[])
claim_text = msgs[1]["content"][0]["text"]
check("verify claim shows the NEW value with no crop/coords",
      "654" in claim_text and "CROP" not in claim_text and "0.7" not in claim_text, claim_text)

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
