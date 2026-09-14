import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import app.tools.sheets as sheet_tools
from app.models.pending_sheet_action import PendingSheetAction
from app.services.google_sheets import GoogleSheetsService
from app.services.pending_sheet_actions import (
    InvalidActionEdit,
    PendingSheetActionService,
)
from app.web.views.sheet_preview import render_sheet_preview


class FakeSheets:
    def __getattr__(self, name):
        raise AssertionError(f"Google write called unexpectedly: {name}")


class SheetToolConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_sheets = sheet_tools.sheets
        sheet_tools.sheets = FakeSheets()

    async def asyncTearDown(self):
        sheet_tools.sheets = self.previous_sheets

    async def test_prepare_tool_creates_pending_actions(self):
        calls = []

        async def create_pending(operation, arguments):
            calls.append((operation, arguments))
            return {
                "status": "pending_confirmation",
                "confirmation_code": "abc123",
                "options": ["confirm", "preview", "cancel"],
            }

        tools = sheet_tools.build_sheet_tools(
            "spreadsheet-id",
            create_pending_action=create_pending,
        )
        by_name = {tool.__name__: tool for tool in tools}

        result = await by_name["prepare_sheet_action"](
            "append_rows",
            sheet_name="Data",
            cell_range="K20:M",
            values=[["A", 1]],
        )
        await by_name["prepare_sheet_action"](
            "update_cells",
            sheet_name="Data",
            cell_range="K21:L21",
            values=[["B", 2]],
        )
        await by_name["prepare_sheet_action"](
            "create_sheet",
            title="Archive",
        )
        await by_name["prepare_sheet_action"](
            "rename_sheet",
            sheet_name="Data",
            new_name="Current",
        )

        self.assertEqual(result["status"], "pending_confirmation")
        self.assertNotIn("url", result)
        self.assertIn("preview", result["options"])
        self.assertEqual(calls[0][0], "append_rows")
        self.assertEqual(calls[1][0], "update_cells")
        self.assertEqual(calls[2][0], "create_sheet")
        self.assertEqual(calls[3][0], "rename_sheet")
        self.assertEqual(
            calls[0][1],
            {
                "sheet_name": "Data",
                "cell_range": "K20:M",
                "values": [["A", 1]],
            },
        )

    async def test_write_tools_are_not_exposed_without_confirmation_service(self):
        tools = sheet_tools.build_sheet_tools("spreadsheet-id")

        self.assertEqual(
            [tool.__name__ for tool in tools],
            ["list_sheets", "read_cells"],
        )

    async def test_confirmation_tools_delegate_ai_choice(self):
        calls = []

        async def create_pending(operation, arguments):
            return {"status": "pending_confirmation"}

        async def manage_pending(choice, code):
            calls.append((choice, code))
            return {"status": "succeeded"}

        tools = sheet_tools.build_sheet_tools(
            "spreadsheet-id",
            create_pending_action=create_pending,
            manage_pending_action=manage_pending,
        )
        by_name = {tool.__name__: tool for tool in tools}
        self.assertEqual(
            list(by_name),
            [
                "list_sheets",
                "read_cells",
                "prepare_sheet_action",
                "resolve_sheet_action",
            ],
        )

        await by_name["resolve_sheet_action"]("confirm", "abc123")
        await by_name["resolve_sheet_action"]("preview", "abc123")
        await by_name["resolve_sheet_action"]("cancel", "abc123")

        self.assertEqual(
            calls,
            [
                ("confirm", "abc123"),
                ("preview", "abc123"),
                ("cancel", "abc123"),
            ],
        )


class GoogleSheetsCoordinateTests(unittest.TestCase):
    def make_service(self, response):
        api = MagicMock()
        values_api = api.spreadsheets.return_value.values.return_value
        values_api.get.return_value.execute.return_value = response
        values_api.update.return_value.execute.return_value = response
        service = object.__new__(GoogleSheetsService)
        service._service = lambda: nullcontext(api)
        return service, values_api

    def test_read_cells_reports_actual_coordinates(self):
        service, values_api = self.make_service(
            {
                "values": [
                    [],
                    [],
                    ["", "", "Name"],
                    ["", "", "Ada", 0],
                ]
            }
        )

        result = service.read_cells("spreadsheet-id", "Data")

        self.assertEqual(result["populated_range"], "Data!C3:D4")
        self.assertEqual(
            result["rows"],
            [
                {"row_number": 3, "cells": {"C": "Name"}},
                {"row_number": 4, "cells": {"C": "Ada", "D": 0}},
            ],
        )
        values_api.get.assert_called_once_with(
            spreadsheetId="spreadsheet-id",
            range="'Data'",
        )

    def test_exact_read_and_update_use_qualified_a1_range(self):
        service, values_api = self.make_service(
            {
                "range": "'Data Set'!K20:L20",
                "values": [["A", 1]],
                "updatedRange": "'Data Set'!K20:L20",
                "updatedRows": 1,
                "updatedCells": 2,
            }
        )

        result = service.read_cells(
            "spreadsheet-id",
            "Data Set",
            "k20:l20",
        )
        service.update_cells(
            "spreadsheet-id",
            "Data Set",
            "K20:L20",
            [["B", 2]],
        )

        self.assertEqual(result["start_column"], "K")
        self.assertEqual(result["start_row"], 20)
        values_api.get.assert_called_once_with(
            spreadsheetId="spreadsheet-id",
            range="'Data Set'!K20:L20",
        )
        values_api.update.assert_called_once_with(
            spreadsheetId="spreadsheet-id",
            range="'Data Set'!K20:L20",
            valueInputOption="USER_ENTERED",
            body={"values": [["B", 2]]},
        )

    def test_update_range_size_rejects_reverse_range(self):
        service = object.__new__(PendingSheetActionService)

        self.assertEqual(service._bounded_range_size("K20:M21"), (2, 3))
        with self.assertRaises(ValueError):
            service._bounded_range_size("M21:K20")

    def test_range_validation_rejects_qualified_and_zero_rows(self):
        self.assertEqual(
            GoogleSheetsService.normalize_cell_range(" k20:m "),
            "K20:M",
        )
        for invalid in ("Data!A1", "A0", "A:2"):
            with self.subTest(cell_range=invalid), self.assertRaises(ValueError):
                GoogleSheetsService.normalize_cell_range(invalid)

    def test_append_range_rejects_rows_wider_than_target(self):
        service = object.__new__(PendingSheetActionService)

        self.assertEqual(service._header_range("K20:M", 3), "K20:M20")
        with self.assertRaisesRegex(ValueError, "3-column append range"):
            service._header_range("K20:M", 4)


class PendingCoordinateActionTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, values):
        class CoordinateSheets:
            def __init__(self):
                self.ranges = []

            def get_spreadsheet_info(self, spreadsheet_id):
                return {
                    "title": "Coordinate Data",
                    "sheets": [{"title": "Data", "sheet_id": 7}],
                }

            def read_cells(self, spreadsheet_id, sheet_name, cell_range):
                self.ranges.append(cell_range)
                return {
                    "values": values,
                    "start_column": "K",
                    "start_row": 20,
                }

        service = object.__new__(PendingSheetActionService)
        service.sheets = CoordinateSheets()
        service.debug_log = None
        return service

    async def test_update_cells_builds_coordinate_preview(self):
        service = self.make_service([["Old", 1]])

        arguments, preview = await service._build_preview(
            "spreadsheet-id",
            "update_cells",
            {
                "sheet_name": "Data",
                "cell_range": "k20:l20",
                "values": [["New", 2]],
            },
        )

        self.assertEqual(arguments["cell_range"], "K20:L20")
        self.assertEqual(preview["before"], [["Old", 1]])
        self.assertEqual(preview["columns"], ["K", "L"])
        self.assertEqual(preview["row_number"], 20)

    async def test_update_cells_requires_values_to_match_range(self):
        service = self.make_service([["Old", 1]])

        with self.assertRaisesRegex(ValueError, "1x2 target range"):
            await service._build_preview(
                "spreadsheet-id",
                "update_cells",
                {
                    "sheet_name": "Data",
                    "cell_range": "K20:L20",
                    "values": [["Only one"]],
                },
            )


class SheetPreviewTests(unittest.TestCase):
    def make_action(self, **overrides):
        values = {
            "code": "abc123",
            "sheet_session_id": 1,
            "user_id": "user",
            "chat_id": "chat",
            "spreadsheet_id": "spreadsheet",
            "operation": "append_rows",
            "arguments": {"sheet_name": "Data", "values": [["A&B", 10]]},
            "preview": {
                "spreadsheet_title": "Budget <2026>",
                "summary": "Tambahkan 1 baris ke Data",
                "sheet_name": "Data",
                "sheet_id": 2,
                "columns": ["Name", "Amount"],
                "rows": [["A&B", 10]],
            },
            "status": "pending",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
        values.update(overrides)
        return PendingSheetAction(**values)

    def test_pending_preview_is_editable_and_escaped(self):
        html = render_sheet_preview(self.make_action())

        self.assertIn('name="cell-0-0"', html)
        self.assertIn("Tempel ke Sheets", html)
        self.assertIn("Budget &lt;2026&gt;", html)
        self.assertIn('value="A&amp;B"', html)
        self.assertNotIn("Budget <2026>", html)
        self.assertIn('href="/static/index.css"', html)
        self.assertNotIn("<style>", html)

    def test_completed_preview_has_no_apply_form(self):
        html = render_sheet_preview(self.make_action(status="succeeded"))

        self.assertIn("Data dikonfirmasi", html)
        self.assertNotIn("Tempel ke Sheets", html)

    def test_expired_pending_preview_has_no_apply_form(self):
        action = self.make_action(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )

        html = render_sheet_preview(action)

        self.assertIn("Link kedaluwarsa", html)
        self.assertNotIn("Tempel ke Sheets", html)

    def test_browser_edits_preserve_unchanged_types(self):
        service = object.__new__(PendingSheetActionService)
        action = self.make_action()

        arguments = service._apply_edits(
            action,
            {"cell-0-0": "Changed", "cell-0-1": "10"},
        )

        self.assertEqual(arguments["values"], [["Changed", 10]])

    def test_browser_edits_reject_missing_cells(self):
        service = object.__new__(PendingSheetActionService)
        action = self.make_action()

        with self.assertRaises(InvalidActionEdit):
            service._apply_edits(action, {"cell-0-0": "Changed"})

    def test_public_preview_requires_https_off_loopback(self):
        with self.assertRaises(ValueError):
            PendingSheetActionService(
                object(),
                "http://preview.example.com",
            )

    def test_same_turn_cannot_confirm_its_own_action(self):
        action = self.make_action(source_message_id="message-1")

        outcome = PendingSheetActionService._reject_same_turn(
            action,
            "message-1",
        )

        self.assertEqual(outcome.status, "awaiting_user_confirmation")


if __name__ == "__main__":
    unittest.main()
