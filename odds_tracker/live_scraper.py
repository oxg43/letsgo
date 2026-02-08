"""
Live In-Play Scraper — scrapes live match odds from OddsPortal.

Extracts currently playing matches with:
- Current score
- Match minute
- Live 1X2 odds
- Pre-match (opening) odds from our DB for comparison
"""
import re
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

LIVE_URL = "https://www.oddsportal.com/inplay-odds/live-now/football/"


def _parse_live_text(text: str) -> list[dict]:
    """
    Parse live matches from OddsPortal in-play page text dump.

    Actual format (each item on its own line):
        Football                    ← sport marker
        /                           ← separator
        Germany                     ← country
        /                           ← separator
        Bundesliga                  ← league
        1                           ← header
        X                           ←
        2                           ←
        B's                         ←
        71'                         ← minute (or HT, FT, etc.)
        Freiburg                    ← home team
        1                           ← home score
        :                           ← separator
        0                           ← away score
        Werder Bremen               ← away team
        1.59                        ← odds 1
        3.23                        ← odds X
        8.44                        ← odds 2
        13                          ← num bookmakers
    """
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    matches = []
    current_country = ''
    current_league = ''
    i = 0

    while i < len(lines):
        line = lines[i]

        # ── League header detection ──
        # Pattern: "Football" -> "/" -> "<Country>" -> "/" -> "<League>"
        if line == 'Football' and i + 4 < len(lines) and lines[i + 1] == '/':
            current_country = lines[i + 2]
            if lines[i + 3] == '/':
                current_league = lines[i + 4]
                i += 5
            else:
                current_league = ''
                i += 3
            # Skip the "1 X 2 B's" header lines
            while i < len(lines) and lines[i] in ('1', 'X', '2', "B's"):
                i += 1
            continue

        # ── Match detection ──
        # A match starts with a minute indicator on its own line
        minute_str = _parse_minute(line)
        if minute_str is not None:
            # Try to read:  home, score_h, ":", score_a, away, odds1, oddsX, odds2, num_bk
            # We need at least 8 more lines after the minute
            if i + 8 < len(lines):
                try:
                    home = lines[i + 1]
                    score_h_str = lines[i + 2]
                    colon = lines[i + 3]
                    score_a_str = lines[i + 4]
                    away = lines[i + 5]
                    odds1_str = lines[i + 6]
                    oddsx_str = lines[i + 7]
                    odds2_str = lines[i + 8]

                    # Validate the colon separator
                    if colon != ':':
                        i += 1
                        continue

                    score_h = int(score_h_str)
                    score_a = int(score_a_str)
                    odds_1 = float(odds1_str)
                    odds_x = float(oddsx_str)
                    odds_2 = float(odds2_str)

                    # Optional: number of bookmakers on next line
                    num_bk = 0
                    advance = 9  # minute + 8 fields
                    if i + 9 < len(lines):
                        try:
                            num_bk = int(lines[i + 9])
                            advance = 10
                        except ValueError:
                            pass

                    matches.append({
                        'minute': minute_str,
                        'home': home,
                        'away': away,
                        'score_home': score_h,
                        'score_away': score_a,
                        'score': f"{score_h}:{score_a}",
                        'odds_1': odds_1,
                        'odds_x': odds_x,
                        'odds_2': odds_2,
                        'num_bookmakers': num_bk,
                        'country': current_country,
                        'league': current_league,
                        'status': 'live',
                    })
                    i += advance
                    continue
                except (ValueError, IndexError):
                    pass

        i += 1

    return matches


def _parse_minute(line: str) -> str | None:
    """
    Check if a line is a match-minute indicator.
    Valid patterns: "30'", "45+'", "45+2'", "90+3'", "HT", "FT", "ET", "Break", "Pen."
    Returns the minute string or None.
    """
    line = line.strip()
    if line in ('HT', 'FT', 'ET', 'Break', 'Pen.', 'Pen'):
        return line
    if re.fullmatch(r"\d+(?:\+\d*)?'", line):
        return line
    return None


def scrape_live_odds(headless: bool = True, timeout_ms: int = 60000) -> list[dict]:
    """
    Scrape all currently live football matches with odds from OddsPortal.
    Returns list of dicts with keys:
        home, away, minute, score, score_home, score_away,
        odds_1, odds_x, odds_2, country, league, status
    """
    matches = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )
        page = context.new_page()

        try:
            ts = datetime.now().strftime('%H:%M:%S')
            print(f"[{ts}] [LIVE] Navigating to in-play page...")
            page.goto(LIVE_URL, wait_until='networkidle', timeout=timeout_ms)

            # Accept cookies
            try:
                accept_btn = page.locator('button:has-text("I Accept")')
                if accept_btn.count() > 0:
                    accept_btn.first.click()
                    time.sleep(2)
            except Exception:
                pass

            # Wait for content
            time.sleep(3)

            # Scroll down to load all matches
            prev_height = 0
            for _ in range(10):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)
                new_height = page.evaluate("() => document.body.scrollHeight")
                if new_height == prev_height:
                    break
                prev_height = new_height

            # Extract text
            full_text = page.evaluate("() => document.body.innerText")
            matches = _parse_live_text(full_text)

            ts = datetime.now().strftime('%H:%M:%S')
            print(f"[{ts}] [LIVE] Scraped {len(matches)} live matches")

        except Exception as e:
            ts = datetime.now().strftime('%H:%M:%S')
            print(f"[{ts}] [LIVE] Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    return matches


if __name__ == '__main__':
    matches = scrape_live_odds(headless=True)
    print(f"\n{len(matches)} LIVE matches:")
    for m in matches[:20]:
        print(
            f"  {m['minute']:>5} | {m['home']:20s} {m['score']} {m['away']:20s} | "
            f"1={m.get('odds_1', '-'):>5} X={m.get('odds_x', '-'):>5} 2={m.get('odds_2', '-'):>5} | "
            f"{m.get('country', '')} / {m.get('league', '')}"
        )
