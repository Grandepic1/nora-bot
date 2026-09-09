import os

from dotenv import load_dotenv

from waha_handler.bot import WahaBot

load_dotenv()
bot = WahaBot(
    waha_url=os.environ["WAHA_BASE_URL"],
    api_key=os.environ["WAHA_API_KEY"],
    prefix=os.getenv("BOT_PREFIX"),
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
    bot.run(host=os.getenv("APP_URL"), port=os.getenv("APP_PORT"))