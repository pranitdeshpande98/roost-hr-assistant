"""Deterministic synthetic employees and leave balances for the mock HR system.

Everything here is fake. The same seed always produces the same data, so
tests, evals and demos are reproducible.
"""

import math
import random
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from faker import Faker

from roost.core.enums import Language, LeaveType, LocationKind, PayrollClass, Role
from roost.seed.reference import (
    CORPORATE_STAFFING,
    EXECUTIVE_OFFICE,
    LOCATIONS,
    OFFICE_LANGUAGES,
    PLANT_ADMINISTRATION,
    PLANT_STAFFING,
    SHIFTS,
    LocationSpec,
    StaffingRule,
)

EMAIL_DOMAIN = "hollowcreek.example"  # .example is reserved and can never be a real domain
FAKER_LOCALES = {Language.EN: "en_US", Language.ES: "es_MX", Language.VI: "vi_VN"}
SHIFT_HOURS = {shift.code: shift.paid_hours for shift in SHIFTS}
TEAM_SIZE = 25  # employees per team lead
SMALL_GROUP = 10  # groups this small report straight to the location head
NEW_HIRE_SHARE = 0.05  # share of employees still in the 90-day introductory period
HOURS = Decimal("0.01")


@dataclass(slots=True)
class SyntheticEmployee:
    """One fake employee, ready to be written to the employee table."""

    id: uuid.UUID
    employee_number: str
    first_name: str
    last_name: str
    preferred_language: Language
    payroll_class: PayrollClass
    department: str
    location_code: str
    shift_code: str
    supervisor_id: uuid.UUID | None
    hire_date: date
    birth_date: date
    email: str | None
    has_teams: bool
    roles: set[Role] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class SyntheticBalance:
    """One leave balance row for the leave_balance table."""

    employee_id: uuid.UUID
    leave_type: LeaveType
    year: int
    accrued_hours: Decimal
    carried_over_hours: Decimal
    used_hours: Decimal


@dataclass(frozen=True, slots=True)
class SeedData:
    """Everything the seed script writes to the database."""

    employees: list[SyntheticEmployee]
    balances: list[SyntheticBalance]


def _ascii(text: str) -> str:
    """Lower-case ASCII for email addresses ('Nguyễn Đức' -> 'nguyenduc')."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if ch.isascii() and ch.isalnum()).lower()


def split_headcount(total: int, shares: Mapping[str, Decimal]) -> dict[str, int]:
    """Split a headcount by share, always summing exactly to total.

    Uses the largest-remainder method, so rounding never loses or adds people.
    """
    exact = {name: share * total for name, share in shares.items()}
    counts = {name: int(value) for name, value in exact.items()}
    leftover = total - sum(counts.values())
    by_remainder = sorted(exact, key=lambda name: exact[name] - counts[name], reverse=True)
    for name in by_remainder[:leftover]:
        counts[name] += 1
    return counts


class _Generator:
    """Builds the organization one location at a time."""

    def __init__(self, *, as_of: date, seed: int, total: int) -> None:
        self.as_of = as_of
        self.rng = random.Random(seed)  # noqa: S311 - fake data, not security
        self.fakers: dict[Language, Faker] = {}
        for offset, (language, locale) in enumerate(FAKER_LOCALES.items()):
            faker = Faker(locale)
            faker.seed_instance(seed + offset)
            self.fakers[language] = faker
        self.numbers = iter(self.rng.sample(range(100_000, 1_000_000), total))
        self.employees: list[SyntheticEmployee] = []

    # ── people ────────────────────────────────────────────────
    def _pick_language(self, weights: Sequence[tuple[Language, int]]) -> Language:
        languages = [language for language, _ in weights]
        return self.rng.choices(languages, weights=[w for _, w in weights])[0]

    def _hire_and_birth_dates(self) -> tuple[date, date]:
        if self.rng.random() < NEW_HIRE_SHARE:
            hire = self.as_of - timedelta(days=self.rng.randint(0, 89))
        else:
            hire = self.as_of - timedelta(days=self.rng.randint(90, 25 * 365))
        age_at_hire_days = self.rng.randint(18 * 366, 60 * 365)
        return hire, hire - timedelta(days=age_at_hire_days)

    def add(
        self,
        *,
        department: str,
        location: LocationSpec,
        payroll_class: PayrollClass,
        shift_code: str,
        languages: Sequence[tuple[Language, int]],
        supervisor: SyntheticEmployee | None,
        roles: set[Role] | None = None,
    ) -> SyntheticEmployee:
        language = self._pick_language(languages)
        faker = self.fakers[language]
        # vi_VN's generic first_name() returns placeholder names; gendered methods work in all locales.
        first = faker.first_name_female() if self.rng.random() < 0.5 else faker.first_name_male()
        last = faker.last_name()
        number = str(next(self.numbers))
        is_driver = payroll_class is PayrollClass.DRIVER
        hire, birth = self._hire_and_birth_dates()
        employee = SyntheticEmployee(
            id=uuid.UUID(int=self.rng.getrandbits(128), version=4),
            employee_number=number,
            first_name=first,
            last_name=last,
            preferred_language=language,
            payroll_class=payroll_class,
            department=department,
            location_code=location.code,
            shift_code=shift_code,
            supervisor_id=supervisor.id if supervisor else None,
            hire_date=hire,
            birth_date=birth,
            email=None if is_driver else f"{_ascii(first)}.{_ascii(last)}.{number}@{EMAIL_DOMAIN}",
            has_teams=not is_driver,
            roles=set(roles or ()),
        )
        if supervisor is not None:
            supervisor.roles.add(Role.SUPERVISOR)
        self.employees.append(employee)
        return employee

    # ── teams ─────────────────────────────────────────────────
    def staff_group(
        self,
        *,
        rule: StaffingRule,
        location: LocationSpec,
        shift_code: str,
        size: int,
        head: SyntheticEmployee,
    ) -> list[SyntheticEmployee]:
        """Staff one department/location/shift group, adding team leads if it is large."""

        def person(supervisor: SyntheticEmployee) -> SyntheticEmployee:
            return self.add(
                department=rule.department,
                location=location,
                payroll_class=rule.payroll_class,
                shift_code=shift_code,
                languages=rule.languages,
                supervisor=supervisor,
            )

        if size <= SMALL_GROUP:
            return [person(head) for _ in range(size)]
        leads = [person(head) for _ in range(math.ceil(size / TEAM_SIZE))]
        members = [person(leads[i % len(leads)]) for i in range(size - len(leads))]
        return leads + members

    def staff_location(
        self,
        location: LocationSpec,
        rules: Sequence[StaffingRule],
        head: SyntheticEmployee,
        size: int,
    ) -> None:
        department_sizes = split_headcount(size, {r.department: r.share for r in rules})
        for rule in rules:
            even_share = Decimal(1) / len(rule.shift_codes)
            per_shift = split_headcount(
                department_sizes[rule.department], dict.fromkeys(rule.shift_codes, even_share)
            )
            for shift_code in rule.shift_codes:
                self.staff_group(
                    rule=rule,
                    location=location,
                    shift_code=shift_code,
                    size=per_shift[shift_code],
                    head=head,
                )


def _accrued_vacation(employee: SyntheticEmployee, as_of: date) -> Decimal:
    """Approximate vacation earned so far this year (handbook Section 6.1)."""
    annual = 10 * SHIFT_HOURS[employee.shift_code]
    periods_per_year, period_days = (
        (26, 14)
        if employee.payroll_class in {PayrollClass.EXECUTIVE, PayrollClass.BIWEEKLY}
        else (52, 7)
    )
    start = max(date(as_of.year, 1, 1), employee.hire_date)
    periods = (as_of - start).days // period_days
    return (annual / periods_per_year * periods).quantize(HOURS, ROUND_HALF_UP)


def _balances(
    employee: SyntheticEmployee, as_of: date, rng: random.Random
) -> list[SyntheticBalance]:
    shift_hours = SHIFT_HOURS[employee.shift_code]
    half_day = shift_hours / 2
    hired_this_year = employee.hire_date.year == as_of.year

    accrued = _accrued_vacation(employee, as_of)
    max_carry_half_days = 0 if hired_this_year else 10  # up to 5 days (Section 6.9)
    carried = half_day * rng.randint(0, max_carry_half_days)
    vacation_used = half_day * rng.randint(0, int((accrued + carried) // half_day))

    sick_granted = 2 * shift_hours  # Section 8.1
    sick_used = half_day * rng.randint(0, 4)

    return [
        SyntheticBalance(
            employee.id, LeaveType.VACATION, as_of.year, accrued, carried, vacation_used
        ),
        SyntheticBalance(
            employee.id, LeaveType.SICK, as_of.year, sick_granted, Decimal(0), sick_used
        ),
    ]


def generate_seed_data(
    *, as_of: date, seed: int = 42, plant_headcount: int = 1450, corporate_headcount: int = 150
) -> SeedData:
    """Generate the whole synthetic organization.

    Structure: CEO -> VPs -> corporate directors -> corporate staff, and
    CEO -> VP of Operations -> plant managers -> team leads -> plant staff.
    """
    plants = [loc for loc in LOCATIONS if loc.kind is LocationKind.PLANT]
    corporate = next(loc for loc in LOCATIONS if loc.kind is LocationKind.CORPORATE)
    total = plant_headcount * len(plants) + corporate_headcount
    gen = _Generator(as_of=as_of, seed=seed, total=total)

    def executive(department: str, supervisor: SyntheticEmployee | None) -> SyntheticEmployee:
        return gen.add(
            department=department,
            location=corporate,
            payroll_class=PayrollClass.EXECUTIVE,
            shift_code="OFFICE",
            languages=OFFICE_LANGUAGES,
            supervisor=supervisor,
        )

    # Executive leadership: CEO and VP of Operations
    ceo = executive(EXECUTIVE_OFFICE, None)
    vp_operations = executive(EXECUTIVE_OFFICE, ceo)

    # Corporate departments: a VP and a Director each, then staff (Section 3.1)
    corporate_size = corporate_headcount - 2 - 2 * len(CORPORATE_STAFFING)
    sizes = split_headcount(corporate_size, {r.department: r.share for r in CORPORATE_STAFFING})
    for rule in CORPORATE_STAFFING:
        vp = executive(rule.department, ceo)
        director = executive(rule.department, vp)
        if rule.department == "Human Resources":
            vp.roles.add(Role.HR_ADMIN)
            director.roles.add(Role.HR_ADMIN)
        staff = gen.staff_group(
            rule=rule,
            location=corporate,
            shift_code="OFFICE",
            size=sizes[rule.department],
            head=director,
        )
        if rule.department == "Human Resources":
            for person in staff:
                person.roles.add(Role.HR_GENERALIST)

    # Plants: a plant manager, then every department and shift
    for plant in plants:
        manager = gen.add(
            department=PLANT_ADMINISTRATION,
            location=plant,
            payroll_class=PayrollClass.WEEKLY_PRODUCTION,
            shift_code="OFFICE",
            languages=OFFICE_LANGUAGES,
            supervisor=vp_operations,
        )
        before = len(gen.employees)
        gen.staff_location(plant, PLANT_STAFFING, manager, plant_headcount - 1)
        admin_staff = [e for e in gen.employees[before:] if e.department == PLANT_ADMINISTRATION]
        for hr_person in admin_staff[:2]:  # two HR generalists per plant (Section 2.2)
            hr_person.roles.add(Role.HR_GENERALIST)

    balances = [b for e in gen.employees for b in _balances(e, as_of, gen.rng)]
    return SeedData(employees=gen.employees, balances=balances)
