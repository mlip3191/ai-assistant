import discord
import os
import json
import re
import httpx  # ← make sure it's up here, not inside the function
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
    Parses time strings like '3pm', '15:00', '2024-12-25 09:00'.
    Returns a timezone-aware datetime or None if parsing fails.
    """
    now = datetime.now()
    time_str = time_str.strip().lower()

    # Try full datetime format first: "2024-12-25 09:00"
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Try "3pm" or "3:30pm" format
    match = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", time_str)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        period = match.group(3)

        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0

        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # If the time has already passed today, schedule for tomorrow
        if dt < now:
            dt = dt.replace(day=dt.day + 1)
        return dt.replace(tzinfo=timezone.utc)

    return None


def schedule_reminder(user_id: int, message: str, time_str: str) -> str:
    """Parse time and schedule the reminder job."""
    run_time = parse_reminder_time(time_str)
    if not run_time:
        return f"Couldn't parse time '{time_str}'. Try formats like '3pm', '15:30', or '2024-12-25 09:00'."

    scheduler.add_job(
        fire_reminder,
        trigger=DateTrigger(run_date=run_time),
        args=[user_id, message],
        id=f"reminder_{user_id}_{run_time.timestamp()}"
    )

    # Also save to file as backup
    set_reminder(message, time_str, user_id)

    return f"✅ Reminder set for {run_time.strftime('%B %d at %I:%M %p UTC')}: '{message}'"


# --- Bot Events ---
@bot.event
async def on_ready():
    scheduler.start()
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