"""Tests for the kiosk PIN policy (FR-40) and hashing (FR-41)."""

from datetime import date

import pytest

from roost.auth.pins import (
    DUMMY_HASH,
    PinProblem,
    hash_pin,
    needs_rehash,
    pin_problem,
    verify_pin,
)

BIRTH = date(1990, 4, 7)


@pytest.mark.parametrize(
    ("pin", "problem"),
    [
        ("12345", PinProblem.NOT_SIX_DIGITS),
        ("1234567", PinProblem.NOT_SIX_DIGITS),
        ("12a456", PinProblem.NOT_SIX_DIGITS),
        ("١٢٣٤٥٧", PinProblem.NOT_SIX_DIGITS),  # Arabic-Indic digits are not ASCII
        ("000000", PinProblem.REPEATING),
        ("999999", PinProblem.REPEATING),
        ("121212", PinProblem.REPEATING),
        ("123123", PinProblem.REPEATING),
        ("123456", PinProblem.SEQUENCE),
        ("654321", PinProblem.SEQUENCE),
        ("345678", PinProblem.SEQUENCE),
        ("012345", PinProblem.SEQUENCE),
        ("112233", PinProblem.COMMON),
        ("159753", PinProblem.COMMON),
        ("040790", PinProblem.BIRTH_DATE),  # MMDDYY
        ("070490", PinProblem.BIRTH_DATE),  # DDMMYY
        ("900407", PinProblem.BIRTH_DATE),  # YYMMDD
        ("041990", PinProblem.BIRTH_DATE),  # MMYYYY
    ],
)
def test_weak_pins_are_refused(pin: str, problem: PinProblem) -> None:
    assert pin_problem(pin, BIRTH) is problem


@pytest.mark.parametrize("pin", ["480715", "902614", "135792", "120034"])
def test_ordinary_pins_are_accepted(pin: str) -> None:
    assert pin_problem(pin, BIRTH) is None


def test_another_persons_birth_date_is_fine() -> None:
    assert pin_problem("040790", date(1985, 12, 25)) is None


def test_hash_is_argon2id_salted_and_verifies() -> None:
    first, second = hash_pin("480715"), hash_pin("480715")

    assert first.startswith("$argon2id$")
    assert first != second  # a fresh salt each time
    assert verify_pin(first, "480715")
    assert not verify_pin(first, "480716")
    assert not needs_rehash(first)


def test_a_damaged_hash_never_verifies() -> None:
    assert not verify_pin("not-a-hash", "480715")
    assert not verify_pin(DUMMY_HASH, "")
