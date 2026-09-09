import os

from dotenv import load_dotenv

from waha_handler.bot import WahaBot

load_dotenv()
bot = WahaBot(
    waha_url=os.environ["WAHA_BASE_URL"],
    api_key=os.environ["WAHA_API_KEY"],
    prefix=os.getenv("BOT_PREFIX")
)

@bot.command("hello")
async def hello(ctx, *args):
    if not args:
        ctx.send("Coba masukin nama lek. Contoh: /hello Revaldo")
        return

    name = " ".join(args)

    if any(item in name for item in ["joan","keyla"]):
        await ctx.send("Ogah")
    else:
        await ctx.send(f"Hello, {name}")

if __name__ == "__main__":
    bot.run(host=os.getenv("APP_URL"), port=os.getenv("APP_PORT"))