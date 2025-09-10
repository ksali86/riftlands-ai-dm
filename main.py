#!/usr/bin/env python3
# Riftlands AI DM v1.3.2 — Fallback-First Hotfix
# - OpenAI fully disabled for now
# - /resolve always uses cinematic fallback narration instantly
# - Removes ephemeral "thinking" messages entirely
# - Forces full slash command re-sync so /act skill dropdowns work immediately

import os, re, json, random, datetime as dt
from typing import Dict, Any, List, Optional, DefaultDict
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_TOKEN".lower())

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
            "settings": {"ai_narration": False},  # force fallback mode
            "sheets_cache": {}
        }
    return state[gid]

# ---------- Dice ----------
DICE_RE = re.compile(r"(?:(\d+))?d(\d+)([+-]\d+)?$", re.IGNORECASE)
def roll_expr(expr: str) -> Dict[str, Any]:
    expr = expr.strip().replace(" ","")
    if not expr: return {"total":0,"breakdown":"0"}
    total, parts = 0, []
    token = '+' + expr
    for t in re.split(r"(?=\+)", token):
        if not t: continue
        t = t.lstrip('+')
        m = DICE_RE.fullmatch(t)
        if m:
            count = int(m.group(1) or '1'); sides = int(m.group(2)); mod = int(m.group(3) or '0')
            rolls = [random.randint(1, sides) for _ in range(count)]
            subtotal = sum(rolls) + mod; total += subtotal
            label = f"{count}d{sides}" if count!=1 else f"d{sides}"
            parts.append(f"{label}={rolls}{mod:+}" if mod else f"{label}={rolls}")
            parts[-1] += f" → {subtotal}"
        else:
            try:
                v = int(t); total += v; parts.append(str(v))
            except: parts.append('?' + t)
    return {"total": total, "breakdown": " + ".join(parts)}

# ---------- Sheets ----------
KNOWN_SKILLS = [
    "acrobatics","animal handling","arcana","athletics","deception","history","insight",
    "intimidation","investigation","medicine","nature","perception","performance","persuasion",
    "religion","sleight of hand","stealth","survival"
]

# ---------- Narration ----------
def fallback_narration(scene_title: str, prompt: str, actions: List[Dict[str,str]]) -> str:
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

# ---------- Bot ----------
class RiftlandsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.state = load_state()

    async def setup_hook(self):
        cmds = await self.tree.sync()
        print(f"✅ Synced {len(cmds)} commands globally.")

bot = RiftlandsBot()

def get_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

# ---------- Commands ----------
@bot.tree.command(name="resolve", description="Resolve the current scene instantly using cinematic narration.")
async def resolve_cmd(inter: discord.Interaction):
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel

    narration = fallback_narration(scene.get("title","Scene"), scene.get("prompt",""), actions)
    await log_channel.send(narration)

    g["scenes"].append({
        "title": scene.get("title") or "Scene",
        "summary": narration[:500],
        "actions": actions,
        "resolved_at": dt.datetime.utcnow().isoformat()
    })
    g["current_scene"]["actions"] = []
    save_state(bot.state)

    await inter.response.send_message(f"✅ Scene resolved — narration posted in {log_channel.mention}.", ephemeral=True)

@bot.tree.command(name="act", description="One-message action: description + optional roll.")
@app_commands.describe(description="What your character does (1–3 sentences).")
@app_commands.describe(roll="Roll type (none/check/attack).")
@app_commands.choices(roll=[app_commands.Choice(name=n, value=n) for n in ["none","check","attack"]])
@app_commands.describe(skill="If roll=check: choose a skill")
@app_commands.choices(skill=[app_commands.Choice(name=s.title(), value=s) for s in KNOWN_SKILLS])
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
        "uid": str(inter.user.id), "name": inter.user.display_name,
        "content": description.strip(), "ts": dt.datetime.utcnow().isoformat()
    })
    save_state(bot.state)

    header = f"📝 **{inter.user.display_name}**: _{description.strip()}_"
    if not roll or roll.value == "none":
        await inter.response.send_message(header, ephemeral=False)
        return

    dice_ch = get_channel(inter.guild, "dice-checks")

    if roll.value == "check":
        skl = (skill.value if isinstance(skill, app_commands.Choice) else (skill or "")).lower().strip()
        if not skl:
            await inter.response.send_message("Please choose a skill.", ephemeral=True)
            return
        res = roll_expr("d20+0")  # defaulting to +0 until pinned sheets ready
        details = [f"🎲 **{inter.user.display_name}** — **{skl.title()} Check**",
                   f"d20+0 → {res['breakdown']} = **{res['total']}**"]
        if dice_ch:
            await dice_ch.send("\n".join(details))
        await inter.response.send_message(header + f"\n✅ **{skl.title()} +0** — see **#dice-checks**.", ephemeral=False)
        return

    if roll.value == "attack":
        atk = roll_expr("d20+0")
        dmg = roll_expr("1d6")
        details = [f"⚔️ **{inter.user.display_name}** — Attack",
                   f"Attack: d20+0 → {atk['breakdown']} = **{atk['total']}**",
                   f"Damage: 1d6 → {dmg['breakdown']} = **{dmg['total']}**"]
        if dice_ch:
            await dice_ch.send("\n".join(details))
        await inter.response.send_message(header + "\n⚔️ Attack — result in **#dice-checks**.", ephemeral=False)

@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Riftlands Adventures"))
