#!/usr/bin/env bash
# apply_suggested.sh — Manually approve and apply suggested combo
# from suggested_combo.json to pipeline_config.json.
#
# Usage:
#   bash apply_suggested.sh           # interactive confirm
#   bash apply_suggested.sh --yes     # skip confirmation
set -e
cd "$(dirname "$0")"

SUGGESTED="suggested_combo.json"
CONFIG="pipeline_config.json"

if [ ! -f "$SUGGESTED" ]; then
    echo "ERROR: $SUGGESTED not found. Run auto_tune_and_suggest.py first."
    exit 1
fi

STATUS=$(python -c "import json; d=json.load(open('$SUGGESTED')); print(d.get('status',''))")
if [ "$STATUS" != "suggested" ]; then
    echo "No approved suggestion (status=$STATUS). Nothing to apply."
    exit 0
fi

echo "=== Current suggested combo ==="
python -c "import json; d=json.load(open('$SUGGESTED')); print(json.dumps(d.get('suggested_combo',{}), indent=2))"
echo ""
echo "=== Metrics ==="
python -c "import json; d=json.load(open('$SUGGESTED')); print(json.dumps(d.get('metrics',{}), indent=2))"
echo ""

if [ "$1" != "--yes" ]; then
    read -p "Apply this combo to $CONFIG? [y/N] " CONFIRM
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi
fi

# Apply combo to config
python -c "
import json
with open('$CONFIG') as f:
    cfg = json.load(f)
with open('$SUGGESTED') as f:
    sug = json.load(f)
combo = sug.get('suggested_combo')
if combo:
    cfg['combo'] = combo
    with open('$CONFIG', 'w') as f:
        json.dump(cfg, f, indent=2)
    print('Applied combo to $CONFIG')
else:
    print('No combo to apply')
"

echo "Done. Restart paper_trade_alerts.py to use new config."
