import sys
sys.path.insert(0, '.')
from odds_tracker.scraper import _parse_from_text

with open('odds_data/debug_page2.txt', encoding='utf-8') as f:
    text = f.read()

matches = _parse_from_text(text)
print(f"Parsed {len(matches)} matches")

for x in matches[:10]:
    ko = x.get('kick_off', '??')
    h = x.get('home', '?')
    a = x.get('away', '?')
    o1 = x.get('odds_1', '-')
    ox = x.get('odds_x', '-')
    o2 = x.get('odds_2', '-')
    st = x.get('status', '?')
    c = x.get('country', '')
    lg = x.get('league', '')
    print(f"  {ko} | {h} vs {a} | 1={o1} X={ox} 2={o2} | {st} | {c}/{lg}")

upcoming = [x for x in matches if x['status'] == 'upcoming']
print(f"\nUpcoming: {len(upcoming)}")
for x in upcoming[:10]:
    ko = x.get('kick_off', '??')
    h = x.get('home', '?')
    a = x.get('away', '?')
    o1 = x.get('odds_1', '-')
    ox = x.get('odds_x', '-')
    o2 = x.get('odds_2', '-')
    print(f"  {ko} | {h} vs {a} | 1={o1} X={ox} 2={o2}")
