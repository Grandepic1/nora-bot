from datetime import datetime, timezone
from html import escape
from typing import Any

from app.models.pending_sheet_action import PendingSheetAction


def render_sheet_preview(
    action: PendingSheetAction | None,
    *,
    error: str | None = None,
) -> str:
    if action is None:
        return _page(
            "Preview tidak tersedia",
            _state_card(
                "Preview tidak tersedia",
                "Link tidak valid atau sudah diganti.",
                "error",
            ),
        )

    status = action.status
    if status == "pending" and action.expires_at <= datetime.now(timezone.utc):
        status = "expired"

    preview = action.preview
    title = escape(str(preview.get("spreadsheet_title", "Google Sheets")))
    summary = escape(str(preview.get("summary", "Perubahan spreadsheet")))

    if status != "pending":
        states = {
            "processing": ("Sedang diproses", "Perubahan sedang dikirim ke Google Sheets."),
            "succeeded": ("Data dikonfirmasi", "Perubahan sudah diterapkan ke spreadsheet."),
            "failed": ("Perubahan gagal", action.error or "Perubahan tidak dapat diterapkan."),
            "expired": ("Link kedaluwarsa", "Minta NORA membuat konfirmasi baru."),
            "stale": ("Session berubah", "Spreadsheet atau session aktif sudah berubah."),
            "conflict": ("Data berubah", action.error or "Periksa spreadsheet dan buat konfirmasi baru."),
            "cancelled": ("Aksi dibatalkan", "Perubahan ini tidak diterapkan."),
        }
        heading, detail = states.get(
            status,
            ("Preview tidak tersedia", "Perubahan ini tidak dapat digunakan."),
        )
        content = _spreadsheet_info(title, summary, ready=False)
        content += _state_card(heading, escape(detail), status)
        return _page(summary, content)

    content = _spreadsheet_info(title, summary, ready=True)
    content += '<div class="data-status"><span>Periksa data di bawah</span><i></i><strong><b></b>Siap dikonfirmasi</strong></div>'
    if error:
        content += f'<div class="alert">{escape(error)}</div>'
    content += '<form method="post" class="preview-form">'
    content += _editable_content(action)
    content += '<button class="apply" type="submit">Tempel ke Sheets</button>'
    content += "</form>"
    return _page(summary, content)


def _editable_content(action: PendingSheetAction) -> str:
    preview = action.preview
    arguments = action.arguments

    if action.operation in {"append_rows", "update_row"}:
        rows = (
            arguments["values"]
            if action.operation == "append_rows"
            else [arguments["values"]]
        )
        columns = preview["columns"]
        header = '<th class="number">#</th>' + "".join(
            f"<th>{escape(str(column))}</th>" for column in columns
        )
        body = []
        first_number = preview.get("row_number", 1)
        for row_index, row in enumerate(rows):
            cells = []
            for column_index, value in enumerate(row):
                field = f"cell-{row_index}-{column_index}"
                cells.append(
                    "<td><input aria-label=\"Edit nilai sel\" "
                    f'name="{field}" value="{escape(str(value), quote=True)}"></td>'
                )
            body.append(
                f'<tr><th class="number">{first_number + row_index}</th>'
                + "".join(cells)
                + "</tr>"
            )
        return (
            '<div class="table-wrap"><table><thead><tr>'
            + header
            + "</tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table></div>"
        )

    field = "title" if action.operation == "create_sheet" else "new_name"
    label = "Nama sheet baru"
    fixed = ""
    if action.operation == "rename_sheet":
        fixed = (
            '<div class="fixed-value"><span>Sheet saat ini</span><strong>'
            f'{escape(arguments["sheet_name"])}</strong></div>'
        )
    return (
        '<div class="field-card">'
        + fixed
        + f'<label for="{field}">{label}</label>'
        + f'<input id="{field}" name="{field}" value="{escape(arguments[field], quote=True)}">'
        + "</div>"
    )


def _spreadsheet_info(title: str, summary: str, *, ready: bool) -> str:
    badge = "Siap dikonfirmasi" if ready else "Status akhir"
    return f"""
    <section class="sheet-info">
      <span class="sheet-icon" aria-hidden="true">{_sheet_svg()}</span>
      <div><small>Spreadsheet</small><strong>{title}</strong><p>{summary}</p></div>
      <span class="badge"><i></i>{badge}</span>
    </section>
    """


def _state_card(heading: str, detail: str, status: str) -> str:
    successful = status == "succeeded"
    icon = "&#10003;" if successful else "!"
    css = " success" if successful else ""
    return f"""
    <div class="state{css}">
      <span>{icon}</span><div><strong>{heading}</strong><p>{detail}</p></div>
    </div>
    """


def _sheet_svg() -> str:
    return """<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><path d="M1.5 6h13M1.5 9.5h13M6 6v7.5M10 6v7.5"/></svg>"""


def _page(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)} | NORA</title>
  <style>
    :root{{--paper:#fffdf9;--ink:#17233b;--secondary:#4b5563;--accent:#ef3f2b;--line:#e5e7eb;--surface:#f3f1ee;--success:#2e9b62}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}}
    main{{width:min(920px,calc(100% - 32px));margin:0 auto;padding:56px 0 72px}}header{{margin-bottom:40px}}.brand{{display:flex;align-items:center;gap:12px}}.logo{{display:grid;place-items:center;width:40px;height:40px;border-radius:12px;background:var(--accent);color:white;font-weight:900;transform:rotate(-3deg)}}h1{{margin:0;font-size:20px;letter-spacing:-.025em}}header p{{margin:8px 0 0;font-size:12px;color:var(--secondary)}}
    .sheet-info{{display:flex;align-items:center;gap:12px;padding:16px 20px;border:1px solid var(--line);border-radius:16px;background:white;box-shadow:0 1px 2px #17233b0d}}.sheet-icon{{display:grid;place-items:center;width:36px;height:36px;flex:none;border-radius:8px;background:var(--surface);color:#17233b80}}.sheet-icon svg{{width:18px}}.sheet-info div{{display:flex;flex-direction:column}}small,.sheet-info p{{font-size:12px;color:var(--secondary)}}.sheet-info strong{{font-size:14px}}.sheet-info p{{margin:3px 0 0}}.badge{{margin-left:auto;display:inline-flex;align-items:center;gap:6px;border-radius:999px;background:#2e9b621a;color:var(--success);padding:4px 10px;font-size:12px;font-weight:600;white-space:nowrap}}.badge i,.data-status b{{width:6px;height:6px;border-radius:50%;background:var(--success)}}
    .data-status{{display:flex;align-items:center;gap:10px;margin:24px 2px 12px;color:var(--secondary);font-size:14px}}.data-status>i{{width:4px;height:4px;border-radius:50%;background:var(--line)}}.data-status strong{{display:flex;align-items:center;gap:6px;color:var(--success);font-weight:600}}.alert{{margin:12px 0;padding:12px 14px;border:1px solid #ef3f2b40;border-radius:10px;background:#ef3f2b0d;color:#a62617;font-size:13px}}
    .table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:white;box-shadow:0 1px 2px #17233b0d}}table{{width:100%;border-collapse:separate;border-spacing:0;text-align:left}}th,td{{border-right:1px solid var(--line);border-bottom:1px solid var(--line)}}tr>*:last-child{{border-right:0}}tbody tr:last-child>*{{border-bottom:0}}th{{min-width:144px;padding:10px 12px;background:var(--surface);font-size:14px}}th.number{{min-width:40px;width:40px;padding:9px 8px;text-align:right;color:var(--secondary);font-size:12px;font-weight:500;font-variant-numeric:tabular-nums}}tbody th.number{{background:#f3f1ee80}}td{{padding:0}}td input{{width:100%;min-width:144px;border:0;background:white;padding:9px 12px;color:var(--ink);font:inherit;font-size:14px;outline:none}}td input:hover{{background:#f3f1ee99}}td input:focus{{position:relative;box-shadow:inset 0 0 0 2px var(--accent);background:white}}
    .field-card{{display:grid;gap:10px;margin-top:20px;padding:20px;border:1px solid var(--line);border-radius:12px;background:white;box-shadow:0 1px 2px #17233b0d}}.field-card label,.fixed-value span{{font-size:12px;color:var(--secondary)}}.field-card>input{{width:100%;border:1px solid var(--line);border-radius:9px;padding:11px 12px;color:var(--ink);font:inherit;outline:none}}.field-card>input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px #ef3f2b1a}}.fixed-value{{display:flex;flex-direction:column;gap:2px;margin-bottom:8px}}
    .apply{{display:block;margin:28px auto 0;border:0;border-radius:12px;background:var(--accent);padding:12px 28px;color:white;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 1px 2px #17233b18}}.apply:hover{{filter:brightness(.95)}}.apply:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
    .state{{display:flex;align-items:center;gap:14px;width:max-content;max-width:100%;margin:32px auto;padding:14px 20px;border:1px solid #ef3f2b40;border-radius:12px;background:#ef3f2b0d}}.state>span{{display:grid;place-items:center;width:24px;height:24px;flex:none;border-radius:50%;background:var(--accent);color:white;font-weight:800}}.state div{{display:flex;flex-direction:column}}.state strong{{font-size:14px}}.state p{{margin:2px 0 0;color:var(--secondary);font-size:12px}}.state.success{{border-color:#2e9b6240;background:#2e9b620d}}.state.success>span{{background:var(--success)}}
    @media(max-width:600px){{main{{width:min(100% - 24px,920px);padding-top:28px}}header{{margin-bottom:28px}}.sheet-info{{align-items:flex-start;flex-wrap:wrap}}.badge{{margin-left:48px}}.data-status{{align-items:flex-start;flex-direction:column;gap:5px}}.data-status>i{{display:none}}}}
  </style>
</head>
<body><main>
  <header><div class="brand"><span class="logo">N</span><h1>NORA</h1></div><p>Periksa data sebelum ditempel ke spreadsheet.</p></header>
  {content}
</main></body>
</html>"""
