"""Unit tests for the synthetic employee generator."""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import pytest

from roost.core.enums import Language, LeaveType, PayrollClass, Role
from roost.seed.employees import SeedData, generate_seed_data, split_headcount
from roost.seed.reference import LOCATIONS, PLANT_ADMINISTRATION, department_locations

AS_OF = date(2026, 11, 1)
CORPORATE_CLASSES = {PayrollClass.EXECUTIVE, PayrollClass.BIWEEKLY, PayrollClass.WEEKLY_IT}


@pytest.fixture(scope="module")
def data() -> SeedData:
    """One small organization shared by all tests in this file (generated once)."""
    return generate_seed_data(as_of=AS_OF, seed=7, plant_headcount=300, corporate_headcount=60)


def test_split_headcount_always_sums_to_total() -> None:
    shares = {"a": Decimal("0.333"), "b": Decimal("0.333"), "c": Decimal("0.334")}

    assert sum(split_headcount(100, shares).values()) == 100
    assert sum(split_headcount(7, shares).values()) == 7


def test_headcount_per_location(data: SeedData) -> None:
    counts = Counter(e.location_code for e in data.employees)

    assert counts == {"GA_PLANT": 300, "AR_PLANT": 300, "NC_PLANT": 300, "TX_CORP": 60}


def test_same_seed_gives_same_data() -> None:
    first = generate_seed_data(as_of=AS_OF, seed=1, plant_headcount=50, corporate_headcount=30)
    second = generate_seed_data(as_of=AS_OF, seed=1, plant_headcount=50, corporate_headcount=30)
    other = generate_seed_data(as_of=AS_OF, seed=2, plant_headcount=50, corporate_headcount=30)

    assert first == second
    assert first != other


def test_employee_numbers_are_unique_six_digits(data: SeedData) -> None:
    numbers = [e.employee_number for e in data.employees]

    assert len(set(numbers)) == len(numbers)
    assert all(len(n) == 6 and n.isdigit() for n in numbers)


def test_exactly_one_employee_has_no_supervisor(data: SeedData) -> None:
    top = [e for e in data.employees if e.supervisor_id is None]

    assert len(top) == 1
    assert top[0].payroll_class is PayrollClass.EXECUTIVE


def test_every_reporting_chain_reaches_the_ceo(data: SeedData) -> None:
    by_id = {e.id: e for e in data.employees}

    for employee in data.employees:
        current, steps = employee, 0
        while current.supervisor_id is not None:
            current = by_id[current.supervisor_id]  # KeyError = dangling supervisor
            steps += 1
            assert steps < 10, "reporting chain too long or circular"


def test_anyone_with_reports_is_a_supervisor(data: SeedData) -> None:
    supervisor_ids = {e.supervisor_id for e in data.employees if e.supervisor_id}

    for employee in data.employees:
        assert (employee.id in supervisor_ids) == (Role.SUPERVISOR in employee.roles)


def test_departments_only_at_locations_where_they_operate(data: SeedData) -> None:
    valid = department_locations()

    assert {(e.department, e.location_code) for e in data.employees} <= valid


def test_payroll_classes_match_location(data: SeedData) -> None:
    for employee in data.employees:
        if employee.location_code == "TX_CORP":
            assert employee.payroll_class in CORPORATE_CLASSES
        else:
            assert employee.payroll_class not in {PayrollClass.EXECUTIVE, PayrollClass.BIWEEKLY}


def test_drivers_have_no_email_or_teams_and_others_do(data: SeedData) -> None:
    for employee in data.employees:
        is_driver = employee.payroll_class is PayrollClass.DRIVER
        assert (employee.email is None) == is_driver
        assert employee.has_teams == (not is_driver)


def test_emails_are_ascii_and_use_reserved_domain(data: SeedData) -> None:
    emails = [e.email for e in data.employees if e.email]

    assert all(email.isascii() for email in emails)
    assert all(email.endswith("@hollowcreek.example") for email in emails)
    assert len(set(emails)) == len(emails)


def test_drivers_work_driver_routes(data: SeedData) -> None:
    drivers = [e for e in data.employees if e.payroll_class is PayrollClass.DRIVER]

    assert drivers
    assert {e.shift_code for e in drivers} == {"DRIVER_ROUTE"}


def test_plants_have_spanish_and_vietnamese_speakers(data: SeedData) -> None:
    plant_languages = {e.preferred_language for e in data.employees if e.location_code != "TX_CORP"}

    assert {Language.EN, Language.ES, Language.VI} <= plant_languages


def test_hr_roles_exist_at_every_location(data: SeedData) -> None:
    for location in LOCATIONS:
        roles = {r for e in data.employees if e.location_code == location.code for r in e.roles}
        assert Role.HR_GENERALIST in roles

    assert any(Role.HR_ADMIN in e.roles for e in data.employees)


def test_plant_hr_generalists_work_in_plant_administration(data: SeedData) -> None:
    plant_hr = [
        e for e in data.employees if Role.HR_GENERALIST in e.roles and e.location_code != "TX_CORP"
    ]

    assert len(plant_hr) == 6  # two per plant
    assert {e.department for e in plant_hr} == {PLANT_ADMINISTRATION}


def test_dates_are_realistic(data: SeedData) -> None:
    for employee in data.employees:
        assert employee.hire_date <= AS_OF
        assert employee.birth_date + timedelta(days=18 * 365) <= employee.hire_date


def test_some_employees_are_in_introductory_period(data: SeedData) -> None:
    recent = [e for e in data.employees if (AS_OF - e.hire_date).days < 90]

    assert recent, "needed to exercise rule V-01"


def test_each_employee_has_vacation_and_sick_balance(data: SeedData) -> None:
    kinds = Counter((b.employee_id, b.leave_type) for b in data.balances)

    assert len(kinds) == 2 * len(data.employees)
    assert set(kinds.values()) == {1}


def test_balances_are_consistent(data: SeedData) -> None:
    hired = {e.id: e.hire_date for e in data.employees}

    for balance in data.balances:
        assert balance.year == AS_OF.year
        assert min(balance.accrued_hours, balance.carried_over_hours, balance.used_hours) >= 0
        assert balance.used_hours <= balance.accrued_hours + balance.carried_over_hours
        if hired[balance.employee_id].year == AS_OF.year:
            assert balance.carried_over_hours == 0


def test_sick_grant_is_two_shifts(data: SeedData) -> None:
    hours = {e.id: e.shift_code for e in data.employees}

    for balance in data.balances:
        if balance.leave_type is LeaveType.SICK:
            expected = (
                Decimal("20.00")
                if hours[balance.employee_id] == "DRIVER_ROUTE"
                else Decimal("16.00")
            )
            assert balance.accrued_hours == expected
