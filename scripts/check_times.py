import csv

infile='2026-02-07.csv'
count_le=0
count_gt=0
unknown=0
samples_le=[]
with open(infile, encoding='utf-8', newline='') as f:
    r=csv.DictReader(f, delimiter=';')
    for row in r:
        s=(row.get('vrijeme') or '').strip()
        if s=='':
            unknown+=1
            continue
        try:
            parts=s.split(':')
            h=int(parts[0]); m=int(parts[1])
            minutes=h*60+m
            if minutes<=13*60:
                count_le+=1
                if len(samples_le)<10:
                    samples_le.append(s)
            else:
                count_gt+=1
        except Exception as e:
            unknown+=1

print('<=13:00=',count_le,'>13:00=',count_gt,'unknown=',unknown)
print('samples <=13:',samples_le)
