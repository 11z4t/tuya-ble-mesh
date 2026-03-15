#!/bin/bash
PASS=0; FAIL=0
echo '=== Q2: en.json referens ==='
EN_KEYS=$(python3 -c "import json; d=json.load(open('custom_components/tuya_ble_mesh/translations/en.json')); exec(\"def c(d):\n r=0\n for v in d.values():\n  if isinstance(v,dict): r+=c(v)\n  elif isinstance(v,str): r+=1\n return r\"); print(c(d))")
SV_KEYS=$(python3 -c "import json; d=json.load(open('custom_components/tuya_ble_mesh/translations/sv.json')); exec(\"def c(d):\n r=0\n for v in d.values():\n  if isinstance(v,dict): r+=c(v)\n  elif isinstance(v,str): r+=1\n return r\"); print(c(d))")
[ "$EN_KEYS" = "$SV_KEYS" ] && { echo "PASS: en=$EN_KEYS sv=$SV_KEYS keys match"; ((PASS++)); true; } || { echo "FAIL: en=$EN_KEYS vs sv=$SV_KEYS keys mismatch"; ((FAIL++)); }
echo ''
echo '=== Q3: Jargongfrihet (values only) ==='
JARGON=$(python3 -c "
import json, os, re
forbidden = ['unicast','daemon','BLE snoop','Proxy Service','payload-offset','ECDH','NetKey','AppKey','DevKey','IV Index','SIG Mesh','PDU','opcode','provisioner']
hits = 0
for f in os.listdir('custom_components/tuya_ble_mesh/translations'):
  if not f.endswith('.json'): continue
  data = json.load(open(f'custom_components/tuya_ble_mesh/translations/{f}'))
  def check(d, path=''):
    global hits
    for k,v in d.items():
      if isinstance(v,dict): check(v, f'{path}.{k}')
      elif isinstance(v,str):
        for term in forbidden:
          if term.lower() in v.lower():
            print(f'  JARGON: {f} {path}.{k} contains \"{term}\"')
            hits += 1
  check(data)
print(f'Total: {hits}')
" 2>&1 | tail -1 | grep -oP '\d+')
[ "$JARGON" = "0" ] && { echo 'PASS: 0 jargon in translation values'; ((PASS++)); true; } || { echo "FAIL: $JARGON jargon hits"; ((FAIL++)); }
echo ''
echo '=== Q5: Tester ==='
python3 -m pytest tests/ -x -q 2>&1 | tail -5
python3 -m pytest tests/ -x -q > /dev/null 2>&1 && { echo 'PASS: pytest passes'; ((PASS++)); true; } || { echo 'FAIL: pytest fails'; ((FAIL++)); }
echo ''
echo '=== Q6: Dokumentation ==='
[ -f CHANGELOG.md ] && { echo 'PASS: CHANGELOG.md exists'; ((PASS++)); true; } || { echo 'FAIL: CHANGELOG.md missing'; ((FAIL++)); }
grep -qi 'HACS' README.md && { echo 'PASS: README mentions HACS'; ((PASS++)); true; } || { echo 'FAIL: README missing HACS instructions'; ((FAIL++)); }
echo ''
echo "=== RESULTAT: $PASS passed, $FAIL failed ==="
[ $FAIL -eq 0 ] && echo 'QC: PASS' || echo 'QC: FAIL'
