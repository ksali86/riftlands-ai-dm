
"""
Riftlands AI DM — v1.6
Includes:
• Safe two‑phase slash-command sync with retries + guild/global fallback.
• /ping command for bot health + latency.
• !ping message-based fallback in case slash commands fail to sync.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from typing import List, Optional

import discord
from discord import app_commands

# ------------- Logging setup -------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)8s] %(name)s: %(message)s",
)
log = logging.getLogger("riftlands.sync")

# ------------- Config -------------
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_ENV = os.getenv("RIFTLANDS_GUILD_ID")
GUILD_ID: Optional[int] = int(GUILD_ID_ENV) if GUILD_ID_ENV and GUILD_ID_ENV.isdigit() else None

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ------------- Example scene state (stub) -------------
SCENE_STATE = {
    "scene_id": "starter-clearing",
    "title": "The Cold Clearing",
    "last_actions": []  # populated by /act, /attack for /recap
}

# ------------- Utility -------------

def log_tree_commands(prefix: str) -> List[str]:
    names = [cmd.name for cmd in tree.get_commands()]
    log.info("%s %d commands in tree: %s", prefix, len(names), ", ".join(names) or "<none>")
    return names

async def push_sync(guild_id: Optional[int]) -> List[app_commands.AppCommand]:
    if guild_id:
        guild_obj = discord.Object(id=guild_id)
        synced = await tree.sync(guild=guild_obj)
        return synced
    else:
        synced = await tree.sync()
        return synced

async def clear_remote_commands(guild_id: Optional[int]):
    """Two-step remote clear: clear local registry and sync empty set to Discord."""
    # Clear GLOBAL
    tree.clear_commands(guild=None)
    await tree.sync()
    log.info("🧹 Cleared ALL global commands")

    # Clear GUILD (if provided)
    if guild_id:
        guild_obj = discord.Object(id=guild_id)
        tree.clear_commands(guild=guild_obj)
        await tree.sync(guild=guild_obj)
        log.info("🧹 Cleared ALL guild commands for guild %s", guild_id)

async def safe_two_phase_sync(guild_id: Optional[int], delay_seconds: int = 10, max_retries: int = 3):
    loaded = log_tree_commands("🌿 Loaded")
    await clear_remote_commands(guild_id)
    log.info("⏳ Waiting %s seconds before pushing commands...", delay_seconds)
    await asyncio.sleep(delay_seconds)
    log_tree_commands("🌱 Ready to sync")

    async def attempt_sync(target_guild_id: Optional[int]) -> List[app_commands.AppCommand]:
        names_before = [cmd.name for cmd in tree.get_commands()]
        synced = await push_sync(target_guild_id)
        log.info(
            "🔄 Sync attempt -> Discord returned %d commands: %s",
            len(synced), ", ".join(cmd.name for cmd in synced) or "<none>",
        )
        expected = set(names_before)
        got = set(cmd.name for cmd in synced)
        if expected and expected != got:
            log.warning("Expected commands %s but Discord shows %s", sorted(expected), sorted(got))
        return synced

    retries = 0
    last_synced: List[app_commands.AppCommand] = []
    target_is_guild = guild_id is not None

    while retries < max_retries:
        last_synced = await attempt_sync(guild_id if target_is_guild else None)
        if len(last_synced) > 0:
            break
        retries += 1
        log.warning("Sync returned 0 commands (attempt %d/%d). Retrying in 3s...", retries, max_retries)
        await asyncio.sleep(3)
        if retries == 1 and target_is_guild:
            log.warning("Falling back to GLOBAL sync (guild-only sync returned 0).")
            target_is_guild = False

    target_name = "Riftland Adventures" if guild_id else "GLOBAL"
    log.info("🔄 Synced %d commands to %s:", len(last_synced), target_name)
    for cmd in last_synced:
        log.info("    • %s: %s", cmd.name, cmd.description or "(no description)")

# ------------- Slash Commands -------------

@tree.command(name="ping", description="Check bot health and latency.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(client.latency * 1000)
    await interaction.response.send_message(f"Pong! 🏓 ({latency_ms}ms)", ephemeral=True)

@tree.command(name="resolve-test", description="Simulate narration without posting.")
async def resolve_test(interaction: discord.Interaction):
    await interaction.response.send_message("(Test) The wind howls over the frost-bitten plain. Nothing is posted to the scene.", ephemeral=True)

@tree.command(name="debug-scene", description="Show current scene and dump JSON to logs.")
async def debug_scene(interaction: discord.Interaction):
    log.info("/debug-scene -> %s", json.dumps(SCENE_STATE, indent=2))
    await interaction.response.send_message(f"Scene: **{SCENE_STATE['title']}** (id: `{SCENE_STATE['scene_id']}`)\nLast actions: {len(SCENE_STATE['last_actions'])}", ephemeral=True)

@tree.command(name="resolve", description="Resolve the current scene (breadcrumb debug).")
async def resolve(interaction: discord.Interaction):
    await interaction.response.send_message("Narrative advances. (stub)")

@tree.command(name="act", description="Describe an action; optional skill check.")
@app_commands.describe(action="Your character's action")
async def act(interaction: discord.Interaction, action: str):
    SCENE_STATE["last_actions"].append({"user": interaction.user.id, "action": action})
    await interaction.response.send_message(f"You act: **{action}** (stub)")

@tree.command(name="attack", description="Quick attack roll with damage output.")
@app_commands.describe(target="Who/what you attack")
async def attack(interaction: discord.Interaction, target: str):
    await interaction.response.send_message(f"You attack **{target}**! (stub)")

@tree.command(name="recap", description="Summarise the current scene.")
async def recap(interaction: discord.Interaction):
    recent = SCENE_STATE.get("last_actions", [])[-5:]
    lines = [f"• <@{a['user']}>: {a['action']}" for a in recent]
    body = "\n".join(lines) if lines else "No actions yet."
    await interaction.response.send_message(f"**Recap — {SCENE_STATE['title']}**\n{body}")

# ------------- Message Fallback for Ping -------------
@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.content.strip().lower() == "!ping":
        latency_ms = round(client.latency * 1000)
        await message.channel.send(f"Pong! 🏓 ({latency_ms}ms)")

# ------------- Startup / on_ready -------------
@client.event
async def on_ready():
    log.info("🤖 Logged in as %s (ID: %s)", client.user, client.user.id)
    log_tree_commands("🌿 Loaded")
    try:
        await safe_two_phase_sync(GUILD_ID, delay_seconds=10, max_retries=3)
    except Exception:
        log.exception("Fatal error during safe_two_phase_sync")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN env var")
    client.run(TOKEN)
