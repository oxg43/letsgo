import csv
from pathlib import Path

infile = Path(r"c:\Users\bibia\Desktop\DANAS\2026-02-07.csv")
out_clean = infile.parent / (infile.stem + "_clean.csv")
out_removed = infile.parent / (infile.stem + "_removed.csv")

threshold_minutes = 13 * 60  # 13:00 inclusive

def time_to_minutes(s):
    if s is None:
        return None
    s = s.strip()
    if s == "":
        return None
    # Expect format HH:MM (possibly H:MM)
    parts = s.split(":")
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
        return h * 60 + m
    except ValueError:
        return None

rows_clean = []
rows_removed = []

with infile.open("r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f, delimiter=';')
    # Remove possible BOM from the first header field (e.g. '\ufeffvrijeme')
    reader.fieldnames = [fn.lstrip('\ufeff') for fn in reader.fieldnames]
    fieldnames = reader.fieldnames
    if fieldnames is None:
        raise SystemExit("CSV nema zaglavlje")
    for row in reader:
        t = time_to_minutes(row.get('vrijeme'))
        if t is None:
            # keep rows with unknown time
            rows_clean.append(row)
        elif t <= threshold_minutes:
            rows_removed.append(row)
        else:
            rows_clean.append(row)

# Write cleaned file
with out_clean.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
    writer.writeheader()
    writer.writerows(rows_clean)

# Write removed file
with out_removed.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
    writer.writeheader()
    writer.writerows(rows_removed)

print(f"Wrote {len(rows_clean)} rows to {out_clean}")
print(f"Wrote {len(rows_removed)} rows to {out_removed}")
