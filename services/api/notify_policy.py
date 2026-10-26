"""Build and read the SNS filter policy that stores a subscription.

There is no table behind /subscribe. The policy on the SNS subscription is the
state, so reading and writing it has to be lossless, which is why this is a pure
module with round-trip tests rather than a few lines inside the handler.

Two SNS rules shape it. Keys inside one policy are ANDed, so "dingo >= 3 OR
koala >= 1" needs a top-level `$or`. And the policy applies to the message body
(FilterPolicyScope=MessageBody) because a file can carry more species than the 10
message attributes SNS allows.
"""

from __future__ import annotations


def _clause(tag: str, minimum: int) -> dict:
    return {"tags": {tag: [{"numeric": [">=", minimum]}]}}


def build_policy(wanted: dict[str, int]) -> dict:
    """One clause is written plain; two or more are ORed."""
    if not wanted:
        raise ValueError("a policy needs at least one species")
    clauses = [_clause(tag, minimum) for tag, minimum in sorted(wanted.items())]
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


def parse_policy(policy: dict) -> dict[str, int]:
    """The inverse of build_policy."""
    clauses = policy.get("$or", [policy])
    wanted: dict[str, int] = {}
    for clause in clauses:
        for tag, rules in clause["tags"].items():
            wanted[tag] = int(rules[0]["numeric"][1])
    return wanted


def with_species(wanted: dict[str, int], tag: str, minimum: int) -> dict[str, int]:
    """A copy with the species added. If it is already there the lower threshold wins,
    because asking again for a species should never make someone miss a sighting."""
    merged = dict(wanted)
    merged[tag] = min(minimum, merged[tag]) if tag in merged else minimum
    return merged


def without_species(wanted: dict[str, int], tag: str) -> dict[str, int]:
    return {name: minimum for name, minimum in wanted.items() if name != tag}
