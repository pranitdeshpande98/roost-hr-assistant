"""Console password policy and temporary passwords."""

import pytest

from roost.auth.passwords import (
    PasswordProblem,
    hash_password,
    password_problem,
    random_password,
    verify_password,
)


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short-pass1", PasswordProblem.TOO_SHORT),
        ("x" * 129 + "abcde", PasswordProblem.TOO_LONG),
        ("abababababab", PasswordProblem.TOO_SIMPLE),
        ("Password1234", PasswordProblem.COMMON),
        ("Welcome2026!!", PasswordProblem.COMMON),
        ("qwerty123456", PasswordProblem.COMMON),
        ("rosa-garcia-2026", PasswordProblem.PERSONAL),
        ("my number is 100425", PasswordProblem.PERSONAL),
        ("correct horse battery staple", None),
        ("Tractor-Maple-Cloud", None),
    ],
)
def test_password_policy(password: str, problem: PasswordProblem | None) -> None:
    assert password_problem(password, personal=("Rosa", "Garcia", "100425")) is problem


def test_short_name_parts_do_not_block_passwords() -> None:
    # A two-letter name would otherwise rule out half the dictionary.
    assert password_problem("long and unrelated words", personal=("Al", "Ng", "100425")) is None


def test_random_passwords_pass_the_policy_and_differ() -> None:
    made = {random_password() for _ in range(50)}
    assert len(made) == 50
    for password in made:
        assert len(password) == 19
        assert password_problem(password) is None
        assert not set(password) & set("0O1lI")


def test_hashes_are_argon2id_and_verify() -> None:
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "correct horse battery staple")
    assert not verify_password(stored, "correct horse battery stapler")
