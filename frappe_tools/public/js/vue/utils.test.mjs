import assert from "node:assert/strict";
import { test } from "node:test";

import { oneAtATime } from "./utils.js";

test("a second save while the first runs is ignored until the first ends", async () => {
  const busy = { value: false };
  let finish;
  let runs = 0;
  const save = oneAtATime(() => { runs += 1; return new Promise((resolve) => { finish = resolve; }); }, busy);

  const first = save();
  await save();
  assert.equal(runs, 1);
  assert.equal(busy.value, true);

  finish();
  await first;
  assert.equal(busy.value, false);
  save();
  assert.equal(runs, 2);
});

test("a failed save frees the next one", async () => {
  const busy = { value: false };
  const save = oneAtATime(async () => { throw new Error("upload failed"); }, busy);
  await assert.rejects(save(), /upload failed/);
  assert.equal(busy.value, false);
});
