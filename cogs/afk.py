import discord
from discord import app_commands
from discord.ext import commands, tasks
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, EMBED_GREEN, EMBED_RED
from datetime import datetime

WATERMARK = "KILLOREZ HELPER"


def parse_time(time_str):
    """Парсит строку времени формата HH:MM в часы и минуты."""
    try:
        parts = time_str.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        pass
    return None, None


def is_time_expired(time_to):
    """Проверяет, истекло ли указанное время окончания AFK."""
    if not time_to:
        return False
    hour, minute = parse_time(time_to)
    if hour is None:
        return False
    now = datetime.now()
    return now.hour > hour or (now.hour == hour and now.minute >= minute)


def is_time_started(time_from):
    """Проверяет, наступило ли время начала AFK."""
    if not time_from:
        return True
    hour, minute = parse_time(time_from)
    if hour is None:
        return True
    now = datetime.now()
    return now.hour > hour or (now.hour == hour and now.minute >= minute)


class AFKCog(commands.Cog, name="AFK"):
    def __init__(self, bot):
        self.bot = bot
        self.afk_auto_remove_loop.start()

    def cog_unload(self):
        self.afk_auto_remove_loop.cancel()

    afk = app_commands.Group(name="afk", description="Система AFK")

    @afk.command(name="on", description="Войти в AFK")
    @app_commands.describe(
        time_from="Время начала AFK (формат HH:MM, например 16:10)",
        time_to="Время окончания AFK (формат HH:MM, например 16:20)",
        reason="Причина AFK"
    )
    async def afk_on(self, interaction: discord.Interaction, time_from: str = "", time_to: str = "", reason: str = ""):
        existing = await fetch_one(
            "SELECT * FROM afk WHERE user_id = ? AND guild_id = ?",
            (interaction.user.id, interaction.guild.id)
        )
        if existing and existing['is_afk']:
            embed = create_error_embed("Ошибка", "Вы уже находитесь в AFK!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Валидация формата времени
        if time_from:
            h, m = parse_time(time_from)
            if h is None:
                embed = create_error_embed("Ошибка", "Неверный формат времени начала! Используйте HH:MM (например 16:10)")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            if not (0 <= h <= 23 and 0 <= m <= 59):
                embed = create_error_embed("Ошибка", "Неверное время начала! Часы: 0-23, минуты: 0-59")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        if time_to:
            h, m = parse_time(time_to)
            if h is None:
                embed = create_error_embed("Ошибка", "Неверный формат времени окончания! Используйте HH:MM (например 16:20)")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            if not (0 <= h <= 23 and 0 <= m <= 59):
                embed = create_error_embed("Ошибка", "Неверное время окончания! Часы: 0-23, минуты: 0-59")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        await execute_query(
            "INSERT INTO afk (user_id, guild_id, reason, time_from, time_to, is_afk) VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT (user_id, guild_id) DO UPDATE SET reason = EXCLUDED.reason, time_from = EXCLUDED.time_from, time_to = EXCLUDED.time_to, is_afk = 1",
            (interaction.user.id, interaction.guild.id, reason, time_from, time_to)
        )

        nickname = interaction.user.display_name
        try:
            await interaction.user.edit(nick=f"[AFK] {nickname}")
        except discord.Forbidden:
            pass

        desc = f"**Участник:** {interaction.user.mention}\n"
        if reason:
            desc += f"**Причина:** {reason}\n"
        if time_from and time_to:
            desc += f"**Время:** с {time_from} по {time_to}\n"
            desc += f"\n*AFK будет автоматически снят в {time_to}*"
        else:
            desc += "\n*Для снятия AFK используйте /afk off*"

        embed = create_embed("AFK", desc, EMBED_GREEN)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

        # Логирование
        await self._log_afk_change(interaction.guild, interaction.user, "AFK включен", reason, time_from, time_to)

    @afk.command(name="off", description="Выйти из AFK")
    async def afk_off(self, interaction: discord.Interaction):
        existing = await fetch_one(
            "SELECT * FROM afk WHERE user_id = ? AND guild_id = ?",
            (interaction.user.id, interaction.guild.id)
        )
        if not existing or not existing['is_afk']:
            embed = create_error_embed("Ошибка", "Вы не находитесь в AFK!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        time_from = existing['time_from'] or ""
        time_to = existing['time_to'] or ""

        await execute_query(
            "UPDATE afk SET is_afk = 0 WHERE user_id = ? AND guild_id = ?",
            (interaction.user.id, interaction.guild.id)
        )

        nickname = interaction.user.display_name
        if nickname.startswith("[AFK] "):
            try:
                await interaction.user.edit(nick=nickname[6:])
            except discord.Forbidden:
                pass

        desc = f"**Участник:** {interaction.user.mention}\n"
        if time_from and time_to:
            desc += f"**Время:** с {time_from} по {time_to}\n"

        embed = create_embed("AFK снят", desc, EMBED_GREEN)
        await interaction.response.send_message(embed=embed)

        # Логирование
        await self._log_afk_change(interaction.guild, interaction.user, "AFK снят", "", time_from, time_to)

    @afk.command(name="lish", description="Список участников в AFK")
    async def afk_list(self, interaction: discord.Interaction):
        rows = await fetch_all(
            "SELECT * FROM afk WHERE guild_id = ? AND is_afk = 1",
            (interaction.guild.id,)
        )
        if not rows:
            embed = create_embed("Список AFK", "Нет участников в AFK", discord.Color.blue())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        desc = ""
        for row in rows:
            user = self.bot.get_user(row['user_id'])
            name = user.mention if user else f"<@{row['user_id']}>"
            desc += f"• {name}"
            if row['reason']:
                desc += f" — {row['reason']}"
            if row['time_from'] and row['time_to']:
                desc += f" (с {row['time_from']} по {row['time_to']})"
            desc += "\n"

        embed = create_embed("Список AFK", desc, discord.Color.blue())
        await interaction.response.send_message(embed=embed)

    async def _log_afk_change(self, guild, user, action, reason, time_from, time_to):
        """Отправляет лог изменения AFK в канал логов."""
        from utils.database import fetch_one as _fetch_one
        settings = await _fetch_one(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (guild.id,)
        )
        if settings and settings['log_channel_id']:
            log_channel = guild.get_channel(settings['log_channel_id'])
            if log_channel:
                desc = f"**Участник:** {user.mention}\n**Действие:** {action}"
                if reason:
                    desc += f"\n**Причина:** {reason}"
                if time_from and time_to:
                    desc += f"\n**Время:** с {time_from} по {time_to}"
                log_embed = create_embed(action, desc, EMBED_GREEN)
                await log_channel.send(embed=log_embed)

    @tasks.loop(minutes=1)
    async def afk_auto_remove_loop(self):
        """Каждую минуту проверяет, истекло ли время AFK у участников, и автоматически снимает его."""
        afk_users = await fetch_all(
            "SELECT * FROM afk WHERE is_afk = 1 AND time_to != '' AND time_to IS NOT NULL"
        )
        for row in afk_users:
            if is_time_expired(row['time_to']):
                guild = self.bot.get_guild(row['guild_id'])
                if not guild:
                    continue

                member = guild.get_member(row['user_id'])
                if not member:
                    continue

                await execute_query(
                    "UPDATE afk SET is_afk = 0 WHERE user_id = ? AND guild_id = ?",
                    (row['user_id'], row['guild_id'])
                )

                # Снять никнейм
                nickname = member.display_name
                if nickname.startswith("[AFK] "):
                    try:
                        await member.edit(nick=nickname[6:])
                    except discord.Forbidden:
                        pass

                # Уведомить в логах
                desc = f"**Участник:** {member.mention}\n**Действие:** AFK автоматически снят"
                if row['time_from'] and row['time_to']:
                    desc += f"\n**Время:** с {row['time_from']} по {row['time_to']}"
                log_embed = create_embed("AFK автоматически снят", desc, EMBED_GREEN)

                settings = await fetch_one(
                    "SELECT * FROM guild_settings WHERE guild_id = ?",
                    (row['guild_id'],)
                )
                if settings and settings['log_channel_id']:
                    log_channel = guild.get_channel(settings['log_channel_id'])
                    if log_channel:
                        await log_channel.send(embed=log_embed)

                # Попробовать отправить DM
                try:
                    dm_embed = create_embed("AFK снят",
                        f"Ваш AFK на сервере **{guild.name}** автоматически снят (время {row['time_to']} истекло).",
                        EMBED_GREEN)
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    pass

    @afk_auto_remove_loop.before_loop
    async def before_afk_auto_remove(self):
        await self.bot.wait_until_ready()


class InactiveCog(commands.Cog, name="Inactive"):
    def __init__(self, bot):
        self.bot = bot
        self.inactive_auto_remove_loop.start()

    def cog_unload(self):
        self.inactive_auto_remove_loop.cancel()

    inactive = app_commands.Group(name="inactive", description="Система отпуска")

    @inactive.command(name="on", description="Войти в отпуск")
    @app_commands.describe(
        time_from="Дата начала отпуска (формат HH:MM или ДД.ММ)",
        time_to="Дата окончания отпуска (формат HH:MM или ДД.ММ)",
        reason="Причина отпуска"
    )
    async def inactive_on(self, interaction: discord.Interaction, time_from: str = "", time_to: str = "", reason: str = ""):
        existing = await fetch_one(
            "SELECT * FROM inactive WHERE user_id = ? AND guild_id = ?",
            (interaction.user.id, interaction.guild.id)
        )
        if existing and existing['is_inactive']:
            embed = create_error_embed("Ошибка", "Вы уже находитесь в отпуске!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "INSERT INTO inactive (user_id, guild_id, reason, time_from, time_to, is_inactive) VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT (user_id, guild_id) DO UPDATE SET reason = EXCLUDED.reason, time_from = EXCLUDED.time_from, time_to = EXCLUDED.time_to, is_inactive = 1",
            (interaction.user.id, interaction.guild.id, reason, time_from, time_to)
        )

        nickname = interaction.user.display_name
        try:
            await interaction.user.edit(nick=f"[ОТПУСК] {nickname}")
        except discord.Forbidden:
            pass

        desc = f"**Участник:** {interaction.user.mention}\n"
        if reason:
            desc += f"**Причина:** {reason}\n"
        if time_from and time_to:
            desc += f"**Период:** с {time_from} по {time_to}\n"
            desc += f"\n*Отпуск будет автоматически снят по окончании {time_to}*"
        else:
            desc += "\n*Для снятия отпуска используйте /inactive off*"

        embed = create_embed("Отпуск", desc, EMBED_GREEN)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @inactive.command(name="off", description="Выйти из отпуска")
    async def inactive_off(self, interaction: discord.Interaction):
        existing = await fetch_one(
            "SELECT * FROM inactive WHERE user_id = ? AND guild_id = ?",
            (interaction.user.id, interaction.guild.id)
        )
        if not existing or not existing['is_inactive']:
            embed = create_error_embed("Ошибка", "Вы не находитесь в отпуске!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE inactive SET is_inactive = 0 WHERE user_id = ? AND guild_id = ?",
            (interaction.user.id, interaction.guild.id)
        )

        nickname = interaction.user.display_name
        if nickname.startswith("[ОТПУСК] "):
            try:
                await interaction.user.edit(nick=nickname[9:])
            except discord.Forbidden:
                pass

        embed = create_embed("Отпуск снят", f"**Участник:** {interaction.user.mention}", EMBED_GREEN)
        await interaction.response.send_message(embed=embed)

    @inactive.command(name="list", description="Список участников в отпуске")
    async def inactive_list(self, interaction: discord.Interaction):
        rows = await fetch_all(
            "SELECT * FROM inactive WHERE guild_id = ? AND is_inactive = 1",
            (interaction.guild.id,)
        )
        if not rows:
            embed = create_embed("Список отпуска", "Нет участников в отпуске", discord.Color.blue())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        desc = ""
        for row in rows:
            user = self.bot.get_user(row['user_id'])
            name = user.mention if user else f"<@{row['user_id']}>"
            desc += f"• {name}"
            if row['reason']:
                desc += f" — {row['reason']}"
            if row['time_from'] and row['time_to']:
                desc += f" (с {row['time_from']} по {row['time_to']})"
            desc += "\n"

        embed = create_embed("Список отпуска", desc, discord.Color.blue())
        await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=1)
    async def inactive_auto_remove_loop(self):
        """Каждую минуту проверяет, истекло ли время отпуска, и автоматически снимает его."""
        inactive_users = await fetch_all(
            "SELECT * FROM inactive WHERE is_inactive = 1 AND time_to != '' AND time_to IS NOT NULL"
        )
        for row in inactive_users:
            if is_time_expired(row['time_to']):
                guild = self.bot.get_guild(row['guild_id'])
                if not guild:
                    continue

                member = guild.get_member(row['user_id'])
                if not member:
                    continue

                await execute_query(
                    "UPDATE inactive SET is_inactive = 0 WHERE user_id = ? AND guild_id = ?",
                    (row['user_id'], row['guild_id'])
                )

                nickname = member.display_name
                if nickname.startswith("[ОТПУСК] "):
                    try:
                        await member.edit(nick=nickname[9:])
                    except discord.Forbidden:
                        pass

                # Уведомить в логах
                desc = f"**Участник:** {member.mention}\n**Действие:** Отпуск автоматически снят"
                if row['time_from'] and row['time_to']:
                    desc += f"\n**Период:** с {row['time_from']} по {row['time_to']}"
                log_embed = create_embed("Отпуск автоматически снят", desc, EMBED_GREEN)

                settings = await fetch_one(
                    "SELECT * FROM guild_settings WHERE guild_id = ?",
                    (row['guild_id'],)
                )
                if settings and settings['log_channel_id']:
                    log_channel = guild.get_channel(settings['log_channel_id'])
                    if log_channel:
                        await log_channel.send(embed=log_embed)

                # DM уведомление
                try:
                    dm_embed = create_embed("Отпуск снят",
                        f"Ваш отпуск на сервере **{guild.name}** автоматически снят (время {row['time_to']} истекло).",
                        EMBED_GREEN)
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    pass

    @inactive_auto_remove_loop.before_loop
    async def before_inactive_auto_remove(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AFKCog(bot))
    await bot.add_cog(InactiveCog(bot))
