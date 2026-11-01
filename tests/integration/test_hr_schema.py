"""Integration tests: migrations and database-enforced HR rules."""

from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from roost.core.enums import Role
from roost.db.base import Base
from roost.db.models import Employee, EmployeeRole
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

AS_OF = date(2026, 11, 1)


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(as_of=AS_OF, seed=3, plant_headcount=40, corporate_headcount=30)


@pytest.fixture
def seeded(db_session: Session, small_org: SeedData) -> Session:
    load_seed_data(db_session, small_org, holiday_years=(AS_OF.year,))
    return db_session


def _any_employee(session: Session) -> Employee:
    return session.scalars(
        select(Employee).where(Employee.supervisor_id.is_not(None)).limit(1)
    ).one()


def _expect_violation(session: Session, sql: str, params: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        session.execute(text(sql), params)
    session.rollback()


def test_models_match_migrated_database(db_session: Session) -> None:
    # Same check as `alembic check`: no differences between models and schema.
    context = MigrationContext.configure(db_session.connection())

    assert compare_metadata(context, Base.metadata) == []


def test_rejects_badly_formatted_employee_number(seeded: Session) -> None:
    employee = _any_employee(seeded)

    _expect_violation(
        seeded, "UPDATE employee SET employee_number = '12345' WHERE id = :id", {"id": employee.id}
    )


def test_rejects_department_at_location_where_it_does_not_operate(seeded: Session) -> None:
    employee = _any_employee(seeded)
    deboning = seeded.scalar(text("SELECT id FROM department WHERE name = 'Deboning'"))
    corporate = seeded.scalar(text("SELECT id FROM location WHERE code = 'TX_CORP'"))

    _expect_violation(
        seeded,
        "UPDATE employee SET department_id = :d, location_id = :l WHERE id = :id",
        {"d": deboning, "l": corporate, "id": employee.id},
    )


def test_rejects_employee_supervising_themselves(seeded: Session) -> None:
    employee = _any_employee(seeded)

    _expect_violation(
        seeded, "UPDATE employee SET supervisor_id = id WHERE id = :id", {"id": employee.id}
    )


def test_rejects_unknown_payroll_class(seeded: Session) -> None:
    employee = _any_employee(seeded)

    _expect_violation(
        seeded,
        "UPDATE employee SET payroll_class = 'contractor' WHERE id = :id",
        {"id": employee.id},
    )


def test_rejects_negative_leave_hours(seeded: Session) -> None:
    employee = _any_employee(seeded)

    _expect_violation(
        seeded,
        "UPDATE leave_balance SET used_hours = -1 WHERE employee_id = :id",
        {"id": employee.id},
    )


def test_deleting_employee_removes_roles_and_balances(seeded: Session) -> None:
    # Pick an HR generalist with no direct reports, so the supervisor key does not block the delete.
    report = aliased(Employee)  # a second, independent reference to the employee table
    supervisor_ids = select(report.supervisor_id).where(report.supervisor_id.is_not(None))
    hr_person = seeded.scalars(
        select(Employee)
        .join(EmployeeRole)
        .where(EmployeeRole.role == Role.HR_GENERALIST, Employee.id.not_in(supervisor_ids))
        .limit(1)
    ).one()

    seeded.execute(text("DELETE FROM employee WHERE id = :id"), {"id": hr_person.id})

    remaining = seeded.scalar(
        text(
            "SELECT (SELECT count(*) FROM employee_role WHERE employee_id = :id)"
            " + (SELECT count(*) FROM leave_balance WHERE employee_id = :id)"
        ),
        {"id": hr_person.id},
    )
    assert remaining == 0
