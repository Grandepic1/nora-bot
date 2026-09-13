import unittest
from datetime import datetime, timedelta, timezone

import app.tools.sheets as sheet_tools
from app.models.pending_sheet_action import PendingSheetAction
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

    async def test_write_tools_create_pending_actions(self):
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

        result = await by_name["append_rows"]("Data", [["A", 1]])
        await by_name["update_row"]("Data", 2, ["B", 2])
        await by_name["create_sheet"]("Archive")
        await by_name["rename_sheet"]("Data", "Current")

        self.assertEqual(result["status"], "pending_confirmation")
        self.assertNotIn("url", result)
        self.assertIn("preview", result["options"])
        self.assertEqual(calls[0][0], "append_rows")
        self.assertEqual(calls[1][0], "update_row")
        self.assertEqual(calls[2][0], "create_sheet")
        self.assertEqual(calls[3][0], "rename_sheet")
        self.assertEqual(
            calls[0][1],
            {"sheet_name": "Data", "values": [["A", 1]]},
        )

    async def test_write_tools_are_not_exposed_without_confirmation_service(self):
        tools = sheet_tools.build_sheet_tools("spreadsheet-id")

        self.assertEqual(
            [tool.__name__ for tool in tools],
            ["list_sheets", "read_sheet", "read_row"],
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

        await by_name["confirm_sheet_action"]("abc123")
        await by_name["preview_sheet_action"]("abc123")
        await by_name["cancel_sheet_action"]("abc123")

        self.assertEqual(
            calls,
            [
                ("confirm", "abc123"),
                ("preview", "abc123"),
                ("cancel", "abc123"),
            ],
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
