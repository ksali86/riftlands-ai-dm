#!/usr/bin/env python3
# Riftlands AI DM v1.3 — full build
# Features:
# - /act: description + optional roll (auto-modifiers from pinned sheets, DM reminders)
# - /check, /attack: classic rolls with visible acks
# - /resolve: AI narration (if enabled + key) with 15s timeout; cinematic fallback + hooks
# - /narration: GM toggle AI narration on/off (per guild, persisted)
# - /force-resolve: GM-only manual narration to #adventure-log
# - /scene-status: GM-only dashboard DM
# - Live sheet reindexing on pins update; on-demand parsing from pinned messages
# - Logs plain text actions in #player-actions, reacts ✅
# - State stored in riftlands_state.json

import os, re, json, random, asyncio, datetime as dt
from typing import Dict, Any, List, Optional, Tuple, DefaultDict
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

# --- Optional OpenAI support
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
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
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
            "players": {},
            "inventory": {},
            "scenes": [],
            "current_scene": {"title": "", "prompt": "", "opened_at": "", "actions": []},
            "settings": {
                "ai_narration": True if OPENAI_KEY else False
            },
            "sheets_cache": {}  # user_id -> {"skills": {skill: +n}, "attacks": {weapon: {"to_hit":"+n","damage":"XdY+Z"}}}
        }
    return state[gid]

# ---------- Dice helpers ----------
DICE_RE = re.compile(r"(?:(\d+))?d(\d+)([+-]\d+)?$", re.IGNORECASE)

def roll_expr(expr: str) -> Dict[str, Any]:
    expr = expr.strip().replace(" ", "")
    if not expr:
        return {"total": 0, "breakdown": "0"}
    total = 0
    parts = []
    # Tokenize to support things like 1d8+3+1
    token = '+' + expr
    tokens = re.split(r"(?=\+)", token)
    for t in tokens:
        if not t:
            continue
        t = t.lstrip('+')
        m = DICE_RE.fullmatch(t)
        if m:
            count = int(m.group(1) or '1')
            sides = int(m.group(2))
            mod = int(m.group(3) or '0')
            rolls = [random.randint(1, sides) for _ in range(count)]
            subtotal = sum(rolls) + mod
            total += subtotal
            label = f"{count}d{sides}" if count != 1 else f"d{sides}"
            modtxt = f"{mod:+}" if mod else ""
            parts.append(f"{label}={rolls}{modtxt} → {subtotal}")
        else:
            try:
                val = int(t)
                total += val
                parts.append(str(val))
            except:
                parts.append(f"?{t}")
    breakdown = " + ".join(parts) if parts else str(total)
    return {"total": total, "breakdown": breakdown}

# ---------- Sheet parsing ----------
KNOWN_SKILLS = {
    "acrobatics","animal handling","arcana","athletics","deception","history","insight",
    "intimidation","investigation","medicine","nature","perception","performance","persuasion",
    "religion","sleight of hand","stealth","survival"
}
# weapon line regex like: /attack longbow +5 1d8+3 OR "Longbow → /attack longbow +5 1d8+3"
ATTACK_CMD_RE = re.compile(r"/attack\s+([A-Za-z][\w \-']+)\s+([+-]\d+)\s+(\d+d\d+(?:[+-]\d+)?)", re.IGNORECASE)
SKILL_LINE_RE = re.compile(r"(?i)\b(" + "|".join([re.escape(s) for s in KNOWN_SKILLS]) + r")\b\s*[:]?[\s]*([+-]\d+)")

async def fetch_pinned_sheet_texts(guild: discord.Guild, user: discord.abc.User) -> List[str]:
    """Look for a private sheet channel for this user (name contains their username or endswith -sheet). Return pinned texts."""
    texts = []
    try:
        for ch in guild.text_channels:
            name = ch.name.lower()
            if name.endswith("-sheet") or user.name.lower() in name or (user.display_name.lower() in name):
                try:
                    if ch.permissions_for(guild.me or guild.get_member(guild.owner_id)).read_message_history:
                        pins = await ch.pins()
                        for msg in pins:
                            if msg.type == discord.MessageType.default and msg.content:
                                texts.append(msg.content)
                except Exception:
                    continue
    except Exception:
        pass
    return texts

def parse_sheet_from_texts(texts: List[str]) -> Dict[str, Any]:
    skills: Dict[str, str] = {}
    attacks: Dict[str, Dict[str,str]] = {}
    blob = "\n".join(texts)
    # skills
    for m in SKILL_LINE_RE.finditer(blob):
        skill = m.group(1).lower()
        mod = m.group(2)
        skills[skill] = mod if mod.startswith(('+','-')) else ('+'+mod)
    # attacks
    for m in ATTACK_CMD_RE.finditer(blob):
        weapon = m.group(1).strip().lower()
        to_hit = m.group(2)
        damage = m.group(3)
        attacks[weapon] = {"to_hit": to_hit, "damage": damage}
    return {"skills": skills, "attacks": attacks}

async def index_user_sheet(guild: discord.Guild, user: discord.abc.User, g: Dict[str, Any]) -> Dict[str, Any]:
    texts = await fetch_pinned_sheet_texts(guild, user)
    parsed = parse_sheet_from_texts(texts)
    g["sheets_cache"][str(user.id)] = parsed
    save_state(bot.state)
    return parsed

def get_cached_sheet(g: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    return g.get("sheets_cache", {}).get(str(user_id), {"skills":{}, "attacks":{}})

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

    async def ai_narrate(self, scene_title: str, prompt: str, actions: List[Dict[str, str]]) -> Optional[str]:
        if not self.use_ai or not self.client:
            return None
        system_msg = (
            "You are a skilled D&D 5e Game Master running a cinematic Riftlands campaign. "
            "Resolve the scene in 7–10 sentences, honoring player intent and hinting at consequences. "
            "End with three bulleted player choices."
        )
        action_lines = "\n".join([f"{a['name']}: {a['content']}" for a in actions]) if actions else "No recorded actions."
        user_msg = f"Scene: {scene_title}\nPrompt: {prompt}\n\nActions:\n{action_lines}\n"
        try:
            # Timeout protection via asyncio.wait_for around the API call
            async def _call():
                return self.client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL","gpt-4o-mini"),
                    messages=[{"role":"system","content":system_msg},
                              {"role":"user","content":user_msg}],
                    temperature=0.9, max_tokens=500
                )
            loop = asyncio.get_event_loop()
            resp = await asyncio.wait_for(loop.run_in_executor(None, _call), timeout=15)
            return resp.choices[0].message.content.strip()
        except Exception:
            return None

    def fallback_narrate(self, scene_title: str, prompt: str, actions: List[Dict[str,str]]) -> str:
        # Group last up to 3 actions per player
        by_player: DefaultDict[str, List[str]] = defaultdict(list)
        for a in actions[-20:]:  # cap to recent
            by_player[a.get("name","Someone")].append(a.get("content","..."))
        for k in by_player:
            by_player[k] = by_player[k][-3:]
        lines = [f"🌫️ **{scene_title or 'Scene'} — Resolution**\n"]
        # Cinematic construction
        for name, acts in by_player.items():
            snippet = acts[-1] if acts else "moves with purpose."
            lines.append(f"**{name}** {snippet.rstrip('.')}.")
        if not by_player:
            lines.append("The winds hiss through the ruins; for a breath, nothing moves.")
        lines.append("\nThe Riftstorm gnashes above; stone groans and ghostlight scatters across the ground.")
        # Hooks
        hooks = [
            "Press the advantage and **pursue** the threat.",
            "**Regroup** and protect the vulnerable.",
            "**Investigate** the mystery before it slips away."
        ]
        lines.append("\n**Choices:**\n" + "\n".join([f"1. {hooks[0]}\n2. {hooks[1]}\n3. {hooks[2]}"]))
        return "\n".join(lines)

# ---------- Permissions ----------
def is_gm(inter: discord.Interaction) -> bool:
    # Allow guild owner or users with Manage Guild permission or role named 'GM'
    if not inter.guild: return True
    user = inter.user
    if inter.guild.owner_id == user.id:
        return True
    member = inter.guild.get_member(user.id)
    if member is None: return False
    if member.guild_permissions.manage_guild:
        return True
    for role in member.roles:
        if role.name.lower() == "gm":
            return True
    return False

# ---------- Bot ----------
class RiftlandsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.state = load_state()
        gkeys = list(self.state.keys())
        self.narrator = Narrator(USE_OPENAI, OPENAI_KEY)

    async def setup_hook(self):
        cmds = await self.tree.sync()
        print(f"✅ Synced {len(cmds)} commands globally.")

bot = RiftlandsBot()

def get_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=name)

async def post_roll_and_ack(inter: discord.Interaction, summary_line: str, detail_lines: List[str]):
    dice_ch = get_channel(inter.guild, "dice-checks")
    target_ch = dice_ch or inter.channel
    await target_ch.send("\n".join(detail_lines))
    if inter.response.is_done():
        await inter.followup.send(summary_line, ephemeral=False)
    else:
        await inter.response.send_message(summary_line, ephemeral=False)

# ---------- Commands ----------
@bot.tree.command(name="check", description="Roll a skill/ability check, e.g., /check stealth +5")
async def check_cmd(inter: discord.Interaction, skill: str, modifier: Optional[str] = "+0"):
    res = roll_expr(f"d20{modifier}")
    user = inter.user.display_name
    details = [f"🎲 **{user}** — **{skill.title()} Check**", f"d20{modifier} → {res['breakdown']} = **{res['total']}**"]
    summary = f"✅ **{user}** rolled **{skill.title()} {modifier}** — see **#dice-checks**."
    await post_roll_and_ack(inter, summary, details)

@bot.tree.command(name="attack", description="Roll an attack and damage, e.g., /attack longsword +6 1d8+3")
async def attack_cmd(inter: discord.Interaction, weapon: str, to_hit: str, damage: str):
    user = inter.user.display_name
    atk = roll_expr(f"d20{to_hit}")
    dmg = roll_expr(damage)
    details = [
        f"⚔️ **{user}** — **{weapon.title()}**",
        f"Attack: d20{to_hit} → {atk['breakdown']} = **{atk['total']}**",
        f"Damage: {damage} → {dmg['breakdown']} = **{dmg['total']}**"
    ]
    summary = f"⚔️ **{user}** attacked with **{weapon.title()}** — result in **#dice-checks**."
    await post_roll_and_ack(inter, summary, details)

class RollType(app_commands.Transform):
    pass  # placeholder to keep signature readable

ROLL_CHOICES = [
    app_commands.Choice(name="none", value="none"),
    app_commands.Choice(name="check", value="check"),
    app_commands.Choice(name="attack", value="attack"),
]

def normalize_mod(mod: Optional[str]) -> str:
    if not mod: return "+0"
    mod = mod.strip()
    if not mod.startswith(("+","-")):
        try:
            n = int(mod)
            mod = f"+{n}" if n>=0 else str(n)
        except:
            mod = "+0"
    return mod

@bot.tree.command(name="act", description="One-message action: include description + optional roll.")
@app_commands.describe(description="What your character does (1–3 sentences).")
@app_commands.describe(roll="Roll type (none/check/attack).")
@app_commands.choices(roll=ROLL_CHOICES)
@app_commands.describe(skill="If roll = check: skill name, e.g., stealth")
@app_commands.describe(modifier="If roll = check: e.g., +5 (auto if omitted)")
@app_commands.describe(weapon="If roll = attack: weapon name (auto if omitted)")
@app_commands.describe(to_hit="If roll = attack: e.g., +6 (auto if omitted)")
@app_commands.describe(damage="If roll = attack: e.g., 1d8+3 (auto if omitted)")
async def act_cmd(
    inter: discord.Interaction,
    description: str,
    roll: app_commands.Choice[str] = None,
    skill: Optional[str] = None,
    modifier: Optional[str] = None,
    weapon: Optional[str] = None,
    to_hit: Optional[str] = None,
    damage: Optional[str] = None,
):
    # Log the action text to current scene
    g = gstate_for(bot.state, inter.guild.id)
    g["current_scene"].setdefault("actions", []).append({
        "uid": str(inter.user.id),
        "name": inter.user.display_name,
        "content": description.strip(),
        "ts": dt.datetime.utcnow().isoformat()
    })
    save_state(bot.state)

    header = f"📝 **{inter.user.display_name}**: _{description.strip()}_"
    if not roll or roll.value == "none":
        if inter.response.is_done(): await inter.followup.send(header, ephemeral=False)
        else: await inter.response.send_message(header, ephemeral=False)
        return

    # Ensure cache
    sheet = get_cached_sheet(g, inter.user.id)
    if not sheet["skills"] and not sheet["attacks"]:
        # try index now
        sheet = await index_user_sheet(inter.guild, inter.user, g)

    dice_ch = get_channel(inter.guild, "dice-checks")

    if roll.value == "check":
        if not skill:
            await inter.response.send_message("Please provide a skill name (e.g., stealth).", ephemeral=True); return
        skl = skill.lower().strip()
        auto_mod = sheet.get("skills", {}).get(skl)
        mod = normalize_mod(modifier or auto_mod or "+0")
        res = roll_expr(f"d20{mod}")
        details = [f"🎲 **{inter.user.display_name}** — **{skl.title()} Check**",
                   f"d20{mod} → {res['breakdown']} = **{res['total']}**"]
        if dice_ch: await dice_ch.send("\n".join(details))
        body = header + f"\n✅ **{skl.title()} {mod}** — " + ("see **#dice-checks**." if dice_ch else "\n" + "\n".join(details))
        if (auto_mod is None) and (modifier is None):
            try: await inter.user.send(f"Hi {inter.user.display_name}! I couldn't find **{skl.title()}** on your pinned sheet. I rolled +0 this time. Update your sheet to get accurate rolls.")
            except Exception: pass
        if inter.response.is_done(): await inter.followup.send(body, ephemeral=False)
        else: await inter.response.send_message(body, ephemeral=False)
        return

    if roll.value == "attack":
        w = (weapon or "").strip().lower()
        atk_entry = sheet.get("attacks", {}).get(w) if w else None
        auto_to_hit = atk_entry["to_hit"] if atk_entry else None
        auto_damage = atk_entry["damage"] if atk_entry else None
        to_hit_n = normalize_mod(to_hit or auto_to_hit or "+0")
        dmg_str = (damage or auto_damage or "1d6")  # default simple
        atk = roll_expr(f"d20{to_hit_n}")
        dmg = roll_expr(dmg_str)
        details = [f"⚔️ **{inter.user.display_name}** — **{(weapon or (w or 'attack')).title()}**",
                   f"Attack: d20{to_hit_n} → {atk['breakdown']} = **{atk['total']}**",
                   f"Damage: {dmg_str} → {dmg['breakdown']} = **{dmg['total']}**"]
        if dice_ch: await dice_ch.send("\n".join(details))
        body = header + f"\n⚔️ **{(weapon or (w or 'Attack')).title()}** — " + ("result in **#dice-checks**." if dice_ch else "\n" + "\n".join(details))
        if (weapon and (auto_to_hit is None or auto_damage is None)) and (to_hit is None or damage is None):
            try: await inter.user.send(f"Hi {inter.user.display_name}! I couldn't find complete stats for **{weapon.title()}** on your pinned sheet. I rolled with defaults. Update your sheet for accurate rolls.")
            except Exception: pass
        if inter.response.is_done(): await inter.followup.send(body, ephemeral=False)
        else: await inter.response.send_message(body, ephemeral=False)
        return

@bot.tree.command(name="start", description="Start a new scene (title + prompt).")
async def start_cmd(inter: discord.Interaction, title: str, prompt: str):
    if not is_gm(inter):
        await inter.response.send_message("Only the GM can start scenes.", ephemeral=True); return
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    g["current_scene"] = {"title": title, "prompt": prompt, "opened_at": dt.datetime.utcnow().isoformat(), "actions": []}
    save_state(bot.state)
    log_channel = get_channel(inter.guild, "adventure-log")
    if log_channel:
        await log_channel.send(f"## {title}\n{prompt}\n\n*(Post your moves in **#player-actions**.)*")
    await inter.followup.send("Scene started.", ephemeral=True)

@bot.tree.command(name="resolve", description="Resolve the current scene and post narration.")
async def resolve_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    # Decide narration mode
    use_ai = g["settings"].get("ai_narration", False) and bool(OPENAI_KEY)
    narrator = bot.narrator
    narration = None
    if use_ai:
        narration = await narrator.ai_narrate(scene.get("title","Scene"), scene.get("prompt",""), actions)
    if not narration:
        narration = narrator.fallback_narrate(scene.get("title","Scene"), scene.get("prompt",""), actions)
    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel
    await log_channel.send(narration)
    # Archive
    g["scenes"].append({
        "title": scene.get("title") or "Scene",
        "summary": narration[:500],
        "actions": actions,
        "resolved_at": dt.datetime.utcnow().isoformat()
    })
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await inter.followup.send(f"✅ Scene resolved — narration posted in {log_channel.mention}.", ephemeral=False)

@bot.tree.command(name="recap", description="Summarise the last few scenes.")
async def recap_cmd(inter: discord.Interaction):
    g = gstate_for(bot.state, inter.guild.id)
    scenes = g.get("scenes", [])
    if not scenes:
        await inter.response.send_message("No scenes to recap yet.", ephemeral=True); return
    last = scenes[-3:]
    recap_text = "**Recent Scenes Recap:**\n" + "\n\n".join([f"**{s['title']}**: {s['summary']}" for s in last])
    await inter.response.send_message(recap_text, ephemeral=False)

@bot.tree.command(name="narration", description="GM: enable or disable AI narration for this server.")
async def narration_cmd(inter: discord.Interaction, enable: bool):
    if not is_gm(inter):
        await inter.response.send_message("Only the GM can change narration mode.", ephemeral=True); return
    g = gstate_for(bot.state, inter.guild.id)
    g["settings"]["ai_narration"] = bool(enable)
    save_state(bot.state)
    mode = "AI (OpenAI)" if (enable and OPENAI_KEY) else "Cinematic fallback"
    await inter.response.send_message(f"🎛️ Narration mode set to **{mode}**.", ephemeral=False)

@bot.tree.command(name="force-resolve", description="GM: post manual narration to #adventure-log (override).")
async def force_resolve_cmd(inter: discord.Interaction, narration: str):
    if not is_gm(inter):
        await inter.response.send_message("Only the GM can force-resolve.", ephemeral=True); return
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel
    out = f"🌅 **Scene Resolution — GM Override**\n\n{narration}"
    await log_channel.send(out)
    # Archive minimal
    g["scenes"].append({
        "title": scene.get("title") or "Scene",
        "summary": narration[:500],
        "actions": scene.get("actions", []),
        "resolved_at": dt.datetime.utcnow().isoformat()
    })
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await inter.followup.send(f"✅ Manual narration posted in {log_channel.mention}.", ephemeral=False)

@bot.tree.command(name="scene-status", description="GM: DM a status snapshot of the current scene.")
async def scene_status_cmd(inter: discord.Interaction):
    if not is_gm(inter):
        await inter.response.send_message("Only the GM can view scene status.", ephemeral=True); return
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    title = scene.get("title") or "Untitled Scene"
    prompt = scene.get("prompt") or "(No prompt set)"
    mode = "AI (OpenAI)" if (g["settings"].get("ai_narration") and OPENAI_KEY) else "Cinematic fallback"
    acts = scene.get("actions", [])[-8:]
    lines = [f"**Scene Status — {title}** 🌫️",
             f"**Prompt:** {prompt}",
             f"**Narration Mode:** {mode}",
             "", "**Recent Actions:**" if acts else "**No actions recorded yet.**"]
    for a in acts:
        lines.append(f"• **{a['name']}** — \"{a['content']}\"")
    try:
        await inter.user.send("\n".join(lines))
        await inter.response.send_message("📬 Sent you a DM with scene status.", ephemeral=True)
    except Exception:
        await inter.response.send_message("\n".join(lines), ephemeral=True)

# ---------- Events ----------
@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user} (ID: {bot.user.id})")
    # Ensure presence
    await bot.change_presence(activity=discord.Game(name="Riftlands Adventures"))

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    # Log actions in player-actions text channel
    if message.channel.name == "player-actions" and message.content.strip():
        g = gstate_for(bot.state, message.guild.id)
        g["current_scene"].setdefault("actions", []).append({
            "uid": str(message.author.id),
            "name": message.author.display_name,
            "content": message.content.strip(),
            "ts": dt.datetime.utcnow().isoformat()
        })
        save_state(bot.state)
        try:
            await message.add_reaction("✅")
        except Exception:
            pass
    await bot.process_commands(message)

@bot.event
async def on_guild_channel_pins_update(channel: discord.abc.GuildChannel, last_pin: Optional[dt.datetime]):
    # Reindex any -sheet channel pins
    try:
        if isinstance(channel, discord.TextChannel) and channel.name.endswith("-sheet"):
            guild = channel.guild
            pins = await channel.pins()
            # Try to infer user by first pin author
            if pins:
                user = pins[0].author
                g = gstate_for(bot.state, guild.id)
                await index_user_sheet(guild, user, g)
                print(f"🔄 Reindexed sheet for {user.display_name} from #{channel.name}")
    except Exception as e:
        print("Pins update error:", e)

def main():
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set.")
        return
    try:
        bot.run(TOKEN)
    except Exception as e:
        print("Bot run error:", e)

if __name__ == "__main__":
    main()
