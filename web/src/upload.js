/**
 * Hash, reserve, send, then poll.
 *
 * The polling is the whole reason this project has an asynchronous design.
 * Tagging takes six seconds warm and twenty cold, and the first invocation after
 * a deployment over forty - far too long to hold an HTTP request open. So the
 * upload returns immediately and the record moves through four states that this
 * module reports as they change.
 */

import { fileStatus, putBytes, reserveUpload } from "./api.js";
import { extensionOf, sha256 } from "./hash.js";
import { checkSize, pollPlan } from "./limits.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Upload one file and resolve with its finished record.
 *
 * `onProgress` is called with {stage, detail} as things happen, so the caller
 * renders progress without this module touching the DOM.
 */
export async function uploadFile(file, onProgress = () => {}) {
  checkSize(file);
  const plan = pollPlan(file.name);

  onProgress({ stage: "hashing", detail: `${(file.size / 1e6).toFixed(1)} MB` });

  const digest = await sha256(file);
  const extension = extensionOf(file);

  onProgress({ stage: "reserving", detail: digest.slice(0, 12) });

  const reservation = await reserveUpload({
    sha256: digest,
    ext: extension,
    size: file.size,
  });

  if (reservation.duplicate) {
    // The point of hashing first: this file cost one DynamoDB read instead of an
    // upload and twenty seconds of inference. Nothing was sent.
    onProgress({ stage: "duplicate", detail: "already in the collection" });
    return { ...(await fileStatus(digest)), fileId: digest, wasDuplicate: true };
  }

  if (reservation.status === "PROCESSING") {
    // Someone else's upload of the same file is mid-flight. Joining their poll is
    // right: issuing a URL now would let the object be replaced under a running
    // worker.
    onProgress({ stage: "joining", detail: "already being processed" });
    return pollUntilSettled(digest, onProgress, plan);
  }

  onProgress({ stage: "sending", detail: `${(file.size / 1e6).toFixed(1)} MB to S3` });
  await putBytes(reservation.uploadUrl, file, reservation.requiredHeaders);

  return pollUntilSettled(digest, onProgress, plan);
}

async function pollUntilSettled(fileId, onProgress, plan) {
  const deadline = Date.now() + plan.timeoutMs;
  let lastStatus = null;

  while (Date.now() < deadline) {
    let record;
    try {
      record = await fileStatus(fileId);
    } catch (error) {
      // A 404 right after the PUT is normal for a moment: the notification and
      // the queue take a beat, and the record may not be readable yet.
      if (error.status !== 404) throw error;
      await sleep(plan.intervalMs);
      continue;
    }

    if (record.status !== lastStatus) {
      lastStatus = record.status;
      onProgress({ stage: record.status.toLowerCase(), detail: plan.hint });
    }

    if (record.status === "DONE" || record.status === "FAILED") {
      return record;
    }

    await sleep(plan.intervalMs);
  }

  // Not a failure of the file - a failure to find out. Said plainly, because
  // "still processing" and "failed" need different responses.
  const minutes = Math.round(plan.timeoutMs / 60_000);
  throw new Error(
    `still processing after ${minutes} minutes. The record will settle; reload to check again.`,
  );
}
