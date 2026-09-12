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

    async def list_sheets(
    ) -> list[dict]:
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


    async def read_sheet(
        sheet_name: str,
    ) -> list[list]:
        """
        Read all populated values from a sheet.

        Args:
            sheet_name:
                The name of the sheet/tab to read.

        Returns:
            All populated rows from the sheet.
        """
        return await _run_sheets(
            service.read_sheet,
            spreadsheet_id,
            sheet_name,
            debug_log=debug_log,
        )


    async def read_row(
        sheet_name: str,
        row_number: int,
    ) -> list:
        """
        Read one row from a Google Sheet.

        Args:
            sheet_name:
                The sheet/tab name.

            row_number:
                The visible Google Sheets row number.
                Row numbering starts at 1.

        Returns:
            Values contained in the requested row.
        """
        return await _run_sheets(
            service.read_row,
            spreadsheet_id,
            sheet_name,
            row_number,
            debug_log=debug_log,
        )


    async def append_rows(
        sheet_name: str,
        values: list[list],
    ) -> dict:
        """
        Prepare rows to append and request user confirmation.
        """
        return await create_pending_action(
            "append_rows",
            {"sheet_name": sheet_name, "values": values},
        )


    async def update_row(
        sheet_name: str,
        row_number: int,
        values: list,
    ) -> dict:
        """
        Prepare replacement values and request user confirmation.
        """
        return await create_pending_action(
            "update_row",
            {
                "sheet_name": sheet_name,
                "row_number": row_number,
                "values": values,
            },
        )


    async def create_sheet(
        title: str,
    ) -> dict:
        """
        Prepare a new sheet/tab and request user confirmation.
        """
        return await create_pending_action(
            "create_sheet",
            {"title": title},
        )


    async def rename_sheet(
        sheet_name: str,
        new_name: str,
    ) -> dict:
        """
        Prepare a sheet/tab rename and request user confirmation.
        """
        return await create_pending_action(
            "rename_sheet",
            {"sheet_name": sheet_name, "new_name": new_name},
        )

    async def confirm_sheet_action(confirmation_code: str) -> dict:
        """Confirm and apply a pending sheet action after the user agrees."""
        return await manage_pending_action("confirm", confirmation_code)

    async def preview_sheet_action(confirmation_code: str) -> dict:
        """Generate a preview link after the user asks to see the preview."""
        return await manage_pending_action("preview", confirmation_code)

    async def cancel_sheet_action(confirmation_code: str) -> dict:
        """Cancel a pending sheet action after the user declines it."""
        return await manage_pending_action("cancel", confirmation_code)


    read_tools = [
        list_sheets,
        read_sheet,
        read_row,
    ]
    if create_pending_action is None:
        return read_tools
    tools = read_tools + [append_rows, update_row, create_sheet, rename_sheet]
    if manage_pending_action is not None:
        tools += [
            confirm_sheet_action,
            preview_sheet_action,
            cancel_sheet_action,
        ]
    return tools
