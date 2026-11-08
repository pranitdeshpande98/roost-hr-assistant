"""Transcripts: record each turn, show them to HR admins, hold and delete them
(FR-45, FR-46, FR-48, ADR-0014).

Every function works in the caller's transaction and writes the audit log there.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from roost.audit import log as audit_log
from roost.auth.console import roles_of
from roost.core.enums import AuditAction, Channel, EmploymentStatus, Language, Role
from roost.db.models import Conversation, Employee, TranscriptMessage, WebSession
from roost.guardrails.citations import cited_sections

KEEP_TRANSCRIPTS = timedelta(days=45)  # FR-45
MAX_HOLD_REASON = 200


@dataclass(frozen=True, slots=True)
class ConversationRow:
    conversation: Conversation
    employee: Employee | None
    messages: int
    sensitive: bool  # any turn routed as a sensitive matter or emergency


# --- recording ---------------------------------------------------------------------------


def conversation_for(
    session: Session,
    *,
    thread_id: uuid.UUID,
    employee_id: uuid.UUID | None,
    channel: Channel,
    language: Language,
    now: datetime,
) -> Conversation:
    """The conversation for an agent thread, created on its first message."""
    found = session.scalars(select(Conversation).where(Conversation.thread_id == thread_id)).first()
    if found is not None:
        return found
    conversation = Conversation(
        thread_id=thread_id,
        employee_id=employee_id,
        channel=channel,
        language=language,
        started_at=now,
        last_activity_at=now,
    )
    session.add(conversation)
    session.flush()
    return conversation


def record_turn(
    session: Session,
    conversation: Conversation,
    *,
    said: str,
    reply: str | None,
    kind: str,
    category: str | None,
    resumed: bool,
    now: datetime,
) -> None:
    """The employee's words and Roost's reply, exactly as shown. A YES or NO to a question
    is recorded as an answer, so the question before it is never lost (FR-45)."""
    session.add(
        TranscriptMessage(
            conversation_id=conversation.id,
            role="employee",
            kind="answer" if resumed else "message",
            content=said,
            category=category,
            created_at=now,
        )
    )
    if reply is not None:
        session.add(
            TranscriptMessage(
                conversation_id=conversation.id,
                role="roost",
                kind=kind,
                content=reply,
                category=category,
                cited_sections=cited_sections(reply),
                created_at=now,
            )
        )
    conversation.last_activity_at = now
    session.flush()


def messages_for_thread(session: Session, thread_id: uuid.UUID) -> list[TranscriptMessage]:
    """What the kiosk shows: this sign-in's transcript, oldest first."""
    return list(
        session.scalars(
            select(TranscriptMessage)
            .join(Conversation)
            .where(Conversation.thread_id == thread_id)
            .order_by(TranscriptMessage.id)
        )
    )


# --- HR admins: read and hold (FR-46, FR-48) -----------------------------------------------


def may_read_transcripts(session: Session, viewer_id: uuid.UUID) -> bool:
    """HR admins only (data-store table, requirements 4.7)."""
    viewer = session.get(Employee, viewer_id)
    if viewer is None or viewer.employment_status is not EmploymentStatus.ACTIVE:
        return False
    return Role.HR_ADMIN in roles_of(session, viewer_id)


def list_conversations(
    session: Session,
    viewer_id: uuid.UUID,
    *,
    employee_number: str | None = None,
    held_only: bool = False,
    limit: int = 100,
) -> list[ConversationRow]:
    """Conversations still kept (the last 45 days, or held), newest first. An HR admin
    never sees their own."""
    if not may_read_transcripts(session, viewer_id):
        return []
    counts = (
        select(
            TranscriptMessage.conversation_id,
            func.count().label("messages"),
            func.bool_or(TranscriptMessage.category.is_not(None)).label("sensitive"),
        )
        .group_by(TranscriptMessage.conversation_id)
        .subquery()
    )
    query = (
        select(Conversation, Employee, counts.c.messages, counts.c.sensitive)
        .join(counts, counts.c.conversation_id == Conversation.id)
        .outerjoin(Employee, Employee.id == Conversation.employee_id)
        .where(Conversation.employee_id.is_distinct_from(viewer_id))
        .order_by(Conversation.last_activity_at.desc())
        .limit(limit)
    )
    if employee_number:
        query = query.where(Employee.employee_number == employee_number.strip())
    if held_only:
        query = query.where(Conversation.legal_hold.is_(True))
    return [
        ConversationRow(conversation, employee, messages, bool(sensitive))
        for conversation, employee, messages, sensitive in session.execute(query)
    ]


def _readable(
    session: Session, viewer_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation | None:
    if not may_read_transcripts(session, viewer_id):
        return None
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.employee_id == viewer_id:
        return None
    return conversation


def view_conversation(
    session: Session, viewer_id: uuid.UUID, conversation_id: uuid.UUID
) -> tuple[Conversation, Employee | None] | None:
    """One transcript for an HR admin. Every view is recorded in the audit log."""
    conversation = _readable(session, viewer_id, conversation_id)
    if conversation is None:
        return None
    audit_log.record(
        session,
        AuditAction.TRANSCRIPT_VIEWED,
        actor=viewer_id,
        entity_type="conversation",
        entity_id=str(conversation.id),
    )
    employee = session.get(Employee, conversation.employee_id) if conversation.employee_id else None
    return conversation, employee


def place_hold(
    session: Session,
    viewer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    reason: str,
    now: datetime,
) -> bool:
    """FR-48: keep this conversation past 45 days until the hold is released."""
    conversation = _readable(session, viewer_id, conversation_id)
    if conversation is None or conversation.legal_hold:
        return False
    conversation.legal_hold = True
    conversation.held_by_id = viewer_id
    conversation.held_at = now
    conversation.hold_reason = reason.strip()[:MAX_HOLD_REASON] or None
    audit_log.record(
        session,
        AuditAction.LEGAL_HOLD_PLACED,
        actor=viewer_id,
        entity_type="conversation",
        entity_id=str(conversation.id),
    )
    session.flush()
    return True


def release_hold(session: Session, viewer_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
    """The 45 days run again from the last message: a conversation older than that is
    deleted at the next retention run."""
    conversation = _readable(session, viewer_id, conversation_id)
    if conversation is None or not conversation.legal_hold:
        return False
    conversation.legal_hold = False
    conversation.held_by_id = None
    conversation.held_at = None
    conversation.hold_reason = None
    audit_log.record(
        session,
        AuditAction.LEGAL_HOLD_RELEASED,
        actor=viewer_id,
        entity_type="conversation",
        entity_id=str(conversation.id),
    )
    session.flush()
    return True


# --- retention (FR-45) ---------------------------------------------------------------------


def delete_expired(session: Session, *, now: datetime) -> list[uuid.UUID]:
    """Delete conversations with no message for 45 days and no legal hold. Returns their
    agent memory threads, which the caller deletes too."""
    expired = session.execute(
        delete(Conversation)
        .where(
            Conversation.last_activity_at < now - KEEP_TRANSCRIPTS,
            Conversation.legal_hold.is_(False),
        )
        .returning(Conversation.thread_id)
    )
    return [row.thread_id for row in expired]


def threads_to_forget(session: Session, thread_ids: Iterable[str]) -> list[str]:
    """Agent memory threads nothing needs any more: no conversation record (expired, or a
    sign-in with no message) and no kiosk session still open."""
    known = {str(t) for t in session.scalars(select(Conversation.thread_id))}
    live = {str(t) for t in session.scalars(select(WebSession.thread_id))}
    return sorted(t for t in thread_ids if t not in known and t not in live)
