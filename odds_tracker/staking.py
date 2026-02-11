"""
Staking Module — Kelly Criterion bankroll management and stake calculation.

Koristi rezultate analize 1,750 utakmica za optimalno dimenzioniranje uloga.

Kelly Formula:  f* = (b*p - q) / b
  b = odds - 1 (decimal odds minus 1)
  p = procijenjeni hit rate
  q = 1 - p

Kelly ¼ = f* / 4  (konzervativna verzija, preporučena)

Monte Carlo potvrda (1000 sim, 28 oklada, Kelly ¼):
  - 0% rizik bankrota
  - 96% simulacija u plusu
  - Medijan završnog banka: 417€ (od 100€)
"""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from odds_tracker.signal_config import STAKING, BASELINE_HIT_RATES, SEGMENT_HIT_RATES, ODDS_SEGMENTS


# ─── Paths ──────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).parent.parent
BANKROLL_STATE_PATH = _BASE_DIR / "odds_data" / "bankroll_state.json"
BANKROLL_LOG_PATH = _BASE_DIR / "odds_data" / "bankroll_log.csv"

BANKROLL_LOG_COLUMNS = [
    'timestamp', 'action',       # 'bet_placed', 'bet_resolved', 'deposit', 'withdrawal'
    'match_key', 'tier', 'bet', 'odds',
    'stake', 'stake_pct',
    'result',                     # 'win', 'loss', 'void', 'pending'
    'profit',
    'bankroll_before', 'bankroll_after',
    'daily_exposure', 'reason',
]


# ═══════════════════════════════════════════════════════════════════
#  KELLY CRITERION
# ═══════════════════════════════════════════════════════════════════

def kelly_full(hit_rate: float, odds: float) -> float:
    """
    Full Kelly stake fraction.

    Args:
        hit_rate: estimated probability of winning (0-1)
        odds: decimal odds (e.g., 2.50)

    Returns:
        Optimal fraction of bankroll to stake (0-1)
    """
    if odds <= 1.0 or hit_rate <= 0 or hit_rate >= 1:
        return 0.0

    b = odds - 1.0
    p = hit_rate
    q = 1.0 - p

    f = (b * p - q) / b

    return max(0.0, f)


def kelly_fraction(hit_rate: float, odds: float, fraction: float = 0.25) -> float:
    """
    Fractional Kelly (default Kelly ¼).
    Returns fraction of bankroll to stake.
    """
    full = kelly_full(hit_rate, odds)
    return full * fraction


def _get_segment_hit_rate(outcome: str, odds: float, fallback: float) -> float:
    """
    Get segment-specific hit rate for Kelly calculation.

    Uses per-segment data from the 1,750-match analysis instead of
    the overall tier average. Falls back to the rule's evidence hit rate
    if no segment data exists.

    This is critical: at odds 1.85 (umjereni favorit), the actual
    Home HR is 72.7%, not the tier-average 44.4%.
    """
    # Find which odds segment this falls into
    segment = None
    for name, (lo, hi) in ODDS_SEGMENTS.items():
        if lo <= odds < hi:
            segment = name
            break

    if segment and outcome in SEGMENT_HIT_RATES:
        seg_hr = SEGMENT_HIT_RATES[outcome].get(segment)
        if seg_hr is not None:
            return seg_hr

    return fallback


def calculate_stake(
    tier: int,
    odds: float,
    rule: dict,
    bankroll: float,
    mode: str = None,
) -> dict:
    """
    Calculate stake amount for a signal.

    Args:
        tier: signal tier (1, 2, 3)
        odds: decimal odds
        rule: tier rule dict from signal_config
        bankroll: current bankroll in €
        mode: override staking mode ('flat', 'kelly_quarter', 'kelly_half')

    Returns:
        dict with: stake_amount, stake_pct, kelly_full_pct, kelly_used_pct, method
    """
    mode = mode or STAKING['mode']

    # Estimated hit rate: prefer segment-specific over tier average
    outcome = rule.get('outcome', '1')
    tier_hit_rate = rule.get('evidence', {}).get('hit_rate', BASELINE_HIT_RATES.get(outcome, 0.33))
    hit_rate = _get_segment_hit_rate(outcome, odds, tier_hit_rate)

    # Kelly calculation
    kf = kelly_full(hit_rate, odds)

    if mode == 'flat':
        stake_pct = STAKING['flat_stake_pct']
    elif mode == 'kelly_quarter':
        stake_pct = kf * 0.25 * 100  # as percentage
    elif mode == 'kelly_half':
        stake_pct = kf * 0.50 * 100
    elif mode == 'kelly_custom':
        fraction = rule.get('kelly_fraction', STAKING['tier_kelly'].get(tier, 0.25))
        stake_pct = kf * fraction * 100
    else:
        stake_pct = kf * 0.25 * 100

    # Apply caps
    max_rule = rule.get('max_stake_pct', 5.0)
    max_global = STAKING['max_stake_single_pct']
    stake_pct = min(stake_pct, max_rule, max_global)
    stake_pct = max(stake_pct, 0.0)

    stake_amount = round(bankroll * stake_pct / 100, 2)

    return {
        'stake_amount': stake_amount,
        'stake_pct': round(stake_pct, 2),
        'kelly_full_pct': round(kf * 100, 2),
        'kelly_used_pct': round(stake_pct, 2),
        'method': mode,
        'hit_rate_used': hit_rate,
    }


# ═══════════════════════════════════════════════════════════════════
#  BANKROLL MANAGER
# ═══════════════════════════════════════════════════════════════════

class BankrollManager:
    """
    Tracks bankroll state, enforces limits, and logs all transactions.

    Persistent state saved to bankroll_state.json.
    All bets logged to bankroll_log.csv.
    """

    def __init__(self):
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load persistent bankroll state."""
        if BANKROLL_STATE_PATH.exists():
            try:
                with open(BANKROLL_STATE_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        # Initialize fresh state
        return {
            'bankroll': STAKING['initial_bankroll'],
            'peak_bankroll': STAKING['initial_bankroll'],
            'initial_bankroll': STAKING['initial_bankroll'],
            'total_bets': 0,
            'total_wins': 0,
            'total_losses': 0,
            'total_profit': 0.0,
            'daily_exposure': 0.0,
            'daily_bets': 0,
            'daily_date': datetime.now().strftime('%Y-%m-%d'),
            'weekly_exposure': 0.0,
            'weekly_start_date': datetime.now().strftime('%Y-%m-%d'),
            'open_bets': [],             # list of pending bets
            'stopped': False,            # stop-loss triggered?
            'stop_reason': '',
            'tier_stats': {              # per-tier tracking
                '1': {'bets': 0, 'wins': 0, 'losses': 0, 'profit': 0.0},
                '2': {'bets': 0, 'wins': 0, 'losses': 0, 'profit': 0.0},
                '3': {'bets': 0, 'wins': 0, 'losses': 0, 'profit': 0.0},
            },
        }

    def _save_state(self):
        """Persist bankroll state."""
        BANKROLL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BANKROLL_STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def _reset_daily(self):
        """Reset daily counters if date changed."""
        today = datetime.now().strftime('%Y-%m-%d')
        if self.state['daily_date'] != today:
            self.state['daily_exposure'] = 0.0
            self.state['daily_bets'] = 0
            self.state['daily_date'] = today

            # Check weekly reset (every Monday)
            try:
                week_start = datetime.strptime(self.state['weekly_start_date'], '%Y-%m-%d')
                if (datetime.now() - week_start).days >= 7:
                    self.state['weekly_exposure'] = 0.0
                    self.state['weekly_start_date'] = today
            except (ValueError, KeyError):
                self.state['weekly_exposure'] = 0.0
                self.state['weekly_start_date'] = today

    @property
    def bankroll(self) -> float:
        return self.state['bankroll']

    @property
    def is_stopped(self) -> bool:
        return self.state.get('stopped', False)

    # ── Check Limits ──

    def check_stop_loss(self) -> tuple[bool, str]:
        """
        Check if any stop-loss condition is triggered.
        Returns (is_stopped, reason).
        """
        self._reset_daily()
        bk = self.state['bankroll']
        peak = self.state['peak_bankroll']
        initial = self.state['initial_bankroll']

        # Daily stop-loss
        daily_loss = self.state.get('daily_exposure', 0) - bk
        # Simplified: check if daily losses exceed threshold
        today = datetime.now().strftime('%Y-%m-%d')
        daily_profit = self._get_daily_profit(today)
        if daily_profit < 0 and abs(daily_profit) >= bk * STAKING['stop_loss_daily_pct'] / 100:
            return True, f"Dnevni stop-loss: izgubljeno {abs(daily_profit):.2f}€ (>{STAKING['stop_loss_daily_pct']}% bankrolla)"

        # Total stop-loss (from peak)
        drawdown_pct = (peak - bk) / peak * 100 if peak > 0 else 0
        if drawdown_pct >= STAKING['stop_loss_total_pct']:
            return True, f"Ukupni stop-loss: drawdown {drawdown_pct:.1f}% od vrha (>{STAKING['stop_loss_total_pct']}%)"

        return False, ''

    def _get_daily_profit(self, date_str: str) -> float:
        """Get total profit for a specific day."""
        tier_stats = self.state.get('tier_stats', {})
        # This is approximate — for accurate tracking, use the log
        return sum(t.get('profit', 0) for t in tier_stats.values())

    def can_place_bet(self, stake_amount: float, match_key: str) -> tuple[bool, str]:
        """
        Check if a bet can be placed given current limits.
        Returns (can_place, reason_if_not).
        """
        self._reset_daily()

        # Stop-loss check
        is_stopped, reason = self.check_stop_loss()
        if is_stopped:
            self.state['stopped'] = True
            self.state['stop_reason'] = reason
            self._save_state()
            return False, f"⛔ STOP-LOSS: {reason}"

        if self.state.get('stopped'):
            return False, f"⛔ Sustav zaustavljen: {self.state.get('stop_reason', 'stop-loss')}"

        bk = self.state['bankroll']

        # Bankroll check
        if stake_amount > bk:
            return False, f"Nedovoljno bankrolla: {stake_amount:.2f}€ > {bk:.2f}€"

        # Max concurrent bets
        open_count = len(self.state.get('open_bets', []))
        if open_count >= STAKING['max_concurrent_bets']:
            return False, f"Max otvorenih oklada ({STAKING['max_concurrent_bets']})"

        # Daily exposure
        daily_exp = self.state.get('daily_exposure', 0)
        max_daily = bk * STAKING['max_daily_exposure_pct'] / 100
        if daily_exp + stake_amount > max_daily:
            return False, f"Dnevni limit: {daily_exp:.2f}€ + {stake_amount:.2f}€ > {max_daily:.2f}€"

        # Weekly exposure
        weekly_exp = self.state.get('weekly_exposure', 0)
        max_weekly = bk * STAKING['max_weekly_exposure_pct'] / 100
        if weekly_exp + stake_amount > max_weekly:
            return False, f"Tjedni limit: {weekly_exp:.2f}€ + {stake_amount:.2f}€ > {max_weekly:.2f}€"

        # Max 1 bet per match
        for ob in self.state.get('open_bets', []):
            if ob.get('match_key') == match_key:
                return False, f"Već postoji oklada na: {match_key}"

        return True, ''

    # ── Place / Resolve Bets ──

    def place_bet(self, signal: dict, stake_info: dict) -> dict:
        """
        Record a bet placement.

        Args:
            signal: signal dict with tier, bet, odds, home, away, etc.
            stake_info: dict from calculate_stake()

        Returns:
            bet record dict
        """
        self._reset_daily()

        match_key = f"{signal.get('home', '')}|{signal.get('away', '')}"
        amount = stake_info['stake_amount']
        tier = signal.get('tier', 0)

        bet_record = {
            'id': f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{match_key}",
            'timestamp': datetime.now().isoformat(),
            'match_key': match_key,
            'home': signal.get('home', ''),
            'away': signal.get('away', ''),
            'kick_off': signal.get('kick_off', ''),
            'tier': tier,
            'tier_rule': signal.get('tier_rule', ''),
            'bet': signal.get('bet', ''),
            'odds': signal.get('latest_odds', 0),
            'stake': amount,
            'stake_pct': stake_info['stake_pct'],
            'result': 'pending',
        }

        # Update state
        self.state['bankroll'] -= amount
        self.state['daily_exposure'] = self.state.get('daily_exposure', 0) + amount
        self.state['weekly_exposure'] = self.state.get('weekly_exposure', 0) + amount
        self.state['daily_bets'] = self.state.get('daily_bets', 0) + 1
        self.state['total_bets'] += 1

        if 'open_bets' not in self.state:
            self.state['open_bets'] = []
        self.state['open_bets'].append(bet_record)

        # Update tier stats
        tier_key = str(tier)
        if tier_key not in self.state['tier_stats']:
            self.state['tier_stats'][tier_key] = {'bets': 0, 'wins': 0, 'losses': 0, 'profit': 0.0}
        self.state['tier_stats'][tier_key]['bets'] += 1

        self._save_state()
        self._log_action('bet_placed', bet_record, amount)

        return bet_record

    def resolve_bet(self, match_key: str, result: str, score: str = ''):
        """
        Resolve an open bet.

        Args:
            match_key: "Home|Away"
            result: 'win', 'loss', 'void'
            score: final score string
        """
        open_bets = self.state.get('open_bets', [])
        resolved = None

        for i, bet in enumerate(open_bets):
            if bet.get('match_key') == match_key:
                resolved = open_bets.pop(i)
                break

        if not resolved:
            return None

        tier_key = str(resolved['tier'])
        odds = resolved.get('odds', 0)
        stake = resolved.get('stake', 0)

        if result == 'win':
            profit = round(stake * (odds - 1), 2)
            self.state['total_wins'] += 1
            self.state['tier_stats'][tier_key]['wins'] += 1
        elif result == 'loss':
            profit = -stake
            self.state['total_losses'] += 1
            self.state['tier_stats'][tier_key]['losses'] += 1
        else:  # void
            profit = 0.0

        self.state['bankroll'] += stake + profit  # return stake + profit
        self.state['total_profit'] += profit
        self.state['tier_stats'][tier_key]['profit'] += profit

        # Update peak
        if self.state['bankroll'] > self.state['peak_bankroll']:
            self.state['peak_bankroll'] = self.state['bankroll']

        resolved['result'] = result
        resolved['profit'] = profit
        resolved['resolved_at'] = datetime.now().isoformat()
        resolved['score'] = score

        self._save_state()
        self._log_action('bet_resolved', resolved, profit)

        return resolved

    def _log_action(self, action: str, record: dict, amount: float):
        """Append to bankroll_log.csv."""
        BANKROLL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_header = not BANKROLL_LOG_PATH.exists()

        with open(BANKROLL_LOG_PATH, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=BANKROLL_LOG_COLUMNS, delimiter=';')
            if write_header:
                writer.writeheader()
            writer.writerow({
                'timestamp': record.get('timestamp', datetime.now().isoformat()),
                'action': action,
                'match_key': record.get('match_key', ''),
                'tier': record.get('tier', ''),
                'bet': record.get('bet', ''),
                'odds': record.get('odds', ''),
                'stake': record.get('stake', ''),
                'stake_pct': record.get('stake_pct', ''),
                'result': record.get('result', ''),
                'profit': record.get('profit', amount),
                'bankroll_before': '',
                'bankroll_after': self.state['bankroll'],
                'daily_exposure': self.state.get('daily_exposure', 0),
                'reason': record.get('tier_rule', ''),
            })

    # ── Status ──

    def get_status(self) -> dict:
        """Return current bankroll status summary."""
        self._reset_daily()
        bk = self.state['bankroll']
        initial = self.state['initial_bankroll']
        peak = self.state['peak_bankroll']

        return {
            'bankroll': bk,
            'initial': initial,
            'peak': peak,
            'total_profit': self.state['total_profit'],
            'total_profit_pct': (bk - initial) / initial * 100 if initial > 0 else 0,
            'total_bets': self.state['total_bets'],
            'total_wins': self.state['total_wins'],
            'total_losses': self.state['total_losses'],
            'win_rate': self.state['total_wins'] / max(1, self.state['total_bets']) * 100,
            'drawdown_from_peak_pct': (peak - bk) / peak * 100 if peak > 0 else 0,
            'open_bets': len(self.state.get('open_bets', [])),
            'daily_exposure': self.state.get('daily_exposure', 0),
            'daily_bets': self.state.get('daily_bets', 0),
            'is_stopped': self.state.get('stopped', False),
            'stop_reason': self.state.get('stop_reason', ''),
            'tier_stats': self.state.get('tier_stats', {}),
        }

    def format_status(self) -> str:
        """Formatted bankroll status for terminal display."""
        s = self.get_status()
        lines = [
            f"  {'═'*60}",
            f"  💰 BANKROLL STATUS",
            f"  {'═'*60}",
            f"  Bankroll:  {s['bankroll']:.2f}€  (početni: {s['initial']:.2f}€)",
            f"  Profit:    {s['total_profit']:+.2f}€  ({s['total_profit_pct']:+.1f}%)",
            f"  Drawdown:  {s['drawdown_from_peak_pct']:.1f}% od vrha ({s['peak']:.2f}€)",
            f"  Oklada:    {s['total_bets']} ukupno | {s['total_wins']}W / {s['total_losses']}L | WR: {s['win_rate']:.1f}%",
            f"  Danas:     {s['daily_bets']} oklada | {s['daily_exposure']:.2f}€ izloženo",
            f"  Otvoreno:  {s['open_bets']} oklada",
        ]

        if s['is_stopped']:
            lines.append(f"  ⛔ ZAUSTAVLJENO: {s['stop_reason']}")

        # Per-tier stats
        lines.append(f"  {'─'*60}")
        for tier in ['1', '2', '3']:
            ts = s['tier_stats'].get(tier, {})
            if ts.get('bets', 0) > 0:
                bets = ts['bets']
                wins = ts.get('wins', 0)
                wr = wins / bets * 100 if bets > 0 else 0
                profit = ts.get('profit', 0)
                lines.append(
                    f"  TIER {tier}: {bets} oklada | {wins}W | WR: {wr:.1f}% | P&L: {profit:+.2f}€"
                )

        lines.append(f"  {'═'*60}")
        return '\n'.join(lines)

    def reset(self, new_bankroll: float = None):
        """Reset bankroll state (for testing or new start)."""
        initial = new_bankroll or STAKING['initial_bankroll']
        self.state = {
            'bankroll': initial,
            'peak_bankroll': initial,
            'initial_bankroll': initial,
            'total_bets': 0, 'total_wins': 0, 'total_losses': 0,
            'total_profit': 0.0,
            'daily_exposure': 0.0, 'daily_bets': 0,
            'daily_date': datetime.now().strftime('%Y-%m-%d'),
            'weekly_exposure': 0.0,
            'weekly_start_date': datetime.now().strftime('%Y-%m-%d'),
            'open_bets': [], 'stopped': False, 'stop_reason': '',
            'tier_stats': {
                '1': {'bets': 0, 'wins': 0, 'losses': 0, 'profit': 0.0},
                '2': {'bets': 0, 'wins': 0, 'losses': 0, 'profit': 0.0},
                '3': {'bets': 0, 'wins': 0, 'losses': 0, 'profit': 0.0},
            },
        }
        self._save_state()
