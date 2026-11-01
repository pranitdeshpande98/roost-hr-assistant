"""The my_pay_schedule tool: the signed-in employee's next paydays (FR-05).

Reads only the employee's payroll class, a category, never any amount (FR-06).
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.agent.tools import ToolContext
from roost.core.enums import PayrollClass
from roost.db.models import Employee
from roost.llm.types import ToolSpec
from roost.payroll.schedule import Payday, is_weekly, upcoming_paydays
from roost.tools.vacation import NOT_SIGNED_IN

CLASS_NAMES = {
    PayrollClass.EXECUTIVE: "Executive",
    PayrollClass.BIWEEKLY: "Biweekly",
    PayrollClass.WEEKLY_IT: "Weekly IT Help Desk",
    PayrollClass.WEEKLY_PRODUCTION: "Weekly Production",
    PayrollClass.DRIVER: "Drivers",
}


def _line(payday: Payday) -> str:
    text = (
        f"{payday.paid_on.isoformat()} ({payday.paid_on.strftime('%A')}), for the pay period "
        f"{payday.period_start.isoformat()} to {payday.period_end.isoformat()}"
    )
    if payday.holiday:
        text += f"; moved from {payday.scheduled.isoformat()} because of {payday.holiday} [§5.2]"
    return text


def describe_schedule(payroll_class: PayrollClass, paydays: list[Payday]) -> str:
    """Tool result: class, frequency and upcoming paydays. Contains no amounts."""
    frequency = "every Friday" if is_weekly(payroll_class) else "every other Friday"
    lines = [
        f"Payroll class: {CLASS_NAMES[payroll_class]} (paid {frequency}) [§5.1].",
        f"Next payday: {_line(paydays[0])}.",
    ]
    lines += [f"Then: {_line(p)}." for p in paydays[1:]]
    lines.append("Do not state or estimate any pay amount; for pay questions refer to HR.")
    return "\n".join(lines)


class PayScheduleTool:
    def __init__(self, sessions: Callable[[], AbstractContextManager[Session]]) -> None:
        self._sessions = sessions

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="my_pay_schedule",
            description=(
                "The signed-in employee's payroll class and next three paydays, with the "
                "pay period each one covers. Dates only, never amounts."
            ),
            parameters={"type": "object", "properties": {}},
        )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:  # noqa: ARG002 - takes no arguments
        if context.employee_id is None:
            return NOT_SIGNED_IN
        with self._sessions() as session:
            payroll_class = session.scalar(
                select(Employee.payroll_class).where(Employee.id == context.employee_id)
            )
        if payroll_class is None:
            return "Error: employee not found."
        return describe_schedule(payroll_class, upcoming_paydays(payroll_class, context.today))
