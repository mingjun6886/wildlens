/**
 * Run with: node --test web/src/limits.test.mjs
 *
 * The limits mirror services/api/upload.py. Checking before hashing saves the
 * browser from hashing a file the API will refuse.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  checkSize,
  isVideo,
  MAX_IMAGE_BYTES,
  MAX_VIDEO_BYTES,
  pollPlan,
} from "./limits.js";

const file = (name, size) => ({ name, size });

test("the limits match the upload API", () => {
  assert.equal(MAX_IMAGE_BYTES, 25 * 1024 * 1024);
  assert.equal(MAX_VIDEO_BYTES, 100 * 1024 * 1024);
});

test("video is recognised by extension, case-insensitively", () => {
  assert.equal(isVideo("clip.MP4"), true);
  assert.equal(isVideo("clip.mov"), true);
  assert.equal(isVideo("clip.avi"), true);
  assert.equal(isVideo("photo.jpg"), false);
  assert.equal(isVideo("no-extension"), false);
});

test("an image at the limit passes and one byte over is refused", () => {
  assert.doesNotThrow(() => checkSize(file("a.jpg", MAX_IMAGE_BYTES)));
  assert.throws(() => checkSize(file("a.jpg", MAX_IMAGE_BYTES + 1)), /25 MB/);
});

test("a video may be four times larger than an image", () => {
  assert.doesNotThrow(() => checkSize(file("a.mp4", MAX_IMAGE_BYTES + 1)));
  assert.doesNotThrow(() => checkSize(file("a.mp4", MAX_VIDEO_BYTES)));
  assert.throws(() => checkSize(file("a.mp4", MAX_VIDEO_BYTES + 1)), /100 MB/);
});

test("video is polled less often and for longer than an image", () => {
  assert.equal(pollPlan("a.jpg").intervalMs, 2000);
  assert.equal(pollPlan("a.jpg").timeoutMs, 120_000);
  assert.equal(pollPlan("a.mov").intervalMs, 10_000);
  assert.equal(pollPlan("a.mov").timeoutMs, 16 * 60_000);
});

test("a video carries a wait hint for every progress line; an image has none", () => {
  // The hint rides on each status update rather than being its own stage, because
  // the first poll replaces a stage at once and the wait is minutes long.
  assert.match(pollPlan("a.avi").hint, /several minutes/);
  assert.equal(pollPlan("a.png").hint, "");
});
