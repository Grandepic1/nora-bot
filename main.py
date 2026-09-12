import os

from dotenv import load_dotenv

from app.database import create_database
from app.waha_handler.bot import WahaBot

load_dotenv()
app_port = int(os.getenv("APP_PORT", "8000"))
database_engine, session_factory = create_database(os.environ["DATABASE_URL"])
bot = WahaBot(
    waha_url=os.environ["WAHA_BASE_URL"],
    api_key=os.environ["WAHA_API_KEY"],
    database_engine=database_engine,
    session_factory=session_factory,
    prefix=os.getenv("BOT_PREFIX", "/"),
    debug=os.getenv("DEBUG", "False") == "True",
    public_base_url=os.getenv(
        "PUBLIC_BASE_URL",
        f"http://127.0.0.1:{app_port}",
    ),
    sheet_action_ttl_seconds=int(
        os.getenv("SHEET_ACTION_TTL_SECONDS", "900")
    ),
)

for filename in os.listdir('./app/commands'):
    if filename.endswith('.py'):
        bot.load_extension(f'app.commands.{filename[:-3]}')


if __name__ == "__main__":
    bot.run(
        host=os.getenv("APP_URL", "127.0.0.1"),
        port=app_port,
    )
