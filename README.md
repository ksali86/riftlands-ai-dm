# Riftlands AI DM — v1.6.1 (Debug Build)

This is a **debug build** to confirm whether Railway detects the `DISCORD_TOKEN` variable.

## What's New
- Prints if `DISCORD_TOKEN` exists.
- Shows the **first 5 characters** of your token (safe).
- If missing, **idles** instead of crashing → Railway stops infinite restart loops.

## Setup
1. Go to Railway → **Variables**.
2. Add:
   - `DISCORD_TOKEN = <your bot token>`
   - *(optional)* `RIFTLANDS_GUILD_ID = 1414706808802644131`
3. Deploy this package.
4. Check logs:
   - **If working:** `✅ DISCORD_TOKEN detected (starts with: MTQxx...)`
   - **If not:** `❌ DISCORD_TOKEN is missing!` → Railway isn’t seeing the token.

