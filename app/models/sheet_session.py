from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Identity, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.active_sheet_session import ActiveSheetSession


class SheetSession(Base):
    __tablename__ = "sheet_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "session_name",
            name="uq_sheet_sessions_user_id_session_name",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    session_name: Mapped[str] = mapped_column(String(255))
    spreadsheet_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(String(255))

    active_session: Mapped[ActiveSheetSession | None] = relationship(
        back_populates="sheet_session"
    )
