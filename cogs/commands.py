import discord
from discord import app_commands
from discord.ext import commands
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, json_to_list, list_to_json, EMBED_GREEN

WATERMARK = "KILLOREZ HELPER"


class OtherCog(commands.Cog, name="Other"):
    def __init__(self, bot):
        self.bot = bot

    # ==================== SET GROUP ====================
    set_group = app_commands.Group(name="set", description="Настройки бота")

    @set_group.command(name="mainroles", description="Выбрать основные роли")
    @app_commands.describe(roles="ID ролей через запятую")
    async def set_mainroles(self, interaction: discord.Interaction, roles: str):
        role_ids = [int(r.strip()) for r in roles.split(",") if r.strip().isdigit()]
        settings = await fetch_one(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE guild_settings SET main_roles = ? WHERE guild_id = ?",
                (list_to_json(role_ids), interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO guild_settings (guild_id, main_roles) VALUES (?, ?)",
                (interaction.guild.id, list_to_json(role_ids))
            )

        role_mentions = ", ".join([f"<@&{r}>" for r in role_ids])
        embed = create_success_embed("Основные роли обновлены", f"Роли: {role_mentions}")
        await interaction.response.send_message(embed=embed)

    # ==================== LOGS GROUP ====================
    logs_group = app_commands.Group(name="logs", description="Система логов")

    @logs_group.command(name="channel", description="Выбрать канал для логов")
    @app_commands.describe(channel="Канал для логов")
    async def logs_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        settings = await fetch_one(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE guild_settings SET log_channel_id = ? WHERE guild_id = ?",
                (channel.id, interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (?, ?)",
                (interaction.guild.id, channel.id)
            )

        embed = create_success_embed("Канал логов обновлен", f"Логи будут отправляться в {channel.mention}")
        await interaction.response.send_message(embed=embed)

    # ==================== MESSAGE GROUP ====================
    message_group = app_commands.Group(name="message", description="Система рассылки")

    @message_group.command(name="send", description="Отправить сообщение в ЛС людям с ролью")
    @app_commands.describe(role="Роль получателей", text="Текст сообщения")
    async def message_send(self, interaction: discord.Interaction, role: discord.Role, text: str):
        await interaction.response.defer(thinking=True)

        members_with_role = [m for m in role.members if not m.bot]
        delivered = 0
        not_delivered = 0

        embed_to_send = create_embed(
            f"Сообщение от {interaction.guild.name}",
            text,
            discord.Color.blue()
        )
        embed_to_send.add_field(name="Отправлено", value=interaction.user.mention, inline=True)
        embed_to_send.set_footer(text=WATERMARK)

        for member in members_with_role:
            try:
                await member.send(embed=embed_to_send)
                delivered += 1
            except (discord.Forbidden, discord.HTTPException):
                not_delivered += 1

        result_embed = create_embed("Рассылка завершена",
            f"**Роль:** {role.mention}\n"
            f"**Доставлено:** {delivered}\n"
            f"**Не доставлено (ЛС закрыт):** {not_delivered}\n"
            f"**Всего участников:** {len(members_with_role)}",
            EMBED_GREEN
        )
        await interaction.followup.send(embed=result_embed)


async def setup(bot):
    await bot.add_cog(OtherCog(bot))
