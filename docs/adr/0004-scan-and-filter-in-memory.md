# 4. Scan the table and filter in memory

**Status:** accepted · Phase 6 · 2026-10-01

## Context

Three search routes query the tag data:

    /search/tags     AND, with minimum counts  {"wombat": 2}
    /search/species  OR, any listed species    ["dingo", "koala"]
    /search/byfile   superset of a sample's tags

Tags are stored as a map on the record: `{"cattle": 6}`. DynamoDB cannot index
into a map. A query like "at least two wombats" has no key to look up.

## Decision

Every search performs a `Scan` with a `status = DONE` filter and evaluates the
predicate in Python. No secondary index.

## Consequences

**Cost and latency are linear in table size, not in result size.** At ten records
a search completes in about 40 ms and consumes a few read units. Each route reads
the whole table whatever it returns.

**The `Scan` must paginate, and this is the dangerous part.** A `Scan` returns at
most 1 MB per call and hands back a continuation key. Reading one page and
stopping returns a partial answer with **no error and nothing to indicate
anything is missing**. At this size it never triggers; it would first trigger when
there is enough data to matter, and it would present as "the tagging is wrong"
rather than "the query read one page". A paginator is used from the first commit
and there is a test for it.

**A filter is not a cheaper scan.** `FilterExpression` is applied after each 1 MB
read, so it narrows the response, not the work. It is there to keep `PENDING` and
`FAILED` records out of results.

**Nor is a projection.** `ProjectionExpression` reduces bytes over the wire and
therefore how many items fit in a page. DynamoDB reads and bills the whole item
regardless. Both of these are commonly believed to reduce cost; neither does.

**Results are capped at 100.** A hundred hits is two hundred presigned
signatures, which is the expensive part of building a response. A client needing
more needs pagination, not a larger page.

## The threshold for changing this

The decision holds while the whole table fits comfortably inside one or two 1 MB
pages — roughly **2,000 records** at the current item size of about 400 bytes.
Beyond that:

| Signal | Response |
|---|---|
| Table over ~2,000 records | Measure search latency; it is no longer negligible |
| Search p95 over 1 s | Move to the inverted-index table below |
| Read costs visible on the bill | Same |

The replacement is not a GSI on the existing table, because there is no scalar
attribute to index — the species are map keys. It is a second table written
alongside the first: one item per `(species, fileId)` pair, partitioned by
species. A query for one species becomes a `Query`; AND becomes an intersection of
several `Query` results; OR becomes a union.

That costs one extra write per tag per upload and a schema that two writers must
keep consistent. At ten records it would be more machinery than the problem.

## Alternatives considered

**A GSI per species.** Twenty indexes for the 46 classes in use, each with its own
write cost, and adding a species would mean a schema change. Rejected.

**Storing tags as a string set and using `contains`.** `contains` works on a set,
which would serve `/search/species` — but the counts would be lost, and "at least
two wombats" is a requirement rather than a nicety. A filter expression also still
scans.

**OpenSearch.** The right answer at a scale this project will never reach, and
about $25 a month for the smallest instance — a quarter of the total budget, for a
query that currently takes 40 ms.
