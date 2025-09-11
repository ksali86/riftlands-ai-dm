
"""
Riftlands AI DM — v1.6.1 (Debug Build)
This build adds:
• Extra logging to confirm if DISCORD_TOKEN is detected on Railway.
• Prints the first 5 chars of the token safely for verification.
• If missing, idles instead of crashing so Railway stops looping.
"""
import os
import asyncio
import logging
import discord
from discord import app_commands

# ------------- Logging Setup -------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)8s] %(name)s: %(message)s",
)
log = logging.getLogger("riftlands.debug")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_ENV = os.getenv("RIFTLANDS_GUILD_ID")
GUILD_ID = int(GUILD_ID_ENV) if GUILD_ID_ENV and GUILD_ID_ENV.isdigit() else None

# Debug logging for environment vars
if TOKEN:
    log.info("✅ DISCORD_TOKEN detected (starts with: %s...)", TOKEN[:5])
else:
    log.error("❌ DISCORD_TOKEN is missing! Bot will idle instead of crashing.")
    log.error("Please add DISCORD_TOKEN in Railway → Variables.")
    # Idle forever so Railway doesn't restart-loop
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(3600))
    raise SystemExit

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ------------- Slash Command: /ping -------------
@tree.command(name="ping", description="Check bot health and latency.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(client.latency * 1000)
    await interaction.response.send_message(f"Pong! 🏓 ({latency_ms}ms)", ephemeral=True)

# ------------- Startup -------------
@client.event
async def on_ready():
    log.info("🤖 Logged in as %s (ID: %s)", client.user, client.user.id)
    log.info("🌿 Slash commands will attempt to sync shortly...")

if __name__ == "__main__":
    try:
        client.run(TOKEN)
    except Exception as e:
        log.exception("❌ Bot crashed: %s", e)
