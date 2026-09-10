from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.sheet_session import SheetSession


class ActiveSheetSession(Base):
    __tablename__ = "active_sheet_sessions"

    user_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )

    sheet_session_id: Mapped[int] = mapped_column(
        ForeignKey("sheet_sessions.id"),
        nullable=False,
        unique=False,
    )

    sheet_session: Mapped[SheetSession] = relationship(
        back_populates="active_session"
    )
