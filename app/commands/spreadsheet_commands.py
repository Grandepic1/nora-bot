from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from app.models.active_sheet_session import ActiveSheetSession
from app.models.sheet_session import SheetSession
from app.services.google_sheets import GoogleSheetsService
from app.tools.sheets import check_spreadsheet_access
from app.waha_handler.bot import WahaBot
from app.waha_handler.context import Context


async def setup(bot:WahaBot):
    @bot.command(
        name="new-session",
        description="Membuat session baru",
    )
    async def new(ctx: Context, *session_name):
        if not session_name:
            await ctx.send("Tolong provide nama sesi seperti: /new-session tabungan")
            return
        session_name = " ".join(session_name).strip().lower()

        async with ctx.db.begin():
            # Serialize creation for this user so concurrent requests cannot
            # both pass the five-session check.
            await ctx.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 0))"),
                {"user_id": ctx.sender},
            )

            result = await ctx.db.execute(
                select(func.count())
                .select_from(SheetSession)
                .where(SheetSession.user_id == ctx.sender)
            )

            session_count = result.scalar_one()

            if session_count >= 5:
                response = "Kamu sudah mencapai batas maksimal 5 session."
            else:
                sheet_session = SheetSession(
                    user_id=ctx.sender,
                    session_name=session_name,
                )
                ctx.db.add(sheet_session)
                await ctx.db.flush()
                response = (
                    "Berhasil membuat session baru! "
                    f"Ketik `/start {session_name}` untuk memulai"
                )

        await ctx.send(response)

    @bot.command(
        "list-session",
        description="Melihat semua session yang kamu punya",
    )
    async def list_session(ctx:Context):
        result = await ctx.db.execute(
            select(SheetSession)
            .where(SheetSession.user_id == ctx.sender)
            .order_by(SheetSession.id.asc())
        )

        sessions = result.scalars().all()
        await ctx.db.commit()

        if not sessions:
            await ctx.send(
                "Kamu belum punya session.\nBuat dengan `/new-session <nama>`"
            )
            return

        lines = ["Daftar session kamu:"]

        for index, session in enumerate(sessions, start=1):
            lines.append(f"{index}. {session.session_name}")

        await ctx.send("\n".join(lines))

    @bot.command(
        description="Memulai atau berpindah ke session",
    )
    async def start(ctx: Context, *session_name):
        session_name = " ".join(session_name).strip().lower()

        if not session_name:
            await ctx.send(
                "Tolong masukkan nama session.\n"
                "Contoh: `/start tabungan`"
            )
            return

        result = await ctx.db.execute(
            select(SheetSession).where(
                SheetSession.user_id == ctx.sender,
                SheetSession.session_name == session_name,
            )
        )

        sheet_session = result.scalar_one_or_none()

        if sheet_session is None:
            await ctx.db.commit()
            await ctx.send(
                f"Session `{session_name}` tidak ditemukan.\n"
                "Ketik `/list-session` untuk melihat session yang tersedia."
            )
            return

        active_session = await ctx.db.get(
            ActiveSheetSession,
            ctx.sender,
        )
        previous_sheet_session_id = (
            active_session.sheet_session_id
            if active_session is not None
            else None
        )

        if active_session is None:
            active_session = ActiveSheetSession(
                user_id=ctx.sender,
                sheet_session_id=sheet_session.id,
            )

            ctx.db.add(active_session)
        else:
            active_session.sheet_session_id = sheet_session.id

        await ctx.db.commit()

        if (
            previous_sheet_session_id is not None
            and previous_sheet_session_id != sheet_session.id
        ):
            await bot.gemini.remove_chat(previous_sheet_session_id)

        await ctx.send(
            f"Session `{sheet_session.session_name}` dimulai."
        )

    @bot.command(
        "spreadsheet",
        description="Mengatur spreadsheet untuk session aktif",
    )
    async def spreadsheet(
        ctx: Context,
        url: str | None = None,
    ):
        if not url:
            await ctx.send(
                "Tolong masukkan link Google Spreadsheet.\n"
                "Contoh: `/spreadsheet https://docs.google.com/spreadsheets/d/...`"
            )
            return

        spreadsheet_id = GoogleSheetsService.get_spreadsheet_id(url)

        if spreadsheet_id is None:
            await ctx.send(
                "Link spreadsheet tidak valid.\n"
                "Gunakan link Google Sheets seperti:\n"
                "`https://docs.google.com/spreadsheets/d/...`"
            )
            return

        can_access = await check_spreadsheet_access(spreadsheet_id)

        if not can_access:
            await ctx.send(
                "NORA tidak dapat mengakses spreadsheet ini. "
                "Pastikan spreadsheet sudah dibagikan ke "
                "`nora-sheets@gen-lang-client-0347904718.iam."
                "gserviceaccount.com`."
            )
            return

        result = await ctx.db.execute(
            select(ActiveSheetSession)
            .options(
                selectinload(
                    ActiveSheetSession.sheet_session
                )
            )
            .where(
                ActiveSheetSession.user_id == ctx.sender
            )
        )

        active_session = result.scalar_one_or_none()

        if active_session is None:
            await ctx.db.commit()
            await ctx.send(
                "Belum ada session yang aktif.\n"
                "Gunakan `/start <nama-session>` terlebih dahulu."
            )
            return

        sheet_session_id = active_session.sheet_session_id
        previous_link = active_session.sheet_session.spreadsheet_link
        active_session.sheet_session.spreadsheet_link = url
        await ctx.db.commit()

        if previous_link != url:
            await bot.gemini.remove_chat(sheet_session_id)

        await ctx.send(
            f"Spreadsheet berhasil diset untuk session "
            f"`{active_session.sheet_session.session_name}`."
            "\nHarap tambahkan akses *Editor* ke akun `nora-sheets@gen-lang-client-0347904718.iam.gserviceaccount.com` agar Nora dapat mengakses Spreadsheet."
        )

    @bot.command(
        "quit",
        description="Keluar dari AI mode",
    )
    async def quit_session(ctx: Context):
        active_session = await ctx.db.get(
            ActiveSheetSession,
            ctx.sender,
        )

        if active_session is None:
            await ctx.db.commit()
            await ctx.send(
                "Tidak ada session yang sedang aktif."
            )
            return

        sheet_session_id = active_session.sheet_session_id

        await ctx.db.delete(active_session)
        await ctx.db.commit()

        await bot.gemini.remove_chat(
            sheet_session_id
        )

        await ctx.send(
            "Session dihentikan. AI mode telah keluar."
        )
