import discord
from discord import app_commands
from discord.ext import commands, tasks
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, json_to_list, list_to_json, EMBED_GREEN, EMBED_RED, EMBED_PURPLE
import time

WATERMARK = "KILLOREZ HELPER"


class CarCog(commands.Cog, name="Car"):
    def __init__(self, bot):
        self.bot = bot
        self.car_reset_loop.start()

    def cog_unload(self):
        self.car_reset_loop.cancel()

    car = app_commands.Group(name="car", description="Система автопарка")

    @car.command(name="add", description="Добавить машину в автопарк")
    @app_commands.describe(name="Название машины")
    async def car_add(self, interaction: discord.Interaction, name: str):
        await execute_query(
            "INSERT INTO cars (guild_id, name) VALUES (?, ?)",
            (interaction.guild.id, name)
        )
        embed = create_success_embed("Машина добавлена", f"Машина **{name}** добавлена в автопарк!")
        await interaction.response.send_message(embed=embed)

    @car.command(name="delete", description="Удалить машину из автопарка")
    @app_commands.describe(car_id="ID машины")
    async def car_delete(self, interaction: discord.Interaction, car_id: int):
        car = await fetch_one(
            "SELECT * FROM cars WHERE car_id = ? AND guild_id = ?",
            (car_id, interaction.guild.id)
        )
        if not car:
            embed = create_error_embed("Ошибка", "Машина с таким ID не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if car['in_use']:
            embed = create_error_embed("Ошибка", "Нельзя удалить машину, которая сейчас используется!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query("DELETE FROM cars WHERE car_id = ?", (car_id,))
        embed = create_success_embed("Машина удалена", f"Машина **{car['name']}** удалена из автопарка!")
        await interaction.response.send_message(embed=embed)

    @car.command(name="settings", description="Изменить время сброса авто")
    @app_commands.describe(reset_time="Время сброса в секундах")
    async def car_settings(self, interaction: discord.Interaction, reset_time: int):
        await execute_query(
            "INSERT INTO car_settings (guild_id, reset_time) VALUES (?, ?) ON CONFLICT (guild_id) DO UPDATE SET reset_time = EXCLUDED.reset_time",
            (interaction.guild.id, reset_time)
        )
        hours = reset_time // 3600
        minutes = (reset_time % 3600) // 60
        embed = create_success_embed("Настройки обновлены", f"Время сброса авто: **{hours}ч {minutes}мин**")
        await interaction.response.send_message(embed=embed)

    @car.command(name="config", description="Выбрать режим счета активности")
    @app_commands.describe(mode="Режим: voice (голосовой) или messages (сообщения)")
    async def car_config_activity(self, interaction: discord.Interaction, mode: str):
        if mode not in ("voice", "messages"):
            embed = create_error_embed("Ошибка", "Режим может быть: voice или messages")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        settings = await fetch_one(
            "SELECT * FROM car_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE car_settings SET activity_mode = ? WHERE guild_id = ?",
                (mode, interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO car_settings (guild_id, activity_mode) VALUES (?, ?)",
                (interaction.guild.id, mode)
            )

        mode_text = "Голосовой онлайн" if mode == "voice" else "Подсчет сообщений"
        embed = create_success_embed("Режим обновлен", f"Режим активности: **{mode_text}**")
        await interaction.response.send_message(embed=embed)

    @car.command(name="set", description="Изменить роли для доступа к машине")
    @app_commands.describe(roles="ID ролей через запятую")
    async def car_set_role(self, interaction: discord.Interaction, roles: str):
        role_ids = [int(r.strip()) for r in roles.split(",") if r.strip().isdigit()]
        settings = await fetch_one(
            "SELECT * FROM car_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE car_settings SET required_roles = ? WHERE guild_id = ?",
                (list_to_json(role_ids), interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO car_settings (guild_id, required_roles) VALUES (?, ?)",
                (interaction.guild.id, list_to_json(role_ids))
            )

        role_mentions = ", ".join([f"<@&{r}>" for r in role_ids])
        embed = create_success_embed("Роли обновлены", f"Роли для доступа к машине: {role_mentions}")
        await interaction.response.send_message(embed=embed)

    @car.command(name="take", description="Взять машину из автопарка")
    @app_commands.describe(car_id="ID машины")
    async def car_take(self, interaction: discord.Interaction, car_id: int):
        car = await fetch_one(
            "SELECT * FROM cars WHERE car_id = ? AND guild_id = ?",
            (car_id, interaction.guild.id)
        )
        if not car:
            embed = create_error_embed("Ошибка", "Машина с таким ID не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if car['in_use']:
            user = self.bot.get_user(car['used_by'])
            embed = create_error_embed("Ошибка", f"Машина уже занята {user.mention if user else 'другим пользователем'}!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        settings = await fetch_one(
            "SELECT * FROM car_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings and settings['required_roles']:
            required_role_ids = json_to_list(settings['required_roles'])
            has_role = any(interaction.guild.get_role(rid) in interaction.user.roles for rid in required_role_ids)
            if not has_role:
                role_mentions = ", ".join([f"<@&{rid}>" for rid in required_role_ids])
                embed = create_error_embed("Ошибка", f"У вас нет нужной роли для использования машины!\nТребуются: {role_mentions}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        current_time = str(int(time.time()))
        await execute_query(
            "UPDATE cars SET in_use = 1, used_by = ?, used_at = ? WHERE car_id = ?",
            (interaction.user.id, current_time, car_id)
        )

        embed = create_success_embed("Машина взята", f"Вы взяли машину **{car['name']}** из автопарка!")
        await interaction.response.send_message(embed=embed)

        # Log
        log_settings = await fetch_one(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if log_settings and log_settings['log_channel_id']:
            log_channel = interaction.guild.get_channel(log_settings['log_channel_id'])
            if log_channel:
                log_embed = create_embed("Машина взята", 
                    f"**Машина:** {car['name']}\n**Участник:** {interaction.user.mention}", EMBED_GREEN)
                await log_channel.send(embed=log_embed)

    @car.command(name="return", description="Вернуть машину в автопарк")
    @app_commands.describe(car_id="ID машины")
    async def car_return(self, interaction: discord.Interaction, car_id: int):
        car = await fetch_one(
            "SELECT * FROM cars WHERE car_id = ? AND guild_id = ?",
            (car_id, interaction.guild.id)
        )
        if not car:
            embed = create_error_embed("Ошибка", "Машина с таким ID не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not car['in_use']:
            embed = create_error_embed("Ошибка", "Эта машина не используется!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if car['used_by'] != interaction.user.id:
            embed = create_error_embed("Ошибка", "Вы не брали эту машину!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE cars SET in_use = 0, used_by = NULL, used_at = NULL WHERE car_id = ?",
            (car_id,)
        )

        embed = create_success_embed("Машина возвращена", f"Вы вернули машину **{car['name']}** в автопарк!")
        await interaction.response.send_message(embed=embed)

    @car.command(name="list", description="Список машин автопарка")
    async def car_list(self, interaction: discord.Interaction):
        cars = await fetch_all(
            "SELECT * FROM cars WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if not cars:
            embed = create_embed("Автопарк", "Автопарк пуст!", discord.Color.blue())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        desc = ""
        for car in cars:
            status = "🔴 Занята" if car['in_use'] else "🟢 Свободна"
            user_text = ""
            if car['in_use'] and car['used_by']:
                user = self.bot.get_user(car['used_by'])
                used_by_id = car['used_by']
                user_text = f" ({user.mention})" if user else f" (<@{used_by_id}>)"
            desc += f"**ID {car['car_id']}** — {car['name']} | {status}{user_text}\n"

        embed = create_embed("Автопарк", desc, EMBED_GREEN)
        await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=5)
    async def car_reset_loop(self):
        cars = await fetch_all("SELECT * FROM cars WHERE in_use = 1")
        for car in cars:
            settings = await fetch_one(
                "SELECT * FROM car_settings WHERE guild_id = ?",
                (car['guild_id'],)
            )
            if not settings:
                continue

            reset_time = settings['reset_time'] or 3600
            used_at = int(car['used_at']) if car['used_at'] else 0
            if used_at and (int(time.time()) - used_at) >= reset_time:
                await execute_query(
                    "UPDATE cars SET in_use = 0, used_by = NULL, used_at = NULL WHERE car_id = ?",
                    (car['car_id'],)
                )

    @car_reset_loop.before_loop
    async def before_car_reset(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(CarCog(bot))
