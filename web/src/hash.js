/**
 * SHA-256 of a File, as lowercase hex.
 *
 * This value is the file's identity everywhere in the system: it is the record's
 * primary key, the S3 object's name, and what lets a duplicate be refused before
 * any bytes are sent. The tagging function recomputes it from what actually
 * arrives and treats a mismatch as a permanent failure, so a wrong digest here is
 * not a cosmetic bug - it is an upload that can never succeed.
 *
 * Three ways to get it wrong, all of which produce a *valid-looking* wrong answer:
 *
 *   FileReader.readAsText   decodes as UTF-8 and mangles binary data silently
 *   missing zero-padding    byte 0x0a becomes "a", so the digest is short
 *   crypto.subtle undefined only exists in a secure context (HTTPS or localhost)
 */

export async function sha256(file) {
  if (!crypto?.subtle) {
    // Worth naming, because the symptom is "sha256 is not a function" on a page
    // that works on localhost and fails once it is served over plain HTTP.
    throw new Error(
      "crypto.subtle is unavailable. It requires a secure context: HTTPS, or localhost.",
    );
  }

  // arrayBuffer(), not readAsText. The bytes have to stay bytes.
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));

  // padStart is load-bearing: toString(16) gives "a" for 10, and a digest with a
  // byte below 0x10 would come out shorter than 64 characters. The upload endpoint
  // rejects that with "sha256 must be 64 hexadecimal characters", which is a
  // confusing message for a hashing bug.
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Extension without the dot, lowercased, as the upload endpoint expects. */
export function extensionOf(file) {
  const match = /\.([a-z0-9]+)$/i.exec(file.name);
  if (!match) {
    throw new Error(`${file.name} has no file extension`);
  }
  return match[1].toLowerCase();
}

/**
 * Largest sample /search/byfile accepts, in original bytes.
 *
 * Not a client-side nicety duplicated from the server: a request larger than this
 * is refused by API Gateway before any of this project's code runs, so the caller
 * would get a bare 413 with no explanation. The limit comes from Lambda's 6 MB
 * synchronous invocation payload, which has to hold the base64 body plus the event
 * API Gateway wraps around it — not from the 25 MB upload limit, which applies to
 * a path that sends bytes straight to S3.
 */
export const MAX_SAMPLE_BYTES = 4 * 1024 * 1024;

/** Read a File as base64, for /search/byfile. */
export async function toBase64(file) {
  if (file.size > MAX_SAMPLE_BYTES) {
    throw new Error(
      `a sample must be under ${MAX_SAMPLE_BYTES / 1024 / 1024} MB; this one is ` +
        `${(file.size / 1024 / 1024).toFixed(1)} MB. ` +
        `Upload it instead — uploads go straight to storage and allow 25 MB.`,
    );
  }

  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  // Chunked, because String.fromCharCode(...bytes) on a multi-megabyte array
  // exceeds the argument limit and throws RangeError.
  for (let index = 0; index < bytes.length; index += 8192) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
  }
  return btoa(binary);
}
