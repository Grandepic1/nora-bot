from sqlalchemy import func, select, text

from app.models.sheet_session import SheetSession
from app.waha_handler.bot import WahaBot
from app.waha_handler.context import Context


async def setup(bot:WahaBot):
    @bot.command(name="new-session")
    async def new(ctx: Context, *session_name):
        if not session_name:
            await ctx.send("Tolong provide nama sesi seperti: /new-session tabungan")
            return
        session_name = " ".join(session_name).strip()

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