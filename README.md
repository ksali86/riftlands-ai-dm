# Riftlands AI DM v1.3 (Full Build)

## Commands
- `/act description:"text" roll:<none/check/attack> [skill] [modifier] [weapon] [to_hit] [damage]`
- `/check <skill> <+mod>`
- `/attack <weapon> <+tohit> <damage>`
- `/start <title> <prompt>` (GM)
- `/resolve` — AI (if enabled) or cinematic fallback + hooks
- `/narration enable:<true/false>` (GM)
- `/force-resolve narration:"text"` (GM)
- `/scene-status` (GM DM)

## Notes
- Auto-modifiers from pinned sheets: put your sheet in your private `#<name>-sheet` channel and **pin** it.
- The bot reindexes whenever pins change in `*-sheet` channels.
- If stats are missing, it rolls with defaults and DMs the player a reminder.
- Results go to `#dice-checks`, confirmations + descriptions to `#player-actions`.
