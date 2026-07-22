"""Regression guard (was: crop-back check fails OPEN on ProviderError).

Scenario: use_crop_back_check=1, ocr_repair=0 (free-form path). Free-form
repair returns a bbox for freight_amount -> assigned + queued. crop_check
call raises ProviderError (e.g. 400/413 on crop payload after all transport
attempts). The engine now FAILS CLOSED: the unconfirmed bbox is stripped,
a crop_back accounting step records it, and next round re-raises
bbox_missing so the never-confirmed box is never silently approved.
"""

import sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fake_frappe

FRAPPE, REQUESTS = fake_frappe.install()

sys.path.insert(0, "/mnt/storage/dev/frappe-v15/apps/frappe_tools")

from frappe_tools.i2a import engine, providers, verify

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + ("  " + str(detail) if detail and not cond else ""))


class StubState:
    def __init__(self, scripted):
        self.rounds = 1
        self.mode = "Automated"
        self.mode_degraded = False
        self.steps = []
        self.scripted = scripted
        self.call_log = []
        # _live_verifier / _mark_verifier_dead read these off the state
        self.dead_verifiers = set()
        self.executor_doc = None

    def call(self, model, messages, purpose):
        self.call_log.append((purpose, messages))
        if purpose == "crop_check":
            # providers.call_model raises ProviderError after all transport
            # attempts fail (providers.py line 68) — e.g. 400/413 on the
            # crop payload specifically.
            raise providers.ProviderError("400 bad request: unsupported image payload")
        return self.scripted[purpose]

    def chat(self, model, purpose, *, session=None, content=None, seed=None):
        # engine now drives repair via a session chat; stub delegates to call
        return self.call(model, [], purpose)

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

item = {"value": 554, "raw_text": "554", "confidence": 0.9, "bbox": None}
fields = {"freight_amount": item}

deficiencies = [
    {"field": "freight_amount", "index": None, "kind": "bbox_missing",
     "detail": "value present without a bounding box"},
]

gctx = {
    "pil": pil, "words": None, "words_tried": True,
    "ocr_repair": 0,        # free-form path (OCR off/unavailable)
    "crop_back": 1,         # THE FLAG IS ON
    "verify_crops": 0,
    "rejected": {},
}

# Free-form repair hallucinates a bbox; value untouched (already correct).
scripted = {
    "repair": {"repairs": [{
        "field": "freight_amount", "index": None,
        "bbox": [310, 690, 340, 760],
    }]},
}

state = StubState(scripted)
action = StubAction()

engine._repair(state, action, executor=None, verifier=StubVerifier(), image_parts=[],
               fields=fields, deficiencies=deficiencies, gctx=gctx)

# --- 1. the crop_check call WAS attempted and failed ---------------------
check("crop_check call attempted", any(p == "crop_check" for p, _ in state.call_log), state.call_log)
check("crop_check_failed step logged",
      any(s["step"] == "crop_check_failed" for s in state.steps), state.steps)

# --- 2. FAIL CLOSED (engine fixed): the unconfirmed bbox is stripped ------
check("UNCONFIRMED bbox stripped on item (fail closed)", item.get("bbox") is None, item)
check("no rejection remembered (never disproven — may be re-proposed later)",
      not gctx["rejected"], gctx["rejected"])
check("mode_degraded NOT set by crop_check failure", state.mode_degraded is False)
check("crop_back accounting step records the stripped box",
      any(s["step"] == "crop_back"
          and any(r["field"] == "freight_amount" for r in s.get("rejected", []))
          for s in state.steps), state.steps)

# --- 3. next round: deterministic_check RE-RAISES bbox_missing -----------
next_defs = verify.deterministic_check(fields, action.parsed_schema())
check("next round re-raises bbox_missing (box stripped, value still present)",
      any(d["kind"] == "bbox_missing" for d in next_defs), next_defs)

# --- 4. the never-confirmed bbox is not carried into an approval ---------
verdict = engine._gate(state, fields, action.parsed_schema(), unresolved=[])
statuses = [v["status"] for v in verdict["freight_amount"]]
check("gate still evaluates the field", statuses == ["Approved"], verdict)
check("no unconfirmed bbox survives on the item (fail closed)",
      item.get("bbox") is None, item)

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
