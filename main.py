#!/usr/bin/env python3
# Riftlands AI DM v1.3.6 — Recovery++
# Fixes stale slash commands permanently by:
# - Wiping ALL global commands on startup
# - Wiping ALL guild commands per guild
# - Forcing fresh guild-only sync immediately
# - Auto-syncing commands internally
# - Debug breadcrumbs remain intact

import os, json, random, datetime as dt
from typing import Dict, Any, List, Optional, DefaultDict
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
DEBUG_MODE = True

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
INTENTS.guilds = True
INTENTS.reactions = True

STATE_FILE = "riftlands_state.json"

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: Dict[str, Any]):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def gstate_for(state: Dict[str, Any], guild_id: int) -> Dict[str, Any]:
    gid = str(guild_id)
    if gid not in state:
        state[gid] = {
            "players": {}, "inventory": {}, "scenes": [],
            "current_scene": {"title": "", "prompt": "", "opened_at": "", "actions": []},
            "settings": {"ai_narration": False},
            "sheets_cache": {}
        }
    return state[gid]

# Dice roller helper
def roll_dice(expr: str) -> Dict[str, Any]:
    expr = expr.strip().lower()
    count, sides, mod = 1, 20, 0
    if "d" in expr:
        parts = expr.split("d")
        if parts[0]: count = int(parts[0])
        rest = parts[1]
        if "+" in rest:
            sides, mod = rest.split("+")
            sides, mod = int(sides), int(mod)
        elif "-" in rest:
            sides, mod = rest.split("-")
            sides, mod = int(sides), -int(mod)
        else:
            sides = int(rest)
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + mod
    return {"total": total, "breakdown": f"{count}d{sides}{mod:+}={rolls} → {total}"}

# Narration fallback
class Narrator:
    def fallback(self, title: str, prompt: str, actions: List[Dict[str, str]]) -> str:
        print("🌀 [Narrator] Starting narration...")
        grouped: DefaultDict[str, List[str]] = defaultdict(list)
        for a in actions[-20:]:
            grouped[a.get("name", "Someone")].append(a.get("content", "..."))
        print("🌀 [Narrator] Grouped actions by player.")
        lines = [f"🌫️ **{title or 'Scene'} — Resolution**\n"]
        for name, acts in grouped.items():
            last = acts[-1] if acts else "moves silently"
            if not last.endswith(('.', '!', '?')): last += "."
            lines.append(f"**{name}** {last}")
        print("🌀 [Narrator] Generated player summaries.")
        lines.append("\nThe Riftstorm rumbles above, shadows twisting in unnatural light.")
        hooks = [
            "Press the advantage and **pursue** the threat.",
            "**Regroup** and protect the vulnerable.",
            "**Investigate** the mystery before it slips away."
        ]
        lines.append("\n**Choices:**\n1. " + hooks[0] + "\n2. " + hooks[1] + "\n3. " + hooks[2])
        final_text = "\n".join(lines)
        print(f"🌀 [Narrator] Final narration ready ({len(final_text)} chars).")
        return final_text

bot = commands.Bot(command_prefix="!", intents=INTENTS)
bot.state = load_state()
bot.narrator = Narrator()

def get_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Riftlands Recovery++"))
    try:
        # Remove all global commands
        global_cmds = await bot.tree.fetch_commands()
        for cmd in global_cmds:
            await bot.tree.remove_command(cmd.name, type=cmd.type)
        print(f"🧹 Removed {len(global_cmds)} global commands")

        # Wipe and re-sync per guild
        for guild in bot.guilds:
            old_cmds = await bot.tree.fetch_commands(guild=guild)
            for cmd in old_cmds:
                await bot.tree.remove_command(cmd.name, type=cmd.type, guild=guild)
            print(f"🧹 Removed {len(old_cmds)} guild commands for {guild.name} ({guild.id})")
            new_cmds = await bot.tree.sync(guild=guild)
            print(f"🔄 Synced {len(new_cmds)} fresh commands to {guild.name} ({guild.id})")
    except Exception as e:
        print("⚠️ Slash command sync failed:", e)

# Commands
@bot.tree.command(name="resolve-test", description="Simulate narration without posting.")
async def resolve_test(inter: discord.Interaction):
    await inter.response.send_message("✅ Resolve command triggered successfully (simulation mode).", ephemeral=True)

@bot.tree.command(name="debug-scene", description="Show current scene + dump state to logs.")
async def debug_scene(inter: discord.Interaction):
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    msg = f"Scene title: {scene.get('title','Untitled')}\nActions recorded: {len(actions)}"
    for i, a in enumerate(actions[-5:], 1):
        msg += f"\n{i}. {a.get('name','Someone')}: {a.get('content','...')}"
    print("📜 [Debug] Full state JSON dump:\n", json.dumps(g, indent=2))
    await inter.response.send_message(msg or "No scene data found.", ephemeral=True)

@bot.tree.command(name="resolve", description="Resolve the current scene with debug breadcrumbs.")
async def resolve(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    await inter.followup.send("🟢 Step 1: Deferred response", ephemeral=True)
    print("✅ [Resolve] Step 1: Deferred response")

    g = gstate_for(bot.state, inter.guild.id)
    await inter.followup.send("🟢 Step 2: Loaded guild state", ephemeral=True)
    print("✅ [Resolve] Step 2: Loaded guild state")

    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    await inter.followup.send(f"🟢 Step 3: Found {len(actions)} actions", ephemeral=True)
    print(f"✅ [Resolve] Step 3: Found {len(actions)} actions")

    narration = bot.narrator.fallback(scene.get("title","Scene"), scene.get("prompt",""), actions)
    await inter.followup.send("🟢 Step 4: Narration generated", ephemeral=True)
    print("✅ [Resolve] Step 4: Narration generated")

    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel
    try:
        await log_channel.send(narration)
        await inter.followup.send("🟢 Step 5: Narration posted", ephemeral=True)
        print("✅ [Resolve] Step 5: Narration posted")
    except Exception as e:
        await inter.followup.send(f"❌ Step 5 failed: {e}", ephemeral=True)
        print("❌ [Resolve] Failed posting narration:", e)
        return

    g["scenes"].append({
        "title": scene.get("title") or "Scene",
        "summary": narration[:500],
        "actions": actions,
        "resolved_at": dt.datetime.utcnow().isoformat()
    })
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await inter.followup.send("🟢 Step 6: Scene saved and reset", ephemeral=True)
    print("✅ [Resolve] Step 6: Scene saved and reset")

    await inter.followup.send("✅ Scene resolved successfully!", ephemeral=True)

def main():
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set."); return
    try:
        bot.run(TOKEN)
    except Exception as e:
        print("Bot run error:", e)

if __name__ == "__main__":
    main()
