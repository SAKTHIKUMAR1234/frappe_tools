import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("document interface keeps task navigation without product branding", () => {
  const shell = source("../components/AppShell.vue");
  const index = source("../../index.html");
  assert.match(shell, /aria-label="Main navigation"/);
  assert.match(shell, /Documents/);
  assert.match(shell, /label="Add document"/);
  assert.match(shell, /boot\?\.site_name/);
  assert.doesNotMatch(shell, /capture-brand|brand-symbol|Frappe Capture/);
  assert.match(index, /<title>Documents<\/title>/);
  assert.match(index, /rel="icon" href="data:,"/);
});

test("upload and review screens do not promote products or model names", () => {
  for (const component of [
    "QueueView",
    "NewDocumentPanel",
    "AgentProgress",
    "ReviewWorkspace",
    "OcrWorkspaceView",
  ]) {
    const code = source(`../features/workspace/${component}.vue`);
    assert.doesNotMatch(
      code,
      /Frappe Capture|Paperwork, ready|Your final say|second pair|agents\?\.(extractor|verifier)\?\.model/,
    );
  }
  const intake = source("../features/workspace/NewDocumentPanel.vue");
  assert.match(intake, /Phone scanner/);
  assert.match(intake, /<LiveScannerBridge/);
  assert.match(intake, /How processing works/);
});
