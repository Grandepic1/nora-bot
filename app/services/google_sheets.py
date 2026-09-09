import os
import re

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
    ]

class GoogleSheetsService:
    def __init__(self):
        credentials_path = os.getenv("GOOGLE_SERVICE_CLIENT_CRED_PATH")

        if not credentials_path:
            raise RuntimeError(
            "GOOGLE_SERVICE_CRED_PATH is not configured"
            )

        credentials = Credentials.from_service_account_file(
            credentials_path,
            scopes=SCOPES
        )

        self.service = build(
            "sheets",
            "v4",
            credentials=credentials,
            cache_discovery=False
        )

    @staticmethod
    def _quote_sheet_name(sheet_name: str) -> str:
        escaped = sheet_name.replace("'","''")
        return f"'{escaped}'"

    @staticmethod
    def extract_spreadsheet_id(
        spreadsheet_url: str,
    ) -> str:
        pattern = r"/spreadsheets/d/([a-zA-Z0-9-_]+)"

        match = re.search(pattern, spreadsheet_url)

        if not match:
            raise ValueError("Invalid Google Sheets URL")

        return match.group(1)

    def get_spreadsheet_info(self, spreadsheet_id:str) -> dict:
        result = (
            self.service.spreadsheets()
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
                    "column_count": grid.get("columnCount")
                }
            )

        return {
            "spreadsheet_id": result["spreadsheetId"],
            "title": result["properties"]["title"],
            "sheets":sheets
        }

    def list_sheets(
        self,
        spreadsheet_id: str,
    ) -> list[dict]:
        return self.get_spreadsheet_info(spreadsheet_id)["sheets"]

    def get_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> dict:
        sheets = self.list_sheets(spreadsheet_id)

        for sheet in sheets:
            if sheet["title"] == sheet_name:
                return sheet

        raise ValueError(f'Sheet "{sheet_name}" does not exist')

    def get_sheet_id(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> int:
        return self.get_sheet(
            spreadsheet_id,
            sheet_name,
        )["sheet_id"]

    def read_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> list[list]:
        range_name = self._quote_sheet_name(sheet_name)

        result = (
            self.service.spreadsheets()
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

        result = (
            self.service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet}!{row_number}:{row_number}",
            )
            .execute()
        )

        values = result.get("values", [])

        if not values:
            return []

        return values[0]

    def append_rows(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        values: list[list],
    ) -> dict:
        if not values:
            raise ValueError("values cannot be empty")

        sheet = self._quote_sheet_name(sheet_name)

        result = (
            self.service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=sheet,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={
                    "values": values,
                },
            )
            .execute()
        )

        updates = result.get("updates", {})

        return {
            "updated_range": updates.get("updatedRange"),
            "updated_rows": updates.get("updatedRows", 0),
            "updated_cells": updates.get("updatedCells", 0),
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

        result = (
            self.service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet}!A{row_number}",
                valueInputOption="USER_ENTERED",
                body={
                    "values": [values],
                },
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

        result = (
            self.service.spreadsheets()
            .values()
            .clear(
                spreadsheetId=spreadsheet_id,
                range=f"{sheet}!{row_number}:{row_number}",
                body={},
            )
            .execute()
        )

        return {
            "cleared_range": result.get("clearedRange"),
        }

    def delete_row(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        row_number: int,
    ) -> dict:
        if row_number < 1:
            raise ValueError("row_number must be >= 1")

        sheet_id = self.get_sheet_id(
            spreadsheet_id,
            sheet_name,
        )

        # API indexes rows from 0, while Sheets UI starts from 1.
        start_index = row_number - 1

        (
            self.service.spreadsheets()
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

    def create_sheet(
        self,
        spreadsheet_id: str,
        title: str,
    ) -> dict:
        if not title.strip():
            raise ValueError("Sheet title cannot be empty")

        result = (
            self.service.spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": title,
                                }
                            }
                        }
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

        sheet_id = self.get_sheet_id(
            spreadsheet_id,
            sheet_name,
        )

        (
            self.service.spreadsheets()
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

    def clear_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> dict:
        sheet = self._quote_sheet_name(sheet_name)

        result = (
            self.service.spreadsheets()
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
    
    def delete_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> dict:
        sheet_id = self.get_sheet_id(
            spreadsheet_id,
            sheet_name,
        )

        (
            self.service.spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [
                        {
                            "deleteSheet": {
                                "sheetId": sheet_id,
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
        }