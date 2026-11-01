"""Tests for roost.leave.repository.new_reference_code."""

import re

from roost.leave.repository import REFERENCE_ALPHABET, new_reference_code


def test_format_matches_the_database_check() -> None:
    assert re.fullmatch(r"VR-[A-Z2-9]{6}", new_reference_code())


def test_no_characters_that_are_easy_to_misread() -> None:
    assert not set("01OI") & set(REFERENCE_ALPHABET)


def test_codes_are_random_not_sequential() -> None:
    assert len({new_reference_code() for _ in range(200)}) == 200
