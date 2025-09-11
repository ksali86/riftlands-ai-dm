# Riftlands AI DM — v1.6

AI-powered Dungeon Master bot for Discord, built for Riftlands remote play.

## Features
- `/act` — Describe an action; optional skill check
- `/attack` — Quick attack roll + damage
- `/resolve` — Advance story + narration
- `/resolve-test` — Simulate narration without posting
- `/debug-scene` — Show current scene JSON + info
- `/recap` — Summarise session state + last 3–5 actions
- `/ping` — Check bot health and latency
- `!ping` — **Message fallback** if slash commands aren’t synced

## Setup

### 1. Environment Variables
```
DISCORD_TOKEN=your_bot_token
RIFTLANDS_GUILD_ID=1414706808802644131   # optional, prefer guild sync
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run Bot
```bash
python main.py
```

### 4. Deploy on Railway
- Add `DISCORD_TOKEN` (required) + `RIFTLANDS_GUILD_ID` (optional) to Railway environment variables.
- Deploy `riftlands_ai_dm_v1_6.zip`.
- Watch logs for confirmation:
```
🔄 Synced 6 commands to Riftland Adventures
```
