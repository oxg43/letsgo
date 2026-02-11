"""
Signal Configuration — Tier-based signal rules derived from deep statistical analysis.

╔══════════════════════════════════════════════════════════════════════╗
║  ANALIZA: 1,750 završenih utakmica │ 943,637 redova │ 4 dana       ║
║  PERIOD:  7-10. veljače 2026.                                       ║
║  DATUM KONFIGURACIJE: 2026-02-10                                    ║
╚══════════════════════════════════════════════════════════════════════╝

TIER 1 (STRONG)  — Najviše povjerenje, puni Kelly ¼ ulog
TIER 2 (MEDIUM)  — Dobar signal, umjereni Kelly ulog
TIER 3 (WEAK)    — Slab signal, mali ulozi ili samo praćenje
WATCH  (tier=0)  — Još nije u prozoru 0-30 min, samo praćenje

BASELINE HIT RATES (bez ikakvih filtera):
  Domaćin (1): 42.29%
  Remi    (X): 25.31%
  Gost    (2): 32.40%

DROP % FORMULA:
  drop_pct = (opening_odds - closing_odds) / opening_odds × 100
  Pozitivan drop = kvota je PALA (novac ulazi na taj ishod)
"""

# ═══════════════════════════════════════════════════════════════════
#  TIER 1 — STRONG SIGNALS
# ═══════════════════════════════════════════════════════════════════
#
# Uvjeti: visok ROI, dovoljan N, pozitivan EV, ispravan vremenski prozor.
#
TIER_1_RULES = [
    {
        'id': 'T1_HOME_DROP20',
        'label': 'Domaćin ≥20% drop — MEGA signal',
        'outcome': '1',
        'min_drop_pct': 20.0,
        'odds_range': (1.01, 3.00),      # Profitabilno samo ispod 3.00
        'max_minutes_to_ko': 30,          # Samo zadnjih 30 min
        'min_snapshots': 3,
        'kelly_fraction': 0.25,           # Kelly ¼
        'max_stake_pct': 5.0,             # Max 5% bankrolla
        'evidence': {
            'n': 28, 'hit_rate': 0.357, 'roi': 1.127,
            'ev': 1.091, 'kelly_full_pct': 5.68,
            'note': 'Najbolji ROI (+112.7%) ali mali uzorak N=28',
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
        'kelly_fraction': 0.25,           # Kelly ¼
        'max_stake_pct': 5.0,
        'evidence': {
            'n': 108, 'hit_rate': 0.444, 'roi': 0.768,
            'ev': 0.311, 'kelly_full_pct': 6.45,
            'note': 'Najsigurniji obrazac — balans ROI/volumen',
        },
    },
]

# ═══════════════════════════════════════════════════════════════════
#  TIER 2 — MEDIUM SIGNALS
# ═══════════════════════════════════════════════════════════════════
#
# Uvjeti: solidan ROI, veći N, umjereni EV.
#
TIER_2_RULES = [
    {
        'id': 'T2_HOME_DROP5',
        'label': 'Domaćin ≥5% drop — solidan signal',
        'outcome': '1',
        'min_drop_pct': 5.0,
        'odds_range': (1.01, 3.00),
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.20,           # Kelly ⅕
        'max_stake_pct': 3.0,
        'evidence': {
            'n': 245, 'hit_rate': 0.461, 'roi': 0.521,
            'ev': 0.246, 'kelly_full_pct': 5.67,
            'note': 'Najveći uzorak za domaćina, stabilan ROI',
        },
    },
    {
        'id': 'T2_DRAW_DROP15',
        'label': 'Remi ≥15% drop — jak remi signal',
        'outcome': 'X',
        'min_drop_pct': 15.0,
        'odds_range': (2.20, 3.50),       # Samo srednje kvote profitabilne
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.20,
        'max_stake_pct': 3.0,
        'evidence': {
            'n': 18, 'hit_rate': 0.333, 'roi': 1.080,
            'ev': 1.357, 'kelly_full_pct': 5.15,
            'note': 'Visok ROI (+108%) ali mali N=18, oprez',
        },
    },
    {
        'id': 'T2_DRAW_DROP10',
        'label': 'Remi ≥10% drop — srednji remi signal',
        'outcome': 'X',
        'min_drop_pct': 10.0,
        'odds_range': (2.20, 3.00),       # Strogo: samo 2.20-3.00 profitabilno
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.15,           # Kelly ≈⅙ (konzervativno za remi)
        'max_stake_pct': 2.5,
        'evidence': {
            'n': 48, 'hit_rate': 0.271, 'roi': 0.418,
            'ev': 0.081, 'kelly_full_pct': 2.47,
            'note': 'Remi srednje kvote — jedini profitabilni segment za X',
        },
    },
]

# ═══════════════════════════════════════════════════════════════════
#  TIER 3 — WEAK SIGNALS (oprez!)
# ═══════════════════════════════════════════════════════════════════
#
# Uvjeti: signali postoje ali male uzorke ili niži ROI.
# Koristiti samo kao dodatak, nikad kao primarni signal.
#
TIER_3_RULES = [
    {
        'id': 'T3_AWAY_FAVORITE',
        'label': 'Gost jaki favorit ≥10% drop',
        'outcome': '2',
        'min_drop_pct': 10.0,
        'odds_range': (1.01, 1.60),       # SAMO jaki favoriti za goste!
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.10,           # Kelly 1/10
        'max_stake_pct': 1.5,
        'evidence': {
            'n': 8, 'hit_rate': 1.00, 'roi': 0.388,
            'ev': 0.147, 'kelly_full_pct': 1.38,
            'note': '⚠ IZUZETNO MALI UZORAK N=8! 100% hit ali nesiguran',
        },
    },
    {
        'id': 'T3_AWAY_DROP15',
        'label': 'Gost ≥15% drop — oprezno',
        'outcome': '2',
        'min_drop_pct': 15.0,
        'odds_range': (1.01, 3.00),       # Širi raspon jer je drop velik
        'max_minutes_to_ko': 30,
        'min_snapshots': 3,
        'kelly_fraction': 0.10,
        'max_stake_pct': 1.5,
        'evidence': {
            'n': 81, 'hit_rate': 0.296, 'roi': 0.499,
            'ev': 0.490, 'kelly_full_pct': 3.07,
            'note': 'Gost najslabiji signal — koristiti s oprezom',
        },
    },
]

# ═══════════════════════════════════════════════════════════════════
#  COMBINED RULES (priority order: T1 → T2 → T3)
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
    3: 'TIER 3 (WEAK)',
}

TIER_EMOJIS = {
    0: '👀',
    1: '🟢',
    2: '🟡',
    3: '🟠',
}

# ═══════════════════════════════════════════════════════════════════
#  ANTI-FILTERI — Kada NE staviti signal
# ═══════════════════════════════════════════════════════════════════
#
# Blokiraju signal čak i ako drop % zadovoljava prag.
# Svaki ishod ima svoje specifične anti-filtere.
#
ANTI_FILTERS = {
    '1': {  # Domaćin
        'max_closing_odds': 3.00,    # Autsajderi: ROI = -35.3%
        'min_drop_pct': 5.0,         # Ispod 5% nema dovoljno signala
    },
    'X': {  # Remi
        'max_closing_odds': 3.50,    # Visoke kvote remija: ROI = -31.6%
        'min_drop_pct': 10.0,        # Ispod 10%: EV negativan
    },
    '2': {  # Gost
        'max_closing_odds': 3.00,    # Ne-favoriti uglavnom neprofitabilni
        'min_drop_pct': 10.0,        # Min 10% za bilo kakav signal
    },
    'global': {
        'max_minutes_to_ko': 30,     # ✅ Jedini prozor s pozitivnim ROI
        'min_snapshots': 3,          # Minimum podataka za pouzdanost
    },
}

# Liga blacklist — dodati nepouzdane lige ovdje
EXCLUDED_LEAGUES = set()
# Primjer: EXCLUDED_LEAGUES = {'Friendlies', 'World - Club Friendly'}

# ═══════════════════════════════════════════════════════════════════
#  STAKING PLAN — Kelly Criterion + Upravljanje bankrollom
# ═══════════════════════════════════════════════════════════════════

STAKING = {
    # Način uloga: 'flat', 'kelly_quarter', 'kelly_half', 'kelly_custom'
    'mode': 'kelly_quarter',

    # Početni bankroll (€)
    'initial_bankroll': 100.0,

    # Za flat mode: fiksni % bankrolla po okladi
    'flat_stake_pct': 2.0,

    # Kelly frakcije po tieru (override individualnih pravila)
    'tier_kelly': {
        1: 0.25,    # Kelly ¼ — TIER 1
        2: 0.20,    # Kelly ⅕ — TIER 2
        3: 0.10,    # Kelly 1/10 — TIER 3
    },

    # ── Limiti rizika ──
    'max_stake_single_pct': 5.0,       # Max 5% bankrolla na jednu okladu
    'max_daily_exposure_pct': 20.0,    # Max 20% bankrolla dnevno izloženo
    'max_weekly_exposure_pct': 40.0,   # Max 40% bankrolla tjedno
    'max_concurrent_bets': 5,          # Max 5 otvorenih oklada istovremeno
    'max_bets_per_match': 1,           # Samo 1 oklada po utakmici

    # ── Stop-loss pravila ──
    'stop_loss_daily_pct': 15.0,       # Zaustavi ako padneš 15% danas
    'stop_loss_weekly_pct': 25.0,      # Zaustavi ako padneš 25% ovaj tjedan
    'stop_loss_total_pct': 40.0,       # Zaustavi ako bankroll padne 40% od vrha
}

# ═══════════════════════════════════════════════════════════════════
#  MONITORING — Praćenje performansi i rekalibracija
# ═══════════════════════════════════════════════════════════════════

MONITORING = {
    # Minimalni broj oklada za evaluaciju pojedinog tiera
    'min_bets_for_evaluation': 30,

    # Rekalibriraj parametre nakon N oklada
    'recalibrate_after_bets': 100,

    # Alert ako ROI tiera padne ispod ovog praga
    'min_roi_threshold_pct': -10.0,

    # Alert ako hit rate padne više od Xpp ispod očekivanog
    'max_hit_rate_deviation_pp': 5.0,

    # Prozor evaluacije (dani)
    'performance_window_days': 14,

    # Min uzorak za statističku značajnost
    'min_sample_significant': 50,

    # Intervali za weekly report
    'report_interval_days': 7,
}

# ═══════════════════════════════════════════════════════════════════
#  REFERENTNI PODACI — iz analize (za dokumentaciju i monitoring)
# ═══════════════════════════════════════════════════════════════════

# Vremenski prozori — performance
TIME_WINDOWS = {
    '0-30':    {'n': 90,  'hit_rate': 0.400, 'roi': 0.037},   # ✅ JEDINI +
    '30-60':   {'n': 34,  'hit_rate': 0.294, 'roi': -0.292},
    '60-120':  {'n': 28,  'hit_rate': 0.286, 'roi': -0.394},
    '120-240': {'n': 74,  'hit_rate': 0.230, 'roi': -0.389},
    '240-500': {'n': 138, 'hit_rate': 0.254, 'roi': -0.329},
    '500+':    {'n': 133, 'hit_rate': 0.308, 'roi': -0.092},
}

# Segmenti kvota
ODDS_SEGMENTS = {
    'jaki_favoriti':      (1.01, 1.60),
    'umjereni_favoriti':  (1.60, 2.20),
    'srednje_kvote':      (2.20, 3.00),
    'autsajderi':         (3.00, 999.0),
}

# CLV statistika
CLV_STATS = {
    'positive': {'pct': 0.470, 'hit_rate': 0.347, 'roi': -0.015},
    'negative': {'pct': 0.530, 'hit_rate': 0.244, 'roi': -0.375},
}

# Expected Value po drop binu (domaćin)
EV_BY_DROP_BIN = {
    '1': {
        '<0%':    0.199, '0-5%':   0.246, '5-10%':  0.311,
        '10-15%': 0.621, '15-20%': 0.439, '20-25%': 1.091, '25%+': 0.744,
    },
    'X': {
        '<0%':    0.019, '0-5%':  -0.007, '5-10%': -0.208,
        '10-15%': 0.081, '15-20%': 1.357,
    },
    '2': {
        '<0%':    0.360, '0-5%':   0.319, '5-10%': -0.027,
        '10-15%': 0.147, '15-20%': 0.490, '20-25%': 0.922,
    },
}

# Monte Carlo simulacija referenca (28 oklada, best pattern)
MONTE_CARLO = {
    'simulations': 1000,
    'best_pattern_n': 28,
    'start_bank': 100,
    'median_final': 417,
    'p5_worst': 122,
    'p95_best': 1423,
    'median_max_drawdown_pct': 29.6,
    'bankrupt_risk_pct': 0.0,
    'profitability_pct': 96.0,
}

# Baseline hit rates (sve utakmice bez filtera)
BASELINE_HIT_RATES = {
    '1': 0.4229,
    'X': 0.2531,
    '2': 0.3240,
}

# Segment-specific hit rates (iz analize, filtriran drop ≥5%+)
# Koriste se za Kelly izračun umjesto prosječnog tier hit rate-a
SEGMENT_HIT_RATES = {
    '1': {  # Domaćin
        'jaki_favoriti':      0.70,    # ROI=+7.0%, odds ~1.35, procjena iz ROI
        'umjereni_favoriti':  0.727,   # Izravno iz analize — HR=72.7%, ROI=+32.2%
        'srednje_kvote':      0.444,   # Izravno iz analize — HR=44.4%, ROI=+14.3%
    },
    'X': {  # Remi
        'srednje_kvote':      0.539,   # N=13, HR=53.9%, ROI=+50.0% (mali uzorak!)
    },
    '2': {  # Gost
        'jaki_favoriti':      0.80,    # Prava HR=100% (N=8), konzerv. korigirano na 80%
    },
}


def get_odds_segment(odds: float) -> str:
    """Return the odds segment name for given odds value."""
    for name, (lo, hi) in ODDS_SEGMENTS.items():
        if lo <= odds < hi:
            return name
    return 'autsajderi'


def get_tier_name(tier: int) -> str:
    """Readable tier name."""
    return TIER_NAMES.get(tier, f'TIER {tier}')


def get_tier_emoji(tier: int) -> str:
    """Emoji for tier."""
    return TIER_EMOJIS.get(tier, '❓')
