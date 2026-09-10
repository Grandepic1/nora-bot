from app.services.google_sheets import GoogleSheetsService


sheets = GoogleSheetsService()

def list_sheets(
    spreadsheet_id: str,
) -> list[dict]:
    """
    List all sheets/tabs in a Google Spreadsheet.

    Args:
        spreadsheet_id:
            The Google Spreadsheet ID.

    Returns:
        A list of sheets containing their names,
        IDs, indexes, row counts, and column counts.
    """
    return sheets.list_sheets(spreadsheet_id)

def read_sheet(
    spreadsheet_id: str,
    sheet_name: str,
) -> list[list]:
    """
    Read all populated values from a sheet.

    Args:
        spreadsheet_id:
            The Google Spreadsheet ID.

        sheet_name:
            The name of the sheet/tab to read.

    Returns:
        All populated rows from the sheet.
    """
    return sheets.read_sheet(
        spreadsheet_id,
        sheet_name,
    )

def read_row(
    spreadsheet_id: str,
    sheet_name: str,
    row_number: int,
) -> list:
    """
    Read one row from a Google Sheet.

    Args:
        spreadsheet_id:
            The Google Spreadsheet ID.

        sheet_name:
            The sheet/tab name.

        row_number:
            The visible Google Sheets row number.
            Row numbering starts at 1.

    Returns:
        Values contained in the requested row.
    """
    return sheets.read_row(
        spreadsheet_id,
        sheet_name,
        row_number,
    )

def append_rows(
    spreadsheet_id: str,
    sheet_name: str,
    values: list[list],
) -> dict:
    """
    Append one or more rows to the end of a sheet.
    """
    return sheets.append_rows(
        spreadsheet_id,
        sheet_name,
        values,
    )

def update_row(
    spreadsheet_id: str,
    sheet_name: str,
    row_number: int,
    values: list,
) -> dict:
    """
    Replace values in an existing row.
    """
    return sheets.update_row(
        spreadsheet_id,
        sheet_name,
        row_number,
        values,
    )

def clear_row(
    spreadsheet_id: str,
    sheet_name: str,
    row_number: int,
) -> dict:
    """
    Clear all values from a row without deleting the row itself.
    """
    return sheets.clear_row(
        spreadsheet_id,
        sheet_name,
        row_number,
    )

def delete_row(
    spreadsheet_id: str,
    sheet_name: str,
    row_number: int,
) -> dict:
    """
    Permanently delete a row and shift following rows upward.
    """
    return sheets.delete_row(
        spreadsheet_id,
        sheet_name,
        row_number,
    )

def create_sheet(
    spreadsheet_id: str,
    title: str,
) -> dict:
    """
    Create a new sheet/tab inside a spreadsheet.
    """
    return sheets.create_sheet(
        spreadsheet_id,
        title,
    )

def rename_sheet(
    spreadsheet_id: str,
    sheet_name: str,
    new_name: str,
) -> dict:
    """
    Rename an existing sheet/tab.
    """
    return sheets.rename_sheet(
        spreadsheet_id,
        sheet_name,
        new_name,
    )

def clear_sheet(
    spreadsheet_id: str,
    sheet_name: str,
) -> dict:
    """
    Clear all values from a sheet while keeping the sheet itself.
    """
    return sheets.clear_sheet(
        spreadsheet_id,
        sheet_name,
    )

def delete_sheet(
    spreadsheet_id: str,
    sheet_name: str,
) -> dict:
    """
    Permanently delete an entire sheet/tab.
    """
    return sheets.delete_sheet(
        spreadsheet_id,
        sheet_name,
    )