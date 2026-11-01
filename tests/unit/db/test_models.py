"""Unit tests for ORM model definitions (no database needed)."""

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKeyConstraint, Table

from roost.core.enums import (
    EmploymentStatus,
    Language,
    LeaveType,
    LocationKind,
    PayrollClass,
    Role,
)
from roost.db.base import Base
from roost.db.models import Employee, LeaveBalance


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _constraint_names(table: Table) -> set[str]:
    return {str(constraint.name) for constraint in table.constraints}


def test_all_hr_tables_are_registered() -> None:
    expected = {
        "location",
        "department",
        "department_location",
        "shift",
        "employee",
        "employee_role",
        "leave_balance",
        "holiday",
    }

    assert expected <= set(Base.metadata.tables)


def test_enum_values_are_stable() -> None:
    # These values are stored in the database. Changing them needs a migration.
    assert [m.value for m in Language] == ["en", "es", "vi"]
    assert [m.value for m in PayrollClass] == [
        "executive",
        "biweekly",
        "weekly_it",
        "weekly_production",
        "driver",
    ]
    assert [m.value for m in LocationKind] == ["plant", "corporate"]
    assert [m.value for m in EmploymentStatus] == ["active", "terminated"]
    assert [m.value for m in Role] == ["supervisor", "hr_generalist", "hr_admin"]
    assert [m.value for m in LeaveType] == ["vacation", "sick"]


def test_enums_are_stored_as_values_with_check_constraint() -> None:
    column_type = _table("employee").c.payroll_class.type

    assert isinstance(column_type, SAEnum)
    assert column_type.native_enum is False
    assert column_type.enums == [m.value for m in PayrollClass]
    assert "ck_employee_payrollclass" in _constraint_names(_table("employee"))


def test_employee_constraints_follow_naming_convention() -> None:
    names = _constraint_names(_table("employee"))

    assert {
        "pk_employee",
        "uq_employee_employee_number",
        "ck_employee_employee_number_format",
        "ck_employee_not_own_supervisor",
        "ck_employee_hire_after_birth",
        "fk_employee_department_id_department_location",
        "fk_employee_supervisor_id_employee",
    } <= names


def test_employee_department_must_operate_at_employee_location() -> None:
    composite = [
        fk
        for fk in _table("employee").foreign_key_constraints
        if isinstance(fk, ForeignKeyConstraint) and len(fk.columns) == 2
    ]

    assert len(composite) == 1
    assert [c.name for c in composite[0].columns] == ["department_id", "location_id"]
    assert {element.column.table.name for element in composite[0].elements} == {
        "department_location"
    }


def test_employee_nullability() -> None:
    columns = _table("employee").c

    assert columns.email.nullable is True
    assert columns.supervisor_id.nullable is True
    assert columns.employee_number.nullable is False
    assert columns.birth_date.nullable is False


def test_leave_balance_primary_key_is_employee_type_year() -> None:
    key = [c.name for c in _table("leave_balance").primary_key.columns]

    assert key == ["employee_id", "leave_type", "year"]
    assert "ck_leave_balance_hours_non_negative" in _constraint_names(_table("leave_balance"))


def test_relationships_raise_instead_of_lazy_loading() -> None:
    for relationship in (
        Employee.location,
        Employee.department,
        Employee.shift,
        Employee.supervisor,
        Employee.roles,
    ):
        assert relationship.property.lazy == "raise"


def test_leave_balance_model_maps_to_table() -> None:
    assert LeaveBalance.__table__ is _table("leave_balance")
