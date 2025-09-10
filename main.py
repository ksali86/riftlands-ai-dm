#!/usr/bin/env python3
# Riftlands AI DM v1.2

import os, re, json, random, datetime as dt
from typing import Dict, Any, List, Optional
import discord
from discord import app_commands
from discord.ext import commands

try:
    from openai import OpenAI
    USE_OPENAI = True
except ImportError:
    USE_OPENAI = False

TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

STATE_FILE = "riftlands_state.json"

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

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
            "current_scene": {"title": "", "prompt": "", "opened_at": "", "actions": []}
        }
    return state[gid]

DICE_RE = re.compile(r"(?:(\d+))?d(\d+)([+-]\d+)?$", re.IGNORECASE)

def roll_expr(expr: str) -> Dict[str, Any]:
    expr = expr.replace(" ", "")
    total, parts_out = 0, []
    tokens = re.split(r"(?=\+)", '+' + expr)
    for tok in tokens:
        if not tok: continue
        tok = tok.lstrip('+')
        m = DICE_RE.fullmatch(tok)
        if m:
            count = int(m.group(1) or "1")
            sides = int(m.group(2))
            mod = int(m.group(3) or "0")
            rolls = [random.randint(1, sides) for _ in range(count)]
            subtotal = sum(rolls) + mod
            total += subtotal
            label = f"{count}d{sides}" if count != 1 else f"d{sides}"
            modtxt = f"{mod:+}" if mod else ""
            parts_out.append(f"{label}={rolls}{modtxt} → {subtotal}")
        else:
            try:
                val = int(tok)
                total += val
                parts_out.append(str(val))
            except:
                parts_out.append(f"?{tok}")
    breakdown = " + ".join(parts_out) if parts_out else str(total)
    return {"total": total, "breakdown": breakdown}

class Narrator:
    def __init__(self, enable_ai: bool, api_key: Optional[str]):
        self.use_ai = enable_ai and bool(api_key)
        self.client = None
        if self.use_ai:
            try:
                self.client = OpenAI(api_key=api_key)
            except Exception:
                self.use_ai = False

    async def narrate(self, scene_title: str, prompt: str, actions: List[Dict[str, str]]) -> str:
        if not self.use_ai:
            lines = [f"🌫️ **{scene_title or 'Scene'} — Resolution**"]
            if actions:
                lines.append("")
                for a in actions:
                    lines.append(f"- **{a.get('name')}**: _{a.get('content')}_")
            lines.append("\n*(AI offline — basic narration)*")
            return "\n".join(lines)
        return "🌫️ AI narration available when OpenAI key set."

class RiftlandsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.state = load_state()
        self.narrator = Narrator(USE_OPENAI, OPENAI_KEY)

    async def setup_hook(self):
        synced = await self.tree.sync()
        print(f"✅ Synced {len(synced)} commands globally!")

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

@bot.tree.command(name="check", description="Roll a skill/ability check")
async def check_cmd(inter: discord.Interaction, skill: str, modifier: Optional[str] = "+0"):
    res = roll_expr(f"d20{modifier}")
    user = inter.user.display_name
    details = [
        f"🎲 **{user}** — **{skill.title()} Check**",
        f"d20{modifier} → {res['breakdown']} = **{res['total']}**"
    ]
    summary = f"✅ **{user}** rolled **{skill.title()} {modifier}** — see #dice-checks."
    await post_roll_and_ack(inter, summary, details)

@bot.tree.command(name="attack", description="Roll an attack and damage")
async def attack_cmd(inter: discord.Interaction, weapon: str, to_hit: str, damage: str):
    user = inter.user.display_name
    atk = roll_expr(f"d20{to_hit}")
    dmg = roll_expr(damage)
    details = [
        f"⚔️ **{user}** — **{weapon.title()}**",
        f"Attack: d20{to_hit} → {atk['breakdown']} = **{atk['total']}**",
        f"Damage: {damage} → {dmg['breakdown']} = **{dmg['total']}**"
    ]
    summary = f"⚔️ **{user}** attacked with **{weapon.title()}** — see #dice-checks."
    await post_roll_and_ack(inter, summary, details)

@bot.tree.command(name="resolve", description="Resolve the current scene")
async def resolve_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    g = gstate_for(bot.state, inter.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    narration = await bot.narrator.narrate(scene.get("title", ""), scene.get("prompt", ""), actions)
    log_channel = get_channel(inter.guild, "adventure-log") or inter.channel
    await log_channel.send(narration)
    g["scenes"].append({"title": scene.get("title"), "summary": narration[:500], "actions": actions})
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await inter.followup.send(f"✅ Scene resolved — narration posted in {log_channel.mention}.", ephemeral=False)

@bot.tree.command(name="recap", description="Summarise the last few scenes")
async def recap_cmd(inter: discord.Interaction):
    g = gstate_for(bot.state, inter.guild.id)
    scenes = g.get("scenes", [])
    if not scenes:
        await inter.response.send_message("No scenes to recap yet.", ephemeral=True)
        return
    last_scenes = scenes[-3:]
    recap_text = "**Recent Scenes Recap:**\n" + "\n\n".join([f"**{s['title']}**: {s['summary']}" for s in last_scenes])
    await inter.response.send_message(recap_text, ephemeral=False)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
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
        except:
            pass
    await bot.process_commands(message)

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set.")
    else:
        bot.run(TOKEN)
