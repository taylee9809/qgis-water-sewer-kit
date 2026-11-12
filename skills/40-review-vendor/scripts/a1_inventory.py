# -*- coding: utf-8 -*-
"""A1 입력 인벤토리 — 납품물 4종 확인 · flo 계통 census · 설계 DB 반영 현황 · 관번호 중복."""
import collections, re
import vendorlib as V

ctx = V.argctx('A1 입력 인벤토리')
cfg = ctx['cfg']


def pre(n):
    m = re.match(r'^([A-Z]+(?:-[A-Z0-9]+?)?)-\d+', n or '')
    return m.group(1) if m else n


# ── 납품물
print('\n── 납품물')
for key, label in (('plan', '평면도'), ('profile', '종단면도')):
    p = ctx[key]
    print('  %-9s %s' % (label, ('%s  (%.1f MB)' % (p.name, p.stat().st_size / 1e6))
                         if p else '★ 없음'))
    for q in ctx[key + '_all'][1:]:
        print('            + 사본 %s' % q.relative_to(ctx['delivery']))
for p in ctx['flo'] + ctx['out']:
    print('  %-9s %s  (%.1f KB)' % (p.suffix.lstrip('.').lower(), p.name,
                                    p.stat().st_size / 1e3))
missing = [l for k, l in (('plan', '평면도'), ('profile', '종단면도')) if not ctx[k]]
if not ctx['flo']:
    missing.append('.flo')
if not ctx['out']:
    missing.append('.OUT')
print('  → 결손: %s' % (', '.join(missing) if missing else '없음 (4종 완비)'))

# ── flo 계통
flo = V.read_all_flo(ctx)
summary = {}
allseg = {}
print('\n── flo 계통')
for f, rows in flo.items():
    nodes_up = {r['up'] for r in rows}
    말단 = sorted({r['dn'] for r in rows} - nodes_up)
    c = collections.Counter(pre(r['up']) for r in rows)
    print('  %s : %d구간 / %.1f m / 관경 %s'
          % (f, len(rows), sum(r['L'] for r in rows),
             dict(collections.Counter(r['D'] for r in rows).most_common())))
    print('      계통 %d : %s' % (len(c), ', '.join('%s(%d)' % kv for kv in sorted(c.items()))))
    print('      말단(기존망 접속점) : %s' % ', '.join(말단))
    summary[f] = dict(구간=len(rows), 연장=round(sum(r['L'] for r in rows), 1),
                      계통=dict(sorted(c.items())), 말단=말단)
    for r in rows:
        allseg[r['up']] = f

# ── OUT
out = V.read_all_out(ctx)
print('\n── OUT')
for f, rows in out.items():
    print('  %s : %d구간 / 판정 %s' % (f, len(rows),
          dict(collections.Counter(v.get('판정') for v in rows.values()).most_common())))
    miss2 = [k for k, v in rows.items() if '2nd' not in v]
    if miss2:
        print('      ★ 2행 결손 %d: %s' % (len(miss2), miss2[:8]))
pair = set(allseg) ^ set().union(*[set(r) for r in out.values()]) if out else set(allseg)
if pair:
    print('  ★ flo ↔ OUT 구간 불일치 %d: %s' % (len(pair), sorted(pair)[:12]))
else:
    print('  flo ↔ OUT 구간 집합 일치')

# ── 설계 DB 반영 현황 (layers.plan_pipe / plan_manhole / plan_pump)
print('\n── 설계 DB 반영 현황 (%s)' % ctx['plan_src'])
for key in V.PLAN_KEYS:
    if not cfg.has_layer(key):
        print('  %-20s (layers.%s 미설정 — 건너뜀)' % ('', key))
        continue
    L = V.Layer(ctx, key)
    n = L.rows('select count(*) from {T}')[0][0]
    print('  %-20s %5d' % (L.name, n))
    asof = V.fname(cfg, key, 'asof')
    if not asof:
        print('        (fields.%s.asof 미설정 — 자료기준일별 집계 생략)' % key)
        continue
    for a0, c0 in L.rows('select %s, count(*) from {T} group by 1 order by 2 desc limit 6'
                         % V.col(cfg, key, 'asof')):
        print('        %-56s %5d' % (a0, c0))

# ── 관번호 중복 (금회 신규가 기존 번호와 부딪치는지)
LP = V.Layer(ctx, 'plan_pipe')
PID = V.col(cfg, 'plan_pipe', 'pipe_id')
qg = {r[0] for r in LP.rows('select %s from {T}' % PID)}
dup = sorted(set(allseg) & qg)
print('\n── 관번호 중복 검사 : flo %d구간 중 설계 DB 기존재 %d건' % (len(allseg), len(dup)))
if dup:
    byz = collections.Counter()
    zq = 'select %s, %s from {T} where %s=?' % (V.col(cfg, 'plan_pipe', 'district', False),
                                                  V.col(cfg, 'plan_pipe', 'asof', False), PID)
    for no in dup:
        r = LP.rows(zq, (no,))[0]
        byz[tuple(r)] += 1
    for (z, a), c0 in byz.most_common():
        print('      %-10s %-56s %4d' % (z, a, c0))
    print('      → 같은 회차를 재반영하는 중이면 정상(교체). 다른 계통과 겹치면 번호 충돌.')
else:
    print('      → 전부 신규')

판정 = 'FAIL' if (missing or pair) else 'PASS'
print('\n' + '=' * 78)
print('A1 판정: %s  (납품물 결손 %d / flo↔OUT 불일치 %d / 관번호 기존재 %d)'
      % (판정, len(missing), len(pair), len(dup)))
if dup:
    print('   관번호 기존재는 같은 회차 재반영(교체)이면 정상 — 판정에 넣지 않았다.')

V.save(ctx, 'a1_inventory.json',
       dict(판정=판정, 납품폴더=ctx['delivery'].name, 결손=missing, flo=summary,
            OUT={f: len(r) for f, r in out.items()},
            flo_OUT_불일치=sorted(pair), 관번호중복=dup))
