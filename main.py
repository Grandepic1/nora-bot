import os

from dotenv import load_dotenv

from app.database import create_database
from app.waha_handler.bot import WahaBot

load_dotenv()
database_engine, session_factory = create_database(os.environ["DATABASE_URL"])
bot = WahaBot(
    waha_url=os.environ["WAHA_BASE_URL"],
    api_key=os.environ["WAHA_API_KEY"],
    database_engine=database_engine,
    session_factory=session_factory,
    prefix=os.getenv("BOT_PREFIX", "/"),
    debug=os.getenv("DEBUG", "False") == "True",
)

@bot.command("hello")
async def hello(ctx, *args):
    if not args:
        await ctx.send("Coba masukin nama lek. Contoh: /hello Revaldo")
        return

    name = " ".join(args)

    await ctx.send(f"Hello, {name}")

if __name__ == "__main__":
    bot.run(
        host=os.getenv("APP_URL", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
    )
