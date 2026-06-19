import anthropic
import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a concise personal assistant. You have access to tools for checking weather, setting and clearing reminders, looking up stock/ETF/crypto prices, getting live sports scores, sending GIFs, and searching the web and files. Keep responses brief and to the point. No unnecessary filler or repetition.

IMPORTANT: Never answer questions about live or current data (scores, prices, weather, news, recent events) from memory or training data. Always call the appropriate tool first. Your training data is outdated — use web_search for anything current or factual you are unsure about. You CAN send GIFs — always call get_gif when asked."""

# Tool schemas sent to Claude so it knows what tools are available and when to call them.
TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "The city name, e.g. 'London' or 'New York'"}
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
                "message": {"type": "string", "description": "The reminder message"},
                "time": {"type": "string", "description": "When to remind, e.g. '3pm', 'in 10 minutes', or '2024-12-25 09:00'"}
            },
            "required": ["message", "time"]
        }
    },
    {
        "name": "clear_reminders",
        "description": "Clear all pending reminders for the current user. Use when the user asks to delete, remove, or clear their reminders.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_price",
        "description": "Get the current price, daily change, and info for a stock, ETF, or crypto. Accepts a ticker symbol (e.g. AAPL, SPY, BTC-USD) or a company/asset name (e.g. 'Apple', 'Bitcoin').",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Ticker symbol or company/asset name"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_score",
        "description": "Get live or recent sports scores from ESPN. ALWAYS call this tool for any sports score question — never answer from memory. To get all games in a league (e.g. 'World Cup scores', 'NBA scores today'), pass only the league. To look up a specific team, pass the team name. Use league='worldcup' for FIFA World Cup 2026 scores.",
        "input_schema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Team name or city, e.g. 'Lakers', 'Chiefs', 'USA'. Omit to get all games in the league."},
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
        "description": "Search Giphy and return a GIF. You MUST call this tool whenever the user asks for a GIF — do not say you cannot send GIFs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term, e.g. 'excited cat', 'celebration'"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the internet for current information, news, or facts. Use for anything not covered by other tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
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
                "query": {"type": "string", "description": "Filename or extension to search for"}
            },
            "required": ["query"]
        }
    }
]


# --- Tool Implementations ---

def get_weather(city: str) -> str:
    # Fetch current weather conditions for a city from OpenWeatherMap.
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
            return f"Couldn't find weather for '{city}'."
        desc = data["weather"][0]["description"].capitalize()
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        return f"{city.title()}: {desc}, {temp}°C (feels like {feels}°C), humidity {humidity}%"
    except Exception as e:
        return f"Weather lookup failed: {e}"


def get_price(query: str) -> str:
    # Look up the current price and daily change for a stock, ETF, or crypto via yfinance.
    # Accepts either a ticker symbol (AAPL) or a common name (Apple) via the NAME_MAP lookup.
    import yfinance as yf
    from datetime import date

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

    symbol = NAME_MAP.get(query.strip().lower()) or query.strip().upper()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        current = info.last_price
        prev = info.previous_close
        if current is None or prev is None:
            return f"Couldn't retrieve price data for '{query}'."
        change = current - prev
        pct = (change / prev) * 100
        direction = "▲" if change >= 0 else "▼"
        company = ticker.info.get("longName") or ticker.info.get("shortName") or symbol
        today = date.today().strftime("%B %d, %Y")
        return (
            f"**{company} ({symbol})**\n"
            f"Date: {today}\n"
            f"Price: ${current:,.2f}\n"
            f"Change: {direction} ${abs(change):,.2f} ({'+' if change >= 0 else ''}{pct:.2f}%)"
        )
    except Exception as e:
        return f"Price lookup failed for '{query}': {e}"


def set_reminder(message: str, time: str, user_id: int = 0) -> str:
    # Save a reminder to disk and schedule a Discord DM if a user_id is provided.
    import app.reminders as reminders
    reminder = {
        "user_id": user_id,
        "message": message,
        "time": time,
        "created_at": datetime.now().isoformat()
    }
    reminders.save(reminder)

    if user_id:
        from app.bot import schedule_reminder
        return schedule_reminder(user_id, message, time)

    return f"Reminder saved: '{message}' at {time}"


def clear_reminders(user_id: int = 0) -> str:
    # Remove all reminders for the user from disk and cancel any scheduled jobs.
    import app.reminders as reminders
    removed = reminders.clear(user_id)

    if user_id:
        from app.bot import scheduler
        jobs = [j for j in scheduler.get_jobs() if str(user_id) in j.id]
        for j in jobs:
            j.remove()
        removed = max(removed, len(jobs))

    return f"✅ Cleared {removed} reminder(s)."


def get_score(team: str = None, league: str = None) -> str:
    # Fetch live or recent scores from the ESPN hidden API.
    # If team is given, searches all relevant leagues for that team's game.
    # If only league is given, returns all current games in that league.
    import httpx

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

    def fetch(sport, slug):
        r = httpx.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard",
            timeout=8
        )
        r.raise_for_status()
        return r.json()

    def format_game(event, comp, slug):
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
        home_label = f"**{home_name}**" if home.get("winner") else home_name
        away_label = f"**{away_name}**" if away.get("winner") else away_name

        try:
            dt = datetime.fromisoformat(event.get("date", "").replace("Z", "+00:00"))
            game_time = dt.strftime("%B %d, %Y at %I:%M %p UTC")
        except Exception:
            game_time = event.get("date", "")

        if state == "in":
            status_line = f"🔴 LIVE — {detail}"
        elif state == "post":
            status_line = f"✅ Final — {detail}"
        else:
            status_line = f"🕐 Upcoming — {detail}"

        league_label = slug.upper().replace("-", " ").replace(".", " ")
        return (
            f"**{league_label}** | {event.get('shortName', event.get('name', ''))}\n"
            f"📅 {game_time}\n"
            f"{status_line}\n"
            f"{away_label} {away_score} @ {home_label} {home_score}"
        )

    try:
        results = []
        for sport, slug in search_leagues:
            try:
                data = fetch(sport, slug)
            except Exception:
                continue
            for event in data.get("events", []):
                for comp in event.get("competitions", []):
                    if team_lower:
                        searchable = []
                        for c in comp.get("competitors", []):
                            t = c.get("team", {})
                            searchable += [t.get("displayName", ""), t.get("shortDisplayName", ""),
                                           t.get("location", ""), t.get("name", ""), t.get("abbreviation", "")]
                        if not any(team_lower in n.lower() for n in searchable if n):
                            continue
                    formatted = format_game(event, comp, slug)
                    if formatted:
                        results.append(formatted)
                        if team_lower:
                            return formatted
        if results:
            return "\n\n".join(results)
        if team_lower:
            return f"No current or recent game found for '{team}'."
        return "No games currently found."
    except Exception as e:
        return f"Score lookup failed: {e}"


def get_gif(query: str) -> str:
    # Search Giphy for a GIF matching the query and return a GIF_EMBED:: protocol string.
    # The bot intercepts this string and sends a Discord embed instead of plain text.
    import httpx
    import random
    api_key = os.getenv("GIPHY_API_KEY")
    if not api_key:
        return "GIF search is not configured. Set GIPHY_API_KEY in .env."
    try:
        r = httpx.get(
            "https://api.giphy.com/v1/gifs/search",
            params={"api_key": api_key, "q": query, "limit": 25, "rating": "pg-13"},
            timeout=5
        )
        r.raise_for_status()
        gifs = r.json().get("data", [])
        if not gifs:
            return f"No GIF found for '{query}'."
        gif = random.choice(gifs)
        url = gif["images"]["original"]["url"]
        return f"GIF_EMBED::{query}::{url}"
    except Exception as e:
        return f"GIF search failed: {e}"


def web_search(query: str) -> str:
    # Search the web via Brave Search API and return the top 5 results as formatted text.
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
        results = r.json().get("web", {}).get("results", [])
        if not results:
            return f"No results found for '{query}'."
        lines = [f"**{r['title']}**\n{r.get('description', '')}\n{r.get('url', '')}" for r in results]
        return "\n\n".join(lines)
    except Exception as e:
        return f"Web search failed: {e}"


def search_files(query: str) -> str:
    # Recursively search /data for files whose names contain the query string.
    results = []
    for root, _, files in os.walk("/data"):
        for filename in files:
            if query.lower() in filename.lower():
                results.append(os.path.join(root, filename))
    if not results:
        return f"No files found matching '{query}'"
    return "Found files:\n" + "\n".join(results)


# --- Tool Router ---

def run_tool(tool_name: str, tool_input: dict, user_id: int = 0) -> str:
    # Dispatch a tool call by name. user_id is passed through to tools that need it (reminders, GIFs).
    if tool_name == "get_weather":        return get_weather(**tool_input)
    if tool_name == "set_reminder":       return set_reminder(**tool_input, user_id=user_id)
    if tool_name == "clear_reminders":    return clear_reminders(user_id=user_id)
    if tool_name == "get_price":          return get_price(**tool_input)
    if tool_name == "get_score":          return get_score(**tool_input)
    if tool_name == "get_gif":            return get_gif(**tool_input)
    if tool_name == "web_search":         return web_search(**tool_input)
    if tool_name == "search_files":       return search_files(**tool_input)
    return f"Unknown tool: {tool_name}"


# --- Chat Loop ---

# Pending GIF embeds keyed by user_id. When get_gif fires, the (title, url) tuple is stored
# here instead of returned as text. bot.py checks this after chat() returns and sends the embed.
pending_gifs: dict[int, tuple[str, str]] = {}


def chat(conversation_history: list[dict], user_id: int = 0) -> str:
    # Run the Claude tool-use loop. Sends the conversation to Claude, handles any tool calls,
    # and returns the final text response. Loops until Claude stops calling tools.
    messages = list(conversation_history)

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )
        log.info("Tokens — input: %d, output: %d", response.usage.input_tokens, response.usage.output_tokens)

        if response.stop_reason == "end_turn":
            return "\n".join(b.text for b in response.content if hasattr(b, "text"))

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = run_tool(block.name, block.input, user_id=user_id)

                # Intercept GIF embeds so bot.py can send them as Discord embeds
                if result.startswith("GIF_EMBED::"):
                    parts = result.split("::", 2)
                    if len(parts) == 3:
                        pending_gifs[user_id] = (parts[1], parts[2])
                    result = "📬 Here's your GIF!"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "Something went wrong in the tool loop."
