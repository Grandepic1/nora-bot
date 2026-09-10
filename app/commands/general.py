from app.waha_handler.bot import WahaBot


async def setup(bot:WahaBot):
    @bot.command(
        "hello",
        description="Menyapa pengguna",
    )
    async def hello(ctx, *args):
        if not args:
            await ctx.send("Coba masukin nama lek. Contoh: /hello Revaldo")
            return

        name = " ".join(args)

        await ctx.send(f"Hello, {name}")

    @bot.command(
        "help",
        description="Menampilkan daftar command",
    )
    async def help_command(ctx):
        lines = ["*Daftar Command NORA*", ""]

        for command_name, command_data in sorted(bot.commands.items()):
            command = f"`{bot.prefix}{command_name}`"
            description = command_data["description"]

            if description:
                command += f" - {description}"

            lines.append(command)

        await ctx.send("\n".join(lines))
