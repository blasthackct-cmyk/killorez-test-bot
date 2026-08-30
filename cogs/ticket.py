import discord
from discord import app_commands
from discord.ext import commands
from utils.database import fetch_one, fetch_all, execute_query
from utils.embeds import create_embed, create_success_embed, create_error_embed, json_to_list, list_to_json, EMBED_GREEN, EMBED_RED, EMBED_PURPLE
import json
import traceback
import aiohttp
import io
import asyncio

WATERMARK = "KILLOREZ HELPER"


def _fix_nl(text):
    if not text:
        return ""
    return text.replace("\\n", "\n")


def format_panel_content(panel):
    name = panel['name'] or "Тикет"
    desc = _fix_nl(panel['description'] or "")

    embed = discord.Embed(
        title=f"🔴 {name}",
        color=0x2F3136
    )

    if desc:
        sections = [s.strip() for s in desc.split("---") if s.strip()]
        for section in sections:
            embed.add_field(name="\u200b", value=section, inline=False)

    embed.add_field(
        name="\u200b",
        value="Ознакомьтесь с условиями выше и нажмите кнопку ниже ↓",
        inline=False
    )
    embed.set_footer(text=WATERMARK)

    return embed


def format_ticket_embed(panel_name, answers, user, created_at):
    embed = discord.Embed(
        title=f"Заявка — {panel_name}",
        color=0xED4245,
        timestamp=created_at
    )

    for label, value in answers.items():
        embed.add_field(name=label, value=f"```\n{value}\n