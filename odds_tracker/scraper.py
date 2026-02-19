"""
OddsPortal scraper using Playwright.
Scrapes today's football matches with 1X2 odds.
"""
import re
import time
from datetime import datetime
from playwright.sync_api import sync_playwright, Page, Browser


def _parse_odds(text: str):
    """Parse odds text to float, return None if invalid."""
    if not text:
        return None
    text = text.strip().replace(',', '.')
    # Remove any non-numeric chars except dots
    text = re.sub(r'[^\d.]', '', text)
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _parse_from_text(text: str) -> list[dict]:
    """
    Parse matches from OddsPortal's plain text dump.
    
    The pattern for each match block is:
    
    UPCOMING MATCH:
        HH:MM
        HomeTeam
        –
        AwayTeam
        odds_1         (float like 3.01)
        odds_x         (float like 2.82)
        odds_2         (float like 2.40)
        NN             (number of bookmakers)
    
    FINISHED MATCH:
        HH:MM
        HomeTeam
        N              (home goals)
        –
        N              (away goals)
        AwayTeam
        [optional: pen., aet., etc.]
        odds_1
        odds_x
        odds_2
        NN
    
    League headers appear as:
        Football
        /
        Country
        /
        LeagueName
        Today, 07 Feb ...
        1
        X
        2
        B's
    """
    matches = []
    lines = text.split('\n')
    # Filter out empty lines (OddsPortal text dump has blanks between every line)
    lines = [l.strip() for l in lines if l.strip()]
    
    current_country = ''
    current_league = ''
    
    time_pattern = re.compile(r'^\d{1,2}:\d{2}$')
    odds_pattern = re.compile(r'^\d+\.\d{2}$')
    score_num_pattern = re.compile(r'^\d+$')
    bookmaker_count_pattern = re.compile(r'^\d{1,3}$')
    # The dash can be –, û, or - depending on encoding
    dash_chars = {'–', '\u2013', 'û', '-'}
    
    i = 0
    n = len(lines)
    
    while i < n:
        line = lines[i]
        
        # Detect league header: "Football" followed by "/" then country
        if line == 'Football' and i + 4 < n and lines[i + 1] == '/':
            current_country = lines[i + 2]
            if i + 4 < n and lines[i + 3] == '/':
                current_league = lines[i + 4]
                i += 5
            else:
                i += 3
            continue
        
        # Detect match: starts with HH:MM
        if time_pattern.match(line):
            kick_off = line
            j = i + 1
            
            # Next line should be home team
            if j >= n:
                i += 1
                continue
            home_team = lines[j]
            j += 1
            
            # Now check: is this a finished match (next is score digit)?
            # Or upcoming match (next is "–")?
            if j >= n:
                i += 1
                continue
            
            score = ''
            away_team = ''
            status = 'upcoming'
            
            if lines[j] in dash_chars:
                # Upcoming match: HomeTeam – AwayTeam
                j += 1  # skip dash
                if j < n:
                    away_team = lines[j]
                    j += 1
                status = 'upcoming'
                
                # Skip optional markers like "FRO", "Postp.", etc.
                while j < n and lines[j] in ('pen.', 'aet.', 'FRO', 'Postp.', 'Canc.', 'Award.', 'WO', 'Abn.'):
                    j += 1
                
            elif score_num_pattern.match(lines[j]):
                # Finished match: HomeTeam N – N AwayTeam
                home_goals = lines[j]
                j += 1
                if j < n and lines[j] in dash_chars:
                    j += 1  # skip dash
                if j < n and score_num_pattern.match(lines[j]):
                    away_goals = lines[j]
                    score = f"{home_goals}-{away_goals}"
                    j += 1
                if j < n:
                    away_team = lines[j]
                    j += 1
                status = 'finished'
                
                # Skip optional markers like "pen.", "aet.", "FRO", etc.
                while j < n and lines[j] in ('pen.', 'aet.', 'FRO', 'Postp.', 'Canc.', 'Award.', 'WO', 'Abn.'):
                    j += 1
            else:
                # Unknown structure, skip
                i += 1
                continue
            
            # Now expect odds: up to 3 float values
            odds = []
            while j < n and len(odds) < 3:
                if odds_pattern.match(lines[j]):
                    odds.append(float(lines[j]))
                    j += 1
                elif lines[j] == '-':
                    # Missing odds
                    odds.append(None)
                    j += 1
                else:
                    break
            
            # Skip bookmaker count (NN)
            if j < n and bookmaker_count_pattern.match(lines[j]):
                j += 1
            
            if home_team and away_team:
                matches.append({
                    'home': home_team,
                    'away': away_team,
                    'kick_off': kick_off,
                    'score': score,
                    'status': status,
                    'odds_1': odds[0] if len(odds) >= 1 else None,
                    'odds_x': odds[1] if len(odds) >= 2 else None,
                    'odds_2': odds[2] if len(odds) >= 3 else None,
                    'country': current_country,
                    'league': current_league,
                    'url': ''
                })
            
            i = j
            continue
        
        i += 1
    
    return matches


def _launch_browser(playwright, headless=True) -> Browser:
    """Launch Chromium with stealth settings."""
    browser = playwright.chromium.launch(
        headless=headless,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-dev-shm-usage',
        ]
    )
    return browser


def _create_page(browser: Browser) -> Page:
    """Create a new page with stealth settings."""
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        locale='en-US',
    )
    page = context.new_page()
    # Hide webdriver property
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    """)
    return page


def _scroll_and_expand(page: Page, max_scrolls: int = 60) -> int:
    """
    Scroll page to bottom, expanding all lazy-loaded content.
    Also clicks 'show more' buttons to expand collapsed league sections.
    Returns final page height.
    """
    # First close any modal overlays
    _close_modals(page)
    
    # Then try to click any 'show more' buttons visible before scrolling
    _click_show_more(page)
    
    prev_height = 0
    stable_count = 0
    for attempt in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.0)
        current_height = page.evaluate("document.body.scrollHeight")
        
        if current_height == prev_height:
            stable_count += 1
            if stable_count >= 3:
                break
        else:
            stable_count = 0
            # After new content loads, check for 'show more' buttons
            if attempt % 5 == 0:
                _click_show_more(page)
        prev_height = current_height
    
    # Final check for show more buttons after full scroll
    _click_show_more(page)
    
    return prev_height


def _click_show_more(page: Page):
    """Click all visible 'show more' buttons (NOT 'next matches' links)."""
    try:
        show_more_btns = page.locator('button:has-text("show more"), button:has-text("Show more")')
        count = show_more_btns.count()
        for idx in range(count):
            try:
                btn = show_more_btns.nth(idx)
                if btn.is_visible():
                    btn.click()
                    time.sleep(0.5)
            except Exception:
                pass
    except Exception:
        pass


def _close_modals(page: Page):
    """Close any modal overlays that might block clicks."""
    try:
        # Close bookie modal overlay
        modal_selectors = [
            'div.overlay-bookie-modal',
            'div[class*="overlay"]',
            'div[class*="modal"]',
            'button[class*="close"]',
            'button:has-text("×")',
            'button:has-text("Close")',
            'a[class*="close"]',
        ]
        for selector in modal_selectors:
            try:
                elements = page.locator(selector)
                for i in range(elements.count()):
                    el = elements.nth(i)
                    if el.is_visible():
                        # Try clicking close button or just hide the modal
                        try:
                            el.click(timeout=1000)
                        except:
                            # Force hide via JS
                            page.evaluate(f"document.querySelectorAll('{selector}').forEach(e => e.style.display = 'none')")
                        time.sleep(0.3)
            except:
                pass
        
        # Also try pressing Escape key to close modals
        page.keyboard.press('Escape')
        time.sleep(0.3)
        
        # Force hide any overlay via JS
        page.evaluate("""
            document.querySelectorAll('[class*="overlay"], [class*="modal"]').forEach(el => {
                if (el.style.position === 'fixed' || el.style.position === 'absolute') {
                    el.style.display = 'none';
                }
            });
        """)
    except Exception:
        pass


def _has_next_page(page: Page) -> bool:
    """Check if a 'next matches' link exists."""
    try:
        link = page.locator('a:has-text("next matches")')
        return link.count() > 0 and link.first.is_visible()
    except Exception:
        return False


def _click_next_page(page: Page) -> bool:
    """Click the 'next matches' link and wait for new page to load. Returns True on success."""
    try:
        # First close any modal overlays
        _close_modals(page)
        time.sleep(0.5)
        
        link = page.locator('a:has-text("next matches")')
        if link.count() > 0 and link.first.is_visible():
            # Try multiple click methods
            try:
                # Method 1: Force click (ignores overlays)
                link.first.click(force=True, timeout=10000)
            except:
                try:
                    # Method 2: JavaScript click
                    page.evaluate("""
                        const link = document.querySelector('a[href*="matches"]');
                        if (link && link.textContent.toLowerCase().includes('next')) {
                            link.click();
                        }
                    """)
                except:
                    # Method 3: Navigate directly
                    href = link.first.get_attribute('href')
                    if href:
                        if not href.startswith('http'):
                            href = 'https://www.oddsportal.com' + href
                        page.goto(href, wait_until='networkidle', timeout=60000)
            
            time.sleep(3)
            page.wait_for_load_state('networkidle', timeout=60000)
            time.sleep(1)
            return True
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Next page click failed: {e}")
    return False


def scrape_odds(url: str, headless: bool = True, timeout_ms: int = 60000) -> list[dict]:
    """
    Scrape all football matches and 1X2 odds from OddsPortal matches page.
    Handles multi-page navigation (OddsPortal splits large days across pages).
    Returns list of dicts with keys:
        home, away, kick_off, odds_1, odds_x, odds_2, country, league, url, status, score
    """
    all_matches = []
    seen_keys = set()  # De-duplicate across pages

    with sync_playwright() as pw:
        browser = _launch_browser(pw, headless=headless)
        page = _create_page(browser)

        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Navigating to OddsPortal...")
            page.goto(url, wait_until='networkidle', timeout=timeout_ms)

            # Handle cookie consent if present
            try:
                accept_btn = page.locator('button:has-text("I Accept")')
                if accept_btn.count() > 0:
                    accept_btn.first.click()
                    time.sleep(2)
            except Exception:
                pass

            # Close any modal overlays that appeared
            _close_modals(page)
            time.sleep(1)

            page_num = 1
            max_pages = 15  # increased for weekend days with more matches
            
            while page_num <= max_pages:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] === Page {page_num} ===")
                
                # Scroll to load all lazy content + expand collapsed sections
                final_height = _scroll_and_expand(page)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Page {page_num} scrolled to height={final_height}")
                
                # Scroll back to top and extract text
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(0.5)
                
                full_text = page.evaluate("() => document.body.innerText")
                page_matches = _parse_from_text(full_text)
                
                # De-duplicate: use (kick_off, home, away) as unique key
                new_count = 0
                for m in page_matches:
                    key = (m['kick_off'], m['home'], m['away'])
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_matches.append(m)
                        new_count += 1
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Page {page_num}: {len(page_matches)} parsed, {new_count} new (total: {len(all_matches)})")
                
                # Stop if this page added nothing new (we're looping)
                if new_count == 0 and page_num > 1:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] No new matches on page {page_num} — stopping pagination")
                    break
                
                # Check for next page
                if _has_next_page(page):
                    if _click_next_page(page):
                        page_num += 1
                        continue
                
                # No more pages
                break

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraping complete: {len(all_matches)} total matches across {page_num} pages")

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraping error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    return all_matches


def scrape_match_detail(url: str, headless: bool = True, timeout_ms: int = 30000) -> dict:
    """
    Scrape detailed odds from a specific match page.
    Returns dict with odds_1, odds_x, odds_2 from multiple bookmakers.
    """
    result = {'odds_1': None, 'odds_x': None, 'odds_2': None, 'bookmaker_odds': []}

    with sync_playwright() as pw:
        browser = _launch_browser(pw, headless=headless)
        page = _create_page(browser)

        try:
            page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)

            # Handle cookies
            try:
                accept_btn = page.locator('button:has-text("I Accept")')
                if accept_btn.count() > 0:
                    accept_btn.first.click()
                    time.sleep(1)
            except Exception:
                pass

            time.sleep(3)

            # Extract average/best odds
            odds_data = page.evaluate("""
                () => {
                    const text = document.body.innerText;
                    const oddsMatches = text.match(/\\b(\\d+\\.\\d{2})\\b/g);
                    return oddsMatches ? oddsMatches.map(Number) : [];
                }
            """)

            if odds_data and len(odds_data) >= 3:
                result['odds_1'] = odds_data[0]
                result['odds_x'] = odds_data[1]
                result['odds_2'] = odds_data[2]

        except Exception as e:
            print(f"Error scraping detail page: {e}")
        finally:
            browser.close()

    return result


if __name__ == '__main__':
    from odds_tracker.config import ODDSPORTAL_URL, HEADLESS
    matches = scrape_odds(ODDSPORTAL_URL, headless=HEADLESS)
    print(f"\nScraped {len(matches)} matches:")
    for m in matches[:10]:
        print(f"  {m.get('kick_off', '??:??')} | {m['home']} vs {m['away']} | "
              f"1={m.get('odds_1', '-')} X={m.get('odds_x', '-')} 2={m.get('odds_2', '-')} | "
              f"{m.get('country', '')} / {m.get('league', '')}")
