#!/usr/bin/env python3
"""
================================================================================
  IMPROVEMENT PLAN - Based on Professional Analysis (2026-02-18)
================================================================================

Analiza 81 resolved paper trades pokazala je sljedeće probleme i rješenja:

╔══════════════════════════════════════════════════════════════════════════════╗
║  OVERALL: -4.26% ROI (blago gubitnička)                                      ║
║  CILJ:    Pozitivni ROI eliminacijom loših obrazaca                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

===============================================================================
  🔴 KRITIČNI PROBLEMI (Odmah riješiti)
===============================================================================

1. AWAY BETS (tip 2) - ROI: -17.5%
   ─────────────────────────────────
   Problem: Gostujuće pobjede značajno gube
   Rješenje: DISABLE AWAY BETS ili zahtijevaj drop >= 15%
   
   Podaci:
   - HOME (1): 39 trades, 59% win rate, +5.9% ROI ⭐
   - AWAY (2): 41 trades, 44% win rate, -17.5% ROI ❌
   - Razlika: 15 postotnih bodova!

2. DROP 6-10% - ROI: -34.5%
   ─────────────────────────────────
   Problem: Srednji dropovi su NAJGORI
   Rješenje: Preskakati drop range 6-10%
   
   Paradoks:
   - Drop <6%:   68% win rate, +29.7% ROI ⭐
   - Drop 6-10%: 37% win rate, -34.5% ROI ❌ (WORST!)
   - Drop 10%+:  ~57% win rate, +7-12% ROI ⭐
   
   Objašnjenje: Mali dropovi (<6%) su bolji jer su to stabilni 
   favoriti. Veliki dropovi (10%+) su legitimni steam moves.
   SREDNJI dropovi (6-10%) su "buka" - nisu ni jedno ni drugo.

3. ODDS 1.30-1.50 - Edge: -29%
   ─────────────────────────────────
   Problem: Favoriti 1.30-1.50 gube jako
   Rješenje: Izbjegavaj ovaj odds range
   
   Odds Performance:
   - 1.00-1.30: +19.5% edge ⭐
   - 1.30-1.50: -29.0% edge ❌ (WORST!)
   - 1.50-1.80: +1.8% edge 
   - 1.80-2.10: -7.4% edge
   - 2.10-2.50: +2.6% edge

4. MIXED SIGNALS - ROI: -24%
   ─────────────────────────────────
   Problem: MIXED signal tip gubi
   Rješenje: DISABLE MIXED signals

5. CONFIDENCE <80% - ROI: -17% to -43%
   ─────────────────────────────────
   Problem: Niski confidence gubi
   Rješenje: Zahtijevaj confidence >= 80%
   
   - 80-100%: +13.7% ROI ⭐
   - 70-80%:  -17.5% ROI ❌
   - <70%:    -43% ROI ❌

===============================================================================
  ✅ ŠTO RADI (Nastaviti/pojačati)
===============================================================================

1. HOME BETS (tip 1) → +5.9% ROI
2. DRAW BETS (tip X) → +140% ROI (mali uzorak, ali radi!)
3. Small drops (<6%) → +29.7% ROI
4. Large drops (>10%) → +7-12% ROI
5. Confidence 80%+ → +13.7% ROI
6. Heavy favorites (1.00-1.30) → +19.5% edge
7. High snapshot count (150+) → stabilniji rezultati

===============================================================================
  📋 KONKRETNE PROMJENE ZA IMPLEMENTACIJU
===============================================================================

PROMJENE U signal_config.py:
────────────────────────────
1. Dodati ANTI-FILTER za odds 1.30-1.50
2. Dodati ANTI-FILTER za drop 6-10%
3. Povećati min_confidence na 0.80

PROMJENE U signal_monitor.py:
─────────────────────────────
4. Disable MIXED signal type
5. Za AWAY bets zahtijevaj drop >= 15%

PROMJENE U signals.py:
──────────────────────
6. Dodati filtar za "suspicious middle drops"
7. Log zašto je signal odbijen

===============================================================================
  🎯 OČEKIVANI REZULTAT
===============================================================================

Prije: 51.9% win rate, -4.26% ROI
Poslije (procjena): 
  - Eliminiramo ~40 loših tradeova (Away + 6-10% drop)
  - Ostaje ~40 dobrih tradeova (Home + mali/veliki drop)
  - Očekivani win rate: ~62-65%
  - Očekivani ROI: +8-15%

===============================================================================
"""

# Primjer novog koda za implementaciju:

IMPROVED_ANTI_FILTERS = {
    '1': {  # Domaćin
        'max_closing_odds': 3.00,
        'min_drop_pct': 5.0,
        'banned_odds_ranges': [(1.30, 1.50)],  # NOVO: Izbjegavaj 1.30-1.50
        'banned_drop_ranges': [(6.0, 10.0)],    # NOVO: Skip middle drops
    },
    '2': {  # Gost - STRIKTNIJE!
        'max_closing_odds': 2.00,              # Samo jako favoriti
        'min_drop_pct': 15.0,                  # POVEĆANO s 10% na 15%
        'banned_drop_ranges': [(6.0, 10.0)],
    },
    'X': {  # Remi - bez promjena, radi ok
        'max_closing_odds': 3.50,
        'min_drop_pct': 10.0,
    },
}

# Min confidence za sve signale
MIN_CONFIDENCE_THRESHOLD = 0.80  # POVEĆANO s 0.70

# Disable signal types
DISABLED_SIGNAL_TYPES = ['MIXED']

print(__doc__)
