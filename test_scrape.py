"""Quick scrape test with more wait time."""
import time, re
from datetime import datetime
from playwright.sync_api import sync_playwright

url = "https://www.oddsportal.com/matches/football/20260207/"

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
    )
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    )
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Navigating...")
    page.goto(url, wait_until='networkidle', timeout=90000)
    
    # Accept cookies
    try:
        btn = page.locator('button:has-text("I Accept")')
        if btn.count() > 0:
            btn.first.click()
            time.sleep(2)
    except:
        pass
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Page loaded, scrolling...")
    
    # Scroll aggressively to load all content
    for i in range(20):
        page.evaluate("window.scrollBy(0, 2000)")
        time.sleep(0.3)
    
    # Scroll back to top
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(3)
    
    # Get text
    text = page.evaluate("() => document.body.innerText")
    
    with open('odds_data/debug_page2.txt', 'w', encoding='utf-8') as f:
        f.write(text)
    
    lines_all = text.split('\n')
    lines_nonempty = [l.strip() for l in lines_all if l.strip()]
    
    print(f"Total chars: {len(text)}")
    print(f"Total lines: {len(lines_all)}, non-empty: {len(lines_nonempty)}")
    
    # Count time patterns
    tp = re.compile(r'^\d{1,2}:\d{2}$')
    time_lines = [(i, l) for i, l in enumerate(lines_nonempty) if tp.match(l)]
    print(f"Time-pattern lines: {len(time_lines)}")
    
    if time_lines:
        idx = time_lines[0][0]
        print(f"\nFirst match block (idx={idx}):")
        for j in range(idx, min(idx+10, len(lines_nonempty))):
            print(f"  {j}: {repr(lines_nonempty[j])}")
    
    browser.close()
