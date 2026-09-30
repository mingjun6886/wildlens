"""Tests for the three search routes.

The predicates are pure functions and are tested directly: they carry the actual
semantics of the API, and getting "at least two wombats" wrong is the kind of
error that returns plausible results rather than an error.

The pagination test is the important one. A single-page scan returns a partial
answer with no error at all, and at this project's size it would never be noticed.
"""

import json

import pytest

import common
import search


class FakePaginator:
    """Returns the pages it was given, as boto3's paginator would."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def paginate(self, **_kwargs):
        self.calls += 1
        return iter(self.pages)


def item(file_id: str, tags: dict[str, int], **extra) -> dict:
    """One record in DynamoDB's wire format."""
    return {
        "fileId": {"S": file_id},
        "status": {"S": "DONE"},
        "tags": {"M": {name: {"N": str(count)} for name, count in tags.items()}},
        "thumbKey": {"S": f"{file_id}.jpg"},
        "s3Key": {"S": f"{file_id}.jpg"},
        **extra,
    }


@pytest.fixture
def pages(monkeypatch):
    """Install a paginator over caller-supplied pages."""

    def install(*page_items):
        paginator = FakePaginator([{"Items": list(page)} for page in page_items])
        monkeypatch.setattr(search.dynamodb, "get_paginator", lambda _name: paginator)
        return paginator

    return install


def event(resource: str, body) -> dict:
    return {
        "resource": resource,
        "requestContext": {"requestId": "test-1", "authorizer": {"claims": {"email": "a@b.c"}}},
        "body": json.dumps(body),
    }


# --- AND with minimum counts ------------------------------------------------


def test_every_requested_species_must_be_present():
    assert search.has_at_least({"dingo": 1, "koala": 1}, {"dingo": 1, "koala": 1})
    assert not search.has_at_least({"dingo": 1}, {"dingo": 1, "koala": 1})


def test_the_count_is_a_minimum_not_an_equality():
    """Asking for two wombats must match a photograph of three."""
    assert search.has_at_least({"wombat": 3}, {"wombat": 2})


def test_a_count_below_the_minimum_does_not_match():
    """The reason counts are stored at all rather than a set of names."""
    assert not search.has_at_least({"wombat": 1}, {"wombat": 2})


def test_extra_species_do_not_prevent_a_match():
    """A file may hold more than was asked for."""
    assert search.has_at_least({"dingo": 1, "magpie": 4}, {"dingo": 1})


# --- OR ---------------------------------------------------------------------


def test_any_one_listed_species_is_enough():
    assert search.has_any({"koala": 1}, ["dingo", "koala"])


def test_none_of_the_listed_species_does_not_match():
    assert not search.has_any({"magpie": 2}, ["dingo", "koala"])


# --- superset ---------------------------------------------------------------


def test_a_superset_matches_but_a_subset_does_not():
    """Query-by-file is not equality: a picture with a dingo and a wallaby is a fair
    answer to "find pictures like this dingo", and a strict match returns nothing."""
    assert search.is_superset_of({"dingo": 1, "wallaby": 2}, {"dingo": 1})
    assert not search.is_superset_of({"dingo": 1}, {"dingo": 1, "wallaby": 2})


# --- pagination -------------------------------------------------------------


def test_records_from_every_page_are_returned(pages):
    """A Scan returns at most 1 MB per call. Reading one page and stopping gives a
    partial answer with no error and nothing to indicate anything is missing - a
    defect that would first appear as "the tagging is wrong"."""
    pages([item("a", {"dingo": 1})], [item("b", {"dingo": 1})], [item("c", {"dingo": 1})])

    found = [record["fileId"] for record in search.collect(lambda tags: "dingo" in tags)]

    assert found == ["a", "b", "c"]


def test_an_empty_page_does_not_end_the_scan(pages):
    """A filtered page can be empty while later pages still hold matches, because
    the filter is applied after each 1 MB read rather than across the scan."""
    pages([item("a", {"dingo": 1})], [], [item("c", {"dingo": 1})])

    assert len(search.collect(lambda tags: "dingo" in tags)) == 2


def test_results_are_capped(pages, monkeypatch):
    """A hundred hits means two hundred signatures; a client needing more needs
    pagination, not a larger page."""
    monkeypatch.setattr(search, "MAX_RESULTS", 2)
    pages([item(str(index), {"dingo": 1}) for index in range(10)])

    assert len(search.collect(lambda tags: "dingo" in tags)) == 2


# --- reading DynamoDB's wire format ----------------------------------------


def test_counts_are_read_as_integers():
    assert search.counts(item("a", {"dingo": 2})) == {"dingo": 2}


def test_a_record_with_no_tags_reads_as_empty_not_an_error():
    """An image with no animals is a valid result."""
    assert search.counts({"fileId": {"S": "a"}}) == {}


# --- dispatch and validation ------------------------------------------------


def test_an_unknown_route_is_a_404():
    result = search.handler(event("/search/nonsense", {"dingo": 1}), None)
    assert result["statusCode"] == 404


def test_tags_search_rejects_an_empty_body():
    assert search.handler(event("/search/tags", {}), None)["statusCode"] == 400


def test_tags_search_rejects_a_non_numeric_count():
    assert search.handler(event("/search/tags", {"dingo": "two"}), None)["statusCode"] == 400


def test_tags_search_rejects_a_zero_minimum():
    """"At least zero dingoes" matches everything, which is never what was meant."""
    assert search.handler(event("/search/tags", {"dingo": 0}), None)["statusCode"] == 400


def test_species_search_requires_an_array():
    assert search.handler(event("/search/species", {"dingo": 1}), None)["statusCode"] == 400


def test_species_search_rejects_an_empty_array():
    assert search.handler(event("/search/species", []), None)["statusCode"] == 400


def test_byfile_requires_a_file_field():
    assert search.handler(event("/search/byfile", {"ext": "jpg"}), None)["statusCode"] == 400


# --- query by file ----------------------------------------------------------


def test_invalid_base64_is_a_400():
    with pytest.raises(common.ClientError) as raised:
        search.tags_of_query_file("not base64!!", "jpg", "cid")
    assert raised.value.status == 400


def test_an_empty_query_file_is_a_400():
    with pytest.raises(common.ClientError):
        search.tags_of_query_file("", "jpg", "cid")


def test_an_oversized_query_file_is_refused_before_being_sent(monkeypatch):
    """API Gateway caps a payload at 10 MB and base64 inflates by a third. Rejecting
    it here gives a message; letting the gateway do it gives a bare 413."""
    monkeypatch.setattr(search, "MAX_QUERY_BYTES", 10)
    import base64

    encoded = base64.b64encode(b"x" * 100).decode()

    with pytest.raises(common.ClientError) as raised:
        search.tags_of_query_file(encoded, "jpg", "cid")
    assert "exceeds" in raised.value.message


def test_a_timeout_becomes_a_503_that_says_when_to_retry(monkeypatch):
    """The honest limitation of synchronous inference behind a 29-second gateway. A
    503 naming the cause beats a bare 504, and beats $15 a month of provisioned
    concurrency for one route."""
    import base64

    import botocore.exceptions

    def timeout(**_kwargs):
        raise botocore.exceptions.ReadTimeoutError(endpoint_url="lambda")

    monkeypatch.setattr(search.lambda_client, "invoke", timeout)

    with pytest.raises(common.ClientError) as raised:
        search.tags_of_query_file(base64.b64encode(b"x").decode(), "jpg", "cid")

    assert raised.value.status == 503
    assert "retry" in raised.value.message


def test_the_invoke_client_never_retries():
    """A retry here re-runs a twenty-second inference that is still going, paying
    twice for one answer. Query mode writes nothing, so the idempotency guard that
    protects uploads does not apply - there is no record to refuse."""
    assert search.lambda_client.meta.config.retries["total_max_attempts"] == 1


def test_the_read_timeout_stays_under_the_gateway_limit():
    """API Gateway cuts an integration off at 29 seconds and cannot be raised. Timing
    out first is what lets this code choose the error the caller sees."""
    assert search.lambda_client.meta.config.read_timeout < 29


def test_a_sample_with_no_animals_returns_nothing_rather_than_everything(monkeypatch):
    """Every file is trivially a superset of an empty tag set. Technically correct
    and useless."""
    monkeypatch.setattr(search, "tags_of_query_file", lambda *_: {})

    body = json.loads(search.handler(event("/search/byfile", {"file": "x"}), None)["body"])

    assert body["results"] == []
    assert "note" in body
