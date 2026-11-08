/**
 * Upload limits and polling by kind of file. Mirrors services/api/upload.py: the
 * API is the authority, and this only spares the browser from hashing a file the
 * API would refuse.
 */

export const MAX_IMAGE_BYTES = 25 * 1024 * 1024;
export const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
export const VIDEO_EXTENSIONS = ["mp4", "mov", "avi"];

export function isVideo(name) {
  const match = /\.([a-z0-9]+)$/i.exec(name);
  return Boolean(match) && VIDEO_EXTENSIONS.includes(match[1].toLowerCase());
}

export function checkSize(file) {
  const video = isVideo(file.name);
  const limit = video ? MAX_VIDEO_BYTES : MAX_IMAGE_BYTES;
  if (file.size > limit) {
    throw new Error(
      `${file.name} is ${(file.size / 1024 / 1024).toFixed(1)} MB; ` +
        `${video ? "a video" : "an image"} may be up to ${limit / 1024 / 1024} MB`,
    );
  }
}

/**
 * How often and how long to poll. An image settles in seconds. A video takes
 * about 7 s a frame for up to 60 frames, under the function's 900 s timeout, plus
 * a cold start and any queue: 16 minutes covers the worst legitimate case, and a
 * 10 s interval keeps that to under a hundred requests.
 */
export function pollPlan(name) {
  return isVideo(name)
    ? { intervalMs: 10_000, timeoutMs: 16 * 60_000 }
    : { intervalMs: 2000, timeoutMs: 120_000 };
}
