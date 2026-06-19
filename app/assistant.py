import anthropic
import os
import json
from datetime import datetime

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a concise personal assistant. You have access to tools for checking weather, setting and clearing reminders, looking up stock/ETF/crypto prices, getting live sports scores, searching the web, sending GIFs, and searching files. Keep responses brief and to the point. No unnecessary filler or repetition.

IMPORTANT: Never answer questions about live or current data (scores, prices, weather, news, recent events) from memory or training data. Always call the appropriate tool first. Your training data is outdated — use web_search for anything current or factual you are unsure about."""

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
        "name": "get_price",
        "description": "Get the current price, daily change, and info for a stock, ETF, or crypto. Accepts a ticker symbol (e.g. AAPL, SPY, BTC-USD) or a company/asset name (e.g. 'Apple', 'Bitcoin').",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Ticker symbol or company/asset name, e.g. 'AAPL', 'Apple', 'SPY', 'Bitcoin', 'BTC-USD'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "clear_reminders",
        "description": "Clear all pending reminders for the current user. Use when the user asks to delete, remove, or clear their reminders.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_score",
        "description": "Get live or recent sports scores from ESPN. ALWAYS call this tool for any sports score question — never answer from memory or training data. To get all games in a league (e.g. 'World Cup scores', 'NBA scores today'), pass only the league. To look up a specific team, pass the team name. Use league='worldcup' for FIFA World Cup 2026 scores.",
        "input_schema": {
            "type": "object",
            "properties": {
                "team": {
                    "type": "string",
                    "description": "Team name or city to filter by, e.g. 'Lakers', 'Chiefs', 'USA', 'Mexico'. Omit to get all games in the league."
                },
                "league": {
                    "type": "string",
                    "description": "League: nfl, nba, mlb, nhl, ncaaf, ncaab, wcbb, wnba, college-baseball, mls, epl, ucl, worldcup",
                    "enum": ["nfl", "nba", "mlb", "nhl", "ncaaf", "ncaab", "wcbb", "wnba", "college-baseball", "mls", "epl", "ucl", "worldcup"]
                }
            },
            "required": []
        }
    },
    {
        "name": "get_gif",
        "description": "Search Giphy and return a GIF URL. You MUST call this tool whenever the user asks for a GIF — do not say you cannot send GIFs. Always call this tool and return the URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term for the GIF, e.g. 'excited cat', 'facepalm', 'celebration'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the internet for current information, news, facts, or anything not covered by other tools. Use this for general knowledge questions, recent events, or anything that requires up-to-date information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query, e.g. 'latest iPhone release date' or 'who is the president of France'"
                }
            },
            "required": ["query"]
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


def get_price(query: str) -> str:
    import yfinance as yf
    from datetime import date

    # Common name → ticker mappings for popular assets
    NAME_MAP = {
        "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL",
        "amazon": "AMZN", "meta": "META", "facebook": "META", "nvidia": "NVDA",
        "tesla": "TSLA", "netflix": "NFLX", "amd": "AMD", "intel": "INTC",
        "s&p 500": "SPY", "s&p500": "SPY", "sp500": "SPY", "spy": "SPY",
        "nasdaq": "QQQ", "qqq": "QQQ", "dow jones": "DIA", "dia": "DIA",
        "bitcoin": "BTC-USD", "btc": "BTC-USD",
        "ethereum": "ETH-USD", "eth": "ETH-USD",
        "dogecoin": "DOGE-USD", "doge": "DOGE-USD",
        "solana": "SOL-USD", "sol": "SOL-USD",
    }

    ticker_symbol = NAME_MAP.get(query.strip().lower()) or query.strip().upper()

    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.fast_info

        current_price = info.last_price
        prev_close = info.previous_close

        if current_price is None or prev_close is None:
            return f"Couldn't retrieve price data for '{query}'. Check the ticker symbol and try again."

        change = current_price - prev_close
        change_pct = (change / prev_close) * 100
        direction = "▲" if change >= 0 else "▼"

        # Prefer longName from .info, fall back to ticker symbol
        full_info = ticker.info
        company = full_info.get("longName") or full_info.get("shortName") or ticker_symbol

        today = date.today().strftime("%B %d, %Y")

        return (
            f"**{company} ({ticker_symbol})**\n"
            f"Date: {today}\n"
            f"Price: ${current_price:,.2f}\n"
            f"Change: {direction} ${abs(change):,.2f} ({'+' if change >= 0 else ''}{change_pct:.2f}%)"
        )
    except Exception as e:
        return f"Price lookup failed for '{query}': {str(e)}"


def get_gif(query: str) -> str:
    import httpx
    api_key = os.getenv("GIPHY_API_KEY")
    if not api_key:
        return "GIF search is not configured. Set GIPHY_API_KEY in .env."
    try:
        r = httpx.get(
            "https://api.giphy.com/v1/gifs/search",
            params={"api_key": api_key, "q": query, "limit": 1, "rating": "pg-13"},
            timeout=5
        )
        r.raise_for_status()
        data = r.json()
        gifs = data.get("data", [])
        if not gifs:
            return f"No GIF found for '{query}'."
        return gifs[0]["images"]["original"]["url"]
    except Exception as e:
        return f"GIF search failed: {str(e)}"


def web_search(query: str) -> str:
    import httpx
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return "Web search is not configured. Set BRAVE_API_KEY in .env."
    try:
        r = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params={"q": query, "count": 5},
            timeout=8
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("web", {}).get("results", [])
        if not results:
            return f"No results found for '{query}'."
        lines = []
        for r in results:
            title = r.get("title", "")
            desc = r.get("description", "")
            url = r.get("url", "")
            lines.append(f"**{title}**\n{desc}\n{url}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"Web search failed: {str(e)}"


def clear_reminders(user_id: int = 0) -> str:
    if user_id:
        from app.bot import scheduler
        user_jobs = [j for j in scheduler.get_jobs() if str(user_id) in j.id]
        for j in user_jobs:
            j.remove()
        count = len(user_jobs)
    else:
        count = 0

    path = "/data/reminders/reminders.json"
    if os.path.exists(path):
        if user_id:
            remaining = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        if str(r.get("user_id", "")) != str(user_id):
                            remaining.append(line)
                    except Exception:
                        pass
            with open(path, "w") as f:
                f.write("\n".join(remaining) + ("\n" if remaining else ""))
        else:
            open(path, "w").close()

    return f"✅ Cleared {count} reminder(s)."


def get_score(team: str = None, league: str = None) -> str:
    import httpx
    from datetime import datetime

    LEAGUES = {
        "nfl":              ("football",   "nfl"),
        "ncaaf":            ("football",   "college-football"),
        "mlb":              ("baseball",   "mlb"),
        "college-baseball": ("baseball",   "college-baseball"),
        "nhl":              ("hockey",     "nhl"),
        "nba":              ("basketball", "nba"),
        "wnba":             ("basketball", "wnba"),
        "ncaab":            ("basketball", "mens-college-basketball"),
        "wcbb":             ("basketball", "womens-college-basketball"),
        "mls":              ("soccer",     "usa.1"),
        "epl":              ("soccer",     "eng.1"),
        "ucl":              ("soccer",     "uefa.champions"),
        "worldcup":         ("soccer",     "fifa.world"),
    }

    search_leagues = [LEAGUES[league]] if league and league in LEAGUES else list(LEAGUES.values())
    team_lower = team.strip().lower() if team else None

    def fetch_scoreboard(sport, slug):
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard"
        r = httpx.get(url, timeout=8)
        r.raise_for_status()
        return r.json()

    def format_game(event: dict, comp: dict, slug: str) -> str:
        status = comp.get("status", {})
        state = status.get("type", {}).get("state", "")
        detail = status.get("type", {}).get("shortDetail", "")

        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            return None

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        home_name = home.get("team", {}).get("displayName", "Home")
        away_name = away.get("team", {}).get("displayName", "Away")
        home_score = home.get("score", "—")
        away_score = away.get("score", "—")

        home_win = home.get("winner", False)
        away_win = away.get("winner", False)
        home_label = f"**{home_name}**" if home_win else home_name
        away_label = f"**{away_name}**" if away_win else away_name

        score_line = f"{away_label} {away_score} @ {home_label} {home_score}"

        date_str = event.get("date", "")
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            game_time = dt.strftime("%B %d, %Y at %I:%M %p UTC")
        except Exception:
            game_time = date_str

        if state == "in":
            status_line = f"🔴 LIVE — {detail}"
        elif state == "post":
            status_line = f"✅ Final — {detail}"
        else:
            status_line = f"🕐 Upcoming — {detail}"

        league_label = slug.upper().replace("-", " ").replace(".", " ")
        return f"**{league_label}** | {event.get('shortName', event.get('name', ''))}\n📅 {game_time}\n{status_line}\n{score_line}"

    try:
        results = []
        for sport, slug in search_leagues:
            try:
                data = fetch_scoreboard(sport, slug)
            except Exception:
                continue

            for event in data.get("events", []):
                for comp in event.get("competitions", []):
                    if team_lower:
                        competitors = comp.get("competitors", [])
                        searchable = []
                        for c in competitors:
                            t = c.get("team", {})
                            searchable += [
                                t.get("displayName", ""),
                                t.get("shortDisplayName", ""),
                                t.get("location", ""),
                                t.get("name", ""),
                                t.get("abbreviation", ""),
                            ]
                        if not any(team_lower in n.lower() for n in searchable if n):
                            continue
                    formatted = format_game(event, comp, slug)
                    if formatted:
                        results.append(formatted)
                        if team_lower:
                            return formatted  # return immediately for specific team lookup

        if results:
            return "\n\n".join(results)

        if team_lower:
            return f"No current or recent game found for '{team}'. The season may be off or try a different spelling."
        return "No games currently found."
    except Exception as e:
        return f"Score lookup failed: {str(e)}"


def set_reminder(message: str, time: str, user_id: int = 0) -> str:
    reminder = {
        "user_id": user_id,
        "message": message,
        "time": time,
        "created_at": datetime.now().isoformat()
    }
    os.makedirs("/data/reminders", exist_ok=True)
    with open("/data/reminders/reminders.json", "a") as f:
        f.write(json.dumps(reminder) + "\n")

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
    elif tool_name == "get_price":
        return get_price(**tool_input)
    elif tool_name == "get_score":
        return get_score(**tool_input)
    elif tool_name == "get_gif":
        return get_gif(**tool_input)
    elif tool_name == "web_search":
        return web_search(**tool_input)
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
                            user_id=user_id
                        )
                    elif block.name == "clear_reminders":
                        result = clear_reminders(user_id=user_id)
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