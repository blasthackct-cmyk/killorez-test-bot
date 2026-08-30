import discord
import json
from datetime import datetime

WATERMARK = "KILLOREZ HELPER"
EMBED_COLOR = 0x5865F2  # Discord blurple
EMBED_GREEN = 0x57F287
EMBED_RED = 0xED4245
EMBED_PURPLE = 0x9B59B6
EMBED_ORANGE = 0xE67E22


def create_embed(title=None, description=None, color=EMBED_COLOR, footer=True):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow()
    )
    if footer:
        embed.set_footer(text=WATERMARK)
    return embed


def create_success_embed(title, description=""):
    return create_embed(title, description, EMBED_GREEN)


def create_error_embed(title, description=""):
    return create_embed(title, description, EMBED_RED)


def create_info_embed(title, description=""):
    return create_embed(title, description, EMBED_COLOR)


def create_warning_embed(title, description=""):
    return create_embed(title, description, EMBED_ORANGE)


def format_time():
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def json_to_list(data):
    if isinstance(data, str):
        return json.loads(data)
    return data if data else []


def list_to_json(data):
    return json.dumps(data)
