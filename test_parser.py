import sys
sys.path.insert(0, '.')
from odds_tracker.scraper import _parse_from_text

with open('odds_data/debug_page.txt', encoding='utf-8') as f:
    text = f.read()

matches = _parse_from_text(text)
print(f'Parsed {len(matches)} matches')
for m in matches[:10]:
    ko = m.get('kick_off', '??')
    h = m.get('home', '?')
    a = m.get('away', '?')
    o1 = m.get('odds_1', '-')
    ox = m.get('odds_x', '-')
    o2 = m.get('odds_2', '-')
    s = m.get('status', '?')
    c = m.get('country', '')
    l = m.get('league', '')
    print(f'  {ko} | {h} vs {a} | 1={o1} X={ox} 2={o2} | {s} | {c}/{l}')
