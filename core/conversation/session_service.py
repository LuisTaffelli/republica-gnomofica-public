from __future__ import annotations

from .chat_session import (
    ChatSession,
    MemoryPolicy,
    SessionMode,
)
from .repository import ConversationRepository


class SessionService:
    def __init__(
        self,
        repository: ConversationRepository,
    ) -> None:
        self.repository = repository

    def create(
        self,
        *,
        profile_name: str = "miku",
        mode: SessionMode = "persistent",
        memory_policy: MemoryPolicy = "enabled",
        title: str | None = None,
    ) -> ChatSession:
        session = ChatSession(
            profile_name=profile_name,
            mode=mode,
            memory_policy=memory_policy,
            title=title,
        )

        if session.is_persistent:
            self.repository.create_session(session)

        return session

    def resume(self, session_id: str) -> ChatSession:
        session = self.repository.load_session(session_id)
        own_count = len(session.messages)

        context_messages = (
            self.repository.load_context_messages(
                session_id
            )
        )

        session.messages = context_messages
        session.metadata["inherited_message_count"] = (
            len(context_messages) - own_count
        )
        return session

    def branch(
        self,
        *,
        parent_session_id: str,
        parent_message_id: str,
        mode: SessionMode = "persistent",
        memory_policy: MemoryPolicy | None = None,
        title: str | None = None,
    ) -> ChatSession:
        branch = self.repository.create_branch(
            parent_session_id=parent_session_id,
            parent_message_id=parent_message_id,
            mode=mode,
            memory_policy=memory_policy,
            title=title,
        )

        inherited_messages = (
            self.repository.load_context_messages_until(
                parent_session_id,
                parent_message_id,
            )
        )

        branch.messages = inherited_messages
        branch.metadata["inherited_message_count"] = len(
            inherited_messages
        )
        return branch
