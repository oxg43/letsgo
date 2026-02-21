"""
═══════════════════════════════════════════════════════════════════════════════
  PROFIT MODE CONFIGURATION
  
  Striktna konfiguracija bazirana na analizi 81 paper tradea.
  Samo PROFITABILNI obrasci - sve ostalo isključeno.
  
  Očekivani rezultat: 62-65% win rate, +8-15% ROI
═══════════════════════════════════════════════════════════════════════════════

AKTIVNI SIGNALI (dokazano profitabilni):
  ✅ HOME bets (tip 1) - +5.9% ROI
  ✅ DRAW bets (tip X) - +140% ROI (mali uzorak)
  ✅ Drop <6% (stabilni favoriti) - +29.7% ROI
  ✅ Drop >10% (pravi steam moves) - +7-12% ROI
  ✅ Heavy favorites odds 1.00-1.30 - +19.5% edge
  ✅ Confidence 80%+ - +13.7% ROI

ISKLJUČENI SIGNALI (gubitnički):
  ❌ AWAY bets (tip 2) - DISABLE (-17.5% ROI)
  ❌ Drop 6-10% - SKIP (-34.5% ROI) 
  ❌ Odds 1.30-1.50 - SKIP (-29% edge)
  ❌ Confidence <80% - SKIP (-17% do -43% ROI)
  ❌ MIXED signals - DISABLE (-24% ROI)

"""

# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — STRONG SIGNALS (SAMO OVI!)
# ═══════════════════════════════════════════════════════════════════

TIER_1_RULES = [
    {
        'id': 'T1_HOME_DROP20',
        'label': 'Domaćin ≥20% drop — MEGA signal',
        'outcome': '1',
        'min_drop_pct': 20.0,
        'odds_range': (1.01, 3.00),
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.25,
        'max_stake_pct': 5.0,
        'evidence': {
            'n': 28, 'hit_rate': 0.357, 'roi': 1.127,
            'note': 'Najbolji ROI (+112.7%)',
        },
    },
    {
        'id': 'T1_HOME_DROP10',
        'label': 'Domaćin ≥10% drop — STRONG signal',
        'outcome': '1',
        'min_drop_pct': 10.0,
        'odds_range': (1.01, 3.00),
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.25,
        'max_stake_pct': 5.0,
        'evidence': {
            'n': 108, 'hit_rate': 0.444, 'roi': 0.768,
            'note': 'Najsigurniji obrazac — balans ROI/volumen',
        },
    },
    {
        'id': 'T1_HOME_SMALL_DROP',
        'label': 'Domaćin favorit <6% drop — stabilan',
        'outcome': '1',
        'min_drop_pct': 3.0,
        'max_drop_pct': 6.0,  # NOVO: Max drop 6%
        'odds_range': (1.01, 1.80),  # Samo favoriti
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.20,
        'max_stake_pct': 4.0,
        'evidence': {
            'n': 19, 'hit_rate': 0.684, 'roi': 0.297,
            'note': 'NOVI: Mali dropovi na favoritima +29.7% ROI',
        },
    },
]

# ═══════════════════════════════════════════════════════════════════
#  TIER 2 — MEDIUM SIGNALS (samo remi)
# ═══════════════════════════════════════════════════════════════════

TIER_2_RULES = [
    {
        'id': 'T2_DRAW_DROP15',
        'label': 'Remi ≥15% drop — jak remi signal',
        'outcome': 'X',
        'min_drop_pct': 15.0,
        'odds_range': (2.20, 3.50),
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.20,
        'max_stake_pct': 3.0,
        'evidence': {
            'n': 18, 'hit_rate': 0.333, 'roi': 1.080,
            'note': 'Visok ROI (+108%)',
        },
    },
    {
        'id': 'T2_DRAW_DROP10',
        'label': 'Remi ≥10% drop — srednji remi signal',
        'outcome': 'X',
        'min_drop_pct': 10.0,
        'odds_range': (2.20, 3.00),
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.15,
        'max_stake_pct': 2.5,
        'evidence': {
            'n': 48, 'hit_rate': 0.271, 'roi': 0.418,
            'note': 'Remi srednje kvote — jedini profitabilni segment za X',
        },
    },
]

# ═══════════════════════════════════════════════════════════════════
#  TIER 3 — PRAZNO! (Away bets ISKLJUČENI jer gube -17.5% ROI)
# ═══════════════════════════════════════════════════════════════════

TIER_3_RULES = []  # AWAY BETS DISABLED!

# ═══════════════════════════════════════════════════════════════════
#  COMBINED RULES
# ═══════════════════════════════════════════════════════════════════

ALL_TIER_RULES = (
    [(1, rule) for rule in TIER_1_RULES] +
    [(2, rule) for rule in TIER_2_RULES] +
    [(3, rule) for rule in TIER_3_RULES]
)

TIER_NAMES = {
    0: 'WATCH',
    1: 'TIER 1 (STRONG)',
    2: 'TIER 2 (MEDIUM)',
    3: 'TIER 3 (DISABLED)',
}

TIER_EMOJIS = {
    0: '👀',
    1: '🟢',
    2: '🟡',
    3: '🔴',  # Disabled
}

# ═══════════════════════════════════════════════════════════════════
#  STRIKTNI ANTI-FILTERI (PROFIT MODE)
# ═══════════════════════════════════════════════════════════════════

ANTI_FILTERS = {
    '1': {  # Domaćin
        'max_closing_odds': 3.00,
        'min_drop_pct': 3.0,  # Min 3%
        'banned_odds_ranges': [(1.30, 1.50)],  # -29% edge
        'banned_drop_ranges': [(6.0, 10.0)],   # -34.5% ROI
    },
    'X': {  # Remi - OK
        'max_closing_odds': 3.50,
        'min_drop_pct': 10.0,
    },
    '2': {  # Gost - POTPUNO ISKLJUČEN
        'max_closing_odds': 0.0,  # Efektivno disable
        'min_drop_pct': 999.0,    # Nemoguće zadovoljiti
    },
    'global': {
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'min_confidence': 0.80,  # 80%+ obavezno
    },
}

# Liga blacklist
EXCLUDED_LEAGUES = set()

# ═══════════════════════════════════════════════════════════════════
#  STAKING PLAN — Konzervativni za profit
# ═══════════════════════════════════════════════════════════════════

STAKING = {
    'mode': 'kelly_quarter',
    'initial_bankroll': 100.0,
    'flat_stake_pct': 2.0,
    
    'tier_kelly': {
        1: 0.25,  # Kelly ¼ — TIER 1
        2: 0.15,  # Kelly ≈⅙ — TIER 2 (konzervativnije)
        3: 0.00,  # DISABLED
    },
    
    # Striktni limiti
    'max_stake_single_pct': 5.0,
    'max_daily_exposure_pct': 15.0,   # Smanjeno s 20%
    'max_weekly_exposure_pct': 30.0,  # Smanjeno s 40%
    'max_concurrent_bets': 3,         # Smanjeno s 5
    'max_bets_per_match': 1,
    
    # Stop-loss
    'stop_loss_daily_pct': 10.0,      # Strože: 10% dnevno
    'stop_loss_weekly_pct': 20.0,     # Strože: 20% tjedno
    'stop_loss_total_pct': 30.0,      # Strože: 30% ukupno
}

# ═══════════════════════════════════════════════════════════════════
#  WATCH RULES (praćenje prije prozora)
# ═══════════════════════════════════════════════════════════════════

WATCH_RULES = [
    {
        'id': 'W_HOME_STEAM',
        'outcome': '1',
        'min_drop_pct': 8.0,
        'odds_range': (1.01, 3.00),
        'min_snapshots': 3,
    },
    {
        'id': 'W_DRAW_STEAM',
        'outcome': 'X',
        'min_drop_pct': 10.0,
        'odds_range': (2.20, 3.50),
        'min_snapshots': 3,
    },
]

# ═══════════════════════════════════════════════════════════════════
#  SEGMENTI KVOTA — potrebno za staking.py
# ═══════════════════════════════════════════════════════════════════

ODDS_SEGMENTS = {
    'jaki_favoriti':      (1.01, 1.60),
    'umjereni_favoriti':  (1.60, 2.20),
    'srednje_kvote':      (2.20, 3.00),
    'autsajderi':         (3.00, 999.0),
}

# Baseline hit rates (sve utakmice bez filtera)
BASELINE_HIT_RATES = {
    '1': 0.4229,
    'X': 0.2531,
    '2': 0.3240,
}

# Segment-specific hit rates (iz analize, filtriran drop ≥5%+)
SEGMENT_HIT_RATES = {
    '1': {
        'jaki_favoriti':      0.70,
        'umjereni_favoriti':  0.727,
        'srednje_kvote':      0.444,
    },
    'X': {
        'srednje_kvote':      0.539,
    },
    '2': {
        'jaki_favoriti':      0.80,
    },
}

# ═══════════════════════════════════════════════════════════════════
#  HELPER FUNKCIJE
# ═══════════════════════════════════════════════════════════════════

def get_odds_segment(odds: float) -> str:
    """Return the odds segment name for given odds value."""
    for name, (lo, hi) in ODDS_SEGMENTS.items():
        if lo <= odds < hi:
            return name
    return 'autsajderi'

def get_tier_name(tier: int) -> str:
    return TIER_NAMES.get(tier, 'UNKNOWN')

def get_tier_emoji(tier: int) -> str:
    return TIER_EMOJIS.get(tier, '❓')

def is_drop_in_banned_range(drop_pct: float, outcome: str) -> bool:
    """Check if drop is in banned range for this outcome."""
    banned = ANTI_FILTERS.get(outcome, {}).get('banned_drop_ranges', [])
    for lo, hi in banned:
        if lo <= drop_pct < hi:
            return True
    return False

def is_odds_in_banned_range(odds: float, outcome: str) -> bool:
    """Check if odds is in banned range for this outcome."""
    banned = ANTI_FILTERS.get(outcome, {}).get('banned_odds_ranges', [])
    for lo, hi in banned:
        if lo <= odds < hi:
            return True
    return False

# ═══════════════════════════════════════════════════════════════════
#  SUMMARY
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 70)
    print("  PROFIT MODE - Aktivna pravila")
    print("=" * 70)
    print()
    print(f"  TIER 1 (HOME): {len(TIER_1_RULES)} pravila")
    print(f"  TIER 2 (DRAW): {len(TIER_2_RULES)} pravila")
    print(f"  TIER 3 (AWAY): {len(TIER_3_RULES)} pravila (DISABLED!)")
    print()
    print("  BLOKIRANO:")
    print("    ❌ Away bets (tip 2)")
    print("    ❌ Drop 6-10%")
    print("    ❌ Odds 1.30-1.50")
    print("    ❌ Confidence <80%")
    print()
    print("  AKTIVNO:")
    for tier, rule in ALL_TIER_RULES:
        print(f"    ✅ TIER {tier}: {rule['id']} ({rule['outcome']})")
