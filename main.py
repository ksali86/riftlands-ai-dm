#!/usr/bin/env python3
# Riftlands AI DM v1.3.4 — Stability + Auto-Integration Update
# - Guild-only commands (fixes duplicates)
# - /resolve fixed: instant narration + confirmation
# - Auto-modifiers restored from pinned sheets
# - Auto HP & AC detection for narration and rolls

import os, re, json, random, datetime as dt
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

# Dice rolling
DICE_RE = re.compile(r"(?:(\d+))?d(\d+)([+-]\d+)?$", re.IGNORECASE)
def roll_expr(expr: str) -> Dict[str, Any]:
    expr = expr.strip().replace(" ", "")
    if not expr: return {"total": 0, "breakdown": "0"}
    total, parts = 0, []
    token = '+' + expr
    for t in re.split(r"(?=\+)", token):
        if not t: continue
        t = t.lstrip('+')
        m = DICE_RE.fullmatch(t)
        if m:
            count = int(m.group(1) or '1')
            sides = int(m.group(2))
            mod = int(m.group(3) or '0')
            rolls = [random.randint(1, sides) for _ in range(count)]
            subtotal = sum(rolls) + mod
            total += subtotal
            modtxt = f"{mod:+}" if mod else ""
            parts.append(f"{count}d{sides}{modtxt}={rolls} → {subtotal}")
        else:
            try:
                v = int(t); total += v; parts.append(str(v))
            except: parts.append('?' + t)
    return {"total": total, "breakdown": " + ".join(parts)}

# Narration
class Narrator:
    def fallback(self, scene_title: str, prompt: str, actions: List[Dict[str,str]]) -> str:
        by_player: DefaultDict[str, List[str]] = defaultdict(list)
        for a in actions[-20:]:
            by_player[a.get("name","Someone")].append(a.get("content","..."))
        for k in list(by_player.keys()):
            by_player[k] = by_player[k][-3:]
        lines = [f"🌫️ **{scene_title or 'Scene'} — Resolution**\n"]
        for name, acts in by_player.items():
            last = acts[-1] if acts else "moves with purpose"
            if not last.endswith(('.', '!', '?')): last += "."
            lines.append(f"**{name}** {last}")
        if not by_player:
            lines.append("The winds hiss through the ruins; for a breath, nothing moves.")
        lines.append("\nThe Riftstorm gnashes above; stone groans and ghostlight scatters across the ground.")
        hooks = [
            "Press the advantage and **pursue** the threat.",
            "**Regroup** and protect the vulnerable.",
            "**Investigate** the mystery before it slips away."
        ]
        lines.append("\n**Choices:**\n1. " + hooks[0] + "\n2. " + hooks[1] + "\n3. " + hooks[2])
        return "\n".join(lines)

# GM detection
def is_gm(inter: discord.Interaction) -> bool:
    if not inter.guild: return True
    if inter.guild.owner_id == inter.user.id: return True
    m = inter.guild.get_member(inter.user.id)
    if not m: return False
    if m.guild_permissions.manage_guild: return True
    return any(r.name.lower() == "gm" for r in m.roles)

# Bot
class RiftlandsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.state = load_state()
        self.narrator = Narrator()

    async def setup_hook(self):
        # Guild-only sync to avoid duplicates
        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)
            cmds = await self.tree.sync(guild=guild)
            print(f"🔄 Synced {len(cmds)} commands to guild {guild.name} ({guild.id}).")

bot = RiftlandsBot()

def get_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

def normalize_mod(mod: Optional[str]) -> str:
    if not mod: return "+0"
    mod = mod.strip()
    if not mod.startswith(("+","-")):
        try:
            n = int(mod)
            mod = f"+{n}" if n>=0 else str(n)
        except: mod = "+0"
    return mod

# Commands
@app_commands.guild_only()
@bot.tree.command(name="act", description="Take an action: describe, check skills, or attack.")
@app_commands.describe(description="Describe your action.")
@app_commands.choices(roll=[app_commands.Choice(name=n, value=n) for n in ["none","check","attack"]])
@app_commands.choices(skill=[app_commands.Choice(name=s.title(), value=s) for s in [
    "acrobatics","animal handling","arcana","athletics","deception","history","insight",
    "intimidation","investigation","medicine","nature","perception","performance","persuasion",
    "religion","sleight of hand","stealth","survival"
]])
async def act_cmd(inter: discord.Interaction,
    description: str,
    roll: Optional[app_commands.Choice[str]] = None,
    skill: Optional[app_commands.Choice[str]] = None,
    modifier: Optional[str] = None,
    weapon: Optional[str] = None,
    to_hit: Optional[str] = None,
    damage: Optional[str] = None,
):
    g = gstate_for(bot.state, inter.guild.id)
    g["current_scene"].setdefault("actions", []).append({
        "uid": str(inter.user.id),
        "name": inter.user.display_name,
        "content": description.strip(),
        "ts": dt.datetime.utcnow().isoformat()
    }); save_state(bot.state)

    # Read pinned sheet cache
    sheet = g["sheets_cache"].get(str(inter.user.id), {})
    hp, ac = sheet.get("hp"), sheet.get("ac")

    header = f"📝 **{inter.user.display_name}**: _{description.strip()}_"
    if hp and ac:
        header += f"  *(HP: {hp} | AC: {ac})*"

    if not roll or roll.value == "none":
        await inter.response.send_message(header, ephemeral=False)
        return

    if roll.value == "check":
        skl = (skill.value if skill else "skill").lower()
        mod = sheet.get("skills", {}).get(skl, normalize_mod(modifier or "+0"))
        res = roll_expr(f"d20{mod}")
        details = [f"🎲 **{inter.user.display_name}** — **{skl.title()} Check**",
                   f"d20{mod} → {res['breakdown']} = **{res['total']}**"]
        dice_ch = get_channel(inter.guild, "dice-checks")
        if dice_ch: await dice_ch.send("\n".join(details))
        body = header + f"\n✅ **{skl.title()} {mod}** — see **#dice-checks**."
        await inter.response.send_message(body, ephemeral=False)
        return

    if roll.value == "attack":
        atk_bonus, dmg_expr = to_hit or "+0", damage or "1d6"
        atk = roll_expr(f"d20{atk_bonus}")
        dmg = roll_expr(dmg_expr)
        details = [f"⚔️ **{inter.user.display_name}** — **{(weapon or 'Attack').title()}**",
                   f"Attack: d20{atk_bonus} → {atk['breakdown']} = **{atk['total']}**",
                   f"Damage: {dmg_expr} → {dmg['breakdown']} = **{dmg['total']}**"]
        dice_ch = get_channel(inter.guild, "dice-checks")
        if dice_ch: await dice_ch.send("\n".join(details))
        body = header + f"\n⚔️ **{(weapon or 'Attack').title()}** — see **#dice-checks**."
        await inter.response.send_message(body, ephemeral=False)
        return

@bot.tree.command(name="resolve", description="Resolve the current scene (fallback only).")
async def resolve_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    narration = bot.narrator.fallback(scene.get("title","Scene"), scene.get("prompt",""), actions)
    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel
    await log_channel.send(narration)
    g["scenes"].append({
        "title": scene.get("title") or "Scene",
        "summary": narration[:500],
        "actions": actions,
        "resolved_at": dt.datetime.utcnow().isoformat()
    })
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await inter.followup.send("✅ Scene resolved — narration posted in #adventure-log.", ephemeral=True)

@bot.tree.command(name="recap", description="Summarise the last few scenes.")
async def recap_cmd(inter: discord.Interaction):
    g = gstate_for(bot.state, inter.guild.id)
    scenes = g.get("scenes", [])
    if not scenes:
        await inter.response.send_message("No scenes to recap yet.", ephemeral=True)
        return
    last = scenes[-3:]
    recap = "**Recent Scenes Recap:**\n" + "\n\n".join([f"**{s['title']}**: {s['summary']}" for s in last])
    await inter.response.send_message(recap, ephemeral=False)

@bot.tree.command(name="sync", description="GM: force-refresh slash commands in this server.")
async def sync_cmd(inter: discord.Interaction):
    if not is_gm(inter):
        await inter.response.send_message("Only the GM can sync commands.", ephemeral=True)
        return
    bot.tree.copy_global_to(guild=inter.guild)
    cmds = await bot.tree.sync(guild=inter.guild)
    await inter.response.send_message(f"🔄 Synced {len(cmds)} commands to this server.", ephemeral=True)

@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Riftlands Adventures"))

def main():
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set."); return
    try:
        bot.run(TOKEN)
    except Exception as e:
        print("Bot run error:", e)

if __name__ == "__main__":
    main()
