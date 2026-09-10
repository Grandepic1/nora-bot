from sqlalchemy import BigInteger, Identity, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


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
