from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.active_sheet_session import ActiveSheetSession
from app.services.google_sheets import GoogleSheetsService
from app.waha_handler.bot import WahaBot
from app.waha_handler.context import Context


async def setup(bot:WahaBot):
    @bot.listener("message")
    async def ai_mode(ctx: Context):
        if ctx.message.strip().startswith(bot.prefix):
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
            return

        sheet_session = active_session.sheet_session
        spreadsheet_link = sheet_session.spreadsheet_link
        spreadsheet_id = None

        if spreadsheet_link:
            spreadsheet_id = GoogleSheetsService.get_spreadsheet_id(
                spreadsheet_link
            )

            if spreadsheet_id is None:
                await ctx.db.commit()
                await ctx.send(
                    "Spreadsheet pada session ini tidak valid."
                )
                return

        async def send_response(text: str):
            await ctx.send(text)

        worker = await bot.gemini.queue_message(
            sheet_session_id=sheet_session.id,
            spreadsheet_id=spreadsheet_id,
            message=ctx.message,
            callback=send_response,
            start_typing=ctx.start_typing,
            stop_typing=ctx.stop_typing,
        )
        bot.track_conversation_task(
            ctx.session,
            ctx.chat_id,
            worker,
        )
