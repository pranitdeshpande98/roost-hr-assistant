"""Talk to Roost in the terminal: the real agent, handbook, model chain and memory.

The conversation is saved in Postgres, so follow-up questions work. Type /new to start
a fresh conversation, or pass --thread to resume one (its id is printed at the start).
Pass --employee to act as a signed-in employee, which vacation requests need.

Usage:
    uv run python scripts/chat.py
    uv run python scripts/chat.py --language vi --channel kiosk --show-tools
    uv run python scripts/chat.py --employee 100042
    uv run python scripts/chat.py --thread <id>
"""

import argparse
import logging
import sys
import time
import uuid
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from roost.agent.conversation import ask
from roost.agent.factory import build_agent
from roost.agent.memory import open_checkpointer
from roost.agent.state import AgentState
from roost.core.config import get_settings
from roost.core.enums import Channel, Language
from roost.db.models import Employee
from roost.llm.types import ChatMessage

logger = logging.getLogger("chat")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with Roost in the terminal.")
    parser.add_argument("--language", choices=[lang.value for lang in Language], default="en")
    parser.add_argument("--channel", choices=[ch.value for ch in Channel], default="kiosk")
    parser.add_argument("--thread", help="resume a saved conversation by its id")
    parser.add_argument("--employee", help="act as this employee number (stands in for sign-in)")
    parser.add_argument(
        "--show-tools", action="store_true", help="print each handbook search and its results"
    )
    return parser.parse_args(argv)


def _write(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _employee_id(session: Session, employee_number: str) -> uuid.UUID:
    found = session.scalar(select(Employee.id).where(Employee.employee_number == employee_number))
    if found is None:
        raise SystemExit(f"No employee with number {employee_number}")
    return found


def _this_turn(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Messages since the employee's latest question."""
    users = [i for i, message in enumerate(messages) if message.role == "user"]
    return messages[users[-1] + 1 :] if users else messages


def _show_tool_activity(agent: CompiledStateGraph[AgentState, Any, Any, Any], thread: str) -> None:
    messages = agent.get_state({"configurable": {"thread_id": thread}}).values["messages"]
    for message in _this_turn(messages):
        for call in message.tool_calls:
            _write(f"  [{call.name}] {call.arguments}")
        if message.role == "tool":
            _write("  [result] " + (message.content or "").splitlines()[0][:120])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    language, channel = Language(args.language), Channel(args.channel)
    database_url = get_settings().resolved_database_url.get_secret_value()

    logger.info("Loading Roost (first run loads the embedding model) ...")
    engine = create_engine(database_url)
    try:
        with open_checkpointer(database_url) as checkpointer:
            sessions = sessionmaker(engine)
            agent = build_agent(sessions, checkpointer=checkpointer)
            employee_id = None
            if args.employee:
                with sessions() as session:
                    employee_id = _employee_id(session, args.employee)
            thread = args.thread or str(uuid.uuid4())
            who = f"employee {args.employee}" if args.employee else "not signed in"
            _write(
                f"Roost is ready ({language.value}, {channel.value}, {who}). Conversation {thread}"
            )
            _write("Type /new for a fresh conversation, or 'quit' to exit.")

            while True:
                try:
                    question = input("\nyou> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if question.lower() in {"quit", "exit"}:
                    break
                if question == "/new":
                    thread = str(uuid.uuid4())
                    _write(f"New conversation {thread}")
                    continue
                if not question:
                    continue

                started = time.perf_counter()
                reply = ask(
                    agent,
                    thread,
                    question,
                    language=language,
                    channel=channel,
                    employee_id=employee_id,
                )
                if args.show_tools:
                    _show_tool_activity(agent, thread)
                _write(f"\nroost> {reply}")
                _write(f"  ({time.perf_counter() - started:.1f} s)")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
