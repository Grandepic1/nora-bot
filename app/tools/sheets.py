import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from app.debug_logging import DebugLogger
from app.services.google_sheets import GoogleSheetsService

sheets: GoogleSheetsService | None = None
SHEETS_LIMIT = asyncio.Semaphore(5)
PendingActionCreator = Callable[
    [str, dict[str, Any]],
    Awaitable[dict[str, Any]],
]
PendingActionManager = Callable[
    [str, str],
    Awaitable[dict[str, Any]],
]


def _get_sheets() -> GoogleSheetsService:
    global sheets

    if sheets is None:
        sheets = GoogleSheetsService()

    return sheets


async def _run_sheets(
    func,
    *args,
    debug_log: DebugLogger | None = None,
):
    loop = asyncio.get_running_loop()
    operation = func.__name__

    if debug_log is not None:
        debug_log.event("sheets.worker.queued", operation=operation)

    async with SHEETS_LIMIT:
        started_at = loop.time()

        if debug_log is not None:
            debug_log.event("sheets.worker.started", operation=operation)

        try:
            result = await asyncio.to_thread(func, *args)
        except Exception as error:
            if debug_log is not None:
                debug_log.failure(
                    "sheets.worker.failed",
                    error,
                    operation=operation,
                    duration_ms=round((loop.time() - started_at) * 1000),
                )
            raise

        if debug_log is not None:
            debug_log.event(
                "sheets.worker.completed",
                operation=operation,
                duration_ms=round((loop.time() - started_at) * 1000),
            )

        return result


async def check_spreadsheet_access(
    spreadsheet_id: str,
    debug_log: DebugLogger | None = None,
) -> bool:
    try:
        service = _get_sheets()
        await _run_sheets(
            service.list_sheets,
            spreadsheet_id,
            debug_log=debug_log,
        )
        if debug_log is not None:
            debug_log.event("sheets.access.checked", accessible=True)
        return True

    except Exception:
        if debug_log is not None:
            debug_log.event("sheets.access.checked", accessible=False)
        return False


def build_sheet_tools(
    spreadsheet_id: str,
    debug_log: DebugLogger | None = None,
    create_pending_action: PendingActionCreator | None = None,
    manage_pending_action: PendingActionManager | None = None,
) -> list:
    service = _get_sheets()

    async def list_sheets() -> list[dict]:
        """
        List all sheets/tabs in a Google Spreadsheet.
        Returns:
            A list of sheets containing their names,
            IDs, indexes, row counts, and column counts.
        """
        return await _run_sheets(
            service.list_sheets,
            spreadsheet_id,
            debug_log=debug_log,
        )

    async def read_cells(
        sheet_name: str,
        cell_range: str | None = None,
    ) -> dict:
        """
        Inspect populated cells or read an exact A1 range from a sheet.

        Args:
            sheet_name:
                The name of the sheet/tab to read.
            cell_range:
                Optional unqualified A1 range such as K20:M30. Omit it to
                discover populated rows and their actual coordinates.

        Returns:
            Coordinate-aware populated cells or values from the exact range.
        """
        return await _run_sheets(
            service.read_cells,
            spreadsheet_id,
            sheet_name,
            cell_range,
            debug_log=debug_log,
        )

    async def prepare_sheet_action(
        operation: Literal[
            "append_rows",
            "update_cells",
            "create_sheet",
            "rename_sheet",
        ],
        sheet_name: str | None = None,
        cell_range: str | None = None,
        values: list[list] | None = None,
        title: str | None = None,
        new_name: str | None = None,
    ) -> dict:
        """
        Prepare one write operation for user confirmation.

        For append_rows, provide sheet_name, a target table cell_range such as
        K20:M, and values. For update_cells, provide sheet_name, the exact A1
        cell_range, and two-dimensional values. For create_sheet, provide
        title. For rename_sheet, provide sheet_name and new_name.
        """
        arguments = {
            key: value
            for key, value in {
                "sheet_name": sheet_name,
                "cell_range": cell_range,
                "values": values,
                "title": title,
                "new_name": new_name,
            }.items()
            if value is not None
        }
        return await create_pending_action(operation, arguments)

    async def resolve_sheet_action(
        choice: Literal["confirm", "preview", "cancel"],
        confirmation_code: str,
    ) -> dict:
        """
        Resolve a pending action after the user explicitly chooses an option.

        Call this only in a later user turn, never in the turn where the action
        was prepared. Use the private confirmation_code returned by the tool;
        never show that code to the user.
        """
        return await manage_pending_action(choice, confirmation_code)

    read_tools = [
        list_sheets,
        read_cells,
    ]
    if create_pending_action is None:
        return read_tools
    tools = read_tools + [prepare_sheet_action]
    if manage_pending_action is not None:
        tools.append(resolve_sheet_action)
    return tools
