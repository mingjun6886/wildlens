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
