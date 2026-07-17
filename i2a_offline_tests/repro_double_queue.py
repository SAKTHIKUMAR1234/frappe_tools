"""Repro for finding: same (field,index) queued twice into crop_back_queue in
one round -> two identical crops in one crop_check call + contradictory
passed/rejected accounting + None appended into gctx['rejected'].

Drives the REAL engine._repair / engine._crop_back_check with stubbed
state/executor/verifier — no network.
"""

import sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fake_frappe

FRAPPE, REQUESTS = fake_frappe.install()

sys.path.insert(0, "/Users/karthikeyan/frappe-bench-v15/apps/frappe_tools")

from frappe_tools.i2a import engine, ground

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + ("  " + str(detail) if detail and not cond else ""))


# ---- stub state -------------------------------------------------------

class StubState:
    def __init__(self, scripted):
        self.rounds = 1
        self.mode = "Automated"
        self.steps = []
        self.scripted = scripted  # purpose -> answer
        self.call_log = []

    def call(self, model, messages, purpose):
        self.call_log.append((purpose, messages))
        return self.scripted[purpose]

    def step(self, kind, **data):
        self.steps.append({"step": kind, **data})


class StubAction:
    name = "test"
    rules = ""

    def parsed_schema(self):
        return [{"key": "freight_amount", "label": "Freight Amount", "required": True, "bbox_required": True}]

    request_notes = None


# ---- fixture: item with a value but NO bbox, plus a value_disagreement ----

from PIL import Image

pil = Image.new("RGB", (1000, 1000), "white")

item = {"value": 554, "raw_text": "554", "confidence": 0.9, "bbox": None}
fields = {"freight_amount": item}

deficiencies = [
    {"field": "freight_amount", "index": None, "kind": "bbox_missing", "detail": "value present without a bounding box"},
    {"field": "freight_amount", "index": None, "kind": "value_disagreement", "detail": "verifier: image shows '545'"},
]

gctx = {
    "pil": pil,
    "words": [  # one clean OCR hit for '554' -> deterministic pick
        {"text": "554", "bbox": {"x": 0.7, "y": 0.3, "w": 0.05, "h": 0.03}, "line": ("1", "1", "1")},
    ],
    "words_tried": True,
    "ocr_repair": 1,
    "crop_back": 1,
    "verify_crops": 0,
    "rejected": {},
}

# Sanity: does the OCR layer actually give a deterministic pick for this word?
clusters = ground.match_value(["554"], gctx["words"])
pick = ground.deterministic_pick(clusters)
check("fixture sane: deterministic OCR pick exists", pick is not None, clusters)

# Scripted model answers:
#  - repair: model fixes the value AND (per the JSON contract) returns a bbox
#    different from the OCR-anchored one.
#  - crop_check: verdicts diverge -> crop1 passes, crop2 rejected.
scripted = {
    "repair": {"repairs": [{
        "field": "freight_amount", "index": None, "value": 545, "raw_text": "545",
        "confidence": 0.9, "bbox": [310, 690, 340, 760],
    }]},
    "crop_check": {"checks": [
        {"crop": 1, "contains": True, "read_text": "545"},
        {"crop": 2, "contains": False, "read_text": "??"},
    ]},
}

state = StubState(scripted)
action = StubAction()

# ---- drive the REAL _repair (which calls _anchored_bbox_repair,
#      _freeform_repair and _crop_back_check) --------------------------

engine._repair(state, action, executor=None, verifier=None, image_parts=[],
               fields=fields, deficiencies=deficiencies, gctx=gctx)

# How many crops were sent in the single crop_check call?
crop_msgs = [m for p, m in state.call_log if p == "crop_check"]
check("one crop_check call made", len(crop_msgs) == 1, len(crop_msgs))
if crop_msgs:
    user = crop_msgs[0][-1]["content"]
    crop_texts = [c["text"] for c in user if isinstance(c, dict) and c.get("type") == "text" and "CROP" in c.get("text", "")]
    check("SAME field cropped twice in one call (double spend)",
          sum("freight_amount" in t for t in crop_texts) == 2, crop_texts)

crop_back_steps = [s for s in state.steps if s["step"] == "crop_back"]
check("crop_back step exists", len(crop_back_steps) == 1, state.steps)
if crop_back_steps:
    s = crop_back_steps[0]
    both = ({"field": "freight_amount", "index": None} in s["passed"]
            and any(r["field"] == "freight_amount" for r in s["rejected"]))
    check("same (field,index) BOTH passed and rejected in one step (trace corruption)", both, s)

check("item bbox nulled despite a recorded pass", item["bbox"] is None, item["bbox"])

# ---- second sub-scenario: both verdicts reject -> None lands in rejected ----

item2 = {"value": 554, "raw_text": "554", "confidence": 0.9, "bbox": None}
fields2 = {"freight_amount": item2}
gctx2 = dict(gctx, rejected={})
scripted2 = dict(scripted)
scripted2["crop_check"] = {"checks": [
    {"crop": 1, "contains": False, "read_text": "x"},
    {"crop": 2, "contains": False, "read_text": "y"},
]}
state2 = StubState(scripted2)

engine._repair(state2, action, executor=None, verifier=None, image_parts=[],
               fields=fields2, deficiencies=[dict(d) for d in deficiencies], gctx=gctx2)

rej = gctx2["rejected"].get(("freight_amount", None), [])
check("double rejection recorded for one box", len(rej) == 2, rej)
check("None appended into gctx['rejected'] (fingerprint inflation)", None in rej, rej)

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
