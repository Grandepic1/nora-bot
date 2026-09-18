from datetime import datetime, timezone

from app.models.pending_sheet_action import PendingSheetAction
from app.web.templating import templates


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
        "destructive": bool(preview.get("destructive")),
        "action_label": (
            "Hapus dari Sheets"
            if preview.get("destructive")
            else "Terapkan ke Sheets"
        ),
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
    if action.operation in {
        "append_rows",
        "update_row",
        "update_cells",
        "delete_row",
    }:
        if action.operation == "delete_row":
            values = preview["rows"]
        else:
            values = (
                arguments["values"]
                if action.operation in {"append_rows", "update_cells"}
                else [arguments["values"]]
            )
        first_number = int(preview.get("row_number", 1))
        rows = []
        for row_index, row in enumerate(values):
            display_row = (
                preview["display_rows"][row_index]
                if action.operation == "update_cells"
                and "display_rows" in preview
                else row
            )
            start_column = (
                int(preview["target_start_column"])
                if action.operation == "update_cells"
                and "display_rows" in preview
                else 0
            )
            display_width = (
                len(preview["display_columns"])
                if action.operation == "update_cells"
                and "display_rows" in preview
                else len(row)
            )
            rows.append(
                {
                    "number": first_number + row_index,
                    "cells": [
                        {
                            "name": (
                                f"cell-{row_index}-{column_index - start_column}"
                                if start_column <= column_index < start_column + len(row)
                                else None
                            ),
                            "value": (
                                row[column_index - start_column]
                                if start_column <= column_index < start_column + len(row)
                                else (
                                    display_row[column_index]
                                    if column_index < len(display_row)
                                    else ""
                                )
                            ),
                            "editable": (
                                action.operation != "delete_row"
                                and start_column <= column_index
                                < start_column + len(row)
                            ),
                            "context": (
                                action.operation == "update_cells"
                                and "display_rows" in preview
                            ),
                        }
                        for column_index in range(display_width)
                    ],
                }
            )
        context.update(
            columns=preview.get("display_columns", preview["columns"]),
            rows=rows,
            detected=len(rows),
            ready=(
                len(rows)
                if action.operation == "delete_row"
                else sum(
                    all(str(value).strip() for value in values_row)
                    for values_row in values
                )
            ),
            editable=action.operation != "delete_row",
        )
    elif action.operation == "delete_sheet":
        context.update(
            deleted_sheet_name=arguments["sheet_name"],
            detected=1,
            ready=1,
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
