"""
Match Watcher — monitor specific live matches and alert when "best moment to bet" conditions occur.
"""
import json
from pathlib import Path
from typing import List

DATA_DIR = Path(__file__).parent.parent / "odds_data"
WATCH_STATE_DIR = DATA_DIR / "watch_state"
WATCH_STATE_DIR.mkdir(exist_ok=True)


def _sanitize_name(name: str) -> str:
    return name.replace(' ', '_').replace('/', '_').replace('\\', '_')


def _state_path(match: str) -> Path:
    return WATCH_STATE_DIR / f"{_sanitize_name(match)}.json"


def _load_state(match: str) -> dict:
    p = _state_path(match)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_state(match: str, state: dict):
    p = _state_path(match)
    p.write_text(json.dumps(state), encoding='utf-8')


def check_and_alert(live_values: List[dict], watch_list: List[str], *,
                    edge_min: float = 0.12, delta_edge: float = 0.05,
                    conf_min: float = 0.65) -> List[str]:
    """Check live values and print alerts for watched matches.

    Returns a list of alert messages generated.
    """
    alerts = []
    if not watch_list:
        return alerts

    # Normalize watch list
    watch_norm = [w.strip().lower() for w in watch_list if w and w.strip()]

    for lv in live_values:
        match_str = f"{lv.get('home','')} vs {lv.get('away','')}".lower()
        for w in watch_norm:
            if w in match_str:
                # Candidate for alert
                edge = float(lv.get('edge', 0)) if lv.get('edge') is not None else 0
                conf = float(lv.get('confidence', 0)) if lv.get('confidence') is not None else 0
                minute = lv.get('minute', '')
                try:
                    minute_num = int(str(minute).rstrip("'+")) if minute else 0
                except Exception:
                    minute_num = 0

                state = _load_state(match_str)
                prev_edge = float(state.get('edge', 0))
                prev_conf = float(state.get('confidence', 0))
                prev_score = state.get('score', '')
                cur_score = lv.get('score', '')

                # Condition 1: high absolute edge/conf
                cond1 = edge >= edge_min and conf >= conf_min
                # Condition 2: significant edge jump
                cond2 = (edge - prev_edge) >= delta_edge and edge >= edge_min/2
                # Only alert if score hasn't changed (i.e., before a goal) or minute progressed
                if cur_score == prev_score and (cond1 or cond2):
                    msg = (f"🔔 ALERT — Best pre-goal moment: {lv.get('home')} vs {lv.get('away')} | "
                           f"Bet: {lv.get('bet_label', lv.get('bet',''))} @ {lv.get('offered_odds',0):.2f} | "
                           f"Edge: {edge:+.1%} | Conf: {conf:.0%} | Minute: {minute} | Reason: "
                           f"{'High edge+conf' if cond1 else 'Edge jump'}")
                    print(msg)
                    alerts.append(msg)

                # Save new state
                _save_state(match_str, {
                    'edge': edge,
                    'confidence': conf,
                    'minute': minute_num,
                    'score': cur_score,
                    'timestamp': lv.get('timestamp','')
                })

    return alerts
