# Riftlands AI DM Bot

This is your **Discord AI Dungeon Master bot** for the Riftlands campaign.

## Features
- Async-friendly D&D 5e bot
- Transparent dice rolls
- Cinematic AI narration (optional, via OpenAI)
- Scene-based gameplay

## Hosting Guide (GitHub + Railway)

### 1. Upload to GitHub
1. Go to https://github.com → New Repository → name it `riftlands-ai-dm`
2. Upload `main.py`, `requirements.txt`, and `README.md`
3. Commit changes.

### 2. Deploy on Railway
1. Go to https://railway.app → New Project → Deploy from GitHub
2. Select `riftlands-ai-dm` repo.
3. Set Environment Variables:
   - `DISCORD_BOT_TOKEN` = your bot token
   - (optional) `OPENAI_API_KEY` = OpenAI key
4. Set start command:
   ```bash
   python3 main.py
   ```
5. In the Railway Shell, run:
   ```bash
   pip3 install -r requirements.txt
   ```
6. Deploy.

### 3. Invite Bot
Use your Discord invite URL and authorize it to your server.

You're done 🎉.
