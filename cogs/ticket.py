import discord
from discord import app_commands
from discord.ext import commands
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, json_to_list, list_to_json, EMBED_GREEN, EMBED_RED, EMBED_PURPLE
import json
import traceback
import aiohttp
import io

WATERMARK = "KILLOREZ HELPER"


def _fix_newlines(text):
    if not text:
        return ""
    return text.replace("\\n", "\n")


def format_panel_description(panel):
    name = panel['name'] or "Тикет"
    desc = _fix_newlines(panel['description'] or "")
    welcome = _fix_newlines(panel['welcome_message'] or "")
    call_msg = _fix_newlines(panel['call_message'] or "")

    sep = "────────────────────────"

    parts = []
    parts.append(f"🔴 **{name}**")
    parts.append("")

    if desc:
        for line in desc.split("\n"):
            parts.append(f"> {line}" if line.strip() else ">")
        parts.append("")
        parts.append(sep)
        parts.append("")

    if welcome:
        for line in welcome.split("\n"):
            parts.append(f"> {line}" if line.strip() else ">")
        parts.append("")
        parts.append(sep)
        parts.append("")

    if call_msg:
        for line in call_msg.split("\n"):
            parts.append(f"> {line}" if line.strip() else ">")
        parts.append("")
        parts.append(sep)
        parts.append("")

    parts.append("Ознакомьтесь с условиями выше и нажмите кнопку ниже ↓")

    return "\n".join(parts)


# ==================== КНОПКА ПОДАЧИ ЗАЯВКИ ====================

class PanelButtonView(discord.ui.View):
    def __init__(self, panel_id):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Подать заявку",
            style=discord.ButtonStyle.green,
            emoji="📩",
            custom_id=f"ticket_panel_btn_{panel_id}"
        )
        button.callback = self._on_button_click
        self.add_item(button)
        self._panel_id = panel_id

    async def _on_button_click(self, interaction: discord.Interaction):
        try:
            panel = await fetch_one(
                "SELECT * FROM ticket_panels WHERE panel_id = ?",
                (self._panel_id,)
            )
            if not panel:
                embed = create_error_embed("Ошибка", "Панель не найдена!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            questions = json_to_list(panel['questions'])
            if not questions:
                embed = create_error_embed("Ошибка", "Вопросы анкеты не настроены!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            modal = ApplicationModal(questions, panel['guild_id'], panel['panel_id'], panel['name'])
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"[TICKET ERROR] PanelButtonView._on_button_click: {e}")
            traceback.print_exc()
            try:
                embed = create_error_embed("Ошибка", "Произошла ошибка.")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                pass


# ==================== МОДАЛ АНКЕТЫ (до 5 вопросов) ====================

class ApplicationModal(discord.ui.Modal):
    def __init__(self, questions, guild_id, panel_id, panel_name):
        super().__init__(title=f"Заявка: {panel_name[:40]}")
        self.guild_id = guild_id
        self.panel_id = panel_id
        self.panel_name = panel_name
        self.questions = questions

        for i, q in enumerate(questions[:5]):
            item = discord.ui.TextInput(
                label=q[:45],
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=400
            )
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            panel = await fetch_one(
                "SELECT * FROM ticket_panels WHERE panel_id = ?",
                (self.panel_id,)
            )
            if not panel:
                embed = create_error_embed("Ошибка", "Панель не найдена!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            category_id = panel['category_id']
            guild = interaction.guild
            category = guild.get_channel(category_id) if category_id else None

            # Права доступа к каналу
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
            }

            # Добавляем роли обзванивающего
            call_roles = json_to_list(panel['call_roles'])
            for role_id in call_roles:
                role = guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

            # Добавляем роли администраторов (могут рассматривать заявки)
            admin_roles = json_to_list(panel['admin_roles'])
            for role_id in admin_roles:
                role = guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)

            # Счётчик тикетов
            existing_tickets = await fetch_all(
                "SELECT * FROM tickets WHERE guild_id = ?",
                (guild.id,)
            )
            ticket_num = len(existing_tickets) + 1

            # Создаём канал
            channel = await guild.create_text_channel(
                name=f"тикет-{ticket_num}",
                category=category,
                overwrites=overwrites
            )

            await execute_query(
                "INSERT INTO tickets (channel_id, guild_id, user_id, panel_id) VALUES (?, ?, ?, ?)",
                (channel.id, guild.id, interaction.user.id, self.panel_id)
            )

            # ===== ФОРМИРУЕМ EMBED С ОТВЕТАМИ =====
            answers = {}
            for i, child in enumerate(self.children):
                if isinstance(child, discord.ui.TextInput):
                    answers[child.label] = child.value

            # Предыдущие заявки
            prev_tickets = await fetch_all(
                "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ?",
                (guild.id, interaction.user.id)
            )
            prev_count = len(prev_tickets) - 1

            desc = ""
            desc += f"**Тип заявки:** {self.panel_name}\n\n"
            desc += "**Предыдущие заявки:**\n"
            desc += "Заявок не найдено.\n" if prev_count <= 0 else f"Найдено заявок: {prev_count}\n"
            desc += "\n**Заявление**\n"

            for label, value in answers.items():
                desc += f"**{label}**\n{value}\n\n"

            desc += f"**Пользователь:** {interaction.user.mention}\n"
            desc += f"**Username / ID:** {interaction.user.name} / {interaction.user.id}\n"
            desc += f"**Сервер, в {interaction.created_at.strftime('%H:%M')}**"

            embed = discord.Embed(
                title=f"Ticket — {self.panel_name}",
                description=desc,
                color=0xED4245
            )
            embed.set_footer(text=WATERMARK)

            view = TicketActionView(self.guild_id, self.panel_id, interaction.user.id)
            await channel.send(embed=embed, view=view)

            confirm_embed = create_success_embed("Заявка подана", f"Ваш тикет: {channel.mention}")
            await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

        except Exception as e:
            print(f"[TICKET ERROR] ApplicationModal.on_submit: {e}")
            traceback.print_exc()
            try:
                embed = create_error_embed("Ошибка", f"Произошла ошибка при создании тикета: {str(e)[:200]}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"[TICKET ERROR] ApplicationModal.on_error: {error}")
        traceback.print_exc()
        try:
            embed = create_error_embed("Ошибка", "Произошла ошибка при обработке анкеты.")
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass


# ==================== КНОПКА ПОДАЧИ ТИКЕТА ====================

class TicketButtonView(discord.ui.View):
    def __init__(self, panel_id, button_label="Подать заявку", button_emoji="📩"):
        super().__init__(timeout=None)
        self.panel_id = panel_id

        button = discord.ui.Button(
            label=button_label or "Подать заявку",
            style=discord.ButtonStyle.green,
            emoji=button_emoji or "📩",
            custom_id=f"ticket_panel_{panel_id}"
        )
        button.callback = self._on_button_click
        self.add_item(button)

    async def _on_button_click(self, interaction: discord.Interaction):
        try:
            panel = await fetch_one(
                "SELECT * FROM ticket_panels WHERE panel_id = ?",
                (self.panel_id,)
            )
            if not panel:
                embed = create_error_embed("Ошибка", "Панель не найдена! Возможно, она была удалена.")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            questions = json_to_list(panel['questions'])
            if not questions:
                embed = create_error_embed("Ошибка", "Вопросы анкеты не настроены!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            modal = ApplicationModal(questions, panel['guild_id'], self.panel_id, panel['name'])
            await interaction.response.send_modal(modal)
        except Exception as e:
            print(f"[TICKET ERROR] TicketButtonView._on_button_click: {e}")
            traceback.print_exc()
            try:
                embed = create_error_embed("Ошибка", "Произошла ошибка при открытии анкеты.")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                pass


# ==================== ВЫБОР КАНАЛА ОБЗВОНА ====================

class CallChannelSelectView(discord.ui.View):
    def __init__(self, guild_id, panel_id, target_user_id, call_channel_ids):
        super().__init__(timeout=60)
        self.add_item(CallChannelSelect(guild_id, panel_id, target_user_id, call_channel_ids))


class CallChannelSelect(discord.ui.Select):
    def __init__(self, guild_id, panel_id, target_user_id, call_channel_ids):
        options = []
        for ch_id in call_channel_ids:
            options.append(discord.SelectOption(
                label=f"Канал {ch_id}",
                value=str(ch_id),
                description=f"ID: {ch_id}"
            ))
        super().__init__(
            placeholder="Выберите голосовой канал для обзвона",
            options=options,
            min_values=1,
            max_values=1
        )
        self.guild_id = guild_id
        self.panel_id = panel_id
        self.target_user_id = target_user_id

    async def callback(self, interaction: discord.Interaction):
        selected_channel_id = int(self.values[0])
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ?",
            (self.panel_id,)
        )
        call_msg = panel['call_message'] if panel and panel['call_message'] else "Обзвон начат! Переходите в голосовой канал."

        user = interaction.guild.get_member(self.target_user_id)
        user_mention = user.mention if user else f"<@{self.target_user_id}>"

        embed = create_embed("Обзвон",
            f"{call_msg}\n\nГолосовой канал: <#{selected_channel_id}>", EMBED_PURPLE)
        embed.add_field(name="Участник", value=user_mention, inline=True)
        embed.add_field(name="Вызвал", value=interaction.user.mention, inline=True)

        await interaction.channel.send(content=user_mention, embed=embed)
        await interaction.response.edit_message(content=None, view=None, embed=None)


# ==================== ДЕЙСТВИЯ В ТИКЕТЕ ====================

class TicketActionView(discord.ui.View):
    def __init__(self, guild_id, panel_id, owner_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.panel_id = panel_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Проверка прав перед любым действием с кнопками тикета"""
        try:
            # Владелец тикета НЕ может рассматривать свою собственную заявку
            if interaction.user.id == self.owner_id:
                embed = create_error_embed("Ошибка", "Вы не можете рассматривать свою собственную заявку!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return False

            # Проверка ролей администратора
            panel = await fetch_one(
                "SELECT * FROM ticket_panels WHERE panel_id = ?",
                (self.panel_id,)
            )
            if not panel:
                embed = create_error_embed("Ошибка", "Панель не найдена!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return False

            admin_roles = json_to_list(panel['admin_roles'])
            if not admin_roles:
                embed = create_error_embed("Нет прав",
                    "Администраторские роли не настроены для этой панели!\n"
                    "Используйте `/ticket panel admin_roles` для настройки.")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return False

            member = interaction.user
            has_admin = False
            for role_id in admin_roles:
                role = interaction.guild.get_role(role_id)
                if role and role in member.roles:
                    has_admin = True
                    break

            if not has_admin:
                role_mentions = ", ".join([f"<@&{rid}>" for rid in admin_roles])
                embed = create_error_embed("Нет прав",
                    f"Только администраторы могут рассматривать заявки!\n"
                    f"Требуемые роли: {role_mentions}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return False

            return True
        except Exception as e:
            print(f"[TICKET ERROR] interaction_check: {e}")
            traceback.print_exc()
            try:
                embed = create_error_embed("Ошибка", "Произошла ошибка при проверке прав.")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                pass
            return False

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="✅")
    async def accept_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            embed = create_error_embed("Ошибка", "Это не канал тикета!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        user = interaction.guild.get_member(ticket['user_id'])
        user_mention = user.mention if user else f"<@{ticket['user_id']}>"

        accept_embed = create_success_embed("Заявка принята",
            f"Заявка от {user_mention} была **принята** администратором {interaction.user.mention}.")
        await interaction.response.send_message(embed=accept_embed)

        if user:
            try:
                dm_embed = create_success_embed("Заявка принята!",
                    f"Ваша заявка на сервере **{interaction.guild.name}** была принята! Поздравляем!")
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        await self._log_action(interaction, "принята", user)

        try:
            original = await interaction.channel.fetch_message(interaction.message.id)
            new_embed = original.embeds[0].copy()
            new_embed.color = 0x57F287
            new_embed.set_footer(text=f"{WATERMARK} | Принята {interaction.user.name}")
            await original.edit(embed=new_embed, view=None)
        except Exception:
            pass

    @discord.ui.button(label="На рассмотрении", style=discord.ButtonStyle.primary, emoji="🔍")
    async def consider_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            embed = create_error_embed("Ошибка", "Это не канал тикета!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        user = interaction.guild.get_member(ticket['user_id'])
        user_mention = user.mention if user else f"<@{ticket['user_id']}>"

        consider_embed = create_embed("На рассмотрении",
            f"Заявка от {user_mention} взята на рассмотрение администратором {interaction.user.mention}.", EMBED_PURPLE)
        await interaction.response.send_message(embed=consider_embed)

    @discord.ui.button(label="Обзвон", style=discord.ButtonStyle.primary, emoji="📞")
    async def call_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ?",
            (self.panel_id,)
        )

        call_channels = json_to_list(panel['call_channels']) if panel else []

        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            embed = create_error_embed("Ошибка", "Это не канал тикета!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        user = interaction.guild.get_member(ticket['user_id'])
        if not user:
            embed = create_error_embed("Ошибка", "Пользователь не найден!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not call_channels:
            embed = create_error_embed("Ошибка", "Голосовые каналы не настроены! Используйте `/ticket panel call_channels`")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = create_embed("Обзвон",
            f"Выберите голосовой канал, куда вызвать {user.mention}", EMBED_PURPLE)
        view = CallChannelSelectView(self.guild_id, self.panel_id, user.id, call_channels)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            embed = create_error_embed("Ошибка", "Это не канал тикета!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        user = interaction.guild.get_member(ticket['user_id'])
        user_mention = user.mention if user else f"<@{ticket['user_id']}>"

        reject_embed = create_embed("Заявка отклонена",
            f"Заявка от {user_mention} была **отклонена** администратором {interaction.user.mention}.", EMBED_RED)
        await interaction.response.send_message(embed=reject_embed)

        if user:
            try:
                dm_embed = create_embed("Заявка отклонена",
                    f"Ваша заявка на сервере **{interaction.guild.name}** была отклонена.", EMBED_RED)
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                pass

        await self._log_action(interaction, "отклонена", user)

        try:
            original = await interaction.channel.fetch_message(interaction.message.id)
            new_embed = original.embeds[0].copy()
            new_embed.color = 0xED4245
            new_embed.set_footer(text=f"{WATERMARK} | Отклонена {interaction.user.name}")
            await original.edit(embed=new_embed, view=None)
        except Exception:
            pass

    async def _log_action(self, interaction, action, user):
        """Логирование действий в канал логов"""
        try:
            panel = await fetch_one(
                "SELECT * FROM ticket_panels WHERE panel_id = ?",
                (self.panel_id,)
            )
            if not panel or not panel['log_channel_id']:
                return

            log_channel = interaction.guild.get_channel(panel['log_channel_id'])
            if not log_channel:
                return

            user_mention = user.mention if user else f"<@{self.owner_id}>"
            log_embed = create_embed(f"Заявка {action}",
                f"**Панель:** {panel['name']}\n**Канал:** {interaction.channel.mention}\n**Участник:** {user_mention}\n**Администратор:** {interaction.user.mention}",
                EMBED_GREEN if action == "принята" else EMBED_RED
            )
            await log_channel.send(embed=log_embed)
        except Exception as e:
            print(f"[TICKET ERROR] _log_action: {e}")


# ==================== МОДАЛЫ НАСТРОЕК ПАНЕЛИ ====================

class PanelQuestionsModal(discord.ui.Modal, title="Вопросы анкеты"):
    questions_input = discord.ui.TextInput(
        label="Вопросы (каждый с новой строки, макс. 5)",
        style=discord.TextStyle.paragraph,
        placeholder="Введите вопросы анкеты (по одному на строку)",
        required=True,
        max_length=500
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            questions = [q.strip() for q in self.questions_input.value.split("\n") if q.strip()]
            if not questions:
                embed = create_error_embed("Ошибка", "Введите хотя бы один вопрос!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            questions = questions[:5]
            await execute_query(
                "UPDATE ticket_panels SET questions = ? WHERE panel_id = ?",
                (list_to_json(questions), self.panel_id)
            )
            questions_text = "\n".join([f"{i+1}. {q}" for i, q in enumerate(questions)])
            embed = create_success_embed("Успешно", f"Вопросы обновлены!\n{questions_text}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[TICKET ERROR] PanelQuestionsModal.on_submit: {e}")
            traceback.print_exc()
            try:
                await interaction.response.send_message(embed=create_error_embed("Ошибка", "Произошла ошибка при сохранении вопросов."), ephemeral=True)
            except Exception:
                pass


class PanelAdminRolesModal(discord.ui.Modal, title="Роли администраторов тикетов"):
    roles_input = discord.ui.TextInput(
        label="ID ролей через запятую",
        placeholder="123456,789012",
        required=True
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            roles = [int(r.strip()) for r in self.roles_input.value.split(",") if r.strip().isdigit()]
            if not roles:
                embed = create_error_embed("Ошибка", "Введите хотя бы один корректный ID роли!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            await execute_query(
                "UPDATE ticket_panels SET admin_roles = ? WHERE panel_id = ?",
                (list_to_json(roles), self.panel_id)
            )
            role_mentions = ", ".join([f"<@&{r}>" for r in roles])
            embed = create_success_embed("Успешно",
                f"Роли администраторов тикетов обновлены!\n{role_mentions}\n\n"
                f"*Только пользователи с этими ролями могут принимать/отклонять заявки.*")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[TICKET ERROR] PanelAdminRolesModal.on_submit: {e}")
            traceback.print_exc()
            try:
                await interaction.response.send_message(embed=create_error_embed("Ошибка", "Произошла ошибка при сохранении ролей."), ephemeral=True)
            except Exception:
                pass


class PanelCallRolesModal(discord.ui.Modal, title="Роли обзванивающего"):
    roles_input = discord.ui.TextInput(
        label="ID ролей через запятую",
        placeholder="123456,789012",
        required=True
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            roles = [int(r.strip()) for r in self.roles_input.value.split(",") if r.strip().isdigit()]
            if not roles:
                embed = create_error_embed("Ошибка", "Введите хотя бы один корректный ID роли!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            await execute_query(
                "UPDATE ticket_panels SET call_roles = ? WHERE panel_id = ?",
                (list_to_json(roles), self.panel_id)
            )
            role_mentions = ", ".join([f"<@&{r}>" for r in roles])
            embed = create_success_embed("Успешно", f"Роли обзванивающего обновлены!\n{role_mentions}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[TICKET ERROR] PanelCallRolesModal.on_submit: {e}")
            traceback.print_exc()
            try:
                await interaction.response.send_message(embed=create_error_embed("Ошибка", "Произошла ошибка."), ephemeral=True)
            except Exception:
                pass


class PanelCallChannelsModal(discord.ui.Modal, title="Голосовые каналы обзвона"):
    channels_input = discord.ui.TextInput(
        label="ID каналов через запятую",
        placeholder="123456,789012",
        required=True
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channels = [int(c.strip()) for c in self.channels_input.value.split(",") if c.strip().isdigit()]
            if not channels:
                embed = create_error_embed("Ошибка", "Введите хотя бы один корректный ID канала!")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            await execute_query(
                "UPDATE ticket_panels SET call_channels = ? WHERE panel_id = ?",
                (list_to_json(channels), self.panel_id)
            )
            channel_mentions = ", ".join([f"<#{c}>" for c in channels])
            embed = create_success_embed("Успешно", f"Голосовые каналы обзвона обновлены!\n{channel_mentions}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[TICKET ERROR] PanelCallChannelsModal.on_submit: {e}")
            traceback.print_exc()
            try:
                await interaction.response.send_message(embed=create_error_embed("Ошибка", "Произошла ошибка."), ephemeral=True)
            except Exception:
                pass


class PanelWelcomeModal(discord.ui.Modal, title="Приветственное сообщение"):
    message_input = discord.ui.TextInput(
        label="Сообщение",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await execute_query(
                "UPDATE ticket_panels SET welcome_message = ? WHERE panel_id = ?",
                (self.message_input.value, self.panel_id)
            )
            embed = create_success_embed("Успешно", "Приветственное сообщение обновлено!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[TICKET ERROR] PanelWelcomeModal.on_submit: {e}")
            try:
                await interaction.response.send_message(embed=create_error_embed("Ошибка", "Произошла ошибка."), ephemeral=True)
            except Exception:
                pass


class PanelCallMessageModal(discord.ui.Modal, title="Сообщение обзвона"):
    message_input = discord.ui.TextInput(
        label="Сообщение обзвона",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await execute_query(
                "UPDATE ticket_panels SET call_message = ? WHERE panel_id = ?",
                (self.message_input.value, self.panel_id)
            )
            embed = create_success_embed("Успешно", "Сообщение обзвона обновлено!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[TICKET ERROR] PanelCallMessageModal.on_submit: {e}")
            try:
                await interaction.response.send_message(embed=create_error_embed("Ошибка", "Произошла ошибка."), ephemeral=True)
            except Exception:
                pass


# ==================== ПАНЕЛЬ НАСТРОЕК С КНОПКАМИ ====================

class PanelLogoModal(discord.ui.Modal, title="Логотип панели"):
    url_input = discord.ui.TextInput(
        label="URL изображения логотипа",
        placeholder="https://example.com/logo.png",
        required=True,
        max_length=500
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            url = self.url_input.value.strip()
            if not url.startswith(("http://", "https://")):
                embed = create_error_embed("Ошибка", "Введите корректный URL (начинается на http:// или https://)")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            await execute_query(
                "UPDATE ticket_panels SET logo_url = ? WHERE panel_id = ?",
                (url, self.panel_id)
            )
            embed = create_success_embed("Успешно", "Логотип панели обновлён!")
            embed.set_image(url=url)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[TICKET ERROR] PanelLogoModal.on_submit: {e}")
            try:
                await interaction.response.send_message(embed=create_error_embed("Ошибка", "Произошла ошибка."), ephemeral=True)
            except Exception:
                pass


class PanelSettingsView(discord.ui.View):
    def __init__(self, panel_id):
        super().__init__(timeout=300)
        self.panel_id = panel_id

    @discord.ui.button(label="Вопросы анкеты", style=discord.ButtonStyle.primary, row=0)
    async def set_questions(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (self.panel_id,))
        modal = PanelQuestionsModal(self.panel_id)
        existing = json_to_list(panel['questions']) if panel else []
        modal.questions_input.default = "\n".join(existing)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Роли админов", style=discord.ButtonStyle.danger, row=0)
    async def set_admin_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PanelAdminRolesModal(self.panel_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Роли обзвона", style=discord.ButtonStyle.primary, row=1)
    async def set_call_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PanelCallRolesModal(self.panel_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Каналы обзвона", style=discord.ButtonStyle.primary, row=1)
    async def set_call_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PanelCallChannelsModal(self.panel_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Приветствие", style=discord.ButtonStyle.secondary, row=2)
    async def set_welcome(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (self.panel_id,))
        modal = PanelWelcomeModal(self.panel_id)
        modal.message_input.default = panel['welcome_message'] if panel and panel['welcome_message'] else ""
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Сообщение обзвона", style=discord.ButtonStyle.secondary, row=2)
    async def set_call_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (self.panel_id,))
        modal = PanelCallMessageModal(self.panel_id)
        modal.message_input.default = panel['call_message'] if panel and panel['call_message'] else ""
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Логотип", style=discord.ButtonStyle.secondary, row=3)
    async def set_logo(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (self.panel_id,))
        modal = PanelLogoModal(self.panel_id)
        if panel and panel['logo_url']:
            modal.url_input.default = panel['logo_url']
        await interaction.response.send_modal(modal)


# ==================== КОГ ====================

class TicketCog(commands.Cog, name="Ticket"):
    def __init__(self, bot):
        self.bot = bot

    ticket = app_commands.Group(name="ticket", description="Система тикетов")
    panel = app_commands.Group(name="panel", parent=ticket, description="Управление панелями тикетов")

    # ==================== ОБРАБОТКА ОШИБОК КОГА ====================

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Глобальная обработка ошибок для всех команд кога"""
        print(f"[TICKET ERROR] cog_app_command_error: {error}")
        traceback.print_exc()

        error_msg = "Произошла неизвестная ошибка."
        if isinstance(error, app_commands.CheckFailure):
            error_msg = "У вас нет прав для выполнения этой команды."
        elif isinstance(error, app_commands.CommandInvokeError):
            original = error.original
            error_msg = f"Ошибка: {str(original)[:200]}"

        embed = create_error_embed("Ошибка", error_msg)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

    # ==================== АВТОКОМПЛИТ ====================

    async def panel_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            panels = await fetch_all(
                "SELECT * FROM ticket_panels WHERE guild_id = ?",
                (interaction.guild.id,)
            )
            choices = []
            for p in panels:
                name = p['name']
                if current.lower() in name.lower():
                    choice_name = f"{name} (ID: {p['panel_id']})"
                    choices.append(app_commands.Choice(
                        name=choice_name[:100],  # Макс 100 символов
                        value=p['panel_id']
                    ))
            return choices[:25]
        except Exception as e:
            print(f"[TICKET ERROR] panel_autocomplete: {e}")
            traceback.print_exc()
            return []

    # ==================== СОЗДАНИЕ ПАНЕЛИ ====================

    @panel.command(name="create", description="Создать новую панель тикетов (отдельная анкета)")
    @app_commands.describe(
        name="Название панели (например: Заявка на вступление)",
        description="Описание панели",
        button_label="Текст кнопки",
        button_emoji="Эмодзи кнопки"
    )
    async def panel_create(self, interaction: discord.Interaction,
                           name: str, description: str = "",
                           button_label: str = "Подать заявку",
                           button_emoji: str = "📩"):
        try:
            panel_id = await execute_query(
                "INSERT INTO ticket_panels (guild_id, name, description, button_label, button_emoji) VALUES (?, ?, ?, ?, ?)",
                (interaction.guild.id, name, description, button_label, button_emoji)
            )

            embed = create_success_embed("Панель создана",
                f"**Название:** {name}\n**ID панели:** {panel_id}\n\n"
                f"Теперь настройте панель:\n"
                f"• `/ticket panel category` — категория для тикетов\n"
                f"• `/ticket panel questions` — вопросы анкеты\n"
                f"• `/ticket panel admin_roles` — роли администраторов (обязательно!)\n"
                f"• `/ticket panel logo` — логотип панели\n"
                f"• `/ticket panel send` — отправить панель в канал")
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            print(f"[TICKET ERROR] panel_create: {e}")
            traceback.print_exc()
            try:
                embed = create_error_embed("Ошибка", f"Не удалось создать панель: {str(e)[:200]}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                pass

    # ==================== ОТПРАВКА ПАНЕЛИ ====================

    @panel.command(name="send", description="Отправить панель тикетов в текущий канал")
    @app_commands.describe(panel_id="ID панели")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_send(self, interaction: discord.Interaction, panel_id: int):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена! Проверьте ID панели.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not panel['category_id']:
            embed = create_error_embed("Ошибка",
                "Сначала настройте категорию тикетов!\n"
                "Используйте `/ticket panel category`")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        questions = json_to_list(panel['questions'])
        if not questions:
            embed = create_error_embed("Ошибка",
                "Сначала настройте вопросы анкеты!\n"
                "Используйте `/ticket panel questions`")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        admin_roles = json_to_list(panel['admin_roles'])
        if not admin_roles:
            embed = create_error_embed("Ошибка",
                "Сначала настройте роли администраторов!\n"
                "Используйте `/ticket panel admin_roles`\n\n"
                "*Без ролей администраторов никто не сможет рассматривать заявки!*")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        description = format_panel_description(panel)
        embed = discord.Embed(
            description=description,
            color=0x2F3136
        )
        embed.set_footer(text=WATERMARK)

        logo_file = None
        if panel['logo_url']:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(panel['logo_url']) as resp:
                        if resp.status == 200:
                            logo_bytes = await resp.read()
                            logo_file = discord.File(io.BytesIO(logo_bytes), filename="logo.png")
            except Exception as e:
                print(f"[TICKET ERROR] Failed to download logo: {e}")

        view = PanelButtonView(panel_id)
        if logo_file:
            await interaction.response.send_message(file=logo_file, embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)

    # ==================== УДАЛЕНИЕ ПАНЕЛИ ====================

    @panel.command(name="delete", description="Удалить панель тикетов")
    @app_commands.describe(panel_id="ID панели")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_delete(self, interaction: discord.Interaction, panel_id: int):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "DELETE FROM ticket_panels WHERE panel_id = ?",
            (panel_id,)
        )
        embed = create_success_embed("Панель удалена",
            f"Панель **{panel['name']}** (ID: {panel_id}) удалена.")
        await interaction.response.send_message(embed=embed)

    # ==================== СПИСОК ПАНЕЛЕЙ ====================

    @panel.command(name="list", description="Список всех панелей тикетов")
    async def panel_list(self, interaction: discord.Interaction):
        panels = await fetch_all(
            "SELECT * FROM ticket_panels WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if not panels:
            embed = create_embed("Список панелей",
                "Нет созданных панелей. Используйте `/ticket panel create`", EMBED_PURPLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        desc = ""
        for p in panels:
            admin_roles = json_to_list(p['admin_roles'])
            admin_str = ", ".join([f"<@&{r}>" for r in admin_roles]) if admin_roles else "Не настроены"
            questions = json_to_list(p['questions'])
            q_count = len(questions)
            cat_str = f"<#{p['category_id']}>" if p['category_id'] else "Не задана"
            log_str = f"<#{p['log_channel_id']}>" if p['log_channel_id'] else "Не задан"
            logo_str = "✓" if p['logo_url'] else "✗"

            desc += f"**{p['name']}** (ID: {p['panel_id']})\n"
            desc += f"  Категория: {cat_str} | Логи: {log_str} | Лого: {logo_str}\n"
            desc += f"  Вопросы: {q_count} | Админ-роли: {admin_str}\n\n"

        embed = create_embed("Список панелей тикетов", desc, EMBED_PURPLE)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==================== КАТЕГОРИЯ ====================

    @panel.command(name="category", description="Установить категорию для тикетов панели")
    @app_commands.describe(panel_id="ID панели", category="Категория для тикетов")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_category(self, interaction: discord.Interaction,
                             panel_id: int, category: discord.CategoryChannel):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET category_id = ? WHERE panel_id = ?",
            (category.id, panel_id)
        )
        embed = create_success_embed("Успешно",
            f"Категория для панели **{panel['name']}** установлена: {category.mention}")
        await interaction.response.send_message(embed=embed)

    # ==================== КАНАЛ ЛОГОВ ====================

    @panel.command(name="log_channel", description="Установить канал логов для панели")
    @app_commands.describe(panel_id="ID панели", channel="Канал логов")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_log_channel(self, interaction: discord.Interaction,
                                panel_id: int, channel: discord.TextChannel):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET log_channel_id = ? WHERE panel_id = ?",
            (channel.id, panel_id)
        )
        embed = create_success_embed("Успешно",
            f"Канал логов для панели **{panel['name']}** установлен: {channel.mention}")
        await interaction.response.send_message(embed=embed)

    # ==================== ВОПРОСЫ АНКЕТЫ ====================

    @panel.command(name="questions", description="Установить вопросы анкеты для панели")
    @app_commands.describe(panel_id="ID панели")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_questions(self, interaction: discord.Interaction, panel_id: int):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        modal = PanelQuestionsModal(panel_id)
        existing = json_to_list(panel['questions'])
        modal.questions_input.default = "\n".join(existing)
        await interaction.response.send_modal(modal)

    # ==================== РОЛИ АДМИНИСТРАТОРОВ ====================

    @panel.command(name="admin_roles", description="Установить роли администраторов для рассмотрения заявок")
    @app_commands.describe(panel_id="ID панели", roles="ID ролей через запятую (например: 123456,789012)")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_admin_roles(self, interaction: discord.Interaction,
                                panel_id: int, roles: str):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        role_ids = [int(r.strip()) for r in roles.split(",") if r.strip().isdigit()]
        if not role_ids:
            embed = create_error_embed("Ошибка", "Введите хотя бы один корректный ID роли!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET admin_roles = ? WHERE panel_id = ?",
            (list_to_json(role_ids), panel_id)
        )
        role_mentions = ", ".join([f"<@&{r}>" for r in role_ids])
        embed = create_success_embed("Успешно",
            f"Роли администраторов для панели **{panel['name']}** обновлены!\n{role_mentions}\n\n"
            f"*Только пользователи с этими ролями могут принимать/отклонять заявки.*")
        await interaction.response.send_message(embed=embed)

    # ==================== РОЛИ ОБЗВАНИВАЮЩЕГО ====================

    @panel.command(name="call_roles", description="Установить роли обзванивающего для панели")
    @app_commands.describe(panel_id="ID панели", roles="ID ролей через запятую")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_call_roles(self, interaction: discord.Interaction,
                               panel_id: int, roles: str):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        role_ids = [int(r.strip()) for r in roles.split(",") if r.strip().isdigit()]
        if not role_ids:
            embed = create_error_embed("Ошибка", "Введите хотя бы один корректный ID роли!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET call_roles = ? WHERE panel_id = ?",
            (list_to_json(role_ids), panel_id)
        )
        role_mentions = ", ".join([f"<@&{r}>" for r in role_ids])
        embed = create_success_embed("Успешно",
            f"Роли обзванивающего для панели **{panel['name']}** обновлены!\n{role_mentions}")
        await interaction.response.send_message(embed=embed)

    # ==================== ГОЛОСОВЫЕ КАНАЛЫ ОБЗВОНА ====================

    @panel.command(name="call_channels", description="Установить голосовые каналы обзвона для панели")
    @app_commands.describe(panel_id="ID панели", channels="ID каналов через запятую")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_call_channels(self, interaction: discord.Interaction,
                                  panel_id: int, channels: str):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        channel_ids = [int(c.strip()) for c in channels.split(",") if c.strip().isdigit()]
        if not channel_ids:
            embed = create_error_embed("Ошибка", "Введите хотя бы один корректный ID канала!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET call_channels = ? WHERE panel_id = ?",
            (list_to_json(channel_ids), panel_id)
        )
        channel_mentions = ", ".join([f"<#{c}>" for c in channel_ids])
        embed = create_success_embed("Успешно",
            f"Голосовые каналы обзвона для панели **{panel['name']}** обновлены!\n{channel_mentions}")
        await interaction.response.send_message(embed=embed)

    # ==================== ПРИВЕТСТВЕННОЕ СООБЩЕНИЕ ====================

    @panel.command(name="welcome", description="Установить приветственное сообщение для панели")
    @app_commands.describe(panel_id="ID панели", message="Приветственное сообщение")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_welcome(self, interaction: discord.Interaction,
                            panel_id: int, message: str):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET welcome_message = ? WHERE panel_id = ?",
            (message, panel_id)
        )
        embed = create_success_embed("Успешно",
            f"Приветственное сообщение для панели **{panel['name']}** обновлено!")
        await interaction.response.send_message(embed=embed)

    # ==================== СООБЩЕНИЕ ОБЗВОНА ====================

    @panel.command(name="call_message", description="Установить сообщение обзвона для панели")
    @app_commands.describe(panel_id="ID панели", message="Сообщение обзвона")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_call_message(self, interaction: discord.Interaction,
                                 panel_id: int, message: str):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET call_message = ? WHERE panel_id = ?",
            (message, panel_id)
        )
        embed = create_success_embed("Успешно",
            f"Сообщение обзвона для панели **{panel['name']}** обновлено!")
        await interaction.response.send_message(embed=embed)

    # ==================== НАСТРОЙКИ ПАНЕЛИ ====================

    @panel.command(name="settings", description="Просмотр и настройка панели тикетов")
    @app_commands.describe(panel_id="ID панели")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_settings(self, interaction: discord.Interaction, panel_id: int):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        admin_roles = json_to_list(panel['admin_roles'])
        call_roles = json_to_list(panel['call_roles'])
        call_channels = json_to_list(panel['call_channels'])
        questions = json_to_list(panel['questions'])

        admin_str = ', '.join([f'<@&{r}>' for r in admin_roles]) or 'Не настроены'
        roles_str = ', '.join([f'<@&{r}>' for r in call_roles]) or 'Не установлены'
        channels_str = ', '.join([f'<#{c}>' for c in call_channels]) or 'Не установлены'
        log_str = f'<#{panel["log_channel_id"]}>' if panel['log_channel_id'] else 'Не установлен'
        cat_str = f'<#{panel["category_id"]}>' if panel['category_id'] else 'Не установлена'
        questions_str = '\n'.join([f'{i+1}. {q}' for i, q in enumerate(questions)]) if questions else 'Не установлены'

        desc = f"**Название:** {panel['name']}\n"
        desc += f"**Категория:** {cat_str}\n"
        desc += f"**Канал логов:** {log_str}\n"
        desc += f"**Роли администраторов:** {admin_str}\n"
        desc += f"**Роли обзванивающего:** {roles_str}\n"
        desc += f"**Голосовые каналы:** {channels_str}\n"
        desc += f"**Вопросы:**\n{questions_str}\n"
        desc += f"\n**Приветствие:** {panel['welcome_message'] or 'По умолчанию'}\n"
        desc += f"**Сообщение обзвона:** {panel['call_message'] or 'По умолчанию'}\n"
        desc += f"**Логотип:** {'Установлен ✓' if panel['logo_url'] else 'Не установлен'}"

        embed = create_embed(f"Настройки панели: {panel['name']}", desc, EMBED_PURPLE)
        if panel['logo_url']:
            embed.set_image(url=panel['logo_url'])
        view = PanelSettingsView(panel_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ==================== РЕДАКТИРОВАНИЕ ПАНЕЛИ ====================

    @panel.command(name="edit", description="Изменить название или описание панели")
    @app_commands.describe(
        panel_id="ID панели",
        name="Новое название",
        description="Новое описание",
        button_label="Новый текст кнопки",
        button_emoji="Новое эмодзи кнопки",
        logo_url="URL логотипа панели"
    )
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_edit(self, interaction: discord.Interaction, panel_id: int,
                         name: str = None, description: str = None,
                         button_label: str = None, button_emoji: str = None,
                         logo_url: str = None):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        updates = []
        if name is not None:
            await execute_query("UPDATE ticket_panels SET name = ? WHERE panel_id = ?", (name, panel_id))
            updates.append(f"**Название:** {name}")
        if description is not None:
            await execute_query("UPDATE ticket_panels SET description = ? WHERE panel_id = ?", (description, panel_id))
            updates.append(f"**Описание:** {description}")
        if button_label is not None:
            await execute_query("UPDATE ticket_panels SET button_label = ? WHERE panel_id = ?", (button_label, panel_id))
            updates.append(f"**Текст кнопки:** {button_label}")
        if button_emoji is not None:
            await execute_query("UPDATE ticket_panels SET button_emoji = ? WHERE panel_id = ?", (button_emoji, panel_id))
            updates.append(f"**Эмодзи кнопки:** {button_emoji}")
        if logo_url is not None:
            if not logo_url.startswith(("http://", "https://")):
                embed = create_error_embed("Ошибка", "URL логотипа должен начинаться на http:// или https://")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            await execute_query("UPDATE ticket_panels SET logo_url = ? WHERE panel_id = ?", (logo_url, panel_id))
            updates.append(f"**Логотип:** установлен")

        if not updates:
            embed = create_error_embed("Ошибка", "Укажите хотя бы один параметр для изменения!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = create_success_embed("Панель обновлена", "\n".join(updates))
        await interaction.response.send_message(embed=embed)

    # ==================== ЛОГОТИП ПАНЕЛИ ====================

    @panel.command(name="logo", description="Установить логотип панели (URL изображения)")
    @app_commands.describe(panel_id="ID панели", url="URL изображения логотипа")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_logo(self, interaction: discord.Interaction,
                         panel_id: int, url: str):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            embed = create_error_embed("Ошибка", "Панель не найдена!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not url.startswith(("http://", "https://")):
            embed = create_error_embed("Ошибка", "Введите корректный URL изображения (начинается на http:// или https://)")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "UPDATE ticket_panels SET logo_url = ? WHERE panel_id = ?",
            (url, panel_id)
        )
        embed = create_success_embed("Успешно",
            f"Логотип для панели **{panel['name']}** обновлён!\n\n"
            f"Для предпросмотра используйте `/ticket panel send`")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        logo_bytes = await resp.read()
                        logo_file = discord.File(io.BytesIO(logo_bytes), filename="logo.png")
                        await interaction.response.send_message(file=logo_file, embed=embed)
                    else:
                        await interaction.response.send_message(embed=embed)
        except Exception:
            await interaction.response.send_message(embed=embed)

    # ==================== ЗАКРЫТИЕ ТИКЕТА ====================

    @ticket.command(name="close", description="Закрыть текущий тикет")
    async def ticket_close(self, interaction: discord.Interaction):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            embed = create_error_embed("Ошибка", "Это не канал тикета!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Получаем панель для канала логов
        panel = None
        if ticket['panel_id']:
            panel = await fetch_one(
                "SELECT * FROM ticket_panels WHERE panel_id = ?",
                (ticket['panel_id'],)
            )

        # Собрать логи
        messages = []
        async for msg in interaction.channel.history(limit=100, oldest_first=True):
            if msg.author.bot:
                continue
            messages.append(f"[{msg.created_at.strftime('%H:%M')}] {msg.author.name}: {msg.content}")

        log_text = "\n".join(messages[-50:]) if messages else "Нет сообщений"

        # Определяем канал логов
        log_channel_id = None
        if panel and panel['log_channel_id']:
            log_channel_id = panel['log_channel_id']
        else:
            # Fallback к старым настройкам
            old_settings = await fetch_one(
                "SELECT * FROM ticket_settings WHERE guild_id = ?",
                (interaction.guild.id,)
            )
            if old_settings and old_settings['log_channel_id']:
                log_channel_id = old_settings['log_channel_id']

        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                owner_mention = f"<@{ticket['user_id']}>"
                panel_name = panel['name'] if panel else "Неизвестно"
                log_embed = create_embed("Тикет закрыт",
                    f"**Панель:** {panel_name}\n**Канал:** {interaction.channel.name}\n**Закрыл:** {interaction.user.mention}\n**Владелец:** {owner_mention}",
                    EMBED_RED)

                if len(log_text) > 1024:
                    log_embed.add_field(name="Сообщения", value="См. прикрепленный файл", inline=False)
                    import io
                    file = discord.File(io.BytesIO(log_text.encode('utf-8')), filename=f"ticket-{interaction.channel.name}.txt")
                    await log_channel.send(embed=log_embed, file=file)
                else:
                    log_embed.add_field(name="Сообщения", value=log_text[:1024] or "Нет", inline=False)
                    await log_channel.send(embed=log_embed)

        await execute_query("DELETE FROM tickets WHERE channel_id = ?", (interaction.channel.id,))

        closing_embed = create_embed("Тикет закрывается...",
            f"Тикет закрыт {interaction.user.mention}. Канал будет удален через 5 секунд.", EMBED_RED)
        await interaction.response.send_message(embed=closing_embed)

        import asyncio
        await asyncio.sleep(5)
        await interaction.channel.delete()


async def setup(bot):
    await bot.add_cog(TicketCog(bot))
