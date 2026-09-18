import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from google.genai import _extra_utils, types

import app.tools.sheets as sheet_tools
from app.models.pending_sheet_action import PendingSheetAction
from app.services.google_sheets import GoogleSheetsService
from app.services.pending_sheet_actions import (
    ActionConflict,
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

        result = await by_name["append_rows"](
            sheet_name="Data",
            cell_range="K20:M",
            values=[["A", 1]],
        )
        await by_name["update_cells"](
            sheet_name="Data",
            cell_range="K21:L21",
            values=[["B", 2]],
        )
        await by_name["create_sheet"](title="Archive")
        await by_name["rename_sheet"](
            sheet_name="Data",
            new_name="Current",
        )
        await by_name["delete_row"](sheet_name="Current", row_number=7)
        await by_name["delete_sheet"](sheet_name="Archive")

        self.assertEqual(result["status"], "pending_confirmation")
        self.assertNotIn("url", result)
        self.assertIn("preview", result["options"])
        self.assertEqual(calls[0][0], "append_rows")
        self.assertEqual(calls[1][0], "update_cells")
        self.assertEqual(calls[2][0], "create_sheet")
        self.assertEqual(calls[3][0], "rename_sheet")
        self.assertEqual(
            calls[4],
            ("delete_row", {"sheet_name": "Current", "row_number": 7}),
        )
        self.assertEqual(
            calls[5],
            ("delete_sheet", {"sheet_name": "Archive"}),
        )
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
            ["list_sheets", "inspect_sheet", "read_cells"],
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
                "inspect_sheet",
                "read_cells",
                "append_rows",
                "update_cells",
                "create_sheet",
                "rename_sheet",
                "delete_row",
                "delete_sheet",
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

    async def test_sdk_can_invoke_every_write_and_confirmation_tool(self):
        prepared = []
        resolved = []

        async def create_pending(operation, arguments):
            prepared.append((operation, arguments))
            return {"status": "pending_confirmation"}

        async def manage_pending(choice, code):
            resolved.append((choice, code))
            return {"status": "pending"}

        tools = {
            tool.__name__: tool
            for tool in sheet_tools.build_sheet_tools(
                "spreadsheet-id",
                create_pending_action=create_pending,
                manage_pending_action=manage_pending,
            )
        }
        calls = [
            ("append_rows", {
                "sheet_name": "Data", "cell_range": "K20:N",
                "values": [["Ada", 10, 2.5, True]],
            }),
            ("update_cells", {
                "sheet_name": "Data", "cell_range": "K21:N21",
                "values": [["", 0, -2.5, False]],
            }),
            ("create_sheet", {"title": "Archive"}),
            ("rename_sheet", {"sheet_name": "Data", "new_name": "Current"}),
            ("delete_row", {"sheet_name": "Current", "row_number": 7.0}),
            ("delete_sheet", {"sheet_name": "Archive"}),
        ]
        calls.extend(
            ("resolve_sheet_action", {"choice": choice, "confirmation_code": "abc123"})
            for choice in ("preview", "confirm", "cancel")
        )
        for name, arguments in calls:
            with self.subTest(tool=name, arguments=arguments):
                response = types.GenerateContentResponse(candidates=[
                    types.Candidate(content=types.Content(parts=[
                        types.Part.from_function_call(name=name, args=arguments),
                    ])),
                ])
                # Direct Python calls and schema checks skip the SDK's argument
                # conversion. Exercise the same path used by automatic calling.
                parts = await _extra_utils.get_function_response_parts_async(
                    response, tools,
                )
                result = parts[0].function_response.response
                self.assertNotIn("error", result)

        self.assertEqual([operation for operation, _ in prepared], [name for name, _ in calls[:6]])
        self.assertEqual(prepared[0][1]["values"], [["Ada", 10, 2.5, True]])
        self.assertEqual(prepared[1][1]["values"], [["", 0, -2.5, False]])
        self.assertIs(type(prepared[4][1]["row_number"]), int)
        self.assertEqual(resolved, [(choice, "abc123") for choice in ("preview", "confirm", "cancel")])

    def test_generated_write_schemas_use_concrete_required_types(self):
        async def create_pending(operation, arguments):
            return {"status": "pending_confirmation"}

        tools = sheet_tools.build_sheet_tools(
            "spreadsheet-id",
            create_pending_action=create_pending,
        )
        declarations = {
            tool.__name__: types.FunctionDeclaration.from_callable_with_api_option(
                callable=tool,
                api_option="GEMINI_API",
                use_json_schema=True,
            )
            for tool in tools
        }

        create_schema = declarations["create_sheet"].parameters_json_schema
        self.assertEqual(
            create_schema["properties"]["title"]["type"],
            "string",
        )
        self.assertEqual(create_schema["required"], ["title"])

        update_schema = declarations["update_cells"].parameters_json_schema
        self.assertEqual(
            update_schema["properties"]["sheet_name"]["type"],
            "string",
        )
        self.assertEqual(
            update_schema["properties"]["cell_range"]["type"],
            "string",
        )
        self.assertEqual(
            update_schema["properties"]["values"]["type"],
            "array",
        )
        self.assertEqual(
            update_schema["required"],
            ["sheet_name", "cell_range", "values"],
        )

        delete_schema = declarations["delete_row"].parameters_json_schema
        self.assertEqual(
            delete_schema["properties"]["row_number"]["type"],
            "integer",
        )
        self.assertEqual(
            delete_schema["required"],
            ["sheet_name", "row_number"],
        )

        inspect_schema = declarations["inspect_sheet"].parameters_json_schema
        self.assertEqual(inspect_schema["required"], ["sheet_name"])
        read_schema = declarations["read_cells"].parameters_json_schema
        self.assertEqual(
            read_schema["properties"]["cell_range"]["type"],
            "string",
        )
        self.assertEqual(
            read_schema["required"],
            ["sheet_name", "cell_range"],
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

    def test_delete_uses_confirmed_sheet_id_without_name_lookup(self):
        service, _ = self.make_service({})
        api = MagicMock()
        service._service = lambda: nullcontext(api)

        service.delete_row("spreadsheet-id", "Data", 7, sheet_id=42)
        service.delete_sheet("spreadsheet-id", "Data", sheet_id=42)

        api.spreadsheets.return_value.get.assert_not_called()
        calls = api.spreadsheets.return_value.batchUpdate.call_args_list
        self.assertEqual(
            calls[0].kwargs["body"]["requests"][0]["deleteDimension"]["range"][
                "sheetId"
            ],
            42,
        )
        self.assertEqual(
            calls[1].kwargs["body"]["requests"][0]["deleteSheet"]["sheetId"],
            42,
        )


class PendingCoordinateActionTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, values, sheets=None):
        class CoordinateSheets:
            def __init__(self):
                self.ranges = []
                self.calls = []

            def get_spreadsheet_info(self, spreadsheet_id):
                return {
                    "title": "Coordinate Data",
                    "sheets": sheets or [{"title": "Data", "sheet_id": 7}],
                }

            def read_cells(self, spreadsheet_id, sheet_name, cell_range):
                self.ranges.append(cell_range)
                return {
                    "values": values,
                    "start_column": "K",
                    "start_row": 20,
                }

            def read_row(self, spreadsheet_id, sheet_name, row_number):
                return ["Name", "Amount"] if row_number == 1 else values[0]

            def delete_row(
                self,
                spreadsheet_id,
                sheet_name,
                row_number,
                sheet_id,
            ):
                self.calls.append(
                    ("delete_row", sheet_name, row_number, sheet_id)
                )
                return {"deleted": True, "row_number": row_number}

            def delete_sheet(self, spreadsheet_id, sheet_name, sheet_id):
                self.calls.append(("delete_sheet", sheet_name, sheet_id))
                return {"deleted": True, "sheet_name": sheet_name}

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

    async def test_single_cell_preview_includes_its_full_row(self):
        service = self.make_service([["Old"]])
        service.sheets.read_row = (
            lambda spreadsheet_id, sheet_name, row_number:
            ["Name", "Status", "Notes"]
            if row_number == 1 else ["Ada", "Old", "Keep this"]
        )

        arguments, preview = await service._build_preview(
            "spreadsheet-id",
            "update_cells",
            {
                "sheet_name": "Data",
                "cell_range": "B20",
                "values": [["New"]],
            },
        )

        self.assertEqual(arguments["cell_range"], "B20")
        self.assertEqual(arguments["values"], [["New"]])
        self.assertEqual(preview["before"], [["Old"]])
        self.assertEqual(preview["display_columns"], ["Name", "Status", "Notes"])
        self.assertEqual(preview["display_rows"], [["Ada", "Old", "Keep this"]])
        self.assertEqual(preview["target_start_column"], 1)

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

    async def test_delete_row_builds_snapshot_and_dispatches(self):
        service = self.make_service([["Ada", 10]])

        arguments, preview = await service._build_preview(
            "spreadsheet-id",
            "delete_row",
            {"sheet_name": "Data", "row_number": 7},
        )
        result = await service._execute(
            SimpleNamespace(
                operation="delete_row",
                spreadsheet_id="spreadsheet-id",
                arguments=arguments,
                preview=preview,
            )
        )

        self.assertEqual(preview["before"], ["Ada", 10])
        self.assertTrue(preview["destructive"])
        self.assertEqual(result["row_number"], 7)
        self.assertEqual(
            service.sheets.calls,
            [("delete_row", "Data", 7, 7)],
        )

    async def test_delete_sheet_requires_another_sheet_and_dispatches(self):
        service = self.make_service([["Ada", 10]])
        with self.assertRaisesRegex(ValueError, "only remaining sheet"):
            await service._build_preview(
                "spreadsheet-id",
                "delete_sheet",
                {"sheet_name": "Data"},
            )

        service = self.make_service(
            [["Ada", 10]],
            sheets=[
                {"title": "Data", "sheet_id": 7},
                {"title": "Archive", "sheet_id": 8},
            ],
        )
        arguments, preview = await service._build_preview(
            "spreadsheet-id",
            "delete_sheet",
            {"sheet_name": "Archive"},
        )
        result = await service._execute(
            SimpleNamespace(
                operation="delete_sheet",
                spreadsheet_id="spreadsheet-id",
                arguments=arguments,
                preview=preview,
            )
        )

        self.assertTrue(preview["destructive"])
        self.assertTrue(result["deleted"])
        self.assertEqual(
            service.sheets.calls,
            [("delete_sheet", "Archive", 8)],
        )

    async def test_delete_row_rejects_changed_target(self):
        service = self.make_service([["Changed", 10]])
        action = SimpleNamespace(
            operation="delete_row",
            spreadsheet_id="spreadsheet-id",
            arguments={"sheet_name": "Data", "row_number": 7},
            preview={
                "sheet_name": "Data",
                "sheet_id": 7,
                "before": ["Original", 10],
            },
        )

        with self.assertRaisesRegex(ActionConflict, "Baris target berubah"):
            await service._check_preconditions(action)


class PendingActionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        action = PendingSheetAction(
            code="abc123", sheet_session_id=1, user_id="user", chat_id="chat",
            source_message_id="message-1", spreadsheet_id="spreadsheet-id",
            operation="create_sheet", arguments={"title": "Archive"},
            preview={"summary": "Create Archive"}, status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        db = MagicMock()
        db.__aenter__.return_value = db
        db.begin.return_value = db
        result = MagicMock()
        result.scalar_one_or_none.return_value = (
            "https://docs.google.com/spreadsheets/d/spreadsheet-id/edit"
        )
        db.execute = AsyncMock(return_value=result)
        service = object.__new__(PendingSheetActionService)
        service.session_factory = lambda: db
        service.public_base_url = "https://preview.example.com"
        service._find_by_code = AsyncMock(return_value=action)
        service._execute = AsyncMock()
        return service, action

    async def test_preview_can_be_issued_in_the_preparation_turn(self):
        service, action = self.make_service()

        outcome = await service.issue_preview_url("abc123", "user", "message-1")

        self.assertEqual(outcome.status, "pending")
        self.assertIn("https://preview.example.com/preview/", outcome.message)
        self.assertIsNotNone(action.token_hash)
        self.assertEqual(action.status, "pending")
        service._execute.assert_not_awaited()

    async def test_confirmation_still_requires_a_later_user_message(self):
        service, action = self.make_service()

        outcome = await service.confirm_code("abc123", "user", "message-1")

        self.assertEqual(outcome.status, "awaiting_user_confirmation")
        self.assertEqual(action.status, "pending")
        service._execute.assert_not_awaited()


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
        self.assertIn("Terapkan ke Sheets", html)
        self.assertIn("Budget &lt;2026&gt;", html)
        self.assertIn('>A&amp;B</textarea>', html)
        self.assertIn('class="preview-cell-input', html)
        self.assertNotIn("Budget <2026>", html)
        self.assertIn('href="/static/index.css"', html)
        self.assertNotIn("<style>", html)

    def test_completed_preview_has_no_apply_form(self):
        html = render_sheet_preview(self.make_action(status="succeeded"))

        self.assertIn("Data dikonfirmasi", html)
        self.assertNotIn("Terapkan ke Sheets", html)

    def test_expired_pending_preview_has_no_apply_form(self):
        action = self.make_action(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )

        html = render_sheet_preview(action)

        self.assertIn("Link kedaluwarsa", html)
        self.assertNotIn("Terapkan ke Sheets", html)

    def test_update_cells_preview_renders_editable_table(self):
        action = self.make_action(
            operation="update_cells",
            arguments={
                "sheet_name": "Data",
                "cell_range": "K20:L20",
                "values": [["New", 20]],
            },
            preview={
                "spreadsheet_title": "Budget",
                "summary": "Perbarui range K20:L20 di Data",
                "sheet_name": "Data",
                "sheet_id": 2,
                "columns": ["K", "L"],
                "rows": [["New", 20]],
                "before": [["Old", 10]],
                "row_number": 20,
            },
        )

        html = render_sheet_preview(action)

        self.assertIn('name="cell-0-0"', html)
        self.assertIn('name="cell-0-1"', html)
        self.assertIn("Terapkan ke Sheets", html)

    def test_single_cell_edit_shows_full_row_but_only_target_is_editable(self):
        action = self.make_action(
            operation="update_cells",
            arguments={
                "sheet_name": "Data",
                "cell_range": "B20",
                "values": [["New"]],
            },
            preview={
                "spreadsheet_title": "Budget",
                "summary": "Perbarui B20 di Data",
                "sheet_name": "Data",
                "sheet_id": 2,
                "columns": ["B"],
                "rows": [["New"]],
                "before": [["Old"]],
                "row_number": 20,
                "display_columns": ["Name", "Status", "Notes"],
                "display_rows": [["Ada", "Old", "Keep this"]],
                "target_start_column": 1,
            },
        )

        html = render_sheet_preview(action)

        self.assertIn("Name", html)
        self.assertIn("Notes", html)
        self.assertIn("Ada", html)
        self.assertIn("Keep this", html)
        self.assertIn('name="cell-0-0"', html)
        self.assertIn('>New</textarea>', html)
        self.assertNotIn('name="cell-0-1"', html)
        service = object.__new__(PendingSheetActionService)
        self.assertEqual(
            service._apply_edits(action, {"cell-0-0": "Confirmed"})["values"],
            [["Confirmed"]],
        )

    def test_long_cell_content_stays_visible_and_escaped(self):
        value = "A long first line & <unsafe>\nA second line"
        action = self.make_action(
            arguments={"sheet_name": "Data", "values": [[value, 10]]},
        )

        html = render_sheet_preview(action)

        self.assertIn(
            "A long first line &amp; &lt;unsafe&gt;\nA second line</textarea>",
            html,
        )
        self.assertIn('field.addEventListener("input", fitContent)', html)

    def test_delete_previews_are_read_only_and_destructive(self):
        row_action = self.make_action(
            operation="delete_row",
            arguments={"sheet_name": "Data", "row_number": 7},
            preview={
                "spreadsheet_title": "Budget",
                "summary": "Hapus baris 7 dari Data",
                "sheet_name": "Data",
                "sheet_id": 2,
                "columns": ["Name", "Amount"],
                "rows": [["Ada", 10]],
                "before": ["Ada", 10],
                "row_number": 7,
                "destructive": True,
            },
        )
        sheet_action = self.make_action(
            operation="delete_sheet",
            arguments={"sheet_name": "Archive"},
            preview={
                "spreadsheet_title": "Budget",
                "summary": 'Hapus sheet "Archive" secara permanen',
                "sheet_name": "Archive",
                "sheet_id": 3,
                "destructive": True,
            },
        )

        row_html = render_sheet_preview(row_action)
        sheet_html = render_sheet_preview(sheet_action)

        self.assertNotIn('name="cell-0-0"', row_html)
        self.assertIn("Hapus dari Sheets", row_html)
        self.assertIn("Sheet akan dihapus permanen", sheet_html)
        self.assertIn("Hapus dari Sheets", sheet_html)

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
