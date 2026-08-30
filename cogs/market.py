import discord
from discord import app_commands
from discord.ext import commands
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, json_to_list, list_to_json, EMBED_GREEN, EMBED_RED, EMBED_PURPLE

WATERMARK = "KILLOREZ HELPER"


# ==================== UI: ОДОБРИТЬ / ОТКЛОНИТЬ ЗАКАЗ ====================

class OrderApproveRejectView(discord.ui.View):
    def __init__(self, user_id, guild_id, product_id, product_name, price):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.guild_id = guild_id
        self.product_id = product_id
        self.product_name = product_name
        self.price = price

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Списываем очки
        points_row = await fetch_one(
            "SELECT * FROM points WHERE user_id = ? AND guild_id = ?",
            (self.user_id, self.guild_id)
        )
        user_points = points_row['amount'] if points_row else 0

        if user_points < self.price:
            embed = create_error_embed("Недостаточно очков",
                f"У {f'<@{self.user_id}>'} **{user_points}** очков, а товар стоит **{self.price}** очков! Заказ невозможно выполнить.")
            await interaction.response.edit_message(embed=embed, view=None)
            return

        new_amount = user_points - self.price
        if new_amount <= 0:
            await execute_query(
                "DELETE FROM points WHERE user_id = ? AND guild_id = ?",
                (self.user_id, self.guild_id)
            )
        else:
            await execute_query(
                "UPDATE points SET amount = ? WHERE user_id = ? AND guild_id = ?",
                (new_amount, self.user_id, self.guild_id)
            )

        user = interaction.client.get_user(self.user_id)

        embed = create_success_embed("Заказ одобрен",
            f"Заказ **{self.product_name}** от {user.mention if user else f'<@{self.user_id}>'} одобрен {interaction.user.mention}!\n"
            f"Списано **{self.price}** очков. Остаток у заказчика: **{new_amount}** очков")
        await interaction.response.edit_message(embed=embed, view=None)

        try:
            dm_embed = create_success_embed("Заказ одобрен!",
                f"Ваш заказ **{self.product_name}** одобрен!\nСписано **{self.price}** очков. Остаток: **{new_amount}** очков")
            if user:
                await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.client.get_user(self.user_id)

        embed = create_embed("Заказ отклонен",
            f"Заказ **{self.product_name}** от {user.mention if user else f'<@{self.user_id}>'} отклонен {interaction.user.mention}.\nОчки не списаны.",
            EMBED_RED)
        await interaction.response.edit_message(embed=embed, view=None)

        try:
            dm_embed = create_embed("Заказ отклонен",
                f"Ваш заказ **{self.product_name}** был отклонен. Очки не списаны.", EMBED_RED)
            if user:
                await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass


# ==================== UI: МАГАЗИН ====================

class MarketSelect(discord.ui.Select):
    def __init__(self, products, guild_id):
        options = []
        for p in products:
            options.append(discord.SelectOption(
                label=p['name'],
                description=f"Цена: {p['price']} очков",
                value=str(p['product_id'])
            ))
        super().__init__(placeholder="Выбрать товар", options=options)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        product_id = int(self.values[0])
        product = await fetch_one(
            "SELECT * FROM market_products WHERE product_id = ? AND guild_id = ?",
            (product_id, self.guild_id)
        )
        if not product:
            embed = create_error_embed("Ошибка", "Товар не найден!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        points_row = await fetch_one(
            "SELECT * FROM points WHERE user_id = ? AND guild_id = ?",
            (interaction.user.id, self.guild_id)
        )
        user_points = points_row['amount'] if points_row else 0

        if user_points < product['price']:
            embed = create_error_embed("Недостаточно очков",
                f"У вас **{user_points}** очков, а товар стоит **{product['price']}** очков!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # НЕ списываем очки сразу — отправляем заказ в логи на рассмотрение
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (self.guild_id,)
        )
        if not settings or not settings['log_channel_id']:
            embed = create_error_embed("Ошибка", "Канал логов магазина не настроен! Используйте `/market log_channel`")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        log_channel = interaction.guild.get_channel(settings['log_channel_id'])
        if not log_channel:
            embed = create_error_embed("Ошибка", "Канал логов не найден!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Отправляем заказ в канал логов с кнопками
        order_embed = create_embed("Новый заказ!",
            f"**Товар:** {product['name']}\n"
            f"**Заказчик:** {interaction.user.mention}\n"
            f"**Цена:** {product['price']} очков\n"
            f"**Баланс заказчика:** {user_points} очков",
            EMBED_PURPLE)
        if product['description']:
            order_embed.add_field(name="Описание товара", value=product['description'], inline=False)

        view = OrderApproveRejectView(
            user_id=interaction.user.id,
            guild_id=self.guild_id,
            product_id=product['product_id'],
            product_name=product['name'],
            price=product['price']
        )

        await log_channel.send(embed=order_embed, view=view)

        confirm_embed = create_success_embed("Заказ отправлен",
            f"Ваш заказ на **{product['name']}** за **{product['price']}** очков отправлен на рассмотрение модераторам!\n"
            f"Очки будут списаны после одобрения заказа.")
        await interaction.response.send_message(embed=confirm_embed, ephemeral=True)


class MarketShopView(discord.ui.View):
    def __init__(self, guild_id, products):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(MarketSelect(products, guild_id))

    @discord.ui.button(label="Вернуться", style=discord.ButtonStyle.red)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        welcome_msg = settings['welcome_message'] if settings else "В данном магазине вы можете обменять свои очки на товары."

        embed = create_embed("Магазин", welcome_msg, EMBED_GREEN)
        view = MarketButtonView(guild_id=interaction.guild.id)
        await interaction.response.edit_message(embed=embed, view=view)


class MarketButtonView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Магазин", style=discord.ButtonStyle.green, emoji="🛒")
    async def shop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (self.guild_id,)
        )
        if not settings:
            embed = create_error_embed("Ошибка", "Магазин не настроен!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if settings['roles']:
            market_role_ids = json_to_list(settings['roles'])
            has_role = any(interaction.guild.get_role(rid) in interaction.user.roles for rid in market_role_ids)
            if not has_role:
                role_mentions = ", ".join([f"<@&{rid}>" for rid in market_role_ids])
                embed = create_error_embed("Ошибка", f"У вас нет нужной роли для доступа к магазину!\nТребуются: {role_mentions}")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

        products = await fetch_all(
            "SELECT * FROM market_products WHERE guild_id = ?",
            (self.guild_id,)
        )
        if not products:
            embed = create_error_embed("Магазин пуст", "Нет товаров в магазине!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        desc = ""
        if settings['exchange_enabled']:
            desc += f"**Внутренняя валюта:**\n🟢 Курс: {settings['exchange_rate']}$/банк\n\n"

        desc += "**Вещественные товары:**\n"
        for i, p in enumerate(products, 1):
            desc += f"{i}) {p['name']} | Цена: {p['price']}\n"

        embed = create_embed("Выберите товар", desc, EMBED_PURPLE)
        view = MarketShopView(self.guild_id, products)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ==================== КОГ ====================

class MarketCog(commands.Cog, name="Market"):
    def __init__(self, bot):
        self.bot = bot

    market = app_commands.Group(name="market", description="Система магазина")

    # --- Создать кнопку магазина ---
    @market.command(name="button", description="Создать кнопку магазина")
    async def market_button(self, interaction: discord.Interaction):
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        welcome_msg = settings['welcome_message'] if settings else "В данном магазине вы можете обменять свои очки либо на другую валюту, либо на товары, устанавливаемые вручную администрацией. Для того, чтобы воспользоваться магазином, нажмите кнопку ниже."

        embed = create_embed("Магазин", welcome_msg, EMBED_GREEN)
        view = MarketButtonView(guild_id=interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view)

    # --- Добавить товар ---
    @market.command(name="add_product", description="Добавить товар в магазин")
    @app_commands.describe(name="Название товара", price="Цена в очках", description="Описание товара")
    async def market_add_product(self, interaction: discord.Interaction, name: str, price: int, description: str = ""):
        await execute_query(
            "INSERT INTO market_products (guild_id, name, price, description) VALUES (?, ?, ?, ?)",
            (interaction.guild.id, name, price, description)
        )
        embed = create_success_embed("Товар добавлен", f"**{name}** добавлен в магазин за **{price}** очков!")
        await interaction.response.send_message(embed=embed)

    # --- Удалить товар ---
    @market.command(name="remove_product", description="Удалить товар из магазина")
    @app_commands.describe(name="Название товара")
    async def market_remove_product(self, interaction: discord.Interaction, name: str):
        result = await fetch_one(
            "SELECT * FROM market_products WHERE guild_id = ? AND name = ?",
            (interaction.guild.id, name)
        )
        if not result:
            embed = create_error_embed("Ошибка", f"Товар **{name}** не найден!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await execute_query(
            "DELETE FROM market_products WHERE guild_id = ? AND name = ?",
            (interaction.guild.id, name)
        )
        embed = create_success_embed("Товар удален", f"**{name}** удален из магазина")
        await interaction.response.send_message(embed=embed)

    # --- Список товаров ---
    @market.command(name="products", description="Список всех товаров")
    async def market_products(self, interaction: discord.Interaction):
        products = await fetch_all(
            "SELECT * FROM market_products WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if not products:
            embed = create_embed("Товары", "Нет товаров в магазине.", EMBED_PURPLE)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        desc = ""
        for i, p in enumerate(products, 1):
            desc += f"{i}. **{p['name']}** — {p['price']} очков"
            if p['description']:
                desc += f" ({p['description']})"
            desc += "\n"

        embed = create_embed("Товары магазина", desc, EMBED_PURPLE)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Роли доступа ---
    @market.command(name="roles", description="Изменить роли для доступа к магазину")
    @app_commands.describe(role1="Роль 1", role2="Роль 2", role3="Роль 3", role4="Роль 4", role5="Роль 5")
    async def market_roles(self, interaction: discord.Interaction,
                           role1: discord.Role = None, role2: discord.Role = None,
                           role3: discord.Role = None, role4: discord.Role = None,
                           role5: discord.Role = None):
        roles = [r for r in [role1, role2, role3, role4, role5] if r is not None]
        if not roles:
            embed = create_error_embed("Ошибка", "Укажите хотя бы одну роль!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        role_ids = [r.id for r in roles]
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE market_settings SET roles = ? WHERE guild_id = ?",
                (list_to_json(role_ids), interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO market_settings (guild_id, roles) VALUES (?, ?)",
                (interaction.guild.id, list_to_json(role_ids))
            )
        role_mentions = ", ".join([r.mention for r in roles])
        embed = create_success_embed("Роли магазина обновлены", f"Роли доступа: {role_mentions}")
        await interaction.response.send_message(embed=embed)

    # --- Админ роли ---
    @market.command(name="admin_roles", description="Изменить админ роли магазина")
    @app_commands.describe(role1="Роль 1", role2="Роль 2", role3="Роль 3", role4="Роль 4", role5="Роль 5")
    async def market_admin_roles(self, interaction: discord.Interaction,
                                  role1: discord.Role = None, role2: discord.Role = None,
                                  role3: discord.Role = None, role4: discord.Role = None,
                                  role5: discord.Role = None):
        roles = [r for r in [role1, role2, role3, role4, role5] if r is not None]
        if not roles:
            embed = create_error_embed("Ошибка", "Укажите хотя бы одну роль!")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        role_ids = [r.id for r in roles]
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE market_settings SET admin_roles = ? WHERE guild_id = ?",
                (list_to_json(role_ids), interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO market_settings (guild_id, admin_roles) VALUES (?, ?)",
                (interaction.guild.id, list_to_json(role_ids))
            )
        role_mentions = ", ".join([r.mention for r in roles])
        embed = create_success_embed("Админ роли обновлены", f"Роли: {role_mentions}")
        await interaction.response.send_message(embed=embed)

    # --- Канал логов магазина ---
    @market.command(name="log_channel", description="Изменить канал логов магазина")
    @app_commands.describe(channel="Канал логов")
    async def market_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE market_settings SET log_channel_id = ? WHERE guild_id = ?",
                (channel.id, interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO market_settings (guild_id, log_channel_id) VALUES (?, ?)",
                (interaction.guild.id, channel.id)
            )
        embed = create_success_embed("Канал логов обновлен", f"Логи магазина: {channel.mention}")
        await interaction.response.send_message(embed=embed)

    # --- Приветственное сообщение ---
    @market.command(name="welcome", description="Изменить приветственное сообщение магазина")
    @app_commands.describe(message="Приветственное сообщение")
    async def market_welcome(self, interaction: discord.Interaction, message: str):
        settings = await fetch_one(
            "SELECT * FROM market_settings WHERE guild_id = ?",
            (interaction.guild.id,)
        )
        if settings:
            await execute_query(
                "UPDATE market_settings SET welcome_message = ? WHERE guild_id = ?",
                (message, interaction.guild.id)
            )
        else:
            await execute_query(
                "INSERT INTO market_settings (guild_id, welcome_message) VALUES (?, ?)",
                (interaction.guild.id, message)
            )
        embed = create_success_embed("Успешно", "Приветственное сообщение магазина обновлено!")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(MarketCog(bot))
