import discord
import os
import re
import httpx
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from app.assistant import chat, pending_gifs
from app.memory import ConversationMemory
import app.reminders as reminders

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# --- Memory ---
user_memories: dict[int, ConversationMemory] = {}

def get_memory(user_id: int) -> ConversationMemory:
    # Return the conversation memory for a user, creating it if it doesn't exist yet.
    if user_id not in user_memories:
        user_memories[user_id] = ConversationMemory()
    return user_memories[user_id]


# --- Bot + Scheduler ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()


# --- Reminders ---
async def fire_reminder(user_id: int, message: str):
    # Called by the scheduler at the reminder's scheduled time — sends a DM to the user.
    try:
        user = await bot.fetch_user(user_id)
        await user.send(f"⏰ **Reminder:** {message}")
    except Exception as e:
        log.error("Failed to send reminder to %d: %s", user_id, e)


def parse_reminder_time(time_str: str) -> datetime | None:
    # Parse a human-readable time string into a UTC-aware datetime.
    # Supports relative ("in 10 minutes"), ISO timestamps, and clock times ("3pm", "15:30").
    now = datetime.now(timezone.utc)
    normalized = time_str.strip().lower()
    normalized = re.sub(r'\s*utc$', '', normalized).strip()

    # Relative: "in X minutes/hours/seconds" or "X minutes"
    rel = re.match(r'(?:in\s+)?(\d+)\s*(second|minute|hour)s?', normalized)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2)
        delta = {'second': timedelta(seconds=amount), 'minute': timedelta(minutes=amount), 'hour': timedelta(hours=amount)}[unit]
        return now + delta

    # ISO / full datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(normalized, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Clock time: "3pm", "3:30pm", "15:00"
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', normalized)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        period = m.group(3)
        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return dt

    return None


def schedule_reminder(user_id: int, message: str, time_str: str) -> str:
    # Parse the time string and register a one-shot scheduler job to DM the user.
    # Also patches the saved reminder file with the resolved ISO time for restart recovery.
    run_time = parse_reminder_time(time_str)
    if not run_time:
        return f"Couldn't parse time '{time_str}'. Try '3pm', 'in 10 minutes', or '2024-12-25 09:00'."

    job_id = f"reminder_{user_id}_{run_time.timestamp()}"
    scheduler.add_job(fire_reminder, trigger=DateTrigger(run_date=run_time), args=[user_id, message], id=job_id)
    reminders.patch_last_time(run_time.isoformat())
    return f"✅ Reminder set for {run_time.strftime('%B %d at %I:%M %p UTC')}: '{message}'"


def reload_reminders():
    # On startup, re-schedule any future reminders saved to disk so they survive container restarts.
    now = datetime.now(timezone.utc)
    loaded = 0
    for r in reminders.load():
        user_id = r.get("user_id", 0)
        if not user_id:
            continue
        run_time = parse_reminder_time(r["time"])
        if not run_time or run_time <= now:
            continue
        job_id = f"reminder_{user_id}_{run_time.timestamp()}"
        if not scheduler.get_job(job_id):
            scheduler.add_job(fire_reminder, trigger=DateTrigger(run_date=run_time), args=[user_id, r["message"]], id=job_id)
            loaded += 1
    if loaded:
        log.info("Reloaded %d pending reminder(s) from disk.", loaded)


# --- Bot Events ---
@bot.event
async def on_ready():
    scheduler.start()
    reload_reminders()
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)


@bot.event
async def on_message(message: discord.Message):
    # Main message handler. Processes !pm/!purge commands first (no mention needed),
    # then handles bot mentions and DMs for AI responses and other commands.
    if message.author == bot.user:
        return

    # !pm / !purge — works with or without @mention, in any channel
    raw = re.sub(r"<@!?\d+>\s*", "", message.content).strip()
    if raw.lower().startswith("!pm") or raw.lower().startswith("!purge"):
        try:
            member = await message.guild.fetch_member(message.author.id)
            has_perm = any(r.name.lower() == "clams" for r in member.roles)
        except Exception:
            has_perm = False
        if not has_perm:
            await message.channel.send("❌ You need the `clams` role to use this command.")
            return
        parts = raw.split()
        try:
            limit = min(int(parts[1]) if len(parts) > 1 else 100, 500)
        except ValueError:
            limit = 100

        def is_meemaw_related(m):
            return (
                m.author == bot.user or
                "meemaw" in m.content.lower() or
                "@meemaw" in m.content.lower() or
                bot.user in m.mentions
            )

        try:
            deleted = await message.channel.purge(limit=limit, check=is_meemaw_related)
        except discord.Forbidden:
            await message.channel.send("❌ Missing 'Manage Messages' permission in this channel.")
            return
        except Exception as e:
            await message.channel.send(f"❌ Purge failed: {e}")
            return
        try:
            await message.delete()
        except Exception:
            pass
        confirm = await message.channel.send(f"🗑️ Deleted {len(deleted)} message(s).")
        await confirm.delete(delay=3)
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user in message.mentions
    if not (is_dm or is_mentioned):
        return

    content = message.content.replace(f"<@{bot.user.id}>", "").strip()
    if not content:
        await message.reply("Yes? How can I help?")
        return

    async with message.channel.typing():
        memory = get_memory(message.author.id)

        # --- Commands ---
        if content.lower() in ("!clear", "clear"):
            memory.clear()
            await message.reply("Memory cleared!")
            return

        if content.lower() in ("!clearreminders", "clearreminders"):
            jobs = [j for j in scheduler.get_jobs() if str(message.author.id) in j.id]
            for j in jobs:
                j.remove()
            removed = reminders.clear(message.author.id)
            await message.reply(f"🗑️ Cleared {max(len(jobs), removed)} reminder(s).")
            return

        if content.lower() in ("!reminders", "reminders"):
            jobs = [j for j in scheduler.get_jobs() if str(message.author.id) in j.id]
            if not jobs:
                await message.reply("You have no pending reminders.")
            else:
                lines = [f"⏰ {j.next_run_time.strftime('%B %d at %I:%M %p UTC')} — {j.args[1]}" for j in jobs]
                await message.reply("**Your reminders:**\n" + "\n".join(lines))
            return

        if content.lower() in ("!joke", "joke"):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get("https://v2.jokeapi.dev/joke/Any", params={"safe-mode": False})
                data = r.json()
                if data.get("type") == "twopart":
                    await message.reply(f"{data['setup']}\n\n||{data['delivery']}||")
                elif data.get("type") == "single":
                    await message.reply(data["joke"])
                else:
                    await message.reply("Couldn't fetch a joke right now.")
            except Exception as e:
                await message.reply(f"Joke fetch failed: {e}")
            return

        # --- AI Response ---
        memory.add_user(content)
        response = chat(memory.get_history(), user_id=message.author.id)
        memory.add_assistant(response)

    await send_response(message, response, user_id=message.author.id)


async def send_response(message: discord.Message, response: str, user_id: int = 0):
    # Send the bot's reply. If a GIF embed was queued during the tool call, send that instead.
    # Splits long responses into 1990-char chunks to stay under Discord's 2000-char limit.
    # Send GIF embed if one was queued during the tool call
    if user_id in pending_gifs:
        title, url = pending_gifs.pop(user_id)
        embed = discord.Embed(title=title, url=url)
        embed.set_image(url=url)
        await message.reply(embed=embed)
        return

    if len(response) <= 2000:
        await message.reply(response)
    else:
        for chunk in [response[i:i+1990] for i in range(0, len(response), 1990)]:
            await message.channel.send(chunk)


def run():
    bot.run(os.getenv("DISCORD_BOT_TOKEN"))
