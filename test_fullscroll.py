"""Quick test: scroll to bottom, look for 'show more' or pagination buttons."""
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

url = "https://www.oddsportal.com/matches/football/20260207/"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    page = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    ).new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    
    page.goto(url, wait_until='networkidle', timeout=90000)
    
    # Accept cookies
    try:
        btn = page.locator('button:has-text("I Accept")')
        if btn.count() > 0:
            btn.first.click()
            time.sleep(2)
    except:
        pass
    
    # Scroll to bottom until stable
    prev_height = 0
    for i in range(80):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
        h = page.evaluate("document.body.scrollHeight")
        if h == prev_height:
            # Look for "show more" or similar buttons
            show_more = page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('button, a, div[role="button"]');
                    const found = [];
                    for (const b of btns) {
                        const txt = (b.textContent || '').trim().toLowerCase();
                        if (txt.includes('show more') || txt.includes('load more') || 
                            txt.includes('see more') || txt.includes('next') ||
                            txt.includes('more matches') || txt.includes('show all')) {
                            found.push({tag: b.tagName, text: txt.substring(0, 60), visible: b.offsetParent !== null});
                        }
                    }
                    return found;
                }
            """)
            
            if show_more:
                print(f"Scroll {i}: Found {len(show_more)} 'show more' buttons:")
                for sm in show_more:
                    print(f"  {sm}")
                # Try clicking the first visible one
                for sm_data in show_more:
                    if sm_data['visible']:
                        try:
                            page.locator(f'{sm_data["tag"].lower()}:has-text("{sm_data["text"][:20]}")').first.click()
                            print(f"  -> Clicked: {sm_data['text']}")
                            time.sleep(3)
                        except Exception as e:
                            print(f"  -> Click failed: {e}")
                        break
            else:
                # Check for pagination
                pagination = page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="page"], a[class*="pag"], div[class*="pag"]');
                        return Array.from(links).map(l => ({
                            tag: l.tagName,
                            text: (l.textContent || '').trim().substring(0, 40),
                            href: l.getAttribute('href') || ''
                        })).slice(0, 10);
                    }
                """)
                if pagination:
                    print(f"Scroll {i}: Found pagination: {pagination}")
                else:
                    print(f"Scroll {i}: Height stable at {h}, no more content or buttons found")
                    break
        else:
            print(f"Scroll {i}: height {prev_height} -> {h}")
        prev_height = h
    
    # Get page text and count time patterns
    import re
    text = page.evaluate("() => document.body.innerText")
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    tp = re.compile(r'^\d{1,2}:\d{2}$')
    time_lines = [l for l in lines if tp.match(l)]
    print(f"\nTotal non-empty lines: {len(lines)}")
    print(f"Time-pattern lines (= matches): {len(time_lines)}")
    print(f"Page height: {prev_height}")
    
    # Save for analysis
    with open('odds_data/debug_full_scroll.txt', 'w', encoding='utf-8') as f:
        f.write(text)
    
    browser.close()
