from app.waha_handler.bot import WahaBot


async def setup(bot:WahaBot):
    @bot.command("hello")
    async def hello(ctx, *args):
        if not args:
            await ctx.send("Coba masukin nama lek. Contoh: /hello Revaldo")
            return

        name = " ".join(args)

        await ctx.send(f"Hello, {name}")
    