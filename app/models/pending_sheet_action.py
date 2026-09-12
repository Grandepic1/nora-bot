from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PendingSheetAction(Base):
    __tablename__ = "pending_sheet_actions"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('append_rows', 'update_row', "
            "'create_sheet', 'rename_sheet')",
            name="pending_sheet_actions_operation",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', "
            "'failed', 'expired', 'stale', 'conflict', 'cancelled')",
            name="pending_sheet_actions_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    code: Mapped[str] = mapped_column(String(12), unique=True)
    token_hash: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        nullable=True,
    )
    sheet_session_id: Mapped[int] = mapped_column(
        ForeignKey("sheet_sessions.id"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    waha_session: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_id: Mapped[str] = mapped_column(String(255))
    source_message_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    spreadsheet_id: Mapped[str] = mapped_column(String(255))
    operation: Mapped[str] = mapped_column(String(32))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="pending",
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
