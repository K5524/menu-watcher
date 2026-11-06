"""Send a Telegram message when a seat becomes free for the watched concerts."""
import datetime
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

PAGES = [
    "https://bilet.bilkent.edu.tr/bilet/3375/mssf-salon",
    "https://bilet.bilkent.edu.tr/bilet/3375/mssf-balkon1",
    "https://bilet.bilkent.edu.tr/bilet/3375/mssf-balkon2",
    "https://bilet.bilkent.edu.tr/bilet/3376/mssf-salon",
    "https://bilet.bilkent.edu.tr/bilet/3376/mssf-balkon1",
    "https://bilet.bilkent.edu.tr/bilet/3376/mssf-balkon2",
]
END_DATE = datetime.date(2026, 12, 30)  # Checks stop when this day starts in Turkey.

TELEGRAM_API = "https://api.telegram.org"
TURKEY_TIME = datetime.timezone(datetime.timedelta(hours=3))
STATE_FILE = "concert_state.json"
FAILED_RUNS_BEFORE_WARNING = 8  # 8 runs is about 2 hours.
MAX_SEATS_LISTED = 20
NOT_FREE_FLAGS = ("closed", "hidden", "locked", "sold", "unavailable")


def fetch_page(url):
    request = urllib.request.Request(url, headers={"User-Agent": "concert-watcher"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def read_json_line(page, name):
    """Read a one-line JSON value from the page's window.appData block."""
    match = re.search(rf"^\s*{name}:\s*(.+?),\s*$", page, re.MULTILINE)
    if not match:
        raise ValueError(f"{name} was not found")
    return json.loads(match.group(1))


def read_seats(page):
    """Return the concert title, section name and free seat numbers on a page."""
    seats = read_json_line(page, "seats")
    floor = read_json_line(page, "selectedFloor")
    if not seats or not isinstance(floor, dict):
        raise ValueError("the seat data was empty")

    title = re.search(r"<h1>(.*?)</h1>", page, re.DOTALL)
    title = html.unescape(title.group(1)).strip() if title else "Concert"
    free = {
        seat["seat_number"]
        for seat in seats
        if str(seat["floor_id"]) == str(floor["id"])
        and not any(seat.get(flag) == "Y" for flag in NOT_FREE_FLAGS)
    }
    return title, floor["name"], free


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {"free_seats": {}, "failed_runs": 0}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)
        file.write("\n")


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("::error::TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")
        sys.exit(1)

    request = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            print("Telegram message sent.")
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        print(f"::error::Telegram refused the message: {details}")
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"::error::Could not reach Telegram: {error}")
        sys.exit(1)


def seat_list(seats):
    ordered = sorted(seats)
    listed = ", ".join(ordered[:MAX_SEATS_LISTED])
    extra = len(ordered) - MAX_SEATS_LISTED
    return f"{listed} and {extra} more" if extra > 0 else listed


def set_finished():
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as file:
            file.write("finished=true\n")


def main():
    today = datetime.datetime.now(TURKEY_TIME).date()
    if today >= END_DATE:
        print("The end date has passed.")
        set_finished()
        return

    testing = os.environ.get("TEST_ALERT") == "true"
    state = read_state()
    free_seats = dict(state["free_seats"])
    new_seats, summary, failures = [], [], []

    for url in PAGES:
        try:
            title, section, free = read_seats(fetch_page(url))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as error:
            print(f"::warning::Check failed for {url}: {error}")
            failures.append(url)
            continue

        print(f"{title}, {section}: {len(free)} free")
        summary.append(f"{title}, {section}: {len(free)} free")
        new = free - set(state["free_seats"].get(url, []))
        free_seats[url] = sorted(free)
        if new:
            new_seats.append(f"{title}, {section}\n{seat_list(new)}\n{url}")

    if testing:
        lines = "\n".join(summary) or "No pages could be read."
        send_telegram(f"Test message from concert-watcher.\n\n{lines}")
    else:
        if new_seats:
            send_telegram("Concert seats are free!\n\n" + "\n\n".join(new_seats))

        failed_runs = state["failed_runs"] + 1 if len(failures) == len(PAGES) else 0
        if failed_runs == FAILED_RUNS_BEFORE_WARNING:
            send_telegram("Concert watcher warning: the ticket pages have failed for about 2 hours.")

        new_state = {"free_seats": free_seats, "failed_runs": failed_runs}
        if new_state != state:
            save_state(new_state)

    if failures and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        sys.exit(1)


if __name__ == "__main__":
    main()
