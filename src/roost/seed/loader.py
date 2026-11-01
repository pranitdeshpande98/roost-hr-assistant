"""Write reference data and synthetic employees into the mock HR tables."""

from collections.abc import Iterable

from sqlalchemy import Result, func, insert, select, text
from sqlalchemy.orm import Session

from roost.db.models import (
    Department,
    DepartmentLocation,
    Employee,
    EmployeeRole,
    Holiday,
    LeaveBalance,
    Location,
    Shift,
)
from roost.leave.holidays import company_holidays
from roost.seed.employees import SeedData
from roost.seed.reference import LOCATIONS, SHIFTS, department_locations

# Every mock HR table, emptied together by reset_hr_tables().
_TRUNCATE_HR_TABLES = text(
    "TRUNCATE leave_balance, employee_role, employee, department_location, "
    "department, shift, location, holiday RESTART IDENTITY CASCADE"
)


def _to_id_map(result: Result[str, int]) -> dict[str, int]:
    """Turn (key, id) rows from INSERT ... RETURNING into a lookup dict.

    .all() is required: Result has a .keys() method, so dict(result) would
    treat it as a mapping instead of a sequence of rows.
    """
    return dict(result.tuples().all())


def is_seeded(session: Session) -> bool:
    """True if the employee table already has rows."""
    return bool(session.scalar(select(func.count()).select_from(Employee)))


def reset_hr_tables(session: Session) -> None:
    """Delete all rows from the mock HR tables. Development use only."""
    session.execute(_TRUNCATE_HR_TABLES)


def load_seed_data(
    session: Session, data: SeedData, *, holiday_years: Iterable[int]
) -> dict[str, int]:
    """Insert reference data, holidays, employees, roles and balances.

    Runs inside the caller's transaction: either everything is written or
    nothing is. Returns the number of rows written per table.
    """
    location_ids = _to_id_map(
        session.execute(
            insert(Location).returning(Location.code, Location.id),
            [
                {
                    "code": loc.code,
                    "name": loc.name,
                    "state": loc.state,
                    "kind": loc.kind,
                    "timezone": loc.timezone,
                    "hr_phone": loc.hr_phone,
                }
                for loc in LOCATIONS
            ],
        )
    )

    shift_ids = _to_id_map(
        session.execute(
            insert(Shift).returning(Shift.code, Shift.id),
            [
                {
                    "code": shift.code,
                    "name": shift.name,
                    "start_time": shift.start_time,
                    "end_time": shift.end_time,
                    "paid_hours": shift.paid_hours,
                    "workdays": list(shift.workdays),
                }
                for shift in SHIFTS
            ],
        )
    )

    pairs = sorted(department_locations())
    department_ids = _to_id_map(
        session.execute(
            insert(Department).returning(Department.name, Department.id),
            [{"name": name} for name in sorted({name for name, _ in pairs})],
        )
    )
    session.execute(
        insert(DepartmentLocation),
        [
            {"department_id": department_ids[name], "location_id": location_ids[code]}
            for name, code in pairs
        ],
    )

    holidays = {
        holiday.observed_date: holiday.name
        for year in holiday_years
        for holiday in company_holidays(year)
    }
    session.execute(
        insert(Holiday),
        [{"observed_date": day, "name": name} for day, name in sorted(holidays.items())],
    )

    # Supervisors are generated before their reports, so self-references resolve in order.
    session.execute(
        insert(Employee),
        [
            {
                "id": e.id,
                "employee_number": e.employee_number,
                "first_name": e.first_name,
                "last_name": e.last_name,
                "preferred_language": e.preferred_language,
                "payroll_class": e.payroll_class,
                "department_id": department_ids[e.department],
                "location_id": location_ids[e.location_code],
                "shift_id": shift_ids[e.shift_code],
                "supervisor_id": e.supervisor_id,
                "hire_date": e.hire_date,
                "birth_date": e.birth_date,
                "email": e.email,
                "has_teams": e.has_teams,
            }
            for e in data.employees
        ],
    )

    roles = [
        {"employee_id": e.id, "role": role} for e in data.employees for role in sorted(e.roles)
    ]
    if roles:
        session.execute(insert(EmployeeRole), roles)

    session.execute(
        insert(LeaveBalance),
        [
            {
                "employee_id": b.employee_id,
                "leave_type": b.leave_type,
                "year": b.year,
                "accrued_hours": b.accrued_hours,
                "carried_over_hours": b.carried_over_hours,
                "used_hours": b.used_hours,
            }
            for b in data.balances
        ],
    )

    return {
        "location": len(location_ids),
        "shift": len(shift_ids),
        "department": len(department_ids),
        "department_location": len(pairs),
        "holiday": len(holidays),
        "employee": len(data.employees),
        "employee_role": len(roles),
        "leave_balance": len(data.balances),
    }
