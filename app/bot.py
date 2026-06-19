import discord
import os
import json
import re
import httpx
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from app.assistant import chat, set_reminder
from app.memory import ConversationMemory

load_dotenv()

# --- Memory ---
user_memories: dict[int, ConversationMemory] = {}

def get_memory(user_id: int) -> ConversationMemory:
    if user_id not in user_memories:
        user_memories[user_id] = ConversationMemory()
    return user_memories[user_id]


# --- Bot + Scheduler Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()


# --- Reminder Firing ---
async def fire_reminder(user_id: int, message: str):
    try:
        user = await bot.fetch_user(user_id)
        await user.send(f"⏰ **Reminder:** {message}")
    except Exception as e:
        print(f"Failed to send reminder to {user_id}: {e}")


def parse_reminder_time(time_str: str) -> datetime | None:
    """
    Parses time strings like '3pm', '15:00', 'in 10 minutes', '2024-12-25 09:00'.
    Returns a timezone-aware datetime or None if parsing fails.
    """
    now = datetime.now(timezone.utc)
    normalized = time_str.strip().lower()
    # Strip trailing timezone labels like "utc"
    normalized = re.sub(r'\s*utc$', '', normalized).strip()

    # Relative: "in X minute(s)/hour(s)/second(s)" or "X minutes/hours"
    rel_match = re.match(r'(?:in\s+)?(\d+)\s*(second|minute|hour)s?', normalized)
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2)
        delta = {'second': timedelta(seconds=amount), 'minute': timedelta(minutes=amount), 'hour': timedelta(hours=amount)}[unit]
        return now + delta

    # Full datetime with optional seconds: "2024-12-25T09:00:00+00:00" or "2024-12-25 09:00"
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(normalized, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass

    # "3pm", "3:30pm", "15:00", "15:30"
    match = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', normalized)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        period = match.group(3)

        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0

        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt < now:
            dt = dt + timedelta(days=1)
        return dt

    return None


def schedule_reminder(user_id: int, message: str, time_str: str) -> str:
    """Parse time and schedule the reminder job."""
    run_time = parse_reminder_time(time_str)
    if not run_time:
        return f"Couldn't parse time '{time_str}'. Try formats like '3pm', '15:30', 'in 10 minutes', or '2024-12-25 09:00'."

    job_id = f"reminder_{user_id}_{run_time.timestamp()}"
    scheduler.add_job(
        fire_reminder,
        trigger=DateTrigger(run_date=run_time),
        args=[user_id, message],
        id=job_id
    )

    # Overwrite the last entry's time with the resolved ISO timestamp so
    # reload_reminders_from_file can reschedule correctly after a restart.
    _patch_last_reminder_time(run_time.isoformat())

    return f"✅ Reminder set for {run_time.strftime('%B %d at %I:%M %p UTC')}: '{message}'"


def _patch_last_reminder_time(iso_time: str):
    """Replace the 'time' field of the last line in reminders.json with an ISO timestamp."""
    path = "/data/reminders/reminders.json"
    if not os.path.exists(path):
        return
    with open(path) as f:
        lines = f.readlines()
    if not lines:
        return
    try:
        last = json.loads(lines[-1])
        last["time"] = iso_time
        lines[-1] = json.dumps(last) + "\n"
        with open(path, "w") as f:
            f.writelines(lines)
    except Exception:
        pass


def reload_reminders_from_file():
    """Re-schedule any future reminders saved to disk (e.g. after a restart)."""
    path = "/data/reminders/reminders.json"
    if not os.path.exists(path):
        return
    now = datetime.now(timezone.utc)
    loaded = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                user_id = r.get("user_id", 0)
                if not user_id:
                    continue
                run_time = parse_reminder_time(r["time"])
                if run_time is None or run_time <= now:
                    continue  # already past
                job_id = f"reminder_{user_id}_{run_time.timestamp()}"
                if not scheduler.get_job(job_id):
                    scheduler.add_job(
                        fire_reminder,
                        trigger=DateTrigger(run_date=run_time),
                        args=[user_id, r["message"]],
                        id=job_id
                    )
                    loaded += 1
            except Exception as e:
                print(f"Skipping bad reminder entry: {e}")
    if loaded:
        print(f"Reloaded {loaded} pending reminder(s) from disk.")


# --- Bot Events ---
@bot.event
async def on_ready():
    scheduler.start()
    reload_reminders_from_file()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Scheduler started.")


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
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

        if content.lower() == "!clear":
            memory.clear()
            await message.reply("Memory cleared!")
            return

        if content.lower() == "!reminders":
            jobs = scheduler.get_jobs()
            user_jobs = [j for j in jobs if str(message.author.id) in j.id]
            if not user_jobs:
                await message.reply("You have no pending reminders.")
            else:
                lines = [
                    f"⏰ {j.next_run_time.strftime('%B %d at %I:%M %p UTC')} — {j.args[1]}"
                    for j in user_jobs
                ]
                await message.reply("**Your reminders:**\n" + "\n".join(lines))
            return

        if content.lower() == "!joke":
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(
                        "https://v2.jokeapi.dev/joke/Any",
                        params={"blacklistFlags": "", "safe-mode": False}
                    )
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

        memory.add_user(content)
        response = chat(memory.get_history(), user_id=message.author.id)  # ← pass user_id
        memory.add_assistant(response)

    await send_response(message, response)


async def send_response(message: discord.Message, response: str):
    if len(response) <= 2000:
        await message.reply(response)
    else:
        chunks = [response[i:i+1990] for i in range(0, len(response), 1990)]
        for chunk in chunks:
            await message.channel.send(chunk)


def run():
    bot.run(os.getenv("DISCORD_BOT_TOKEN"))