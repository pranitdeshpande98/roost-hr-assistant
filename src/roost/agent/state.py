"""The state every node in the agent graph reads and updates."""

import operator
import uuid
from typing import Annotated

from pydantic import BaseModel, Field

from roost.core.enums import Channel, Language
from roost.llm.types import ChatMessage


class AgentState(BaseModel):
    """One conversation's working memory while the graph runs.

    Nodes never change this object directly. Each node returns a dict of updates,
    and LangGraph merges them in: most fields are replaced, but `messages` uses
    `operator.add`, so a node's new messages are appended to the history.
    """

    messages: Annotated[list[ChatMessage], operator.add] = Field(default_factory=list)
    language: Language
    channel: Channel
    employee_id: uuid.UUID | None = None  # set after kiosk login or Teams sign-in
    tool_rounds: int = 0  # how many times tools have run this turn (loop guard)
    reply: str | None = None  # final text shown to the employee, set by the last node
