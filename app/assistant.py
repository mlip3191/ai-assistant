import anthropic
import os
import json
from datetime import datetime

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a concise personal assistant. You have access to tools 
for checking weather, and setting reminders. Keep responses brief and to the point. No unnecessary filler or repetition."""

# --- Tool Definitions (sent to Claude) ---
TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "The city name, e.g. 'London' or 'New York'"
                }
            },
            "required": ["city"]
        }
    },
    {
        "name": "set_reminder",
        "description": "Set a reminder with a message and time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The reminder message"
                },
                "time": {
                    "type": "string",
                    "description": "When to remind, e.g. '3pm' or '2024-12-25 09:00'"
                }
            },
            "required": ["message", "time"]
        }
    },
    {
        "name": "search_files",
        "description": "Search for files by name or extension in the /data directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Filename or extension to search for, e.g. 'report' or '.pdf'"
                }
            },
            "required": ["query"]
        }
    }
]


# --- Actual Tool Implementations ---

def get_weather(city: str) -> str:
    import httpx
    api_key = os.getenv("OPENWEATHER_API_KEY")
    try:
        r = httpx.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": api_key, "units": "metric"},
            timeout=5
        )
        data = r.json()
        if data.get("cod") != 200:
            return f"Couldn't find weather for '{city}'. Try a different city name."
        desc = data["weather"][0]["description"].capitalize()
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        return f"{city.title()}: {desc}, {temp}°C (feels like {feels}°C), humidity {humidity}%"
    except Exception as e:
        return f"Weather lookup failed: {str(e)}"


def set_reminder(message: str, time: str, user_id: int = 0) -> str:
    # Save to file
    reminder = {
        "user_id": user_id,
        "message": message,
        "time": time,
        "created_at": datetime.now().isoformat()
    }
    os.makedirs("/data/reminders", exist_ok=True)
    with open("/data/reminders/reminders.json", "a") as f:
        f.write(json.dumps(reminder) + "\n")

    # Schedule the actual Discord DM if we have a user_id
    if user_id:
        from app.bot import schedule_reminder
        return schedule_reminder(user_id, message, time)

    return f"Reminder saved: '{message}' at {time}"


def search_files(query: str) -> str:
    results = []
    search_root = "/data"
    os.makedirs(search_root, exist_ok=True)

    for root, dirs, files in os.walk(search_root):
        for filename in files:
            if query.lower() in filename.lower():
                full_path = os.path.join(root, filename)
                results.append(full_path)

    if not results:
        return f"No files found matching '{query}'"
    return "Found files:\n" + "\n".join(results)


# --- Tool Router ---

def run_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "get_weather":
        return get_weather(**tool_input)
    elif tool_name == "set_reminder":
        return set_reminder(**tool_input)
    elif tool_name == "search_files":
        return search_files(**tool_input)
    else:
        return f"Unknown tool: {tool_name}"


# --- Model Picker ---

def pick_model(message: str) -> str:
    if len(message.split()) < 15:
        return "claude-haiku-4-5-20251001"
    return "claude-sonnet-4-6"


# --- Main Chat Function (handles tool loop) ---

def chat(conversation_history: list[dict], user_id: int = 0) -> str:
    messages = list(conversation_history)
    last_message = messages[-1]["content"]

    while True:
        response = client.messages.create(
            model=pick_model(last_message),
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )

        print(f"Tokens — input: {response.usage.input_tokens}, output: {response.usage.output_tokens}, total: {response.usage.input_tokens + response.usage.output_tokens}")

        if response.stop_reason == "end_turn":
            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            return "\n".join(text_blocks)

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    # Handle set_reminder with user_id
                    if block.name == "set_reminder":
                        result = set_reminder(
                            message=block.input["message"],
                            time=block.input["time"],
                            user_id=user_id  # ← pass user_id through
                        )
                    else:
                        result = run_tool(block.name, block.input)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            break

    return "Something went wrong in the tool loop."