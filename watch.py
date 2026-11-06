"""Send a Telegram message once a week when this week's menu is on the page."""
import datetime
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

URL = "https://le-piment-rouge-menu.bilkent.edu.tr/"
NOTICE = "The menus for this week are not set yet"
EXPECTED = "Le Piment Rouge"  # Text a normal page always has, to catch error pages.

TELEGRAM_API = "https://api.telegram.org"
TURKEY_TIME = datetime.timezone(datetime.timedelta(hours=3))
STATE_FILE = "state.txt"  # Holds the Monday of the last week we sent a message for.


def current_week(now=None):
    """Return this week's Monday to Friday as a list of dates, in Turkey time."""
    today = (now or datetime.datetime.now(TURKEY_TIME)).astimezone(TURKEY_TIME).date()
    monday = today - datetime.timedelta(days=today.weekday())
    return [monday + datetime.timedelta(days=offset) for offset in range(5)]


def fetch_page():
    request = urllib.request.Request(URL, headers={"User-Agent": "menu-watcher"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError:
        return None


def is_this_weeks_menu(page, week):
    """True if the notice is gone and the page shows a date from this week.

    Last week's menus stay on the page, so the notice being gone is not enough.
    """
    text = " ".join(page.split())
    if NOTICE in text:
        return False
    return any(day.strftime("%B %d") in text for day in week)


def menu_text(page):
    """Return the menu section as plain text, or "" if it cannot be found."""
    section = re.search(r'<section class="menu card">(.*?)</section>', page, re.DOTALL)
    if not section:
        return ""
    without_comments = re.sub(r"<!--.*?-->", "", section.group(1), flags=re.DOTALL)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", without_comments))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:3500]


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


def stop_after_failed_check(reason):
    print(f"::warning::Page check failed: {reason}")
    # Manual runs fail visibly. Scheduled runs stay quiet and try again next time.
    sys.exit(1 if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" else 0)


def main():
    testing = os.environ.get("TEST_ALERT") == "true"
    week = current_week()
    week_id = week[0].isoformat()

    if testing:
        send_telegram(f"Test message from menu-watcher.\n{URL}")
    elif read_state() == week_id:
        print(f"Already sent the message for the week of {week_id}.")
        return

    try:
        page = fetch_page()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        stop_after_failed_check(str(error))

    if EXPECTED not in page:
        stop_after_failed_check("the page did not look normal")

    found = is_this_weeks_menu(page, week)
    print(f"Page check OK. Menu for the week of {week_id} found: {found}.")

    if found and not testing:
        send_telegram(f"Le Piment Rouge menu is up!\n\n{menu_text(page)}\n\n{URL}")
        with open(STATE_FILE, "w", encoding="utf-8") as file:
            file.write(week_id + "\n")


if __name__ == "__main__":
    main()
