"""
Signal Monitor — Performance tracking, recalibration alerts, and tier evaluation.

Prati performanse signalnog sustava po tieru, upozorava na devijacije,
i predlaže kada je potrebna rekalibracija parametara.

Minimum 30 oklada po tieru za evaluaciju (MONITORING['min_bets_for_evaluation']).
Rekalibracija nakon 100 ukupnih oklada (MONITORING['recalibrate_after_bets']).
"""
import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from odds_tracker.signal_config import (
    MONITORING, TIER_1_RULES, TIER_2_RULES, TIER_3_RULES,
    BASELINE_HIT_RATES, TIER_NAMES, ALL_TIER_RULES,
)


_BASE_DIR = Path(__file__).parent.parent
MONITOR_STATE_PATH = _BASE_DIR / "odds_data" / "signal_monitor_state.json"
SIGNAL_RESULTS_PATH = _BASE_DIR / "odds_data" / "signal_results.csv"

RESULTS_COLUMNS = [
    'timestamp', 'match_key', 'tier', 'tier_rule',
    'outcome', 'odds', 'drop_pct', 'odds_segment',
    'minutes_to_ko', 'snapshots',
    'result',         # 'win', 'loss', 'void'
    'profit',
    'score',          # final match score
]


class SignalMonitor:
    """
    Tracks signal performance and generates health reports.

    Usage:
        monitor = SignalMonitor()
        monitor.record_result(signal_dict, result='win', score='2:1')
        report = monitor.generate_report()
        alerts = monitor.check_alerts()
    """

    def __init__(self):
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load monitor state."""
        if MONITOR_STATE_PATH.exists():
            try:
                with open(MONITOR_STATE_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            'total_signals': 0,
            'total_resolved': 0,
            'last_report_date': '',
            'last_recalibration': '',
            'tier_performance': {
                '1': {'bets': 0, 'wins': 0, 'profit': 0.0, 'total_odds': 0.0},
                '2': {'bets': 0, 'wins': 0, 'profit': 0.0, 'total_odds': 0.0},
                '3': {'bets': 0, 'wins': 0, 'profit': 0.0, 'total_odds': 0.0},
            },
            'rule_performance': {},  # per-rule tracking
            'alerts': [],
        }

    def _save_state(self):
        MONITOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MONITOR_STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def record_result(self, signal: dict, result: str, score: str = '', profit: float = 0.0):
        """
        Record a resolved signal result.

        Args:
            signal: signal dict with tier, tier_rule, bet, odds, etc.
            result: 'win', 'loss', 'void'
            score: final match score
            profit: actual profit/loss amount
        """
        tier = str(signal.get('tier', 0))
        tier_rule = signal.get('tier_rule', 'unknown')
        odds = signal.get('latest_odds', 0) or signal.get('odds', 0)

        # Update tier stats
        if tier in self.state['tier_performance']:
            tp = self.state['tier_performance'][tier]
            tp['bets'] += 1
            tp['total_odds'] += odds
            if result == 'win':
                tp['wins'] += 1
            tp['profit'] += profit

        # Update rule stats
        if tier_rule not in self.state['rule_performance']:
            self.state['rule_performance'][tier_rule] = {
                'bets': 0, 'wins': 0, 'profit': 0.0, 'total_odds': 0.0,
            }
        rp = self.state['rule_performance'][tier_rule]
        rp['bets'] += 1
        rp['total_odds'] += odds
        if result == 'win':
            rp['wins'] += 1
        rp['profit'] += profit

        self.state['total_resolved'] += 1

        # Log to CSV
        self._log_result(signal, result, score, profit)
        self._save_state()

    def _log_result(self, signal: dict, result: str, score: str, profit: float):
        """Append result to signal_results.csv."""
        SIGNAL_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_header = not SIGNAL_RESULTS_PATH.exists()

        with open(SIGNAL_RESULTS_PATH, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=RESULTS_COLUMNS, delimiter=';')
            if write_header:
                writer.writeheader()
            writer.writerow({
                'timestamp': datetime.now().isoformat(),
                'match_key': f"{signal.get('home', '')}|{signal.get('away', '')}",
                'tier': signal.get('tier', ''),
                'tier_rule': signal.get('tier_rule', ''),
                'outcome': signal.get('bet', ''),
                'odds': signal.get('latest_odds', 0),
                'drop_pct': signal.get('drop_pct', 0),
                'odds_segment': signal.get('odds_segment', ''),
                'minutes_to_ko': signal.get('minutes_to_ko', ''),
                'snapshots': signal.get('snapshots', ''),
                'result': result,
                'profit': profit,
                'score': score,
            })

    def check_alerts(self) -> list[dict]:
        """
        Check all monitoring conditions and return alerts.

        Returns list of alert dicts: {'type': str, 'tier': str, 'message': str, 'severity': str}
        Severity: 'info', 'warning', 'critical'
        """
        alerts = []

        # Get expected stats per tier
        expected = {}
        for tier_n, rules in [(1, TIER_1_RULES), (2, TIER_2_RULES), (3, TIER_3_RULES)]:
            # Use best rule in tier as benchmark
            best_rule = max(rules, key=lambda r: r['evidence']['roi'])
            expected[str(tier_n)] = {
                'hit_rate': best_rule['evidence']['hit_rate'],
                'roi': best_rule['evidence']['roi'],
            }

        for tier_key, tp in self.state['tier_performance'].items():
            bets = tp['bets']
            if bets < MONITORING['min_bets_for_evaluation']:
                continue  # Not enough data

            # Hit rate check
            actual_hr = tp['wins'] / bets if bets > 0 else 0
            expected_hr = expected.get(tier_key, {}).get('hit_rate', 0.33)
            hr_diff_pp = (actual_hr - expected_hr) * 100

            if hr_diff_pp < -MONITORING['max_hit_rate_deviation_pp']:
                alerts.append({
                    'type': 'hit_rate_low',
                    'tier': tier_key,
                    'message': (
                        f"TIER {tier_key} hit rate {actual_hr:.1%} je {abs(hr_diff_pp):.1f}pp "
                        f"ispod očekivanog ({expected_hr:.1%}) nakon {bets} oklada"
                    ),
                    'severity': 'warning' if hr_diff_pp > -10 else 'critical',
                })

            # ROI check
            avg_odds = tp['total_odds'] / bets if bets > 0 else 2.0
            actual_roi = tp['profit'] / bets if bets > 0 else 0
            min_roi = MONITORING['min_roi_threshold_pct'] / 100

            if actual_roi < min_roi:
                alerts.append({
                    'type': 'roi_low',
                    'tier': tier_key,
                    'message': (
                        f"TIER {tier_key} ROI {actual_roi:.1%} je ispod praga "
                        f"({min_roi:.1%}) nakon {bets} oklada"
                    ),
                    'severity': 'warning' if actual_roi > -0.20 else 'critical',
                })

        # Recalibration check
        total = self.state['total_resolved']
        if total >= MONITORING['recalibrate_after_bets']:
            last_recal = self.state.get('last_recalibration', '')
            if not last_recal:
                alerts.append({
                    'type': 'recalibrate',
                    'tier': 'all',
                    'message': (
                        f"Dostignuto {total} oklada — preporučena rekalibracija parametara. "
                        f"Pokreni analizu s novim podacima."
                    ),
                    'severity': 'info',
                })

        self.state['alerts'] = alerts
        self._save_state()
        return alerts

    def generate_report(self) -> dict:
        """
        Generate comprehensive signal performance report.

        Returns report dict with per-tier and per-rule breakdowns.
        """
        report = {
            'generated_at': datetime.now().isoformat(),
            'total_signals': self.state['total_signals'],
            'total_resolved': self.state['total_resolved'],
            'tiers': {},
            'rules': {},
            'alerts': self.check_alerts(),
        }

        # Per-tier report
        for tier_key, tp in self.state['tier_performance'].items():
            bets = tp['bets']
            if bets == 0:
                continue
            wins = tp['wins']
            losses = bets - wins
            avg_odds = tp['total_odds'] / bets if bets > 0 else 0

            report['tiers'][tier_key] = {
                'bets': bets,
                'wins': wins,
                'losses': losses,
                'hit_rate': wins / bets,
                'roi': tp['profit'] / bets if bets > 0 else 0,
                'total_profit': tp['profit'],
                'avg_odds': avg_odds,
                'significant': bets >= MONITORING['min_sample_significant'],
            }

        # Per-rule report
        for rule_id, rp in self.state.get('rule_performance', {}).items():
            bets = rp['bets']
            if bets == 0:
                continue
            report['rules'][rule_id] = {
                'bets': bets,
                'wins': rp['wins'],
                'hit_rate': rp['wins'] / bets,
                'roi': rp['profit'] / bets if bets > 0 else 0,
                'total_profit': rp['profit'],
                'avg_odds': rp['total_odds'] / bets if bets > 0 else 0,
            }

        return report

    def format_report(self) -> str:
        """Human-readable performance report."""
        report = self.generate_report()
        lines = [
            f"\n  {'═'*70}",
            f"  📊 SIGNAL PERFORMANCE REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"  {'═'*70}",
            f"  Ukupno signala: {report['total_signals']} | Riješeno: {report['total_resolved']}",
        ]

        for tier_key in ['1', '2', '3']:
            td = report['tiers'].get(tier_key)
            if not td:
                continue
            sig = '✅' if td['significant'] else '⚠️ (mali uzorak)'
            lines.append(f"\n  ── {TIER_NAMES.get(int(tier_key), f'TIER {tier_key}')} ──")
            lines.append(
                f"  Oklada: {td['bets']} | WR: {td['hit_rate']:.1%} | "
                f"ROI: {td['roi']:.1%} | Profit: {td['total_profit']:+.2f}€ | "
                f"Avg odds: {td['avg_odds']:.2f} {sig}"
            )

        # Rules breakdown
        if report['rules']:
            lines.append(f"\n  ── Po pravilima ──")
            for rule_id, rd in sorted(report['rules'].items()):
                lines.append(
                    f"  {rule_id}: {rd['bets']}B | WR:{rd['hit_rate']:.0%} | "
                    f"ROI:{rd['roi']:.0%} | P&L:{rd['total_profit']:+.2f}€"
                )

        # Alerts
        if report['alerts']:
            lines.append(f"\n  ── ⚠ UPOZORENJA ──")
            for alert in report['alerts']:
                icon = {'info': 'ℹ️', 'warning': '⚠️', 'critical': '🚨'}.get(alert['severity'], '❓')
                lines.append(f"  {icon} {alert['message']}")

        lines.append(f"  {'═'*70}")
        return '\n'.join(lines)

    def should_recalibrate(self) -> bool:
        """Check if system should be recalibrated."""
        total = self.state['total_resolved']
        return total >= MONITORING['recalibrate_after_bets']

    def mark_recalibrated(self):
        """Mark recalibration as done."""
        self.state['last_recalibration'] = datetime.now().isoformat()
        self.state['total_resolved'] = 0  # Reset counter
        self._save_state()

    def increment_signal_count(self, n: int = 1):
        """Track total signals generated."""
        self.state['total_signals'] += n
        self._save_state()


def wilson_confidence_interval(wins: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """
    Wilson score interval for binomial proportion.
    More accurate than normal approximation for small samples.

    Args:
        wins: number of successes
        total: total trials
        z: z-score (1.96 for 95% CI)

    Returns:
        (lower_bound, upper_bound) of true win rate
    """
    if total == 0:
        return 0.0, 1.0

    p = wins / total
    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denominator

    return max(0, center - spread), min(1, center + spread)


def evaluate_tier_significance(bets: int, wins: int, expected_hr: float) -> dict:
    """
    Evaluate if a tier's performance is statistically significant.

    Returns dict with:
        actual_hr, expected_hr, ci_lower, ci_upper,
        is_significant, is_underperforming, verdict
    """
    actual_hr = wins / bets if bets > 0 else 0
    ci_lower, ci_upper = wilson_confidence_interval(wins, bets)

    is_significant = bets >= MONITORING['min_sample_significant']
    is_underperforming = ci_upper < expected_hr  # Entire CI below expected

    if not is_significant:
        verdict = 'Nedovoljno podataka za zaključak'
    elif is_underperforming:
        verdict = f'UNDERPERFORMING — CI [{ci_lower:.1%}, {ci_upper:.1%}] ispod očekivanog {expected_hr:.1%}'
    elif ci_lower > expected_hr:
        verdict = f'OUTPERFORMING — CI [{ci_lower:.1%}, {ci_upper:.1%}] iznad očekivanog {expected_hr:.1%}'
    else:
        verdict = f'U SKLADU — CI [{ci_lower:.1%}, {ci_upper:.1%}] uključuje očekivani {expected_hr:.1%}'

    return {
        'actual_hr': actual_hr,
        'expected_hr': expected_hr,
        'ci_lower': ci_lower,
        'ci_upper': ci_upper,
        'is_significant': is_significant,
        'is_underperforming': is_underperforming,
        'verdict': verdict,
    }
