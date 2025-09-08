#!/usr/bin/env python3
"""
Riftlands AI DM — Discord Bot (5e, Async, Transparent Rolls + Narrative)
Author: Khurem's Custom Setup

Features:
- Async D&D 5e gameplay via Discord
- Scene-based narration with cinematic AI outcomes (optional, OpenAI-driven)
- Handles /check, /attack, /save, /inventory, /recap, and /start commands
- Transparent dice rolls + automatic narrative updates
- Optimised for GitHub + Railway deployment

Requirements:
- Python 3.9+
- discord.py
- openai (optional)
- python-dotenv

Start command:
    python3 main.py
"""

import os, re, json, random, datetime as dt
from typing import Dict, Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

# Optional OpenAI
try:
    from openai import OpenAI
    USE_OPENAI = True
except ImportError:
    USE_OPENAI = False

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

STATE_FILE = "riftlands_state.json"

# ---------------- State Management ----------------
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
            "current_scene": {
                "title": "",
                "prompt": "",
                "opened_at": "",
                "actions": []
            }
        }
    return state[gid]

# ---------------- Dice Utilities ----------------
DICE_RE = re.compile(r"(?:(\d+))?d(\d+)([+-]\d+)?", re.IGNORECASE)

def roll_expr(expr: str) -> Dict[str, Any]:
    parts = [p.strip() for p in re.split(r"(\+)", expr) if p.strip()]
    total = 0
    out_parts = []
    i = 0
    while i < len(parts):
        token = parts[i]
        if token == "+":
            i += 1
            continue
        m = DICE_RE.fullmatch(token)
        if m:
            count = int(m.group(1) or "1")
            sides = int(m.group(2))
            mod = int(m.group(3) or "0")
            rolls = [random.randint(1, sides) for _ in range(count)]
            subtotal = sum(rolls) + mod
            total += subtotal
            label = f"{count}d{sides}" if count != 1 else f"d{sides}"
            modtxt = f"{mod:+}" if mod else ""
            out_parts.append(f"{label}={rolls}{modtxt} → {subtotal}")
        else:
            try:
                val = int(token)
                total += val
                out_parts.append(str(val))
            except:
                out_parts.append(f"?{token}")
        i += 1
    breakdown = " + ".join(out_parts)
    return {"total": total, "breakdown": breakdown}

# ---------------- Narration ----------------
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
            lines = [f"**{scene_title or 'Scene'} — Resolution**"]
            for a in actions:
                lines.append(f"- **{a.get('name')}**: _{a.get('content')}_")
            lines.append("\n*(AI offline — basic narration)*")
            return "\n".join(lines)

        system_msg = (
            "You are a skilled D&D 5e Game Master running a Riftlands campaign. "
            "Resolve the scene using cinematic narrative. Keep it under 10 sentences."
        )
        user_msg = (
            f"Scene: {scene_title}\nPrompt: {prompt}\n\n"
            "Players' actions:\n" +
            "\n".join([f"{a['name']}: {a['content']}" for a in actions]) +
            "\n\nProvide consequences and end with 2-3 choices."
        )

        try:
            resp = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg}
                ],
                temperature=0.9,
                max_tokens=400
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            lines = [f"**{scene_title or 'Scene'} — Resolution (fallback)**"]
            for a in actions:
                lines.append(f"- **{a.get('name')}**: _{a.get('content')}_")
            return "\n".join(lines)

# ---------------- Bot Setup ----------------
class RiftlandsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        # self.tree = app_commands.CommandTree(self)
        self.state = load_state()
        self.narrator = Narrator(USE_OPENAI, OPENAI_KEY)

    async def setup_hook(self):
        await self.tree.sync()

bot = RiftlandsBot()

# ---------------- Commands ----------------
@bot.tree.command(name="start", description="Start a new scene.")
async def start(interaction: discord.Interaction, title: str, prompt: str):
    await interaction.response.defer(ephemeral=True)
    g = gstate_for(bot.state, interaction.guild.id)
    g["current_scene"] = {
        "title": title,
        "prompt": prompt,
        "opened_at": dt.datetime.utcnow().isoformat(),
        "actions": []
    }
    save_state(bot.state)
    log_channel = discord.utils.get(interaction.guild.text_channels, name="adventure-log")
    if log_channel:
        await log_channel.send(f"## {title}\n{prompt}\n\n*Post your moves in* #player-actions")
    await interaction.followup.send("Scene started!", ephemeral=True)

@bot.tree.command(name="check", description="Make a skill or ability check.")
async def check(interaction: discord.Interaction, skill: str, modifier: Optional[str] = "+0"):
    res = roll_expr(f"d20{modifier}")
    msg = f"🎲 **{interaction.user.display_name}** — **{skill.title()} Check**: {res['breakdown']} = **{res['total']}**"
    dice_channel = discord.utils.get(interaction.guild.text_channels, name="dice-checks")
    if dice_channel:
        await dice_channel.send(msg)
    else:
        await interaction.response.send_message(msg)

@bot.tree.command(name="attack", description="Roll attack + damage.")
async def attack(interaction: discord.Interaction, weapon: str, to_hit: str, dmg: str):
    atk = roll_expr(f"d20{to_hit}")
    dmg_roll = roll_expr(dmg)
    msg = (
        f"⚔️ **{interaction.user.display_name}** — **{weapon.title()}**\n"
        f"Attack: {atk['breakdown']} = **{atk['total']}**\n"
        f"Damage: {dmg} → {dmg_roll['breakdown']} = **{dmg_roll['total']}**"
    )
    dice_channel = discord.utils.get(interaction.guild.text_channels, name="dice-checks")
    if dice_channel:
        await dice_channel.send(msg)
    else:
        await interaction.response.send_message(msg)

@bot.tree.command(name="resolve", description="Resolve the current scene.")
async def resolve(interaction: discord.Interaction):
    g = gstate_for(bot.state, interaction.guild.id)
    scene = g.get("current_scene", {})
    actions = scene.get("actions", [])
    narration = await bot.narrator.narrate(scene.get("title", ""), scene.get("prompt", ""), actions)
    log_channel = discord.utils.get(interaction.guild.text_channels, name="adventure-log")
    if log_channel:
        await log_channel.send(narration)
    g["scenes"].append({
        "title": scene.get("title", "Scene"),
        "summary": narration[:500],
        "actions": actions,
        "resolved_at": dt.datetime.utcnow().isoformat()
    })
    g["current_scene"]["actions"] = []
    save_state(bot.state)
    await interaction.response.send_message("Scene resolved!", ephemeral=True)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.name == "player-actions":
        g = gstate_for(bot.state, message.guild.id)
        g["current_scene"].setdefault("actions", []).append({
            "uid": str(message.author.id),
            "name": message.author.display_name,
            "content": message.content,
            "ts": dt.datetime.utcnow().isoformat()
        })
        save_state(bot.state)
        await message.add_reaction("✅")
    await bot.process_commands(message)

def main():
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set.")
        return
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
