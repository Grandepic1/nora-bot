from sqlalchemy import select, func

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

        result = await ctx.db.execute(
            select(func.count())
            .select_from(SheetSession)
            .where(SheetSession.user_id == ctx.sender)
        )

        session_count = result.scalar_one()

        if session_count >= 5:
            await ctx.send("Kamu sudah mencapai batas maksimal 5 session.")
            return

        sheet_session= SheetSession(
            user_id=ctx.sender,
            session_name=session_name
        )

        ctx.db.add(sheet_session)

        await ctx.db.flush()

        await ctx.send(f"Berhasil membuat session baru! Ketik `/start {session_name}` untuk memulai")



    