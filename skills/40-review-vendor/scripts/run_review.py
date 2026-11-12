# -*- coding: utf-8 -*-
"""A1~A5 일괄 실행 → 검토요약.md.

    python run_review.py                 # 최신 납품 폴더
    python run_review.py -d YYMMDD        # 날짜·폴더명 일부·경로
    python run_review.py --only a2 a4
    python run_review.py --list           # 납품 폴더 목록만
    python run_review.py --gpkg <백업.gpkg>   # 설계 레이어를 그 회차 백업에서
    python run_review.py --config <sewer.config.json>   # 생략하면 현재 폴더에서 위로 탐색

산출 = <outputs.root>/40-review/<납품폴더명>/. 각 단계의 콘솔 출력은 `<단계>.log` 로,
수치는 `<단계>.json` 으로 남는다.
"""
import sys, os, json, subprocess, datetime, argparse
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vendorlib as V

STEPS = [('a1', 'a1_inventory.py', 'A1 입력 인벤토리'),
         ('a2', 'a2_len.py', 'A2 연장 전수 대조'),
         ('a3', 'a3_topology.py', 'A3 계통·토폴로지'),
         ('a4', 'a4_existing.py', 'A4 기존 접속부 관저고'),
         ('a5', 'a5_memo.py', 'A5 비출력 메모')]
HERE = Path(__file__).resolve().parent

ap = argparse.ArgumentParser(description='40 외주 납품물 검토 일괄 (A1~A5 → 검토요약.md)')
ap.add_argument('-d', '--delivery', default=None,
                help='납품 폴더(날짜 6자리·폴더명 일부·경로). 생략하면 최신 from 폴더')
ap.add_argument('--gpkg', default=None,
                help='설계 레이어(plan_pipe/plan_manhole/plan_pump)를 이 GPKG 에서 같은 레이어명으로 '
                     '읽는다 — 그 회차 백업과 대조할 때')
V.kitconfig.add_config_arg(ap)
ap.add_argument('--only', nargs='*', default=None, help='a1 a2 … 일부만')
ap.add_argument('--list', action='store_true', help='납품 폴더 목록 출력')
a = ap.parse_args()

cfg = V.load_config(a.config)
if a.list:
    print('설정 %s' % cfg.path)
    print('납품(수신) 폴더 — 최신순')
    for p in V.deliveries(cfg):
        inp = V.collect_inputs(p, cfg)
        print('  %-62s 평면%s 종단%s flo%d OUT%d'
              % (p.name, 'O' if inp['plan'] else 'X', 'O' if inp['profile'] else 'X',
                 len(inp['flo']), len(inp['out'])))
    sys.exit()

ctx = V.context(a.delivery, str(cfg.path), gpkg=a.gpkg)
steps = [s for s in STEPS if (not a.only or s[0] in a.only)]
print('검토 대상: %s' % ctx['delivery'].name)
print('실행: %s\n' % ', '.join(s[2] for s in steps))

results = {}
for key, script, label in steps:
    # 하위 단계에는 해석이 끝난 절대경로를 넘긴다(작업 폴더·자동선택에 흔들리지 않게)
    argv = [sys.executable, str(HERE / script), '-d', str(ctx['delivery']),
            '--config', str(cfg.path)]
    if ctx['gpkg_override']:
        argv += ['--gpkg', str(ctx['gpkg_override'])]
    print('─' * 78)
    print('▶ %s' % label)
    r = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=str(HERE))
    log = (r.stdout or '') + (('\n[stderr]\n' + r.stderr) if r.stderr.strip() else '')
    (ctx['outdir'] / ('%s.log' % key)).write_text(log, encoding='utf-8')
    jf = ctx['outdir'] / ('%s_%s.json' % (key, script[3:-3]))
    verdict = '실행실패'
    if jf.exists():
        try:
            verdict = json.loads(jf.read_text(encoding='utf-8')).get('판정', '-')
        except Exception:
            verdict = '-'
    if r.returncode != 0:
        verdict = '실행실패'
        print(r.stderr.strip()[-800:] or r.stdout.strip()[-800:])
    results[key] = dict(label=label, 판정=verdict, log='%s.log' % key,
                        json=jf.name if jf.exists() else None)
    print('   판정 %s   (로그 %s.log)' % (verdict, key))

# ── 요약
def grab(key, name):
    jf = ctx['outdir'] / name
    if jf.exists():
        try:
            return json.loads(jf.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


a1 = grab('a1', 'a1_inventory.json')
a2 = grab('a2', 'a2_len.json')
a3 = grab('a3', 'a3_topology.json')
a4 = grab('a4', 'a4_existing.json')
a5 = grab('a5', 'a5_memo.json')

L = []
L.append('# 40 외주 납품물 검토 — %s\n' % ctx['delivery'].name)
L.append('| | |')
L.append('|---|---|')
L.append('| 검토일 | %s |' % datetime.date.today().isoformat())
L.append('| 납품 폴더 | `%s` |' % ctx['delivery'].name)
L.append('| 대조 설계DB | `%s`%s |' % (ctx['plan_src'], ' (--gpkg)' if ctx['gpkg_override'] else ''))
L.append('')
L.append('## 판정')
L.append('')
L.append('| 단계 | 검토 | 판정 |')
L.append('|---|---|---|')
JF = {'a1': 'a1_inventory.json', 'a2': 'a2_len.json', 'a3': 'a3_topology.json',
      'a4': 'a4_existing.json', 'a5': 'a5_memo.json'}
for key, _, label in STEPS:
    r = results.get(key)
    if r:
        v = r['판정']
    else:                       # --only 로 건너뛴 단계는 이전 산출물의 판정을 보여준다
        j = grab(key, JF[key])
        v = ('%s (이전 실행)' % j['판정']) if j.get('판정') else '미실행'
    L.append('| %s | %s | **%s** |' % (key.upper(), label.split(' ', 1)[1], v))
L.append('')

if a1:
    L.append('## A1 입력')
    L.append('')
    L.append('- 결손: %s' % (', '.join(a1.get('결손') or []) or '없음 (4종 완비)'))
    for f, v in (a1.get('flo') or {}).items():
        L.append('- `%s` %d구간 / %.1f m / 말단 %s'
                 % (f, v['구간'], v['연장'], ', '.join(v['말단'])))
    L.append('')

if a2:
    L.append('## A2 연장 전수 대조 — %s' % a2.get('판정'))
    L.append('')
    L.append('허용차 ±%s m, 압송 판정 D ≤ %s mm, 금회 범위 %s구간'
             % (a2.get('허용차'), a2.get('압송판정D'), a2.get('금회범위')))
    L.append('')
    for k, t in (('불일치_막대_도면기재', '막대 실측폭 ↔ 도면 기재 L='),
                 ('불일치_종단_flo', '종단 ↔ flo'), ('불일치_종단_QGIS', '종단 ↔ QGIS'),
                 ('불일치_flo_QGIS', 'flo ↔ QGIS')):
        v = a2.get(k) or []
        L.append('- %s 불일치 **%d건**%s'
                 % (t, len(v), (' — ' + ', '.join('%s(%+.2f)' % (d['관번호'], d['차'])
                                                  for d in v[:8])) if v else ''))
    if a2.get('번호불일치_누락후보'):
        L.append('- ★ 번호 불일치·누락 후보: %s' % ', '.join(a2['번호불일치_누락후보']))
    if a2.get('행짝짓기실패'):
        L.append('- ★ 행 짝짓기 실패: %s (시트 끝 잘림·이중작도 확인)' % a2['행짝짓기실패'])
    L.append('- 참고(금회 범위 밖) 불일치 %d건 — 판정 제외' % len(a2.get('참고_범위밖') or []))
    L.append('')

if a3:
    L.append('## A3 계통·토폴로지 — %s' % a3.get('판정'))
    L.append('')
    for x in (a3.get('지적') or ['지적 없음']):
        L.append('- %s' % x)
    L.append('')

if a4:
    L.append('## A4 기존 접속부 관저고 — %s' % a4.get('판정'))
    L.append('')
    L.append('| 접속점 | 상류구간 | 신설 IL | 설계사 기존 IL | 준공맨홀 | 이격 | 준공 IL | 차 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for d in a4.get('상세') or []:
        L.append('| %s | %s | %s | %s | %s | %s | %s | %s |'
                 % (d['접속점'], d['상류구간'], d['신설하류관저'], d['설계사_기존관저'],
                    d['준공맨홀'] or '—', d['이격_m'] if d['이격_m'] is not None else '—',
                    d['준공_유출관저'] if d['준공_유출관저'] is not None else '—',
                    ('%+.2f' % d['차_m']) if d['차_m'] is not None else '—'))
    L.append('')
    if a4.get('자기값복사'):
        L.append('- ★ flo 기존관저가 신설 하류관저 +0~0.08 로 복사된 접속점 **%d/%d**: %s'
                 % (len(a4['자기값복사']), a4.get('접속점수'), ', '.join(a4['자기값복사'])))
        L.append('  → 실측 조회를 하지 않았다는 뜻. **전 접속점 관저고 재확인** 지적.')
    L.append('')

if a5:
    L.append('## A5 비출력 메모 — %s' % a5.get('판정'))
    L.append('')
    if not (a5.get('메모') or []):
        L.append('- 작업메모 없음 (행 이름표 %d개만)' % len(a5.get('행이름표') or []))
    for m in a5.get('메모') or []:
        L.append('- row%d 「%s」%s → %s'
                 % (m['row'], m['text'], ' **미확정**' if m.get('미확정') else '',
                    ', '.join(m.get('걸친구간') or []) or '구간 미상'))
    L.append('')

L.append('## 지적사항 서술 틀')
L.append('')
L.append('> `관번호` 측점 — 납품값 vs 기준값 — **차이** — 영향(구간수·연장·공사비)')
L.append('')
L.append('---')
L.append('')
L.append('스킬: `40-review-vendor` (qgis-water-sewer-kit) · 설정: `%s` · 함정: `standard/pitfalls-dxf.md`'
         % cfg.path.name)

p = ctx['outdir'] / '검토요약.md'
p.write_text('\n'.join(L), encoding='utf-8')
print('─' * 78)
print('요약 %s' % p)
for key, _, label in STEPS:
    r = results.get(key)
    if r:
        print('   %-28s %s' % (label, r['판정']))
