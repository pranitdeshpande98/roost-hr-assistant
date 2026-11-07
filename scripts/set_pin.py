"""Set or reset an employee's kiosk PIN, as plant HR does in person (FR-41).

The employee must choose their own PIN at their next sign-in. The action is recorded in
the audit log under the HR employee who ran it.

Usage:
    uv run python scripts/set_pin.py --employee 100425 --hr 100007 --generate
    uv run python scripts/set_pin.py --employee 100425 --hr 100007   # type the PIN
"""

import argparse
import getpass
import logging
import secrets
import sys
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from roost.auth.kiosk import NotAllowedError, PinRejectedError, set_pin
from roost.auth.pins import PIN_LENGTH, pin_problem
from roost.core.config import get_settings
from roost.db.models import Employee

logger = logging.getLogger("set_pin")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set or reset a kiosk PIN.")
    parser.add_argument("--employee", required=True, help="employee number of the PIN owner")
    parser.add_argument("--hr", required=True, help="employee number of the HR person")
    parser.add_argument(
        "--generate", action="store_true", help="make a random temporary PIN and show it once"
    )
    return parser.parse_args(argv)


def random_pin(birth_date: date) -> str:
    """A random PIN that passes the policy (FR-40)."""
    while True:
        pin = "".join(secrets.choice("0123456789") for _ in range(PIN_LENGTH))
        if pin_problem(pin, birth_date) is None:
            return pin


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            employee = session.scalars(
                select(Employee).where(Employee.employee_number == args.employee)
            ).one_or_none()
            hr = session.scalars(
                select(Employee.id).where(Employee.employee_number == args.hr)
            ).one_or_none()
            if employee is None or hr is None:
                logger.error("Unknown employee number.")
                return 1
            if args.generate:
                pin = random_pin(employee.birth_date)
            else:
                pin = getpass.getpass("New temporary PIN: ")
                if getpass.getpass("Type it again: ") != pin:
                    logger.error("The two PINs differ.")
                    return 1
            try:
                set_pin(session, args.employee, pin, by=hr)
            except PinRejectedError as error:
                logger.error("PIN not accepted: %s", error.problem.value)
                return 1
            except NotAllowedError as error:
                logger.error("Not allowed: %s", error)
                return 1
    finally:
        engine.dispose()
    if args.generate:
        logger.info("Temporary PIN for %s: %s  (give it in person; shown once)", args.employee, pin)
    else:
        logger.info("PIN set for %s.", args.employee)
    logger.info("The employee must choose a new PIN at their next sign-in.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
