"""Quick validation of the tier-based signal system."""
from odds_tracker.signals import _compute_drop_pct, _check_anti_filters, _match_tier_rules
from odds_tracker.staking import calculate_stake
from odds_tracker.signal_config import TIER_1_RULES, TIER_2_RULES, TIER_3_RULES

print("=" * 60)
print("TIER SYSTEM VALIDATION")
print("=" * 60)

# Test 1: Home drop 15.9% @ odds 1.85, 15 min to KO
dp = _compute_drop_pct(2.20, 1.85)
print(f"\n[TEST 1] Home 2.20 -> 1.85 = {dp:.1f}% drop")
blocked, reason = _check_anti_filters("1", dp, 1.85, 15, 40)
print(f"  Anti-filter: {'BLOCKED: ' + reason if blocked else 'PASSED'}")
tier, rule = _match_tier_rules("1", dp, 1.85, 15, 40)
if tier:
    stake = calculate_stake(tier, 1.85, rule, 100.0)
    print(f"  TIER {tier} | Rule: {rule['id']} | Stake: {stake['stake_amount']}EUR ({stake['stake_pct']}%)")
    print(f"  Kelly full: {stake['kelly_full_pct']}% | HR used: {stake['hit_rate_used']}")

# Test 2: Draw 17.6% drop @ odds 2.80
dp2 = _compute_drop_pct(3.40, 2.80)
print(f"\n[TEST 2] Draw 3.40 -> 2.80 = {dp2:.1f}% drop")
blocked2, reason2 = _check_anti_filters("X", dp2, 2.80, 8, 25)
print(f"  Anti-filter: {'BLOCKED: ' + reason2 if blocked2 else 'PASSED'}")
tier2, rule2 = _match_tier_rules("X", dp2, 2.80, 8, 25)
if tier2:
    stake2 = calculate_stake(tier2, 2.80, rule2, 100.0)
    print(f"  TIER {tier2} | Rule: {rule2['id']} | Stake: {stake2['stake_amount']}EUR ({stake2['stake_pct']}%)")
    print(f"  Kelly full: {stake2['kelly_full_pct']}% | HR used: {stake2['hit_rate_used']}")

# Test 3: Away jaki favorit 11.7% drop @ odds 1.28
dp3 = _compute_drop_pct(1.45, 1.28)
print(f"\n[TEST 3] Away 1.45 -> 1.28 = {dp3:.1f}% drop (jaki favorit)")
blocked3, reason3 = _check_anti_filters("2", dp3, 1.28, 20, 10)
print(f"  Anti-filter: {'BLOCKED: ' + reason3 if blocked3 else 'PASSED'}")
tier3, rule3 = _match_tier_rules("2", dp3, 1.28, 20, 10)
if tier3:
    stake3 = calculate_stake(tier3, 1.28, rule3, 100.0)
    print(f"  TIER {tier3} | Rule: {rule3['id']} | Stake: {stake3['stake_amount']}EUR ({stake3['stake_pct']}%)")
    print(f"  Kelly full: {stake3['kelly_full_pct']}% | HR used: {stake3['hit_rate_used']}")

# Test 4: Away @ odds 2.10 — should be BLOCKED (not jaki favorit)
dp4 = _compute_drop_pct(2.40, 2.10)
print(f"\n[TEST 4] Away 2.40 -> 2.10 = {dp4:.1f}% drop @ odds 2.10")
blocked4, reason4 = _check_anti_filters("2", dp4, 2.10, 15, 40)
print(f"  Anti-filter: {'BLOCKED: ' + reason4 if blocked4 else 'PASSED'}")
tier4, rule4 = _match_tier_rules("2", dp4, 2.10, 15, 40)
print(f"  Tier match: {'TIER ' + str(tier4) + ' ' + rule4['id'] if tier4 else 'NONE (correctly rejected)'}")

# Test 5: Home @ odds 3.50 — should be BLOCKED (autsajder)
dp5 = _compute_drop_pct(4.00, 3.50)
print(f"\n[TEST 5] Home 4.00 -> 3.50 = {dp5:.1f}% drop @ odds 3.50")
blocked5, reason5 = _check_anti_filters("1", dp5, 3.50, 10, 40)
print(f"  Anti-filter: {'BLOCKED: ' + reason5 if blocked5 else 'PASSED'}")

# Test 6: Home 4% drop — below TIER threshold
dp6 = _compute_drop_pct(2.00, 1.92)
print(f"\n[TEST 6] Home 2.00 -> 1.92 = {dp6:.1f}% drop (below threshold)")
blocked6, reason6 = _check_anti_filters("1", dp6, 1.92, 10, 40)
print(f"  Anti-filter: {'BLOCKED: ' + reason6 if blocked6 else 'PASSED'}")

# Test 7: Draw 5% drop — below 10% minimum
dp7 = _compute_drop_pct(3.00, 2.85)
print(f"\n[TEST 7] Draw 3.00 -> 2.85 = {dp7:.1f}% drop (below draw minimum)")
blocked7, reason7 = _check_anti_filters("X", dp7, 2.85, 10, 40)
print(f"  Anti-filter: {'BLOCKED: ' + reason7 if blocked7 else 'PASSED'}")

# Test 8: Home good drop but 45 min to KO — should be WATCH only
dp8 = _compute_drop_pct(2.20, 1.85)
print(f"\n[TEST 8] Home {dp8:.1f}% drop but 45 min to KO (>30 min)")
tier8, rule8 = _match_tier_rules("1", dp8, 1.85, 45, 40)
print(f"  Tier match at 45min: {'TIER ' + str(tier8) if tier8 else 'NONE (outside window -> WATCH)'}")

from odds_tracker.signals import _match_watch_rules
watch = _match_watch_rules("1", dp8, 1.85, 40)
print(f"  Watch match: {'Rule ' + watch['id'] if watch else 'NONE'}")

# Summary
print(f"\n{'=' * 60}")
print(f"All {7} tier rules loaded:")
for tier_n, rules in [(1, TIER_1_RULES), (2, TIER_2_RULES), (3, TIER_3_RULES)]:
    for r in rules:
        ev = r['evidence']
        print(f"  TIER {tier_n} | {r['id']:20s} | {r['outcome']} | ≥{r['min_drop_pct']:.0f}% | "
              f"odds {r['odds_range'][0]:.2f}-{r['odds_range'][1]:.2f} | "
              f"N={ev['n']:3d} HR={ev['hit_rate']:.1%} ROI={ev['roi']:+.1%}")
print("=" * 60)
