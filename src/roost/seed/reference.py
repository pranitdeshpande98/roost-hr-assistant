"""Reference data for Hollow Creek Poultry Co. (fictional), taken from the handbook."""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from roost.core.enums import Language, LocationKind, PayrollClass


@dataclass(frozen=True, slots=True)
class LocationSpec:
    """A plant or the corporate office (handbook Section 2)."""

    code: str
    name: str
    state: str
    kind: LocationKind
    timezone: str
    hr_phone: str


@dataclass(frozen=True, slots=True)
class ShiftSpec:
    """A work schedule (handbook Section 4)."""

    code: str
    name: str
    start_time: time
    end_time: time
    paid_hours: Decimal
    workdays: tuple[int, ...]  # ISO weekdays: 1 = Monday


@dataclass(frozen=True, slots=True)
class StaffingRule:
    """How one department is staffed at a type of location."""

    department: str
    share: Decimal  # fraction of the location's headcount
    payroll_class: PayrollClass
    shift_codes: tuple[str, ...]
    languages: tuple[tuple[Language, int], ...]  # (language, weight)


LOCATIONS: tuple[LocationSpec, ...] = (
    LocationSpec(
        "GA_PLANT", "Georgia Plant", "GA", LocationKind.PLANT, "America/New_York", "(555) 010-1100"
    ),
    LocationSpec(
        "AR_PLANT", "Arkansas Plant", "AR", LocationKind.PLANT, "America/Chicago", "(555) 010-2200"
    ),
    LocationSpec(
        "NC_PLANT",
        "North Carolina Plant",
        "NC",
        LocationKind.PLANT,
        "America/New_York",
        "(555) 010-3300",
    ),
    LocationSpec(
        "TX_CORP",
        "Corporate Office",
        "TX",
        LocationKind.CORPORATE,
        "America/Chicago",
        "(555) 010-4400",
    ),
)

WEEKDAYS = (1, 2, 3, 4, 5)

SHIFTS: tuple[ShiftSpec, ...] = (
    ShiftSpec(
        "PROD_1ST", "Production 1st shift", time(6, 0), time(14, 30), Decimal("8.00"), WEEKDAYS
    ),
    ShiftSpec(
        "PROD_2ND", "Production 2nd shift", time(15, 0), time(23, 30), Decimal("8.00"), WEEKDAYS
    ),
    ShiftSpec("SANITATION", "Sanitation", time(23, 30), time(6, 0), Decimal("8.00"), WEEKDAYS),
    ShiftSpec("OFFICE", "Office hours", time(8, 0), time(17, 0), Decimal("8.00"), WEEKDAYS),
    ShiftSpec(
        "DRIVER_ROUTE", "Driver routes", time(5, 0), time(15, 0), Decimal("10.00"), (1, 2, 3, 4)
    ),
)

PRODUCTION_LANGUAGES = ((Language.ES, 45), (Language.VI, 20), (Language.EN, 35))
SKILLED_LANGUAGES = ((Language.EN, 60), (Language.ES, 30), (Language.VI, 10))
DRIVER_LANGUAGES = ((Language.EN, 80), (Language.ES, 20))
OFFICE_LANGUAGES = ((Language.EN, 85), (Language.ES, 10), (Language.VI, 5))

PRODUCTION_SHIFTS = ("PROD_1ST", "PROD_2ND")

PLANT_STAFFING: tuple[StaffingRule, ...] = (
    StaffingRule(
        "Live Receiving",
        Decimal("0.08"),
        PayrollClass.WEEKLY_PRODUCTION,
        PRODUCTION_SHIFTS,
        PRODUCTION_LANGUAGES,
    ),
    StaffingRule(
        "Evisceration",
        Decimal("0.20"),
        PayrollClass.WEEKLY_PRODUCTION,
        PRODUCTION_SHIFTS,
        PRODUCTION_LANGUAGES,
    ),
    StaffingRule(
        "Deboning",
        Decimal("0.28"),
        PayrollClass.WEEKLY_PRODUCTION,
        PRODUCTION_SHIFTS,
        PRODUCTION_LANGUAGES,
    ),
    StaffingRule(
        "Packaging",
        Decimal("0.18"),
        PayrollClass.WEEKLY_PRODUCTION,
        PRODUCTION_SHIFTS,
        PRODUCTION_LANGUAGES,
    ),
    StaffingRule(
        "Sanitation",
        Decimal("0.08"),
        PayrollClass.WEEKLY_PRODUCTION,
        ("SANITATION",),
        PRODUCTION_LANGUAGES,
    ),
    StaffingRule(
        "Maintenance",
        Decimal("0.05"),
        PayrollClass.WEEKLY_PRODUCTION,
        PRODUCTION_SHIFTS,
        SKILLED_LANGUAGES,
    ),
    StaffingRule(
        "Quality Assurance",
        Decimal("0.04"),
        PayrollClass.WEEKLY_PRODUCTION,
        PRODUCTION_SHIFTS,
        SKILLED_LANGUAGES,
    ),
    StaffingRule(
        "Shipping and Logistics",
        Decimal("0.06"),
        PayrollClass.DRIVER,
        ("DRIVER_ROUTE",),
        DRIVER_LANGUAGES,
    ),
    StaffingRule(
        "IT Help Desk", Decimal("0.01"), PayrollClass.WEEKLY_IT, ("OFFICE",), OFFICE_LANGUAGES
    ),
    StaffingRule(
        "Plant Administration",
        Decimal("0.02"),
        PayrollClass.WEEKLY_PRODUCTION,
        ("OFFICE",),
        OFFICE_LANGUAGES,
    ),
)

CORPORATE_STAFFING: tuple[StaffingRule, ...] = (
    StaffingRule("Finance", Decimal("0.25"), PayrollClass.BIWEEKLY, ("OFFICE",), OFFICE_LANGUAGES),
    StaffingRule(
        "Human Resources", Decimal("0.20"), PayrollClass.BIWEEKLY, ("OFFICE",), OFFICE_LANGUAGES
    ),
    StaffingRule("Sales", Decimal("0.25"), PayrollClass.BIWEEKLY, ("OFFICE",), OFFICE_LANGUAGES),
    StaffingRule(
        "Supply Chain", Decimal("0.15"), PayrollClass.BIWEEKLY, ("OFFICE",), OFFICE_LANGUAGES
    ),
    StaffingRule(
        "Information Technology",
        Decimal("0.15"),
        PayrollClass.WEEKLY_IT,
        ("OFFICE",),
        OFFICE_LANGUAGES,
    ),
)

EXECUTIVE_OFFICE = "Executive Office"
PLANT_ADMINISTRATION = "Plant Administration"


def department_locations() -> set[tuple[str, str]]:
    """Every (department name, location code) pair that exists."""
    pairs: set[tuple[str, str]] = set()
    for location in LOCATIONS:
        rules = PLANT_STAFFING if location.kind is LocationKind.PLANT else CORPORATE_STAFFING
        pairs.update((rule.department, location.code) for rule in rules)
    pairs.add((EXECUTIVE_OFFICE, "TX_CORP"))
    return pairs
