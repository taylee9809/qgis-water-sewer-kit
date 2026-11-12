# -*- coding: utf-8 -*-
"""A3 계통·토폴로지 대조 — flo 노드체인 ↔ 종단 작도 순서.

보는 것
  1. flo 체인: 계통별 상·하류 순서, 합류점(유입 2개 이상), 말단(기존망 접속점), 순환·끊김.
  2. **유향**: 종단면도에 그려진 순서(행 안 x순)가 flo 체인 순서와 반대인 계통을 찾는다.
     한 납품 회차에서 그랬다 — 같은 관로를 반대로 그린 뒤 펌프장을 붙여 놨다.
  3. 압송·펌프장 유무: flo 압송 구간수·연장 ↔ 종단 '압송' 라벨 수.
     (라벨 낱말 = review_vendor.profile.pressure_word, 압송 판정 D ≤ design.pressure_max_d_mm)
  4. 한쪽에만 있는 계통 (flo · 종단 · 설계 DB 계획관로).
"""
import collections, re
import vendorlib as V

ctx = V.argctx('A3 계통·토폴로지')
cfg = ctx['cfg']
DMAX = V.pressure_dmax(cfg)
P = V.profile_conf(cfg)


def pre(n):
    m = re.match(r'^([A-Z]+(?:-[A-Z0-9]+?)?)-\d+', n or '')
    return m.group(1) if m else n


flo = V.read_all_flo(ctx)
findings = []

# ── 1. 체인
print('\n' + '=' * 78)
chains = {}
for f, rows in flo.items():
    nxt = {r['up']: r['dn'] for r in rows}
    inflow = collections.defaultdict(list)
    for r in rows:
        inflow[r['dn']].append(r['up'])
    말단 = sorted(set(nxt.values()) - set(nxt))
    합류 = {k: v for k, v in inflow.items() if len(v) > 1}
    print('\n── %s : %d구간 / 말단 %s / 합류점 %d' % (f, len(rows), 말단, len(합류)))
    for k in sorted(합류):
        print('     %-14s ← %s' % (k, ', '.join(sorted(합류[k]))))
    # 순환·끊김
    bad = []
    for start in rows:
        cur, seen = start['up'], set()
        while cur in nxt:
            if cur in seen:
                bad.append(start['up'])
                break
            seen.add(cur)
            cur = nxt[cur]
    if bad:
        print('     ★ 순환 의심 %d: %s' % (len(bad), sorted(set(bad))[:8]))
        findings.append('%s 순환 의심 %d구간' % (f, len(set(bad))))
    chains[f] = dict(nxt=nxt, 말단=말단, 합류={k: sorted(v) for k, v in 합류.items()})

    # 계통별 체인 순서
    order = {}
    for r in rows:
        p = pre(r['up'])
        order.setdefault(p, [])
    for p in order:
        mem = [r['up'] for r in rows if pre(r['up']) == p]
        head = [m for m in mem if not any(nxt.get(x) == m for x in mem)]
        seq, cur = [], (head[0] if head else mem[0])
        while cur in nxt and pre(cur) == p and cur not in seq:
            seq.append(cur)
            cur = nxt[cur]
        order[p] = seq
    chains[f]['order'] = order

# ── 2. 종단 작도 순서 ↔ flo 체인 순서
pb = V.profile_bars(ctx['profile'], cfg, verbose=False) if ctx['profile'] else None
print('\n' + '=' * 78)
print('\n── 유향 대조 (종단 작도 순서 vs flo 체인 순서)')
if not pb:
    print('   종단면도 없음 — 건너뜀')
else:
    drawn = collections.defaultdict(list)          # 계통 → 종단 x순 라벨
    for row, x, nm in sorted(pb['texts'], key=lambda t: (t[0], t[1])):
        drawn[pre(nm)].append(nm)

    def inversions(seq, rank):
        v = [rank[s] for s in seq if s in rank]
        return sum(1 for i in range(len(v)) for j in range(i + 1, len(v)) if v[i] > v[j]), len(v)

    rev = []
    for f, ch in chains.items():
        for p, seq in sorted(ch['order'].items()):
            if len(seq) < 3:
                continue
            rank = {n: i for i, n in enumerate(seq)}
            d = drawn.get(p) or []
            inv, n = inversions(d, rank)
            if n < 3:
                print('   %-10s 종단 라벨 %d개 — 비교 불가' % (p, n))
                continue
            tot = n * (n - 1) // 2
            ratio = inv / tot if tot else 0
            mark = ''
            if ratio > 0.7:
                mark = '  ★ 유향 반대로 작도'
                rev.append(p)
                findings.append('%s 계통이 종단에 유향 반대로 작도됨' % p)
            elif ratio > 0.15:
                mark = '  ★ 순서 뒤섞임'
                rev.append(p)
                findings.append('%s 계통 종단 작도 순서 뒤섞임(%.0f%%)' % (p, ratio * 100))
            print('   %-10s flo %3d / 종단 %3d / 역순비 %5.1f%%%s' % (p, len(seq), n, ratio * 100, mark))
    if not rev:
        print('   → 전 계통 유향 일치')

# ── 3. 압송·펌프장
print('\n── 압송 대조')
for f, rows in flo.items():
    press = [r for r in rows if r['D'] and float(r['D']) <= DMAX]
    키 = sorted({V.mother(r['up']) for r in press})
    print('   %s : 압송 %d구간 / %.1f m / 모관번호 %d개 → %s'
          % (f, len(press), sum(r['L'] for r in press), len(키), ', '.join(키)))
if ctx['profile']:
    import ezdxf
    doc = ezdxf.readfile(str(ctx['profile']))
    pl = []

    def h(e, t, ox, oy):
        if t in ('TEXT', 'ATTRIB'):
            s = (e.dxf.text or '').strip()
            if P['pressure_word'] in s:
                pl.append((V.rowof(e.dxf.insert[1] + oy, P['row_pitch']),
                           round(e.dxf.insert[0] + ox, 1), s))
    V.walk(doc, doc.modelspace(), h)
    print('   종단 "압송" 라벨 %d개 / 행별 %s'
          % (len(pl), dict(collections.Counter(p[0] for p in pl).most_common())))
    print('   문구 종류:', dict(collections.Counter(p[2] for p in pl).most_common(6)))

    # ★ 종단 관종·관경 주기(「신설압송, D=80MM」 국소y 112)는 **구간마다가 아니라 관종이
    #   바뀌는 지점에 한 번**씩 찍힌다(한 회차: 막대 785개에 주기 116개). 따라서 압송
    #   모관번호 하나하나에 라벨을 요구하면 오탐이 난다 — **연속 압송 run** 단위로 센다.
    #   (예: XX-A-024 → XX-A-025 는 2단 압송이지만 도면상 838.2 m 연속이라 라벨 1개가 맞다)
    if pb:
        seg = collections.defaultdict(list)
        for row, x, nm in sorted(pb['texts'], key=lambda t: (t[0], t[1])):
            seg[row].append(nm)
        bar = collections.defaultdict(list)
        for row, x0, x1 in sorted(pb['bars']):
            bar[row].append((x0, x1))
        span = {}
        for row in seg:
            if len(seg[row]) == len(bar[row]):
                span[row] = list(zip(bar[row], seg[row]))
        drawn_press = set()
        for row, x, _ in pl:
            for (x0, x1), nm in span.get(row, []):
                if x0 - 2 <= x <= x1 + 2:
                    drawn_press.add(nm)
                    break

        # 연속 압송 run 구성
        nxt_all, press = {}, {}
        for f, rows in flo.items():
            for r in rows:
                nxt_all[r['up']] = r['dn']
                press[r['up']] = bool(r['D']) and float(r['D']) <= DMAX
        seen, runs = set(), []
        for up in [r['up'] for rows in flo.values() for r in rows]:
            if not press.get(up) or up in seen:
                continue
            if any(press.get(k) for k, v in nxt_all.items() if v == up):
                continue                      # 상류도 압송 → run 시작점이 아니다
            run, cur = [], up
            while cur in nxt_all and press.get(cur):
                run.append(cur); seen.add(cur); cur = nxt_all[cur]
            if run:
                runs.append((run, cur))
        print('   연속 압송 run %d개' % len(runs))
        nolabel = []
        for run, end in runs:
            ms = []
            for x in run:
                if V.mother(x) not in ms:
                    ms.append(V.mother(x))
            L = sum(r['L'] for rows in flo.values() for r in rows if r['up'] in run)
            has = bool(set(ms) & drawn_press)
            print('     %-32s %7.1f m → 토출 %-12s 종단 압송 주기 %s'
                  % (' + '.join(ms), L, end, 'O' if has else '★ 없음'))
            if not has:
                nolabel.append(' + '.join(ms))
        if nolabel:
            findings.append('종단에 압송 주기가 없는 압송 run: %s' % ' / '.join(nolabel))
        scope = {V.mother(r['up']) for rows in flo.values() for r in rows}
        press_keys = {V.mother(u) for u in press if press[u]}
        only_prof_p = sorted((drawn_press & scope) - press_keys)
        if only_prof_p:
            print('   ★ 종단은 압송 표기인데 flo 는 자연유하인 구간 %d: %s'
                  % (len(only_prof_p), only_prof_p))
            findings.append('종단만 압송 표기: %s' % ', '.join(only_prof_p))

# ── 4. 계통 집합
qg = V.plan_routes(ctx)
flo_pre = {pre(r['up']) for rows in flo.values() for r in rows}
prof_pre = {pre(n) for _, _, n in (pb['texts'] if pb else [])}
qg_pre = {pre(k) for k in qg}
print('\n── 계통 집합')
print('   flo  %d : %s' % (len(flo_pre), ', '.join(sorted(flo_pre))))
only_prof = sorted((prof_pre & qg_pre) and (prof_pre - flo_pre - qg_pre) or (prof_pre - flo_pre - qg_pre))
if only_prof:
    print('   ★ 종단에만 있는 계통 %d : %s' % (len(only_prof), ', '.join(only_prof)))
    findings.append('종단에만 있는 계통: %s' % ', '.join(only_prof))
only_flo = sorted(flo_pre - prof_pre) if pb else []
if only_flo:
    print('   ★ flo 에만 있는 계통 %d : %s' % (len(only_flo), ', '.join(only_flo)))
    findings.append('flo 에만 있는 계통: %s' % ', '.join(only_flo))

판정 = 'PASS' if not findings else 'CHECK'
print('\n' + '=' * 78)
print('A3 판정: %s' % 판정)
for x in findings:
    print('   · %s' % x)

V.save(ctx, 'a3_topology.json',
       dict(판정=판정, 지적=findings,
            계통={f: dict(말단=c['말단'], 합류=c['합류'],
                        계통수=len(c['order'])) for f, c in chains.items()}))
