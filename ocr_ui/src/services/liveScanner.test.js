import assert from "node:assert/strict";
import test from "node:test";
import { LiveImageAssembler } from "./liveScanner.js";

test("assembles ordered WebRTC chunks into the exact image bytes", () => {
  const assembler = new LiveImageAssembler();
  const source = Uint8Array.from([0, 1, 2, 127, 128, 255]);
  const envelope = JSON.stringify({
    data: Buffer.from(source).toString("base64"),
  });
  const midpoint = Math.ceil(envelope.length / 2);

  assert.equal(
    assembler.add({
      type: "chunk",
      id: "scan-1",
      index: 1,
      total: 2,
      data: envelope.slice(midpoint),
    }),
    null,
  );
  const result = assembler.add({
    type: "chunk",
    id: "scan-1",
    index: 0,
    total: 2,
    data: envelope.slice(0, midpoint),
  });

  assert.equal(result.id, "scan-1");
  assert.deepEqual(result.bytes, source);
});

test("ignores duplicate chunks and rejects a changed transfer shape", () => {
  const assembler = new LiveImageAssembler();
  const chunk = { type: "chunk", id: "scan-2", index: 0, total: 2, data: "{" };

  assert.equal(assembler.add(chunk), null);
  assert.equal(assembler.add(chunk), null);
  assert.throws(
    () => assembler.add({ ...chunk, total: 3 }),
    /changed the image transfer size/,
  );
});
