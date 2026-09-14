import os
import re
from contextlib import contextmanager
from urllib.parse import urlparse

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


class GoogleSheetsService:
    def __init__(self):
        self.credentials_path = os.getenv("GOOGLE_SERVICE_CLIENT_CRED_PATH")

        if not self.credentials_path:
            raise RuntimeError(
                "GOOGLE_SERVICE_CLIENT_CRED_PATH is not configured"
            )

    @contextmanager
    def _service(self):
        credentials = Credentials.from_service_account_file(
            self.credentials_path,
            scopes=SCOPES,
        )
        service = build(
            "sheets",
            "v4",
            credentials=credentials,
            cache_discovery=False,
        )

        try:
            yield service
        finally:
            service.close()

    @staticmethod
    def _quote_sheet_name(sheet_name: str) -> str:
        escaped = sheet_name.replace("'", "''")
        return f"'{escaped}'"

    @staticmethod
    def normalize_cell_range(cell_range: str) -> str:
        normalized = cell_range.strip().upper()
        if (
            not normalized
            or len(normalized) > 100
            or "!" in normalized
            or not re.fullmatch(
                r"(?:[A-Z]+\d+(?::[A-Z]+\d*)?|[A-Z]+:[A-Z]+|\d+:\d+)",
                normalized,
            )
            or any(int(row) < 1 for row in re.findall(r"\d+", normalized))
        ):
            raise ValueError("cell_range must use A1 notation, such as K20:M30")
        return normalized

    @classmethod
    def _qualified_range(cls, sheet_name: str, cell_range: str) -> str:
        return (
            f"{cls._quote_sheet_name(sheet_name)}!"
            f"{cls.normalize_cell_range(cell_range)}"
        )

    @staticmethod
    def _column_name(number: int) -> str:
        name = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            name = chr(65 + remainder) + name
        return name

    @staticmethod
    def get_spreadsheet_id(url: str) -> str | None:
        try:
            parsed = urlparse(url)

            if parsed.scheme not in {"http", "https"}:
                return None

            if parsed.netloc != "docs.google.com":
                return None

            parts = parsed.path.strip("/").split("/")

            # Expected:
            # /spreadsheets/d/{spreadsheet_id}/...
            if len(parts) < 3:
                return None

            if parts[0] != "spreadsheets" or parts[1] != "d":
                return None

            spreadsheet_id = parts[2].strip()

            if not spreadsheet_id:
                return None

            return spreadsheet_id

        except Exception:
            return None

    @staticmethod
    def _get_spreadsheet_info(service, spreadsheet_id: str) -> dict:
        result = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields=(
                    "spreadsheetId,"
                    "properties(title),"
                    "sheets(properties("
                    "sheetId,"
                    "title,"
                    "index,"
                    "gridProperties(rowCount,columnCount)"
                    "))"
                ),
            )
            .execute()
        )

        sheets = []

        for sheet in result.get("sheets", []):
            props = sheet["properties"]
            grid = props.get("gridProperties", {})
            sheets.append(
                {
                    "sheet_id": props["sheetId"],
                    "title": props["title"],
                    "index": props["index"],
                    "row_count": grid.get("rowCount"),
                    "column_count": grid.get("columnCount"),
                }
            )

        return {
            "spreadsheet_id": result["spreadsheetId"],
            "title": result["properties"]["title"],
            "sheets": sheets,
        }

    @classmethod
    def _get_sheet(cls, service, spreadsheet_id: str, sheet_name: str) -> dict:
        sheets = cls._get_spreadsheet_info(service, spreadsheet_id)["sheets"]

        for sheet in sheets:
            if sheet["title"] == sheet_name:
                return sheet

        raise ValueError(f'Sheet "{sheet_name}" does not exist')

    @classmethod
    def _get_sheet_id(
        cls,
        service,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> int:
        return cls._get_sheet(service, spreadsheet_id, sheet_name)["sheet_id"]

    def get_spreadsheet_info(self, spreadsheet_id: str) -> dict:
        with self._service() as service:
            return self._get_spreadsheet_info(service, spreadsheet_id)

    def list_sheets(self, spreadsheet_id: str) -> list[dict]:
        with self._service() as service:
            return self._get_spreadsheet_info(service, spreadsheet_id)["sheets"]

    def get_sheet(self, spreadsheet_id: str, sheet_name: str) -> dict:
        with self._service() as service:
            return self._get_sheet(service, spreadsheet_id, sheet_name)

    def get_sheet_id(self, spreadsheet_id: str, sheet_name: str) -> int:
        with self._service() as service:
            return self._get_sheet_id(service, spreadsheet_id, sheet_name)

    def read_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> list[list]:
        range_name = self._quote_sheet_name(sheet_name)

        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                )
                .execute()
            )

        return result.get("values", [])

    def read_row(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        row_number: int,
    ) -> list:
        if row_number < 1:
            raise ValueError("row_number must be >= 1")

        sheet = self._quote_sheet_name(sheet_name)

        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet}!{row_number}:{row_number}",
                )
                .execute()
            )

        values = result.get("values", [])
        return values[0] if values else []

    def read_cells(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        cell_range: str | None = None,
    ) -> dict:
        range_name = (
            self._qualified_range(sheet_name, cell_range)
            if cell_range is not None
            else self._quote_sheet_name(sheet_name)
        )

        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                )
                .execute()
            )

        values = result.get("values", [])
        if cell_range is not None:
            normalized = self.normalize_cell_range(cell_range)
            start = re.match(r"([A-Z]+)?(\d+)?", normalized)
            return {
                "sheet_name": sheet_name,
                "range": result.get("range", range_name),
                "start_column": start.group(1) if start else None,
                "start_row": (
                    int(start.group(2)) if start and start.group(2) else None
                ),
                "values": values,
            }

        compact_rows = []
        populated = []
        for row_number, row in enumerate(values, start=1):
            cells = {}
            for column_number, value in enumerate(row, start=1):
                if value in {"", None}:
                    continue
                column = self._column_name(column_number)
                cells[column] = value
                populated.append((row_number, column_number))
            if cells:
                compact_rows.append({"row_number": row_number, "cells": cells})

        populated_range = None
        if populated:
            first_row = min(row for row, _ in populated)
            last_row = max(row for row, _ in populated)
            first_column = min(column for _, column in populated)
            last_column = max(column for _, column in populated)
            populated_range = (
                f"{sheet_name}!{self._column_name(first_column)}{first_row}:"
                f"{self._column_name(last_column)}{last_row}"
            )

        return {
            "sheet_name": sheet_name,
            "populated_range": populated_range,
            "rows": compact_rows,
        }

    def append_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        values: list[list],
        cell_range: str | None = None,
    ) -> dict:
        if not values:
            raise ValueError("values cannot be empty")

        sheet = (
            self._qualified_range(sheet_name, cell_range)
            if cell_range is not None
            else self._quote_sheet_name(sheet_name)
        )

        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=sheet,
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": values},
                )
                .execute()
            )

        updates = result.get("updates", {})
        return {
            "updated_range": updates.get("updatedRange"),
            "updated_rows": updates.get("updatedRows", 0),
            "updated_cells": updates.get("updatedCells", 0),
        }

    def update_cells(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        cell_range: str,
        values: list[list],
    ) -> dict:
        if not values or any(not isinstance(row, list) for row in values):
            raise ValueError("values must contain at least one row")

        range_name = self._qualified_range(sheet_name, cell_range)
        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                )
                .execute()
            )

        return {
            "updated_range": result.get("updatedRange"),
            "updated_rows": result.get("updatedRows", 0),
            "updated_cells": result.get("updatedCells", 0),
        }

    def update_row(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        row_number: int,
        values: list,
    ) -> dict:
        if row_number < 1:
            raise ValueError("row_number must be >= 1")
        if not values:
            raise ValueError("values cannot be empty")

        sheet = self._quote_sheet_name(sheet_name)

        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet}!A{row_number}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [values]},
                )
                .execute()
            )

        return {
            "updated_range": result.get("updatedRange"),
            "updated_rows": result.get("updatedRows", 0),
            "updated_cells": result.get("updatedCells", 0),
        }

    def clear_row(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        row_number: int,
    ) -> dict:
        if row_number < 1:
            raise ValueError("row_number must be >= 1")

        sheet = self._quote_sheet_name(sheet_name)

        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .clear(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet}!{row_number}:{row_number}",
                    body={},
                )
                .execute()
            )

        return {"cleared_range": result.get("clearedRange")}

    def delete_row(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        row_number: int,
    ) -> dict:
        if row_number < 1:
            raise ValueError("row_number must be >= 1")

        start_index = row_number - 1

        with self._service() as service:
            sheet_id = self._get_sheet_id(service, spreadsheet_id, sheet_name)
            (
                service.spreadsheets()
                .batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={
                        "requests": [
                            {
                                "deleteDimension": {
                                    "range": {
                                        "sheetId": sheet_id,
                                        "dimension": "ROWS",
                                        "startIndex": start_index,
                                        "endIndex": start_index + 1,
                                    }
                                }
                            }
                        ]
                    },
                )
                .execute()
            )

        return {
            "deleted": True,
            "sheet_name": sheet_name,
            "row_number": row_number,
        }

    def create_sheet(self, spreadsheet_id: str, title: str) -> dict:
        if not title.strip():
            raise ValueError("Sheet title cannot be empty")

        with self._service() as service:
            result = (
                service.spreadsheets()
                .batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={
                        "requests": [
                            {"addSheet": {"properties": {"title": title}}}
                        ]
                    },
                )
                .execute()
            )

        properties = result["replies"][0]["addSheet"]["properties"]
        return {
            "sheet_id": properties["sheetId"],
            "title": properties["title"],
            "index": properties["index"],
        }

    def rename_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        new_name: str,
    ) -> dict:
        if not new_name.strip():
            raise ValueError("New sheet name cannot be empty")

        with self._service() as service:
            sheet_id = self._get_sheet_id(service, spreadsheet_id, sheet_name)
            (
                service.spreadsheets()
                .batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={
                        "requests": [
                            {
                                "updateSheetProperties": {
                                    "properties": {
                                        "sheetId": sheet_id,
                                        "title": new_name,
                                    },
                                    "fields": "title",
                                }
                            }
                        ]
                    },
                )
                .execute()
            )

        return {
            "renamed": True,
            "old_name": sheet_name,
            "new_name": new_name,
        }

    def clear_sheet(self, spreadsheet_id: str, sheet_name: str) -> dict:
        sheet = self._quote_sheet_name(sheet_name)

        with self._service() as service:
            result = (
                service.spreadsheets()
                .values()
                .clear(
                    spreadsheetId=spreadsheet_id,
                    range=sheet,
                    body={},
                )
                .execute()
            )

        return {
            "cleared": True,
            "sheet_name": sheet_name,
            "cleared_range": result.get("clearedRange"),
        }

    def delete_sheet(self, spreadsheet_id: str, sheet_name: str) -> dict:
        with self._service() as service:
            sheet_id = self._get_sheet_id(service, spreadsheet_id, sheet_name)
            (
                service.spreadsheets()
                .batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={
                        "requests": [
                            {"deleteSheet": {"sheetId": sheet_id}}
                        ]
                    },
                )
                .execute()
            )

        return {
            "deleted": True,
            "sheet_name": sheet_name,
        }
