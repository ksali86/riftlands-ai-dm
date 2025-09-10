#!/usr/bin/env python3
# Riftlands AI DM v1.4.0 — Force Sync
# Ensures slash commands are fully re-synced after clearing

import os, json, random, datetime as dt
from typing import Dict, Any, List, Optional, DefaultDict
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
INTENTS.guilds = True

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

# Dice roller
def roll(expr: str) -> Dict[str, Any]:
    expr = expr.strip().lower()
    count, sides, mod = 1, 20, 0
    if "d" in expr:
        a, b = expr.split("d", 1)
        if a: count = int(a)
        if "+" in b:
            s, m = b.split("+", 1); sides, mod = int(s), int(m)
        elif "-" in b:
            s, m = b.split("-", 1); sides, mod = int(s), -int(m)
        else:
            sides = int(b)
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + mod
    return {"total": total, "breakdown": f"{count}d{sides}{mod:+}={rolls} → {total}"}

# Narration fallback
class Narrator:
    def fallback(self, title: str, prompt: str, actions: List[Dict[str,str]]) -> str:
        grouped: DefaultDict[str, List[str]] = defaultdict(list)
        for a in actions[-20:]:
            grouped[a.get("name","Someone")].append(a.get("content","..."))
        lines = [f"🌫️ **{title or 'Scene'} — Resolution**\n"]
        for name, acts in grouped.items():
            last = acts[-1] if acts else "moves silently"
            if not last.endswith(('.', '!', '?')): last += "."
            lines.append(f"**{name}** {last}")
        lines.append("\nThe Riftstorm growls; ghostlight drifts across broken stone.")
        lines.append("\n**Choices:**\n1. Press the advantage.\n2. Regroup.\n3. Investigate.")
        return "\n".join(lines)

bot = commands.Bot(command_prefix="!", intents=INTENTS)
bot.state = load_state()
bot.narrator = Narrator()

def get_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

async def hard_reset_and_force_sync():
    cmds = bot.tree.get_commands()
    print(f"🌿 Loaded {len(cmds)} commands into tree before sync:")
    for cmd in cmds:
        print(f"   • {cmd.name}: {cmd.description or '(no description)'}")

    if len(cmds) == 0:
        print("⚠️ No commands detected in tree — Discord will show none!")

    # Clear ALL global commands
    try:
        await bot.http.bulk_upsert_global_commands(bot.user.id, [])
        print("🧹 Cleared ALL global commands")
    except Exception as e:
        print("⚠️ Failed clearing global commands:", e)

    # Wipe and re-sync per guild
    for guild in bot.guilds:
        try:
            await bot.http.bulk_upsert_guild_commands(bot.user.id, guild.id, [])
            print(f"🧹 Cleared ALL guild commands for {guild.name} ({guild.id})")

            # Now push commands back to Discord
            synced = await bot.tree.sync(guild=guild)
            print(f"🔄 Synced {len(synced)} commands to {guild.name}:")
            for cmd in synced:
                print(f"   • {cmd.name}")
        except Exception as e:
            print(f"⚠️ Failed syncing for guild {guild.name} ({guild.id}):", e)

@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Riftlands v1.4.0 Force Sync"))
    await bot.wait_until_ready()
    await hard_reset_and_force_sync()

# --- Commands ---

@bot.tree.command(name="resolve-test", description="Simulate narration without posting.")
async def resolve_test(inter: discord.Interaction):
    await inter.response.send_message("✅ Resolve command triggered successfully (simulation mode).", ephemeral=True)

@bot.tree.command(name="debug-scene", description="Show current scene and dump JSON to logs.")
async def debug_scene(inter: discord.Interaction):
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    msg = f"Scene: {scene.get('title','Untitled')}\nActions recorded: {len(actions)}"
    for i, a in enumerate(actions[-5:], 1):
        msg += f"\n{i}. {a.get('name','Someone')}: {a.get('content','...')}"
    print("📜 [Debug] State JSON:\n", json.dumps(g, indent=2))
    await inter.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="resolve", description="Resolve the current scene (breadcrumb debug).")
async def resolve_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    await inter.followup.send("🟢 Step 1: Deferred", ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    await inter.followup.send("🟢 Step 2: Loaded state", ephemeral=True)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    await inter.followup.send(f"🟢 Step 3: {len(actions)} actions", ephemeral=True)
    text = bot.narrator.fallback(scene.get("title","Scene"), scene.get("prompt",""), actions)
    await inter.followup.send("🟢 Step 4: Narration built", ephemeral=True)
    ch = get_channel(inter.guild, "adventure-log") or inter.channel
    try:
        await ch.send(text)
        await inter.followup.send("🟢 Step 5: Narration posted", ephemeral=True)
    except Exception as e:
        await inter.followup.send(f"❌ Step 5 failed: {e}", ephemeral=True)
        return
    g["scenes"].append({"title": scene.get("title") or "Scene","summary": text[:500],"actions": actions,"resolved_at": dt.datetime.utcnow().isoformat()})
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await inter.followup.send("🟢 Step 6: Scene saved/reset", ephemeral=True)
    await inter.followup.send("✅ Done", ephemeral=True)

@app_commands.choices(skill=[app_commands.Choice(name=s.title(), value=s) for s in [
    "acrobatics","animal handling","arcana","athletics","deception","history","insight",
    "intimidation","investigation","medicine","nature","perception","performance","persuasion",
    "religion","sleight of hand","stealth","survival"
]])
@bot.tree.command(name="act", description="Describe an action; optional skill check.")
async def act_cmd(inter: discord.Interaction, description: str, skill: Optional[app_commands.Choice[str]] = None, modifier: Optional[str] = None):
    g = gstate_for(bot.state, inter.guild.id)
    g["current_scene"].setdefault("actions", []).append({"uid": str(inter.user.id), "name": inter.user.display_name, "content": description.strip(), "ts": dt.datetime.utcnow().isoformat()})
    save_state(bot.state)
    header = f"📝 **{inter.user.display_name}**: _{description.strip()}_"
    if not skill:
        await inter.response.send_message(header, ephemeral=False); return
    mod = modifier.strip() if modifier else "+0"
    if not mod.startswith(("+","-")):
        try:
            n=int(mod); mod = f"+{n}" if n>=0 else str(n)
        except: mod="+0"
    r = roll(f"d20{mod}")
    dice_ch = get_channel(inter.guild, "dice-checks") or inter.channel
    await dice_ch.send(f"🎲 **{inter.user.display_name}** — **{skill.name} Check**\n{r['breakdown']}")
    await inter.response.send_message(header + f"\n✅ **{skill.name} {mod}** — see **#dice-checks**.", ephemeral=False)

@bot.tree.command(name="attack", description="Quick attack roll.")
async def attack_cmd(inter: discord.Interaction, weapon: str, to_hit: str, damage: str):
    atk = roll(f"d20{to_hit}")
    dmg = roll(damage)
    dice_ch = get_channel(inter.guild, "dice-checks") or inter.channel
    await dice_ch.send(f"⚔️ **{inter.user.display_name}** — **{weapon.title()}**\nAttack: {atk['breakdown']}\nDamage: {dmg['breakdown']}")
    await inter.response.send_message(f"⚔️ {weapon.title()} — see **#dice-checks**.", ephemeral=False)

def main():
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set."); return
    try:
        bot.run(TOKEN)
    except Exception as e:
        print("Bot run error:", e)

if __name__ == "__main__":
    main()
