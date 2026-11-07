"""Set or reset an approver's console password, as HR does (ADR-0013).

Only supervisors and HR can have one. The approver must choose their own password at
their next sign-in. The action is recorded in the audit log under the HR employee who
ran it. Phase 12 replaces these passwords with Microsoft Entra ID sign-in.

Usage:
    uv run python scripts/set_console_password.py --employee 100425 --hr 100007 --generate
    uv run python scripts/set_console_password.py --employee 100425 --hr 100007   # type it
"""

import argparse
import getpass
import logging
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from roost.auth.console import PasswordRejectedError, set_password
from roost.auth.kiosk import NotAllowedError
from roost.auth.passwords import random_password
from roost.core.config import get_settings
from roost.db.models import Employee

logger = logging.getLogger("set_console_password")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set or reset an approver console password.")
    parser.add_argument("--employee", required=True, help="employee number of the approver")
    parser.add_argument("--hr", required=True, help="employee number of the HR person")
    parser.add_argument(
        "--generate", action="store_true", help="make a random temporary password, shown once"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.generate:
        password = random_password()
    else:
        password = getpass.getpass("New temporary password: ")
        if getpass.getpass("Type it again: ") != password:
            logger.error("The two passwords differ.")
            return 1
    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            hr = session.scalars(
                select(Employee.id).where(Employee.employee_number == args.hr)
            ).one_or_none()
            if hr is None:
                logger.error("Unknown employee number.")
                return 1
            for _ in range(5):  # a random one can, very rarely, contain a name
                try:
                    set_password(session, args.employee, password, by=hr)
                    break
                except NotAllowedError as error:
                    logger.error("Not allowed: %s", error)
                    return 1
                except PasswordRejectedError as error:
                    if not args.generate:
                        logger.error("Password not accepted: %s", error.problem.value)
                        return 1
                    password = random_password()
            else:
                logger.error("Could not generate an acceptable password; try again.")
                return 1
    finally:
        engine.dispose()
    if args.generate:
        logger.info(
            "Temporary password for %s: %s  (give it privately; shown once)",
            args.employee,
            password,
        )
    else:
        logger.info("Password set for %s.", args.employee)
    logger.info("They must choose their own password at their next sign-in at /console.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
