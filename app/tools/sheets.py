import asyncio

from app.services.google_sheets import GoogleSheetsService


sheets: GoogleSheetsService | None = None
SHEETS_LIMIT = asyncio.Semaphore(5)


def _get_sheets() -> GoogleSheetsService:
    global sheets

    if sheets is None:
        sheets = GoogleSheetsService()

    return sheets


async def _run_sheets(func, *args):
    async with SHEETS_LIMIT:
        return await asyncio.to_thread(func, *args)

async def check_spreadsheet_access(
    spreadsheet_id: str,
) -> bool:
    try:
        service = _get_sheets()
        await _run_sheets(
            service.list_sheets,
            spreadsheet_id,
        )
        return True

    except Exception:
        return False

def build_sheet_tools(spreadsheet_id: str) -> list:
    service = _get_sheets()

    async def list_sheets(
    ) -> list[dict]:
        """
        List all sheets/tabs in a Google Spreadsheet.
        Returns:
            A list of sheets containing their names,
            IDs, indexes, row counts, and column counts.
        """
        return await _run_sheets(service.list_sheets, spreadsheet_id)


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
        )


    async def append_rows(
        sheet_name: str,
        values: list[list],
    ) -> dict:
        """
        Append one or more rows to the end of a sheet.
        """
        return await _run_sheets(
            service.append_rows,
            spreadsheet_id,
            sheet_name,
            values,
        )


    async def update_row(
        sheet_name: str,
        row_number: int,
        values: list,
    ) -> dict:
        """
        Replace values in an existing row.
        """
        return await _run_sheets(
            service.update_row,
            spreadsheet_id,
            sheet_name,
            row_number,
            values,
        )


    async def create_sheet(
        title: str,
    ) -> dict:
        """
        Create a new sheet/tab inside a spreadsheet.
        """
        return await _run_sheets(
            service.create_sheet,
            spreadsheet_id,
            title,
        )


    async def rename_sheet(
        sheet_name: str,
        new_name: str,
    ) -> dict:
        """
        Rename an existing sheet/tab.
        """
        return await _run_sheets(
            service.rename_sheet,
            spreadsheet_id,
            sheet_name,
            new_name,
        )


    return [
        list_sheets,
        read_sheet,
        read_row,
        append_rows,
        update_row,
        create_sheet,
        rename_sheet,
    ]
