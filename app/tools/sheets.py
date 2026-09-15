import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

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
CellValue = str | int | float | bool


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

    async def inspect_sheet(sheet_name: str) -> dict:
        """Discover populated cells and their actual coordinates in a sheet."""
        return await _run_sheets(
            service.read_cells,
            spreadsheet_id,
            sheet_name,
            None,
            debug_log=debug_log,
        )

    async def read_cells(sheet_name: str, cell_range: str) -> dict:
        """
        Read values from an exact A1 range in a sheet.

        Args:
            sheet_name:
                The name of the sheet/tab to read.
            cell_range:
                An unqualified A1 range such as K20:M30.

        Returns:
            Values and coordinates from the exact range.
        """
        return await _run_sheets(
            service.read_cells,
            spreadsheet_id,
            sheet_name,
            cell_range,
            debug_log=debug_log,
        )

    async def append_rows(
        sheet_name: str,
        cell_range: str,
        values: list[list[CellValue]],
    ) -> dict:
        """
        Prepare rows to append to a table and request user confirmation.

        cell_range must identify the table starting at its header, such as
        K20:M. values contains only the new data rows, not the header.
        """
        return await create_pending_action(
            "append_rows",
            {
                "sheet_name": sheet_name,
                "cell_range": cell_range,
                "values": values,
            },
        )

    async def update_cells(
        sheet_name: str,
        cell_range: str,
        values: list[list[CellValue]],
    ) -> dict:
        """
        Prepare replacement values for an exact A1 range and request confirmation.

        cell_range must be bounded, such as K20:M21, and values must have the
        same row and column dimensions as that range.
        """
        return await create_pending_action(
            "update_cells",
            {
                "sheet_name": sheet_name,
                "cell_range": cell_range,
                "values": values,
            },
        )

    async def create_sheet(title: str) -> dict:
        """Prepare creation of a new sheet/tab and request user confirmation."""
        return await create_pending_action("create_sheet", {"title": title})

    async def rename_sheet(sheet_name: str, new_name: str) -> dict:
        """Prepare a sheet/tab rename and request user confirmation."""
        return await create_pending_action(
            "rename_sheet",
            {"sheet_name": sheet_name, "new_name": new_name},
        )

    async def delete_row(sheet_name: str, row_number: int) -> dict:
        """Prepare permanent deletion of one row and request confirmation."""
        return await create_pending_action(
            "delete_row",
            {"sheet_name": sheet_name, "row_number": row_number},
        )

    async def delete_sheet(sheet_name: str) -> dict:
        """Prepare permanent deletion of one sheet/tab and request confirmation."""
        return await create_pending_action(
            "delete_sheet",
            {"sheet_name": sheet_name},
        )

    async def resolve_sheet_action(
        choice: str,
        confirmation_code: str,
    ) -> dict:
        """
        Resolve a pending action after the user explicitly chooses an option.

        Confirm or cancel only in a later user turn. Preview may be requested
        immediately if the user asked for one. Use the private confirmation_code
        returned by the tool; never show that code to the user.

        Args:
            choice: One of "confirm", "preview", or "cancel".
            confirmation_code: The private code returned when preparing the action.
        """
        # The SDK generates a schema for Literal but cannot invoke it: its
        # argument converter uses isinstance(), which rejects Literal types.
        if choice not in {"confirm", "preview", "cancel"}:
            raise ValueError("choice must be confirm, preview, or cancel")
        return await manage_pending_action(choice, confirmation_code)

    read_tools = [
        list_sheets,
        inspect_sheet,
        read_cells,
    ]
    if create_pending_action is None:
        return read_tools
    tools = read_tools + [
        append_rows,
        update_cells,
        create_sheet,
        rename_sheet,
        delete_row,
        delete_sheet,
    ]
    if manage_pending_action is not None:
        tools.append(resolve_sheet_action)
    return tools
