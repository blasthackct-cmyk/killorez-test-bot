import discord
from discord import app_commands
from discord.ext import commands
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, json_to_list, list_to_json, EMBED_GREEN, EMBED_RED, EMBED_PURPLE
import json
import traceback
import aiohttp
import io
import re
import asyncio

WATERMARK = "KILLOREZ HELPER"


def _normalize_requiem_markdown(text: str) -> str:
    """Очищает текст и форматирует разделители так, чтобы Discord рисовал тонкие линии."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\\n", "\n")
    # Discord требует строгие пустые строки до и после трех дефисов без пробелов в строке
    text = re.sub(r'(?m)^[ \t]*---[ \t]*$', '\n---\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_requiem_template(panel_name: str) -> str:
    """Шаблон 1:1 со скриншота с корректными разделителями."""
    return (
        f"📢 **{panel_name}**\n\n"
        "> Заявки в семью принимаются только на сервер **Redwood**.\n"
        "> Возраст для рассмотрения заявки: от **14 лет**.\n\n"
        "---\n\n"
        "**Срок рассмотрения заявок:** от пары часов до **2 дней**.\n"
        "Решение направляется ботом в **личные сообщения + в ветку тикета**, где вас пригласят на **собеседование**."
        " Отсутствие ответа в течение **24 часов** приводит к автоматическому закрытию тикета.\n\n"
        "Внимательно прочитайте шаблон заявки при её подаче.\n\n"
        "---\n\n"
        "**Дополнительные требования:**\n"
        "• Откаты с DM должны быть записаны **не более 1 недели** назад;\n"
        "• Минимальное кол-во людей на DM — **10 человек**;\n"
        "• Минимальная продолжительность DM — **10 минут**;\n"
        "• Видео-откат загружен на **YouTube / RuTube / Google Диск / Яндекс Диск**.\n\n"
        "> После подачи заявки следите за **ЛС** или за **сгенерированным тикетом** на общение от рекрутеров.\n\n"
        "---\n\n"
        "Ознакомьтесь с условиями выше и нажмите кнопку ниже ↓"
    )


# ==================== ВЫПАДАЮЩИЙ СПИСОК СЕМЕЙ ====================

class PanelFamilySelect(discord.ui.Select):
    def __init__(self, panels):
        options = []
        for p in panels:
            options.append(
                discord.SelectOption(
                    label=p['name'][:100],
                    value=str(p['panel_id']),
                    description=(p['description'][:95] if p['description'] else "Подать заявку")
                )
            )

        super().__init__(
            placeholder="Выберите семью для подачи заявки...",
            min_values=1,
            max_values=1,
            custom_id="requiem_family_select",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        panel_id = int(self.values[0])
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (panel_id,))
        if not panel:
            await interaction.response.send_message(
                embed=create_error_embed("Ошибка", "Панель не найдена!"),
                ephemeral=True
            )
            return

        questions = json_to_list(panel['questions'])
        if not questions:
            await interaction.response.send_message(
                embed=create_error_embed("Ошибка", "Вопросы анкеты не настроены!"),
                ephemeral=True
            )
            return

        modal = ApplicationModal(questions, panel['guild_id'], panel['panel_id'], panel['name'])
        await interaction.response.send_modal(modal)


class PanelFamilySelectView(discord.ui.View):
    def __init__(self, panels):
        super().__init__(timeout=None)
        self.add_item(PanelFamilySelect(panels))


# ==================== МОДАЛЬНОЕ ОКНО АНКЕТЫ ====================

class ApplicationModal(discord.ui.Modal):
    def __init__(self, questions, guild_id, panel_id, panel_name):
        super().__init__(title=f"Заявка: {panel_name[:40]}")
        self.guild_id = guild_id
        self.panel_id = panel_id
        self.panel_name = panel_name
        self.questions = questions

        for q in questions[:5]:
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
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", "Панель не найдена!"),
                    ephemeral=True
                )
                return

            category_id = panel['category_id']
            guild = interaction.guild
            category = guild.get_channel(category_id) if category_id else None

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, attach_files=True, read_message_history=True
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
                )
            }

            for role_id in json_to_list(panel['call_roles']):
                role = guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

            for role_id in json_to_list(panel['admin_roles']):
                role = guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)

            existing_tickets = await fetch_all("SELECT * FROM tickets WHERE guild_id = ?", (guild.id,))
            ticket_num = len(existing_tickets) + 1

            channel = await guild.create_text_channel(
                name=f"тикет-{ticket_num}",
                category=category,
                overwrites=overwrites
            )

            await execute_query(
                "INSERT INTO tickets (channel_id, guild_id, user_id, panel_id) VALUES (?, ?, ?, ?)",
                (channel.id, guild.id, interaction.user.id, self.panel_id)
            )

            answers = {child.label: child.value for child in self.children if isinstance(child, discord.ui.TextInput)}

            prev_tickets = await fetch_all(
                "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ?",
                (guild.id, interaction.user.id)
            )
            prev_count = len(prev_tickets) - 1

            desc = f"**Тип заявки:** {self.panel_name}\n\n"
            desc += f"**Предыдущие заявки:**\n{'Заявок не найдено.' if prev_count <= 0 else f'Найдено заявок: {prev_count}'}\n\n"
            desc += "**Заявление**\n"

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

            view = TicketActionView(self.guild_id, self.panel_id, interaction.user.id)
            await channel.send(embed=embed, view=view)

            confirm_embed = create_success_embed("Заявка подана", f"Ваш тикет: {channel.mention}")
            await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

        except Exception as e:
            print(f"[TICKET ERROR] ApplicationModal.on_submit: {e}")
            traceback.print_exc()
            await interaction.response.send_message(
                embed=create_error_embed("Ошибка", f"Произошла ошибка при создании тикета: {str(e)[:200]}"),
                ephemeral=True
            )


# ==================== УПРАВЛЕНИЕ ВНУТРИ ТИКЕТА ====================

class CallChannelSelect(discord.ui.Select):
    def __init__(self, guild_id, panel_id, target_user_id, call_channel_ids):
        options = [
            discord.SelectOption(label=f"Канал {ch_id}", value=str(ch_id), description=f"ID: {ch_id}")
            for ch_id in call_channel_ids
        ]
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
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (self.panel_id,))
        call_msg = panel['call_message'] if panel and panel['call_message'] else "Обзвон начат! Переходите в голосовой канал."

        user = interaction.guild.get_member(self.target_user_id)
        user_mention = user.mention if user else f"<@{self.target_user_id}>"

        embed = create_embed(
            "Обзвон",
            f"{call_msg}\n\nГолосовой канал: <#{selected_channel_id}>",
            EMBED_PURPLE,
            footer=False
        )
        embed.add_field(name="Участник", value=user_mention, inline=True)
        embed.add_field(name="Вызвал", value=interaction.user.mention, inline=True)

        await interaction.channel.send(content=user_mention, embed=embed)
        await interaction.response.edit_message(content=None, view=None, embed=None)


class CallChannelSelectView(discord.ui.View):
    def __init__(self, guild_id, panel_id, target_user_id, call_channel_ids):
        super().__init__(timeout=60)
        self.add_item(CallChannelSelect(guild_id, panel_id, target_user_id, call_channel_ids))


class TicketActionView(discord.ui.View):
    def __init__(self, guild_id, panel_id, owner_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.panel_id = panel_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            await interaction.response.send_message(
                embed=create_error_embed("Ошибка", "Вы не можете рассматривать свою собственную заявку!"),
                ephemeral=True
            )
            return False

        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (self.panel_id,))
        if not panel:
            await interaction.response.send_message(embed=create_error_embed("Ошибка", "Панель не найдена!"), ephemeral=True)
            return False

        admin_roles = json_to_list(panel['admin_roles'])
        if not admin_roles:
            await interaction.response.send_message(
                embed=create_error_embed("Нет прав", "Администраторские роли не настроены!"),
                ephemeral=True
            )
            return False

        if not any(role.id in admin_roles for role in interaction.user.roles):
            role_mentions = ", ".join([f"<@&{r}>" for r in admin_roles])
            await interaction.response.send_message(
                embed=create_error_embed("Нет прав", f"Требуемые роли: {role_mentions}"),
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="✅")
    async def accept_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Это не канал тикета!"), ephemeral=True)

        user = interaction.guild.get_member(ticket['user_id'])
        user_mention = user.mention if user else f"<@{ticket['user_id']}>"

        accept_embed = create_success_embed("Заявка принята", f"Заявка от {user_mention} принята администратором {interaction.user.mention}.")
        await interaction.response.send_message(embed=accept_embed)

        if user:
            try:
                await user.send(embed=create_success_embed("Заявка принята!", f"Ваша заявка на сервере **{interaction.guild.name}** принята!"))
            except discord.Forbidden:
                pass

        try:
            original = await interaction.channel.fetch_message(interaction.message.id)
            new_embed = original.embeds[0].copy()
            new_embed.color = 0x57F287
            await original.edit(embed=new_embed, view=None)
        except Exception:
            pass

    @discord.ui.button(label="На рассмотрении", style=discord.ButtonStyle.primary, emoji="🔍")
    async def consider_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Это не канал тикета!"), ephemeral=True)

        user = interaction.guild.get_member(ticket['user_id'])
        user_mention = user.mention if user else f"<@{ticket['user_id']}>"
        await interaction.response.send_message(embed=create_embed("На рассмотрении", f"Заявка от {user_mention} взята на рассмотрение.", EMBED_PURPLE, footer=False))

    @discord.ui.button(label="Обзвон", style=discord.ButtonStyle.primary, emoji="📞")
    async def call_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ?", (self.panel_id,))
        call_channels = json_to_list(panel['call_channels']) if panel else []

        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket or not (user := interaction.guild.get_member(ticket['user_id'])):
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Пользователь или тикет не найден!"), ephemeral=True)

        if not call_channels:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Каналы обзвона не настроены!"), ephemeral=True)

        view = CallChannelSelectView(self.guild_id, self.panel_id, user.id, call_channels)
        await interaction.response.send_message(embed=create_embed("Обзвон", f"Выберите канал для {user.mention}", EMBED_PURPLE, footer=False), view=view, ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Это не канал тикета!"), ephemeral=True)

        user = interaction.guild.get_member(ticket['user_id'])
        user_mention = user.mention if user else f"<@{ticket['user_id']}>"

        await interaction.response.send_message(embed=create_embed("Заявка отклонена", f"Заявка от {user_mention} отклонена.", EMBED_RED, footer=False))

        if user:
            try:
                await user.send(embed=create_embed("Заявка отклонена", f"Ваша заявка на сервере **{interaction.guild.name}** отклонена.", EMBED_RED, footer=False))
            except discord.Forbidden:
                pass

        try:
            original = await interaction.channel.fetch_message(interaction.message.id)
            new_embed = original.embeds[0].copy()
            new_embed.color = 0xED4245
            await original.edit(embed=new_embed, view=None)
        except Exception:
            pass


# ==================== МОДАЛЫ НАСТРОЙКИ ====================

class PanelQuestionsModal(discord.ui.Modal, title="Вопросы анкеты"):
    questions_input = discord.ui.TextInput(
        label="Вопросы (каждый с новой строки, макс. 5)",
        style=discord.TextStyle.paragraph,
        placeholder="Введите вопросы анкеты",
        required=True,
        max_length=500
    )

    def __init__(self, panel_id):
        super().__init__()
        self.panel_id = panel_id

    async def on_submit(self, interaction: discord.Interaction):
        questions = [q.strip() for q in self.questions_input.value.split("\n") if q.strip()]
        if not questions:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Введите хотя бы один вопрос!"), ephemeral=True)

        await execute_query(
            "UPDATE ticket_panels SET questions = ? WHERE panel_id = ?",
            (list_to_json(questions[:5]), self.panel_id)
        )
        await interaction.response.send_message(embed=create_success_embed("Успешно", "Вопросы обновлены!"), ephemeral=True)


# ==================== КОГ ====================

class TicketCog(commands.Cog, name="Ticket"):
    def __init__(self, bot):
        self.bot = bot

    ticket = app_commands.Group(name="ticket", description="Система тикетов")
    panel = app_commands.Group(name="panel", parent=ticket, description="Управление панелями тикетов")

    async def panel_autocomplete(self, interaction: discord.Interaction, current: str):
        panels = await fetch_all("SELECT * FROM ticket_panels WHERE guild_id = ?", (interaction.guild.id,))
        return [
            app_commands.Choice(name=f"{p['name']} (ID: {p['panel_id']})"[:100], value=p['panel_id'])
            for p in panels if current.lower() in p['name'].lower()
        ][:25]

    # ==================== ОТПРАВКА ПАНЕЛИ ====================

    @panel.command(name="send", description="Отправить панель в точности как в REQUIEM")
    @app_commands.describe(panel_id="ID панели")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_send(self, interaction: discord.Interaction, panel_id: int):
        panel = await fetch_one(
            "SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?",
            (panel_id, interaction.guild.id)
        )
        if not panel:
            return await interaction.response.send_message(
                embed=create_error_embed("Ошибка", "Панель не найдена!"),
                ephemeral=True
            )

        # Если в БД кастомное описание отсутствует или слишком короткое — ставим стандартный шаблон
        if panel['description'] and len(panel['description'].strip()) > 20:
            content = _normalize_requiem_markdown(panel['description'])
        else:
            content = get_requiem_template(panel['name'])

        all_panels = await fetch_all("SELECT * FROM ticket_panels WHERE guild_id = ?", (interaction.guild.id,))
        view = PanelFamilySelectView(all_panels if all_panels else [panel])

        await interaction.response.defer()

        # Баннер отправляется как прикрепленный файл без рамок Embed
        if panel['logo_url']:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(panel['logo_url']) as resp:
                        if resp.status == 200:
                            img_data = await resp.read()
                            file = discord.File(io.BytesIO(img_data), filename="banner.png")
                            await interaction.followup.send(content=content, file=file, view=view)
                            return
            except Exception as e:
                print(f"[TICKET LOGO ERROR] {e}")

        await interaction.followup.send(content=content, view=view)

    # ==================== КОМАНДЫ НАСТРОЙКИ ====================

    @panel.command(name="create", description="Создать семью/панель")
    async def panel_create(self, interaction: discord.Interaction, name: str, description: str = ""):
        panel_id = await execute_query(
            "INSERT INTO ticket_panels (guild_id, name, description) VALUES (?, ?, ?)",
            (interaction.guild.id, name, description)
        )
        embed = create_success_embed(
            "Панель создана",
            f"**Название:** {name}\n**ID:** {panel_id}\n\n"
            f"Настройте:\n"
            f"• `/ticket panel logo` — баннер (прямая ссылка .png/.jpg)\n"
            f"• `/ticket panel category` — категория каналов\n"
            f"• `/ticket panel admin_roles` — роли администраторов\n"
            f"• `/ticket panel questions` — вопросы анкеты\n"
            f"• `/ticket panel send` — отправить"
        )
        await interaction.response.send_message(embed=embed)

    @panel.command(name="logo", description="Установить баннер сверху (прямая ссылка на картинку)")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_logo(self, interaction: discord.Interaction, panel_id: int, url: str):
        if not url.startswith(("http://", "https://")):
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "URL должен начинаться с http:// или https://"), ephemeral=True)
        await execute_query("UPDATE ticket_panels SET logo_url = ? WHERE panel_id = ?", (url, panel_id))
        await interaction.response.send_message(embed=create_success_embed("Успешно", "Баннер обновлен!"))

    @panel.command(name="category", description="Установить категорию для каналов тикетов")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_category(self, interaction: discord.Interaction, panel_id: int, category: discord.CategoryChannel):
        await execute_query("UPDATE ticket_panels SET category_id = ? WHERE panel_id = ?", (category.id, panel_id))
        await interaction.response.send_message(embed=create_success_embed("Успешно", f"Категория: {category.mention}"))

    @panel.command(name="admin_roles", description="ID ролей администраторов через запятую")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_admin_roles(self, interaction: discord.Interaction, panel_id: int, roles: str):
        role_ids = [int(r.strip()) for r in roles.split(",") if r.strip().isdigit()]
        if not role_ids:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Укажите корректные ID!"), ephemeral=True)
        await execute_query("UPDATE ticket_panels SET admin_roles = ? WHERE panel_id = ?", (list_to_json(role_ids), panel_id))
        await interaction.response.send_message(embed=create_success_embed("Успешно", "Роли администраторов обновлены!"))

    @panel.command(name="questions", description="Настроить вопросы анкеты")
    @app_commands.autocomplete(panel_id=panel_autocomplete)
    async def panel_questions(self, interaction: discord.Interaction, panel_id: int):
        panel = await fetch_one("SELECT * FROM ticket_panels WHERE panel_id = ? AND guild_id = ?", (panel_id, interaction.guild.id))
        if not panel:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Панель не найдена!"), ephemeral=True)

        modal = PanelQuestionsModal(panel_id)
        existing = json_to_list(panel['questions'])
        modal.questions_input.default = "\n".join(existing)
        await interaction.response.send_modal(modal)

    @ticket.command(name="close", description="Закрыть тикет")
    async def ticket_close(self, interaction: discord.Interaction):
        ticket = await fetch_one("SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        if not ticket:
            return await interaction.response.send_message(embed=create_error_embed("Ошибка", "Это не канал тикета!"), ephemeral=True)

        await execute_query("DELETE FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        await interaction.response.send_message(embed=create_embed("Тикет закрывается...", "Удаление через 5 секунд.", EMBED_RED, footer=False))
        await asyncio.sleep(5)
        await interaction.channel.delete()


async def setup(bot):
    await bot.add_cog(TicketCog(bot))