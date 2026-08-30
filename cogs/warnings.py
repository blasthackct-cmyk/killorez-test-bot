import discord
from discord import app_commands
from discord.ext import commands
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, json_to_list, list_to_json, EMBED_GREEN, EMBED_RED, EMBED_ORANGE

WATERMARK = "KILLOREZ HELPER"


class WarnRoleSelect(discord.ui.Select):
    """Выпадающий список с ролями штрафа."""
    def __init__(self, roles, member, admin, reason, guild_id):
        options = []
        for role in roles:
            options.append(discord.SelectOption(
                label=role.name,
                description=f"ID: {role.id}",
                value=str(role.id)
            ))
        options.append(discord.SelectOption(
            label="Без роли штрафа",
            description="Выдать штраф без роли",
            value="none"
        ))
        super().__init__(
            placeholder="Выберите роль штрафа",
            options=options
        )
        self.member = member
        self.admin = admin
        self.reason = reason
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]

        if selected == "none":
            warn_role = None
            warn_role_id = None
        else:
            warn_role_id = int(selected)
            warn_role = interaction.guild.get_role(warn_role_id)

        # Записываем штраф в БД
        await execute_query(
            "INSERT INTO warnings (guild_id, user_id, admin_id, reason, warn_role_id) VALUES (?, ?, ?, ?, ?)",
            (self.guild_id, self.member.id, self.admin.id, self.reason, warn_role_id)
        )

        # Выдаем роль
        if warn_role:
            try:
                await self.member.add_roles(warn_role)
            except discord.Forbidden:
                pass

        # Формируем embed
        desc = f"**Участник:** {self.member.mention}\n**Админ:** {self.admin.mention}\n"
        if self.reason:
            desc += f"**Причина:** {self.reason}\n"
        if warn_role:
            desc += f"**Роль штрафа:** {warn_role.mention}\n"

        embed = create_embed("Штраф выдан", desc, EMBED_ORANGE)
        await interaction.response.edit_message(embed=embed, view=None)

        # Лог
        log_settings = await fetch_one(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (self.guild_id,)
        )
        if log_settings and log_settings['log_channel_id']:
            log_channel = interaction.guild.get_channel(log_settings['log_channel_id'])
            if log_channel:
                log_embed = create_embed("Штраф выдан", desc, EMBED_ORANGE)
                await log_channel.send(embed=log_embed)

        # DM уведомление
        try:
            reason_text = self.reason or "Не указана"
            role_text = f"\n**Роль:** {warn_role.mention}" if warn_role else ""
            dm_embed = create_embed("Штраф",
                f"Вам был выдан штраф на сервере **{interaction.guild.name}**\n**Причина:** {reason_text}{role_text}",
                EMBED_ORANGE)
            await self.member.send(embed=dm_embed)
        except discord.Forbidden:
            pass


class WarnRoleView(discord.ui.View):
    """View с выпадающим списком ролей штрафа."""
    def __init__(self, roles, member, admin, reason, guild_id):
        super().__init__(timeout=120)
        self.add_item(WarnRoleSelect(roles, member, admin, reason, guild_id))


class WarningsCog(commands.Cog, name="Warnings"):
    def __init__(self, bot):
        self.bot = bot

    warning = app_commands.Group(name="warning", description="Система штрафов")

    @warning.command(name="add", description="Выдать штраф участнику")
    @app_commands.describe(member="Участник", reason="Причина")
    async def warning_add(self, interaction: discord.Interaction, member: discord.Member, reason: str = ""):
        # Проверка ролей админа
        settings = await fetch_one(
            "SELECT * FROM warning_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings and settings['admin_roles']:
            admin_role_ids = json_to_list(settings['admin_roles'])
            has_admin = any(interaction.guild.get_role(rid) in interaction.user.roles for rid in admin_role_ids)
            if not has_admin:
                admin_roles_mentions = ", ".join([f"<@&{rid}>" for rid in admin_role_ids])
                embed = create_error_embed("Ошибка", f"У вас нет нужной роли для выдачи штрафов!\nТребуются: {admin_roles_mentions}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        # Получаем настроенные роли штрафа
        warn_role_ids = []
        if settings:
            warn_role_ids = json_to_list(settings['warn_roles'])

        warn_roles = []
        for rid in warn_role_ids:
            role = interaction.guild.get_role(rid)
            if role:
                warn_roles.append(role)

        if not warn_roles:
            embed = create_error_embed("Ошибка",
                "Роли штрафа не настроены!\n"
                "Сначала используйте `/warning set` чтобы добавить роли штрафа.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Показываем выпадающий список с ролями
        desc = f"**Участник:** {member.mention}\n**Админ:** {interaction.user.mention}\n"
        if reason:
            desc += f"**Причина:** {reason}\n"
        desc += "\nВыберите роль штрафа из списка ниже:"

        embed = create_embed("Выдача штрафа", desc, EMBED_ORANGE)
        view = WarnRoleView(warn_roles, member, interaction.user, reason, interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @warning.command(name="delete", description="Удалить штраф с участника")
    @app_commands.describe(member="Участник", warning_id="ID штрафа (необязательно — удалит все)")
    async def warning_delete(self, interaction: discord.Interaction, member: discord.Member, warning_id: int = None):
        if warning_id:
            warn = await fetch_one(
                "SELECT * FROM warnings WHERE warning_id = ? AND guild_id = ?",
                (warning_id, interaction.guild.id)
            )
            if not warn:
                embed = create_error_embed("Ошибка", "Штраф с таким ID не найден!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            if warn['warn_role_id']:
                role = interaction.guild.get_role(warn['warn_role_id'])
                if role:
                    try:
                        await member.remove_roles(role)
                    except discord.Forbidden:
                        pass

            await execute_query("DELETE FROM warnings WHERE warning_id = ?", (warning_id,))
            embed = create_success_embed("Штраф удален", f"Штраф #{warning_id} удален с участника {member.mention}")
            await interaction.response.send_message(embed=embed)
        else:
            # Удалить все штрафы участника
            warns = await fetch_all(
                "SELECT * FROM warnings WHERE user_id = ? AND guild_id = ?",
                (member.id, interaction.guild.id)
            )
            for w in warns:
                if w['warn_role_id']:
                    role = interaction.guild.get_role(w['warn_role_id'])
                    if role:
                        try:
                            await member.remove_roles(role)
                        except discord.Forbidden:
                            pass

            await execute_query(
                "DELETE FROM warnings WHERE user_id = ? AND guild_id = ?",
                (member.id, interaction.guild.id)
            )
            embed = create_success_embed("Штрафы удалены", f"Все штрафы удалены с участника {member.mention}")
            await interaction.response.send_message(embed=embed)

    @warning.command(name="set", description="Выбрать роли штрафа")
    @app_commands.describe(roles="ID ролей через запятую")
    async def warning_set_roles(self, interaction: discord.Interaction, roles: str):
        role_ids = [int(r.strip()) for r in roles.split(",") if r.strip().isdigit()]
        settings = await fetch_one(
            "SELECT * FROM warning_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE warning_settings SET warn_roles = ? WHERE guild_id = ?",
                (list_to_json(role_ids), interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO warning_settings (guild_id, warn_roles) VALUES (?, ?)",
                (interaction.guild.id, list_to_json(role_ids))
            )

        role_mentions = ", ".join([f"<@&{r}>" for r in role_ids])
        embed = create_success_embed("Роли штрафа обновлены", f"Роли: {role_mentions}")
        await interaction.response.send_message(embed=embed)

    @warning.command(name="admin", description="Выбрать роли админов для штрафов")
    @app_commands.describe(roles="ID ролей через запятую")
    async def warning_set_admin(self, interaction: discord.Interaction, roles: str):
        role_ids = [int(r.strip()) for r in roles.split(",") if r.strip().isdigit()]
        settings = await fetch_one(
            "SELECT * FROM warning_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE warning_settings SET admin_roles = ? WHERE guild_id = ?",
                (list_to_json(role_ids), interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO warning_settings (guild_id, admin_roles) VALUES (?, ?)",
                (interaction.guild.id, list_to_json(role_ids))
            )

        role_mentions = ", ".join([f"<@&{r}>" for r in role_ids])
        embed = create_success_embed("Роли админов обновлены", f"Роли: {role_mentions}")
        await interaction.response.send_message(embed=embed)

    @warning.command(name="list", description="Список всех оштрафованных")
    async def warning_list(self, interaction: discord.Interaction):
        warns = await fetch_all(
            "SELECT * FROM warnings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if not warns:
            embed = create_embed("Список штрафов", "Нет оштрафованных участников", discord.Color.blue())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        desc = ""
        for w in warns:
            user = self.bot.get_user(w['user_id'])
            admin = self.bot.get_user(w['admin_id'])
            name = user.mention if user else f"<@{w['user_id']}>"
            admin_name = admin.mention if admin else f"<@{w['admin_id']}>"
            desc += f"**#{w['warning_id']}** | {name} | Админ: {admin_name}"
            if w['reason']:
                desc += f" | {w['reason']}"
            if w['warn_role_id']:
                desc += f" | <@&{w['warn_role_id']}>"
            desc += "\n"

        embed = create_embed("Список штрафов", desc, EMBED_ORANGE)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(WarningsCog(bot))
