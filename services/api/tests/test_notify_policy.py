"""The filter policy is the whole subscription state, so these round-trips matter."""

import pytest

from notify_policy import build_policy, parse_policy, with_species, without_species


def test_one_species_is_a_plain_policy():
    assert build_policy({"dingo": 3}) == {"tags": {"dingo": [{"numeric": [">=", 3]}]}}


def test_two_species_are_ored():
    assert build_policy({"dingo": 3, "koala": 1}) == {
        "$or": [
            {"tags": {"dingo": [{"numeric": [">=", 3]}]}},
            {"tags": {"koala": [{"numeric": [">=", 1]}]}},
        ]
    }


@pytest.mark.parametrize("wanted", [{"dingo": 3}, {"dingo": 3, "koala": 1, "wild boar": 7}])
def test_parse_inverts_build(wanted):
    assert parse_policy(build_policy(wanted)) == wanted


def test_building_an_empty_policy_is_an_error():
    with pytest.raises(ValueError):
        build_policy({})


def test_with_species_adds_and_keeps_the_lower_threshold():
    assert with_species({"dingo": 3}, "koala", 2) == {"dingo": 3, "koala": 2}
    assert with_species({"dingo": 3}, "dingo", 5) == {"dingo": 3}
    assert with_species({"dingo": 3}, "dingo", 1) == {"dingo": 1}


def test_helpers_do_not_mutate_their_input():
    original = {"dingo": 3}
    with_species(original, "koala", 1)
    without_species(original, "dingo")
    assert original == {"dingo": 3}


def test_without_species_removes_only_that_one():
    assert without_species({"dingo": 3, "koala": 1}, "dingo") == {"koala": 1}
    assert without_species({"dingo": 3}, "emu") == {"dingo": 3}
