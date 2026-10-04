# 6. Verify content against the key it was stored under

**Status:** accepted · Phase 6 · 2026-10-01

## Context

The browser hashes a file before uploading, so a file already in the system is
never sent twice. That hash becomes the record's primary key and the S3 object's
name, because the browser must know the key before it can upload to a presigned
URL.

The digest the client sends is therefore a **claim**, made before any bytes have
been seen.

## Decision

The tagging function recomputes the digest from the bytes that actually arrived
and compares it against the key. A mismatch is a permanent failure, recorded
against the **claimed** digest with an explanatory reason.

The check applies only to keys that look like a 64-character hex digest, so direct
uploads with descriptive filenames — `aws s3 cp`, and the pre-Phase-6 test path —
still work.

## Consequences

**A real substitution attack is closed.** Without the check, a client can compute
the digest of file B, request an upload, and send the bytes of file A. The record
stays keyed by the true digest, so B's *record* is unharmed — but the object at
B's key now holds A's bytes, and B's `fullUrl` would serve them. Content
substitution under another file's identifier.

Verified end to end: a request claiming the digest of a wild-boar photograph while
uploading a cat is rejected with

    content does not match the key it was stored under:
    key claims cef6aeb6d60e..., content hashes to df47bb6d18e5...

the dead-letter queue stays empty, and no record is written for the substituted
content.

**Signing `ContentLength` is part of the same defence.** A presigned PUT cannot
otherwise bound what is uploaded: a client claiming one megabyte can send a
hundred and the only limit is the bill. With the length signed, claiming 1 MB and
uploading 5.7 MB returns `403`. The browser sets `Content-Length` itself, so an
honest client does nothing differently.

**The failure must be recorded against the claimed digest, not the computed one.**
The first version of this check recorded it under the computed digest — which is
not the row the client created or is polling. That left the caller's own record at
`PENDING` until its hour expired, so an honest client with buggy hashing would be
told "processing" for an hour instead of "your file did not match".

This is the same defect the Phase 4 reordering fixed, arriving by a different
route. The lesson is narrower than the Phase 4 one and more useful: when recording
a failure, the question is not *whose failure is this* but **who is waiting for
this answer**. Those can point at different rows.

**One residue, by design.** The object itself remains at the claimed key holding
the wrong bytes, until the legitimate owner uploads and overwrites it. No `DONE`
record points at it, so nothing serves it, and the upload function will issue a
URL for that digest again because a `FAILED` record is retryable.

## Alternatives considered

**Trust the client's digest and skip the check.** What the original design did.
Rejected once the substitution path was written out.

**Name objects with a server-generated id instead of the digest.** Removes the
claim entirely, and removes deduplication with it: the client could no longer ask
"have you seen this file?" before uploading, which is what makes a duplicate cost
one read instead of an upload and twenty seconds of inference.

**Check the digest in the upload function.** It has not seen the bytes. Only the
function that downloads the object can compare them, which is why this lives in
the tagger.
