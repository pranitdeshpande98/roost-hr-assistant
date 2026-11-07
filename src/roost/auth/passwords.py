"""Approver console passwords: what a password may be, and a random temporary one.

Passwords are hashed with the same Argon2id settings as kiosk PINs (pins.py). Length is
what makes a password strong, so the rules are few: at least 12 characters, not a
well-known password, and not built from the person's own name or employee number. The
lockout (five failures, then fifteen minutes) stops online guessing; see console.py.
"""

import secrets
from enum import StrEnum

from roost.auth.pins import hash_pin, needs_rehash, verify_pin

MIN_LENGTH = 12
MAX_LENGTH = 128  # Argon2 copes with any length; this only stops absurd input

# Long enough to pass the length rule, and among the first ones people try.
_COMMON = frozenset(
    {
        "password1234",
        "password12345",
        "password123456",
        "passw0rd1234",
        "123456789012",
        "1234567890123",
        "qwertyuiop12",
        "qwerty123456",
        "qwertyuiopas",
        "iloveyou1234",
        "letmein12345",
        "welcome12345",
        "welcome123456",
        "changeme1234",
        "administrator",
        "abc123456789",
        "abcdefghijkl",
        "aaaaaaaaaaaa",
        "111111111111",
        "football1234",
        "baseball1234",
        "sunshine1234",
        "princess1234",
        "superman1234",
        "trustno11234",
        "monkey123456",
        "dragon123456",
        "chicken12345",
        "poultry12345",
        "roostroost12",
    }
)
_COMMON_WORDS = ("password", "passw0rd", "qwerty", "letmein", "welcome", "changeme", "roost")


class PasswordProblem(StrEnum):
    """Why a new password was refused. Shown to the approver in their language."""

    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    TOO_SIMPLE = "too_simple"  # few different characters: aaaa..., 1212...
    COMMON = "common"
    PERSONAL = "personal"  # contains the person's name or employee number
    SAME_AS_CURRENT = "same_as_current"


def password_problem(password: str, *, personal: tuple[str, ...] = ()) -> PasswordProblem | None:
    """Why `password` may not be used, or None if it is acceptable. `personal` holds the
    person's names and employee number."""
    if len(password) < MIN_LENGTH:
        return PasswordProblem.TOO_SHORT
    if len(password) > MAX_LENGTH:
        return PasswordProblem.TOO_LONG
    if len(set(password)) < 5:
        return PasswordProblem.TOO_SIMPLE
    lowered = password.lower()
    if lowered in _COMMON or any(lowered.startswith(word) for word in _COMMON_WORDS):
        return PasswordProblem.COMMON
    if any(len(part) >= 3 and part.lower() in lowered for part in personal):
        return PasswordProblem.PERSONAL
    return None


# No 0/O, 1/l/I: a temporary password is read off a screen and typed in.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def random_password() -> str:
    """A temporary password for HR to hand over: four groups of four, about 79 bits."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(4))


def hash_password(password: str) -> str:
    return hash_pin(password)


def verify_password(password_hash: str, password: str) -> bool:
    return verify_pin(password_hash, password)


__all__ = [
    "MAX_LENGTH",
    "MIN_LENGTH",
    "PasswordProblem",
    "hash_password",
    "needs_rehash",
    "password_problem",
    "random_password",
    "verify_password",
]
