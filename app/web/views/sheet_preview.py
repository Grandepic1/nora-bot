from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models.pending_sheet_action import PendingSheetAction

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(("html",)),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_sheet_preview(
    action: PendingSheetAction | None,
    *,
    error: str | None = None,
) -> str:
    template = templates.get_template("pages/PreviewPage.html")
    return template.render(**_preview_context(action, error))


def _preview_context(
    action: PendingSheetAction | None,
    error: str | None,
) -> dict:
    if action is None:
        return {
            "page_title": "Preview tidak tersedia",
            "spreadsheet": None,
            "pending": False,
            "successful": False,
            "state": {
                "heading": "Preview tidak tersedia",
                "detail": "Link tidak valid atau sudah diganti.",
            },
        }

    status = action.status
    if status == "pending" and action.expires_at <= datetime.now(timezone.utc):
        status = "expired"

    preview = action.preview
    summary = str(preview.get("summary", "Perubahan spreadsheet"))
    context = {
        "page_title": summary,
        "spreadsheet": {
            "name": str(
                preview.get("spreadsheet_title", "Google Sheets")
            ),
        },
        "summary": summary,
        "pending": status == "pending",
        "successful": status == "succeeded",
        "operation": action.operation,
        "error": error,
    }

    if status != "pending":
        states = {
            "processing": (
                "Sedang diproses",
                "Perubahan sedang dikirim ke Google Sheets.",
            ),
            "succeeded": (
                "Data dikonfirmasi",
                "Perubahan sudah diterapkan ke spreadsheet.",
            ),
            "failed": (
                "Perubahan gagal",
                action.error or "Perubahan tidak dapat diterapkan.",
            ),
            "expired": (
                "Link kedaluwarsa",
                "Minta NORA membuat konfirmasi baru.",
            ),
            "stale": (
                "Session berubah",
                "Spreadsheet atau session aktif sudah berubah.",
            ),
            "conflict": (
                "Data berubah",
                action.error
                or "Periksa spreadsheet dan buat konfirmasi baru.",
            ),
            "cancelled": (
                "Aksi dibatalkan",
                "Perubahan ini tidak diterapkan.",
            ),
        }
        heading, detail = states.get(
            status,
            ("Preview tidak tersedia", "Perubahan ini tidak dapat digunakan."),
        )
        context["state"] = {"heading": heading, "detail": detail}
        return context

    arguments = action.arguments
    if action.operation in {"append_rows", "update_row"}:
        values = (
            arguments["values"]
            if action.operation == "append_rows"
            else [arguments["values"]]
        )
        first_number = int(preview.get("row_number", 1))
        rows = []
        for row_index, row in enumerate(values):
            rows.append(
                {
                    "number": first_number + row_index,
                    "cells": [
                        {
                            "name": f"cell-{row_index}-{column_index}",
                            "value": value,
                        }
                        for column_index, value in enumerate(row)
                    ],
                }
            )
        context.update(
            columns=preview["columns"],
            rows=rows,
            detected=len(rows),
            ready=sum(
                all(str(cell["value"]).strip() for cell in row["cells"])
                for row in rows
            ),
        )
    else:
        field_name = (
            "title" if action.operation == "create_sheet" else "new_name"
        )
        context.update(
            field={"name": field_name, "value": arguments[field_name]},
            fixed_sheet_name=(
                arguments.get("sheet_name")
                if action.operation == "rename_sheet"
                else None
            ),
            detected=1,
            ready=1 if str(arguments[field_name]).strip() else 0,
        )

    return context
