import json
import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)

REMINDERS_PATH = "/data/reminders/reminders.json"


def _load_lines() -> list[str]:
    # Read all non-empty lines from the reminders file. Returns empty list if file doesn't exist.
    if not os.path.exists(REMINDERS_PATH):
        return []
    with open(REMINDERS_PATH) as f:
        return [l.strip() for l in f if l.strip()]


def _write_lines(lines: list[str]):
    # Overwrite the reminders file with the given lines, creating the directory if needed.
    os.makedirs(os.path.dirname(REMINDERS_PATH), exist_ok=True)
    with open(REMINDERS_PATH, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def load() -> list[dict]:
    # Parse and return all reminders from disk as a list of dicts. Skips malformed lines.
    results = []
    for line in _load_lines():
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("Skipping malformed reminder line: %s", line)
    return results


def save(reminder: dict):
    # Append a single reminder dict to the reminders file as a JSON line.
    os.makedirs(os.path.dirname(REMINDERS_PATH), exist_ok=True)
    with open(REMINDERS_PATH, "a") as f:
        f.write(json.dumps(reminder) + "\n")


def clear(user_id: int) -> int:
    # Remove all reminders belonging to the given user. Returns the number of reminders removed.
    lines = _load_lines()
    kept, removed = [], 0
    for line in lines:
        try:
            r = json.loads(line)
            if str(r.get("user_id", "")) == str(user_id):
                removed += 1
            else:
                kept.append(line)
        except json.JSONDecodeError:
            kept.append(line)
    _write_lines(kept)
    return removed


def patch_last_time(iso_time: str):
    # Replace the 'time' field of the last reminder entry with a resolved ISO timestamp.
    # Called after scheduling so restarts can re-parse an exact time instead of a relative string.
    lines = _load_lines()
    if not lines:
        return
    try:
        last = json.loads(lines[-1])
        last["time"] = iso_time
        lines[-1] = json.dumps(last)
        _write_lines(lines)
    except Exception as e:
        log.warning("Failed to patch last reminder time: %s", e)
