import asyncio
import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.debug_logging import DebugLogger
from app.models.active_sheet_session import ActiveSheetSession
from app.models.pending_sheet_action import PendingSheetAction
from app.models.sheet_session import SheetSession
from app.services.google_sheets import GoogleSheetsService
from app.tools.sheets import _run_sheets

MAX_ROWS = 500
MAX_COLUMNS = 100
MAX_CELL_LENGTH = 50_000
MAX_TOTAL_CELLS = 5_000
MAX_TOTAL_CHARACTERS = 200_000


@dataclass(frozen=True)
class ActionOrigin:
    user_id: str
    waha_session: str | None
    chat_id: str
    message_id: str | None
    sheet_session_id: int


@dataclass(frozen=True)
class ActionOutcome:
    status: str
    message: str
    code: str | None = None
    waha_session: str | None = None
    chat_id: str | None = None
    completed_now: bool = False


class InvalidActionEdit(ValueError):
    pass


class PendingSheetActionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        public_base_url: str,
        ttl_seconds: int = 900,
        debug_log: DebugLogger | None = None,
    ):
        if ttl_seconds <= 0:
            raise ValueError("Sheet action TTL must be positive")
        parsed_url = urlsplit(public_base_url)
        is_loopback = parsed_url.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or (parsed_url.scheme != "https" and not is_loopback)
            or parsed_url.path not in {"", "/"}
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "PUBLIC_BASE_URL must use HTTPS (HTTP is allowed on loopback)"
            )

        self.session_factory = session_factory
        self.public_base_url = public_base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds
        self.debug_log = debug_log or DebugLogger(False)
        self.sheets = GoogleSheetsService()
        self.execution_tasks: set[asyncio.Task[ActionOutcome]] = set()

    async def close(self) -> None:
        if self.execution_tasks:
            await asyncio.gather(*self.execution_tasks, return_exceptions=True)

    async def create_action(
        self,
        origin: ActionOrigin,
        spreadsheet_id: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        normalized, preview = await self._build_preview(
            spreadsheet_id,
            operation,
            arguments,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self.ttl_seconds
        )
        action = PendingSheetAction(
            code=secrets.token_hex(6),
            sheet_session_id=origin.sheet_session_id,
            user_id=origin.user_id,
            waha_session=origin.waha_session,
            chat_id=origin.chat_id,
            source_message_id=origin.message_id,
            spreadsheet_id=spreadsheet_id,
            operation=operation,
            arguments=normalized,
            preview=preview,
            expires_at=expires_at,
        )

        async with self.session_factory() as db:
            db.add(action)
            await db.commit()

        self.debug_log.event(
            "sheet_action.created",
            action_id=action.id,
            operation=operation,
        )
        return {
            "status": "pending_confirmation",
            "confirmation_code": action.code,
            "summary": preview["summary"],
            "expires_at": expires_at.isoformat(),
            "options": ["confirm", "preview", "cancel"],
            "instruction": (
                "Ask the user to reply with their choice. Do not choose for "
                "them or show a preview link yet."
            ),
        }

    async def issue_preview_url(
        self,
        code: str,
        user_id: str,
        message_id: str | None = None,
    ) -> ActionOutcome:
        now = datetime.now(timezone.utc)
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw_token)

        async with self.session_factory() as db:
            async with db.begin():
                action = await self._find_by_code(db, code, user_id, lock=True)
                same_turn = self._reject_same_turn(action, message_id)
                if same_turn is not None:
                    return same_turn
                invalid = await self._validate_pending(db, action, now)
                if invalid is not None:
                    return invalid

                action.token_hash = token_hash

        url = f"{self.public_base_url}/preview/{quote(raw_token)}"
        remaining = max(0, (action.expires_at - now).total_seconds())
        minutes = max(1, math.ceil(remaining / 60))
        return ActionOutcome(
            "pending",
            f"Preview: {url}\nLink berlaku selama {minutes} menit.",
            code=action.code,
        )

    async def handle_ai_action(
        self,
        origin: ActionOrigin,
        choice: str,
        code: str,
    ) -> dict[str, Any]:
        if choice == "confirm":
            outcome = await self.confirm_code(
                code,
                origin.user_id,
                origin.message_id,
            )
        elif choice == "preview":
            outcome = await self.issue_preview_url(
                code,
                origin.user_id,
                origin.message_id,
            )
        elif choice == "cancel":
            outcome = await self.cancel(
                code,
                origin.user_id,
                origin.message_id,
            )
        else:
            raise ValueError("Unsupported confirmation choice")

        return {"status": outcome.status, "message": outcome.message}

    async def cancel(
        self,
        code: str,
        user_id: str,
        message_id: str | None = None,
    ) -> ActionOutcome:
        now = datetime.now(timezone.utc)

        async with self.session_factory() as db:
            async with db.begin():
                action = await self._find_by_code(db, code, user_id, lock=True)
                same_turn = self._reject_same_turn(action, message_id)
                if same_turn is not None:
                    return same_turn
                invalid = await self._validate_pending(db, action, now)
                if invalid is not None:
                    return invalid

                action.status = "cancelled"
                action.processed_at = now

        return ActionOutcome(
            "cancelled",
            f"Aksi `{action.code}` dibatalkan.",
            code=action.code,
        )

    async def confirm_code(
        self,
        code: str,
        user_id: str,
        message_id: str | None = None,
    ) -> ActionOutcome:
        return await self._confirm(
            code=code,
            user_id=user_id,
            message_id=message_id,
        )

    async def confirm_token(
        self,
        raw_token: str,
        edits: dict[str, str],
    ) -> ActionOutcome:
        return await self._confirm(
            token_hash=self._hash_token(raw_token),
            edits=edits,
        )

    async def get_by_token(
        self,
        raw_token: str,
    ) -> PendingSheetAction | None:
        token_hash = self._hash_token(raw_token)
        async with self.session_factory() as db:
            result = await db.execute(
                select(PendingSheetAction).where(
                    PendingSheetAction.token_hash == token_hash
                )
            )
            action = result.scalar_one_or_none()
            if (
                action is not None
                and action.expires_at <= datetime.now(timezone.utc)
            ):
                return None
            return action

    async def _confirm(
        self,
        *,
        code: str | None = None,
        user_id: str | None = None,
        token_hash: str | None = None,
        edits: dict[str, str] | None = None,
        message_id: str | None = None,
    ) -> ActionOutcome:
        now = datetime.now(timezone.utc)

        async with self.session_factory() as db:
            async with db.begin():
                if token_hash is not None:
                    action = await self._find_by_token(db, token_hash, lock=True)
                else:
                    action = await self._find_by_code(
                        db,
                        code or "",
                        user_id or "",
                        lock=True,
                    )

                same_turn = self._reject_same_turn(action, message_id)
                if same_turn is not None:
                    return same_turn
                invalid = await self._validate_pending(db, action, now)
                if invalid is not None:
                    return invalid

                try:
                    final_arguments = self._apply_edits(action, edits)
                except InvalidActionEdit:
                    raise

                action.arguments = final_arguments
                action.status = "processing"

        self.debug_log.event(
            "sheet_action.processing",
            action_id=action.id,
            operation=action.operation,
        )

        task = asyncio.create_task(self._complete_claimed_action(action))
        self.execution_tasks.add(task)
        task.add_done_callback(self.execution_tasks.discard)
        return await asyncio.shield(task)

    async def _complete_claimed_action(
        self,
        action: PendingSheetAction,
    ) -> ActionOutcome:

        try:
            await self._check_preconditions(action)
            result = await self._execute(action)
        except ActionConflict as error:
            status = "conflict"
            public_error = str(error)
            result = None
        except Exception as error:
            self.debug_log.failure(
                "sheet_action.execution_failed",
                error,
                action_id=action.id,
                operation=action.operation,
            )
            status = "failed"
            public_error = "Google Sheets tidak dapat menerapkan perubahan."
            result = None
        else:
            status = "succeeded"
            public_error = None

        processed_at = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            async with db.begin():
                stored = await db.get(PendingSheetAction, action.id)
                stored.status = status
                stored.result = result
                stored.error = public_error
                stored.processed_at = processed_at

        if status == "succeeded":
            message = f"Aksi `{action.code}` berhasil diterapkan ke Google Sheets."
        else:
            message = f"Aksi `{action.code}` tidak diterapkan. {public_error}"

        return ActionOutcome(
            status,
            message,
            code=action.code,
            waha_session=action.waha_session,
            chat_id=action.chat_id,
            completed_now=True,
        )

    async def _validate_pending(
        self,
        db: AsyncSession,
        action: PendingSheetAction | None,
        now: datetime,
    ) -> ActionOutcome | None:
        if action is None:
            return ActionOutcome("not_found", "Aksi tidak ditemukan.")
        if action.status != "pending":
            return self._status_outcome(action)
        if action.expires_at <= now:
            action.status = "expired"
            action.processed_at = now
            return ActionOutcome(
                "expired",
                f"Aksi `{action.code}` sudah kedaluwarsa.",
                code=action.code,
            )
        if await self._is_stale(db, action):
            action.status = "stale"
            action.processed_at = now
            return ActionOutcome(
                "stale",
                "Aksi tidak diterapkan karena session atau spreadsheet berubah.",
                code=action.code,
            )
        return None

    async def _is_stale(
        self,
        db: AsyncSession,
        action: PendingSheetAction,
    ) -> bool:
        result = await db.execute(
            select(SheetSession.spreadsheet_link)
            .join(
                ActiveSheetSession,
                ActiveSheetSession.sheet_session_id == SheetSession.id,
            )
            .where(
                ActiveSheetSession.user_id == action.user_id,
                SheetSession.id == action.sheet_session_id,
                SheetSession.user_id == action.user_id,
            )
        )
        spreadsheet_link = result.scalar_one_or_none()
        return (
            spreadsheet_link is None
            or GoogleSheetsService.get_spreadsheet_id(spreadsheet_link)
            != action.spreadsheet_id
        )

    async def _find_by_code(
        self,
        db: AsyncSession,
        code: str,
        user_id: str,
        *,
        lock: bool,
    ) -> PendingSheetAction | None:
        statement = select(PendingSheetAction).where(
            PendingSheetAction.code == code.lower(),
            PendingSheetAction.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def _find_by_token(
        self,
        db: AsyncSession,
        token_hash: str,
        *,
        lock: bool,
    ) -> PendingSheetAction | None:
        statement = select(PendingSheetAction).where(
            PendingSheetAction.token_hash == token_hash
        )
        if lock:
            statement = statement.with_for_update()
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    async def _build_preview(
        self,
        spreadsheet_id: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if operation not in {
            "append_rows",
            "update_row",
            "create_sheet",
            "rename_sheet",
        }:
            raise ValueError("Unsupported sheet operation")

        info = await _run_sheets(
            self.sheets.get_spreadsheet_info,
            spreadsheet_id,
            debug_log=self.debug_log,
        )
        preview: dict[str, Any] = {
            "spreadsheet_title": info["title"],
            "summary": "",
        }

        if operation == "create_sheet":
            title = self._validate_title(arguments.get("title"), "title")
            preview.update(
                summary=f'Buat sheet baru "{title}"',
                title=title,
            )
            return {"title": title}, preview

        sheet_name = self._validate_title(
            arguments.get("sheet_name"),
            "sheet_name",
        )
        sheet = next(
            (item for item in info["sheets"] if item["title"] == sheet_name),
            None,
        )
        if sheet is None:
            raise ValueError(f'Sheet "{sheet_name}" does not exist')
        preview.update(sheet_name=sheet_name, sheet_id=sheet["sheet_id"])

        if operation == "rename_sheet":
            new_name = self._validate_title(
                arguments.get("new_name"),
                "new_name",
            )
            preview.update(
                summary=f'Ubah nama sheet "{sheet_name}" menjadi "{new_name}"',
                new_name=new_name,
            )
            return {"sheet_name": sheet_name, "new_name": new_name}, preview

        header = await _run_sheets(
            self.sheets.read_row,
            spreadsheet_id,
            sheet_name,
            1,
            debug_log=self.debug_log,
        )

        if operation == "append_rows":
            values = self._validate_rows(arguments.get("values"))
            width = max(len(row) for row in values)
            preview.update(
                summary=f"Tambahkan {len(values)} baris ke {sheet_name}",
                columns=self._column_labels(header, width),
                rows=values,
            )
            return {"sheet_name": sheet_name, "values": values}, preview

        row_number = arguments.get("row_number")
        if not isinstance(row_number, int) or isinstance(row_number, bool):
            raise ValueError("row_number must be an integer")
        if row_number < 1:
            raise ValueError("row_number must be >= 1")
        values = self._validate_row(arguments.get("values"))
        before = await _run_sheets(
            self.sheets.read_row,
            spreadsheet_id,
            sheet_name,
            row_number,
            debug_log=self.debug_log,
        )
        preview.update(
            summary=f"Perbarui baris {row_number} di {sheet_name}",
            columns=self._column_labels(header, len(values)),
            rows=[values],
            before=before,
            row_number=row_number,
        )
        return {
            "sheet_name": sheet_name,
            "row_number": row_number,
            "values": values,
        }, preview

    def _apply_edits(
        self,
        action: PendingSheetAction,
        edits: dict[str, str] | None,
    ) -> dict[str, Any]:
        arguments = dict(action.arguments)
        if edits is None:
            return arguments

        if action.operation in {"append_rows", "update_row"}:
            original_rows = (
                arguments["values"]
                if action.operation == "append_rows"
                else [arguments["values"]]
            )
            expected = {
                f"cell-{row_index}-{column_index}"
                for row_index, row in enumerate(original_rows)
                for column_index in range(len(row))
            }
            if set(edits) != expected:
                raise InvalidActionEdit("Data preview tidak lengkap atau tidak valid.")

            rows = []
            for row_index, row in enumerate(original_rows):
                next_row = []
                for column_index, original in enumerate(row):
                    value = edits[f"cell-{row_index}-{column_index}"]
                    try:
                        self._validate_cell(value)
                    except ValueError as error:
                        raise InvalidActionEdit(str(error)) from error
                    next_row.append(original if value == str(original) else value)
                rows.append(next_row)
            arguments["values"] = rows if action.operation == "append_rows" else rows[0]
            return arguments

        field = "title" if action.operation == "create_sheet" else "new_name"
        if set(edits) != {field}:
            raise InvalidActionEdit("Data preview tidak lengkap atau tidak valid.")
        try:
            arguments[field] = self._validate_title(edits[field], field)
        except ValueError as error:
            raise InvalidActionEdit(str(error)) from error
        return arguments

    async def _check_preconditions(self, action: PendingSheetAction) -> None:
        info = await _run_sheets(
            self.sheets.get_spreadsheet_info,
            action.spreadsheet_id,
            debug_log=self.debug_log,
        )
        arguments = action.arguments

        if action.operation == "create_sheet":
            if any(
                sheet["title"] == arguments["title"]
                for sheet in info["sheets"]
            ):
                raise ActionConflict("Nama sheet sudah digunakan.")
            return

        sheet = next(
            (
                item
                for item in info["sheets"]
                if item["sheet_id"] == action.preview["sheet_id"]
            ),
            None,
        )
        if sheet is None or sheet["title"] != action.preview["sheet_name"]:
            raise ActionConflict("Sheet target sudah berubah.")

        if action.operation == "rename_sheet":
            if any(
                item["title"] == arguments["new_name"]
                and item["sheet_id"] != sheet["sheet_id"]
                for item in info["sheets"]
            ):
                raise ActionConflict("Nama sheet baru sudah digunakan.")
            return

        if action.operation == "update_row":
            current = await _run_sheets(
                self.sheets.read_row,
                action.spreadsheet_id,
                arguments["sheet_name"],
                arguments["row_number"],
                debug_log=self.debug_log,
            )
            if current != action.preview["before"]:
                raise ActionConflict("Baris target berubah sejak preview dibuat.")

    async def _execute(self, action: PendingSheetAction) -> dict[str, Any]:
        arguments = action.arguments
        method = getattr(self.sheets, action.operation)

        if action.operation == "append_rows":
            args = (arguments["sheet_name"], arguments["values"])
        elif action.operation == "update_row":
            args = (
                arguments["sheet_name"],
                arguments["row_number"],
                arguments["values"],
            )
        elif action.operation == "create_sheet":
            args = (arguments["title"],)
        else:
            args = (arguments["sheet_name"], arguments["new_name"])

        return await _run_sheets(
            method,
            action.spreadsheet_id,
            *args,
            debug_log=self.debug_log,
        )

    def _status_outcome(self, action: PendingSheetAction) -> ActionOutcome:
        messages = {
            "processing": "Aksi sedang diproses.",
            "succeeded": "Aksi ini sudah diterapkan.",
            "failed": "Aksi ini gagal dan tidak dapat digunakan lagi.",
            "expired": "Aksi ini sudah kedaluwarsa.",
            "stale": "Session atau spreadsheet untuk aksi ini sudah berubah.",
            "conflict": "Data spreadsheet berubah sebelum aksi diterapkan.",
            "cancelled": "Aksi ini sudah dibatalkan.",
        }
        return ActionOutcome(
            action.status,
            messages.get(action.status, "Aksi tidak tersedia."),
            code=action.code,
            waha_session=action.waha_session,
            chat_id=action.chat_id,
        )

    @staticmethod
    def _reject_same_turn(
        action: PendingSheetAction | None,
        message_id: str | None,
    ) -> ActionOutcome | None:
        if (
            action is not None
            and message_id is not None
            and action.source_message_id == message_id
        ):
            return ActionOutcome(
                "awaiting_user_confirmation",
                "Tunggu pilihan pengguna pada pesan berikutnya.",
                code=action.code,
            )
        return None

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_title(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} cannot be empty")
        if len(value) > 100:
            raise ValueError(f"{field} is too long")
        return value

    @classmethod
    def _validate_rows(cls, values: Any) -> list[list[Any]]:
        if not isinstance(values, list) or not values or len(values) > MAX_ROWS:
            raise ValueError("values must contain between 1 and 500 rows")
        rows = [cls._validate_row(row) for row in values]
        if sum(len(row) for row in rows) > MAX_TOTAL_CELLS:
            raise ValueError("values contain too many cells")
        if sum(len(str(value)) for row in rows for value in row) > MAX_TOTAL_CHARACTERS:
            raise ValueError("values are too large")
        return rows

    @classmethod
    def _validate_row(cls, row: Any) -> list[Any]:
        if not isinstance(row, list) or not row or len(row) > MAX_COLUMNS:
            raise ValueError("Each row must contain between 1 and 100 cells")
        for value in row:
            cls._validate_cell(value)
        if sum(len(str(value)) for value in row) > MAX_TOTAL_CHARACTERS:
            raise ValueError("Row values are too large")
        return row

    @staticmethod
    def _validate_cell(value: Any) -> None:
        if not isinstance(value, (str, int, float, bool)) or isinstance(
            value, complex
        ):
            raise ValueError("Cells must contain text, numbers, or booleans")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Cell numbers must be finite")
        if len(str(value)) > MAX_CELL_LENGTH:
            raise ValueError("Cell value is too long")

    @classmethod
    def _column_labels(cls, header: list, width: int) -> list[str]:
        labels = []
        for index in range(width):
            if index < len(header) and str(header[index]).strip():
                labels.append(str(header[index]))
            else:
                labels.append(cls._column_name(index + 1))
        return labels

    @staticmethod
    def _column_name(number: int) -> str:
        name = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            name = chr(65 + remainder) + name
        return name


class ActionConflict(RuntimeError):
    pass
