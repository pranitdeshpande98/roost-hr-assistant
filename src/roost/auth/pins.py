"""Kiosk PINs: what a PIN may be (FR-40) and how it is stored (FR-41).

A 6-digit PIN has only a million values, so the policy removes the ones people pick
first, and Argon2id makes each guess against a stolen hash slow. The lockout (five
failures, then fifteen minutes) is what really protects an account; see kiosk.py.
"""

from datetime import date
from enum import StrEnum
from itertools import pairwise

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PIN_LENGTH = 6

# Argon2id with the library's defaults (RFC 9106 "second recommended" profile): about
# 50 ms and 64 MiB per hash, slow enough to hurt guessing, fast enough for a kiosk.
_HASHER = PasswordHasher()

# The most common 6-digit PINs not already caught by the pattern rules below.
_COMMON = frozenset(
    {"112233", "121212", "123123", "123321", "147258", "159753", "102030", "696969", "789456"}
)


class PinProblem(StrEnum):
    """Why a new PIN was refused. Shown to the employee in their language."""

    NOT_SIX_DIGITS = "not_six_digits"
    REPEATING = "repeating"  # 000000, 121212, 123123
    SEQUENCE = "sequence"  # 123456, 654321, 345678
    COMMON = "common"
    BIRTH_DATE = "birth_date"
    SAME_AS_CURRENT = "same_as_current"


def _is_repeating(pin: str) -> bool:
    """Made of one block of 1, 2 or 3 digits repeated: 000000, 121212, 123123."""
    return any(pin == pin[:size] * (len(pin) // size) for size in (1, 2, 3))


def _is_sequence(pin: str) -> bool:
    """Each digit one more (or one less) than the last: 123456, 654321."""
    steps = {int(b) - int(a) for a, b in pairwise(pin)}
    return steps in ({1}, {-1})


def _birth_date_forms(birth: date) -> set[str]:
    """Every 6-digit way of writing the date that people use as a PIN."""
    d, m, yy, yyyy = (
        f"{birth.day:02}",
        f"{birth.month:02}",
        f"{birth.year % 100:02}",
        str(birth.year),
    )
    return {
        m + d + yy,
        d + m + yy,
        yy + m + d,
        m + yyyy,
        yyyy[2:] + m + d,
        d + m + yyyy[2:],
        m + d + yyyy[2:],
    }


def pin_problem(pin: str, birth_date: date) -> PinProblem | None:
    """Why `pin` may not be used, or None if it is acceptable (FR-40)."""
    if len(pin) != PIN_LENGTH or not pin.isascii() or not pin.isdigit():
        return PinProblem.NOT_SIX_DIGITS
    if _is_repeating(pin):
        return PinProblem.REPEATING
    if _is_sequence(pin):
        return PinProblem.SEQUENCE
    if pin in _COMMON:
        return PinProblem.COMMON
    if pin in _birth_date_forms(birth_date):
        return PinProblem.BIRTH_DATE
    return None


def hash_pin(pin: str) -> str:
    """An Argon2id hash with its own random salt (FR-41). The PIN itself is never stored."""
    return _HASHER.hash(pin)


def verify_pin(pin_hash: str, pin: str) -> bool:
    """Whether `pin` matches. Takes the same time whether it matches or not."""
    try:
        return _HASHER.verify(pin_hash, pin)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(pin_hash: str) -> bool:
    """True when the hash was made with older settings: re-hash at the next sign-in."""
    return _HASHER.check_needs_rehash(pin_hash)


# Verified when the employee number is unknown, so a wrong number takes as long as a
# wrong PIN and the timing does not reveal which employee numbers exist.
DUMMY_HASH = hash_pin("480715")
