#!/usr/bin/env python3
# Riftlands AI DM v1.3.1 — hotfix
# - Respect /narration toggle (skip AI entirely when disabled)
# - 3s hard timeout on AI; instant fallback afterward
# - Posts a temporary "Generating narration…" message and edits it
# - /act adds skill dropdown choices
# - Keeps all v1.3 features (auto-mods, DM reminders, live reindex, GM tools)

import os, re, json, random, asyncio, datetime as dt
from typing import Dict, Any, List, Optional, DefaultDict
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

# Optional OpenAI
USE_OPENAI = False
try:
    from openai import OpenAI
    USE_OPENAI = True
except Exception:
    USE_OPENAI = False

TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_TOKEN".lower())
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

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
            "settings": {"ai_narration": True if OPENAI_KEY else False},
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
            parts.append(f"{label}={rolls}{mod:+}" if mod else f"{label}={rolls} → {subtotal}")
            parts[-1] = f"{label}={rolls}{mod:+}" if mod else f"{label}={rolls}"
            parts[-1] += f" → {subtotal}"
        else:
            try:
                v = int(t); total += v; parts.append(str(v))
            except: parts.append('?' + t)
    return {"total": total, "breakdown": " + ".join(parts)}

# ---------- Sheets ----------
KNOWN_SKILLS = ['acrobatics', 'animal handling', 'arcana', 'athletics', 'deception', 'history', 'insight', 'intimidation', 'investigation', 'medicine', 'nature', 'perception', 'performance', 'persuasion', 'religion', 'sleight of hand', 'stealth', 'survival']
ATTACK_CMD_RE = re.compile(r"/attack\s+([A-Za-z][\w \-']+)\s+([+-]\d+)\s+(\d+d\d+(?:[+-]\d+)?)", re.IGNORECASE)
SKILL_LINE_RE = re.compile(r"(?i)\b(" + "|".join([re.escape(s) for s in KNOWN_SKILLS]) + r")\b\s*:?\s*([+-]\d+)")

async def fetch_pinned_sheet_texts(guild: discord.Guild, user: discord.abc.User) -> List[str]:
    texts = []
    for ch in guild.text_channels:
        name = ch.name.lower()
        if name.endswith("-sheet") or user.name.lower() in name or user.display_name.lower() in name:
            try:
                pins = await ch.pins()
                for msg in pins:
                    if msg.type == discord.MessageType.default and msg.content:
                        texts.append(msg.content)
            except: pass
    return texts

def parse_sheet(texts: List[str]) -> Dict[str, Any]:
    skills, attacks = {}, {}
    blob = "\n".join(texts)
    for m in SKILL_LINE_RE.finditer(blob):
        skill = m.group(1).lower(); mod = m.group(2)
        skills[skill] = mod if mod.startswith(('+','-')) else ('+'+mod)
    for m in ATTACK_CMD_RE.finditer(blob):
        weapon = m.group(1).strip().lower(); to_hit = m.group(2); damage = m.group(3)
        attacks[weapon] = {"to_hit": to_hit, "damage": damage}
    return {"skills": skills, "attacks": attacks}

async def index_user_sheet(guild: discord.Guild, user: discord.abc.User, g: Dict[str, Any]) -> Dict[str, Any]:
    texts = await fetch_pinned_sheet_texts(guild, user)
    parsed = parse_sheet(texts)
    g["sheets_cache"][str(user.id)] = parsed
    save_state(bot.state)
    return parsed

def get_cached_sheet(g: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    return g.get("sheets_cache", {}).get(str(user_id), {"skills": {}, "attacks": {}})

# ---------- Narration ----------
class Narrator:
    def __init__(self, enable_ai: bool, api_key: Optional[str]):
        self.use_ai = enable_ai and bool(api_key)
        self.client = None
        if self.use_ai:
            try:
                self.client = OpenAI(api_key=api_key)
            except Exception:
                self.use_ai = False

    async def ai_narrate(self, scene_title: str, prompt: str, actions: List[Dict[str,str]]) -> Optional[str]:
        if not self.use_ai or not self.client:
            return None
        system_msg = (
            "You are a skilled D&D 5e Game Master running a cinematic Riftlands campaign. "
            "Resolve the scene in 7–10 sentences and end with three bulleted player choices."
        )
        action_lines = "\n".join([f"{a['name']}: {a['content']}" for a in actions]) if actions else "No actions recorded."
        user_msg = f"Scene: {scene_title}\nPrompt: {prompt}\n\nActions:\n{action_lines}"
        try:
            loop = asyncio.get_event_loop()
            def _call():
                return self.client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL","gpt-4o-mini"),
                    messages=[{"role":"system","content":system_msg},{"role":"user","content":user_msg}],
                    temperature=0.9, max_tokens=500)
            resp = await asyncio.wait_for(loop.run_in_executor(None, _call), timeout=3)
            return resp.choices[0].message.content.strip()
        except Exception:
            return None

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

# ---------- GM detection ----------
def is_gm(inter: discord.Interaction) -> bool:
    if not inter.guild: return True
    if inter.guild.owner_id == inter.user.id: return True
    m = inter.guild.get_member(inter.user.id)
    if not m: return False
    if m.guild_permissions.manage_guild: return True
    return any(r.name.lower() == "gm" for r in m.roles)

# ---------- Bot ----------
class RiftlandsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.state = load_state()
        self.narrator = Narrator(True if OPENAI_KEY else False, OPENAI_KEY)

    async def setup_hook(self):
        cmds = await self.tree.sync()
        print(f"✅ Synced {len(cmds)} commands globally.")

bot = RiftlandsBot()

def get_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

async def post_roll_and_ack(inter: discord.Interaction, summary: str, details: List[str]):
    dice_ch = get_channel(inter.guild, "dice-checks")
    await (dice_ch or inter.channel).send("\n".join(details))
    if inter.response.is_done(): await inter.followup.send(summary, ephemeral=False)
    else: await inter.response.send_message(summary, ephemeral=False)

def normalize_mod(mod: Optional[str]) -> str:
    if not mod: return "+0"
    mod = mod.strip()
    if not mod.startswith(("+","-")):
        try:
            n = int(mod); mod = f"+{n}" if n>=0 else str(n)
        except: mod = "+0"
    return mod

# ---------- Commands ----------
@bot.tree.command(name="act", description="One-message action: description + optional roll.")
@app_commands.describe(description="What your character does (1–3 sentences).")
@app_commands.describe(roll="Roll type (none/check/attack).")
@app_commands.choices(roll=[app_commands.Choice(name=n, value=n) for n in ["none","check","attack"]])
@app_commands.describe(skill="If roll=check: choose a skill")
@app_commands.choices(skill=[app_commands.Choice(name=s.title(), value=s) for s in KNOWN_SKILLS])
@app_commands.describe(modifier="If roll=check: e.g., +5 (auto if omitted)")
@app_commands.describe(weapon="If roll=attack: weapon name (auto if omitted)")
@app_commands.describe(to_hit="If roll=attack: e.g., +6 (auto if omitted)")
@app_commands.describe(damage="If roll=attack: e.g., 1d8+3 (auto if omitted)")
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
        "uid": str(inter.user.id), "name": inter.user.display_name, "content": description.strip(),
        "ts": dt.datetime.utcnow().isoformat()
    }); save_state(bot.state)

    header = f"📝 **{inter.user.display_name}**: _{description.strip()}_"
    if not roll or roll.value == "none":
        if inter.response.is_done(): await inter.followup.send(header, ephemeral=False)
        else: await inter.response.send_message(header, ephemeral=False)
        return

    # ensure sheet cache
    sheet = g.get("sheets_cache", {}).get(str(inter.user.id))
    if not sheet:
        sheet = await index_user_sheet(inter.guild, inter.user, g)

    dice_ch = get_channel(inter.guild, "dice-checks")

    if roll.value == "check":
        skl = (skill.value if isinstance(skill, app_commands.Choice) else (skill or "")).lower().strip()
        if not skl:
            await inter.response.send_message("Please pick a skill.", ephemeral=True); return
        auto = sheet.get("skills", {}).get(skl)
        mod = normalize_mod(modifier or auto or "+0")
        res = roll_expr(f"d20{mod}")
        details = [f"🎲 **{inter.user.display_name}** — **{skl.title()} Check**",
                   f"d20{mod} → {res['breakdown']} = **{res['total']}**"]
        if dice_ch: await dice_ch.send("\n".join(details))
        body = header + f"\n✅ **{skl.title()} {mod}** — " + ("see **#dice-checks**." if dice_ch else "\n" + "\n".join(details))
        if (auto is None) and (modifier is None):
            try: await inter.user.send(f"Hi {inter.user.display_name}! I couldn't find **{skl.title()}** on your pinned sheet. Rolled +0 this time.")
            except: pass
        if inter.response.is_done(): await inter.followup.send(body, ephemeral=False)
        else: await inter.response.send_message(body, ephemeral=False)
        return

    if roll.value == "attack":
        w = (weapon or "").strip().lower()
        atk_entry = sheet.get("attacks", {}).get(w) if w else None
        auto_to_hit = (atk_entry or {}).get("to_hit")
        auto_damage = (atk_entry or {}).get("damage")
        to_hit_n = normalize_mod(to_hit or auto_to_hit or "+0")
        dmg_str = (damage or auto_damage or "1d6")
        atk = roll_expr(f"d20{to_hit_n}"); dmg = roll_expr(dmg_str)
        details = [f"⚔️ **{inter.user.display_name}** — **{(weapon or (w or 'Attack')).title()}**",
                   f"Attack: d20{to_hit_n} → {atk['breakdown']} = **{atk['total']}**",
                   f"Damage: {dmg_str} → {dmg['breakdown']} = **{dmg['total']}**"]
        if dice_ch: await dice_ch.send("\n".join(details))
        body = header + f"\n⚔️ **{(weapon or (w or 'Attack')).title()}** — " + ("result in **#dice-checks**." if dice_ch else "\n" + "\n".join(details))
        if (weapon and (auto_to_hit is None or auto_damage is None)) and (to_hit is None or damage is None):
            try: await inter.user.send(f"Hi {inter.user.display_name}! I couldn't find full stats for **{weapon.title()}**; used defaults.")
            except: pass
        if inter.response.is_done(): await inter.followup.send(body, ephemeral=False)
        else: await inter.response.send_message(body, ephemeral=False)
        return

@bot.tree.command(name="resolve", description="Resolve the current scene and post narration.")
async def resolve_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel

    # Post temp message
    temp = await log_channel.send("⏳ **Riftlands AI DM is generating narration…**")

    # Respect toggle
    use_ai = g["settings"].get("ai_narration", False) and bool(OPENAI_KEY)
    narration = None
    if use_ai:
        narration = await bot.narrator.ai_narrate(scene.get("title","Scene"), scene.get("prompt",""), actions)
    if not narration:
        narration = bot.narrator.fallback(scene.get("title","Scene"), scene.get("prompt",""), actions)

    await temp.edit(content=narration)

    # Archive and clear
    g["scenes"].append({
        "title": scene.get("title") or "Scene",
        "summary": narration[:500],
        "actions": actions,
        "resolved_at": dt.datetime.utcnow().isoformat()
    })
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await inter.followup.send(f"✅ Scene resolved — narration posted in {log_channel.mention}.", ephemeral=False)

@bot.tree.command(name="narration", description="GM: enable or disable AI narration for this server.")
async def narration_cmd(inter: discord.Interaction, enable: bool):
    # GM check
    if not (inter.guild and (inter.user.id == inter.guild.owner_id or inter.guild.get_member(inter.user.id).guild_permissions.manage_guild)):
        await inter.response.send_message("Only the GM can change narration mode.", ephemeral=True); return
    g = gstate_for(bot.state, inter.guild.id)
    g["settings"]["ai_narration"] = bool(enable); save_state(bot.state)
    mode = "AI (OpenAI)" if (enable and OPENAI_KEY) else "Cinematic fallback"
    await inter.response.send_message(f"🎛️ Narration mode set to **{mode}**.", ephemeral=False)

@bot.tree.command(name="force-resolve", description="GM: post manual narration to #adventure-log (override).")
async def force_resolve_cmd(inter: discord.Interaction, narration: str):
    if not (inter.guild and (inter.user.id == inter.guild.owner_id or inter.guild.get_member(inter.user.id).guild_permissions.manage_guild)):
        await inter.response.send_message("Only the GM can force-resolve.", ephemeral=True); return
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel
    out = f"🌅 **Scene Resolution — GM Override**\n\n{narration}"
    await log_channel.send(out)
    g["scenes"].append({"title": g.get("current_scene", {}).get("title") or "Scene", "summary": narration[:500], "actions": g.get('current_scene',{}).get('actions',[])})
    g["current_scene"]["actions"] = []; save_state(bot.state)
    await inter.followup.send(f"✅ Manual narration posted in {log_channel.mention}.", ephemeral=False)

@bot.tree.command(name="recap", description="Summarise the last few scenes.")
async def recap_cmd(inter: discord.Interaction):
    g = gstate_for(bot.state, inter.guild.id)
    scenes = g.get("scenes", [])
    if not scenes:
        await inter.response.send_message("No scenes to recap yet.", ephemeral=True); return
    last = scenes[-3:]
    recap = "**Recent Scenes Recap:**\n" + "\n\n".join([f"**{s['title']}**: {s['summary']}" for s in last])
    await inter.response.send_message(recap, ephemeral=False)

@bot.tree.command(name="start", description="Start a new scene (title + prompt).")
async def start_cmd(inter: discord.Interaction, title: str, prompt: str):
    if not (inter.guild and (inter.user.id == inter.guild.owner_id or inter.guild.get_member(inter.user.id).guild_permissions.manage_guild)):
        await inter.response.send_message("Only the GM can start scenes.", ephemeral=True); return
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    g["current_scene"] = {"title": title, "prompt": prompt, "opened_at": dt.datetime.utcnow().isoformat(), "actions": []}
    save_state(bot.state)
    log_channel = get_channel(inter.guild, "adventure-log")
    if log_channel: await log_channel.send(f"## {title}\n{prompt}\n\n*(Post your moves in **#player-actions**.)*")
    await inter.followup.send("Scene started.", ephemeral=True)

@bot.tree.command(name="check", description="Roll a skill/ability check.")
async def check_cmd(inter: discord.Interaction, skill: str, modifier: Optional[str] = "+0"):
    res = roll_expr(f"d20{modifier}"); user = inter.user.display_name
    details = [f"🎲 **{user}** — **{skill.title()} Check**", f"d20{modifier} → {res['breakdown']} = **{res['total']}**"]
    await post_roll_and_ack(inter, f"✅ **{user}** rolled **{skill.title()} {modifier}** — see **#dice-checks**.", details)

@bot.tree.command(name="attack", description="Roll an attack and damage.")
async def attack_cmd(inter: discord.Interaction, weapon: str, to_hit: str, damage: str):
    user = inter.user.display_name; atk = roll_expr(f"d20{to_hit}"); dmg = roll_expr(damage)
    details = [f"⚔️ **{user}** — **{weapon.title()}**", f"Attack: d20{to_hit} → {atk['breakdown']} = **{atk['total']}**", f"Damage: {damage} → {dmg['breakdown']} = **{dmg['total']}**"]
    await post_roll_and_ack(inter, f"⚔️ **{user}** attacked with **{weapon.title()}** — result in **#dice-checks**.", details)

# Events
@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="Riftlands Adventures"))

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild: return
    if message.channel.name == "player-actions" and message.content.strip():
        g = gstate_for(bot.state, message.guild.id)
        g["current_scene"].setdefault("actions", []).append({"uid": str(message.author.id), "name": message.author.display_name, "content": message.content.strip(), "ts": dt.datetime.utcnow().isoformat()})
        save_state(bot.state)
        try: await message.add_reaction("✅")
        except: pass
    await bot.process_commands(message)

@bot.event
async def on_guild_channel_pins_update(channel: discord.abc.GuildChannel, last_pin: Optional[dt.datetime]):
    try:
        if isinstance(channel, discord.TextChannel) and channel.name.endswith("-sheet"):
            guild = channel.guild; pins = await channel.pins()
            if pins:
                user = pins[0].author; g = gstate_for(bot.state, guild.id)
                await index_user_sheet(guild, user, g)
                print(f"🔄 Reindexed sheet for {user.display_name} from #{channel.name}")
    except Exception as e:
        print("Pins update error:", e)

def main():
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set."); return
    try:
        bot.run(TOKEN)
    except Exception as e:
        print("Bot run error:", e)

if __name__ == "__main__":
    main()
