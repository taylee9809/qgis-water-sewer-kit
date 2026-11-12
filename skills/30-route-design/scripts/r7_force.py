# -*- coding: utf-8 -*-
"""r7 압송관 종단 직선화 + 맨홀펌프장 용량(전양정·동력)  →  force.json

    python r7_force.py
    python r7_force.py --special 압송특수.json        # {펌프장:{mode:'하월'|'매달기', 하천:[{a,b,하상}], 교량:[[a,b]]}}
    python r7_force.py --fixprof 지정종단.json        # {펌프장:[[측점,관저],…]} 설계사·사용자 지정 종단을 채택
    python r7_force.py --maxseg XX-102-004:3          # 구배구간 수 지정
    python r7_force.py --pump-q inflow                # 펌프 용량 = 유입 기준(원 계산서) / velocity = 압송 0.6 m/s 확보(기본)

압송 종단(원 파이프라인 방식) — 지반 추종(토피 고정)은 요철을 관에 옮겨 국부 저점(퇴적)·고점(공기)을 만든다.
  ① 지반고: DEM 출처 점만 3점 이동중앙값(2회) 평활, 측량 점은 원본. 평활값보다 force_spike_m 넘게 낮으면 하천·교량 의심.
  ② 통로: 상한 U = GL − force_cover_m − D, 하한 L = GL − force_depth_m.
     지장물: 상한 = 외저 − 이격 − (BOX·U·V 관보호공) − D. 하천 하월: 하상 − bed_cover_m − 관보호공 − D.
     관매달기: 교면 − hang_depth_m 직선 고정 + 교대 전후 전이점(abutment_trans_m)은 GL − 토피 − D.
  ③ 통로 안에서 꺾임(IP) 최소 직선열(greedy min-link + 1-step lookahead)
  ④ force_min_seg_m 미만 구간 병합 → 상향 완화 → 상한 위반점 분할(수렴까지)
  ⑤ 남은 국부 저점 = 배니밸브, 국부 고점 = 공기밸브 위치로 보고
  지정 종단(--fixprof)이 지장물을 관통하면 그 측점에 외저 기준 정점을 넣고, 앞쪽에서 첫 정점→삽입점 직선보다
  낮은 정점은 지운다(불필요한 국부 저점 방지).

맨홀펌프장(SKILL.md 7절, 원 계산서 방식 — 신설만 산정, 기존은 준공 스펙 고정)
  HWL = 유입관저 − 0.05, LWL = HWL − 0.5, 저면 = LWL − 0.55 · ha = 압송관 최상단 관저(BOP) − LWL
  hf = 기계배관(50A) 손실 · hl = Hazen-Williams C=100(후단 관경 = 압송관경 — 80A/D75 에 따라 1.37배 차, 보고에 적는다)
  H = ROUND(ha + hf + hl + 1.5, 0) · P = 0.163·q·H / η(0.23) × 1.15 → 표준전동기
  q: inflow = max(시간최대 올림 0.01, 0.10 ㎥/min) / velocity = max(inflow, 압송 0.6 m/s 확보 유량)
"""
import sys, os, io, math, itertools, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R

EPS = 1e-6
DIA_F = COV_MIN = DEPTH_SOFT = DEPTH_MAX = LMIN_SEG = SPIKE = SEP = PROT_BOX = COV_RIV = HANG = TRANS = None


# ---------- ① 지반고 정리 (원 방식 그대로) ----------
def ground(pts, obs=(), spec=None):
    raw, seen = [], set()
    for t, x, y, isv, z, src in pts:
        if z is None:
            continue
        k = round(t, 2)
        if k in seen:
            continue
        seen.add(k)
        raw.append([k, x, y, isv, z, src])
    raw.sort(key=lambda r: r[0])
    g = [r[4] for r in raw]
    for _ in range(2):                                    # DEM 점만 3점 이동중앙값
        ng = g[:]
        for i in range(1, len(g) - 1):
            if str(raw[i][5]).startswith('DEM'):
                ng[i] = sorted([g[i - 1], g[i], g[i + 1]])[1]
        g = ng
    C, spikes = [], []
    for i, r in enumerate(raw):
        gl = round(g[i], 2)
        if r[4] < gl - SPIKE:
            spikes.append(dict(s=r[0], gl_raw=r[4], gl_use=gl, src=r[5]))
        C.append(dict(s=r[0], x=r[1], y=r[2], vtx=r[3], gl=gl, gl_raw=r[4], src=r[5], obs=None,
                      U=round(gl - COV_MIN - DIA_F, 4), L=round(gl - DEPTH_SOFT, 4),
                      LH=round(gl - DEPTH_MAX, 4)))
    for o in obs:                                         # 지장물 교차: 측점 삽입 + 상한 조이기
        if o.get('외저') is None:
            continue
        t = float(o['s'])
        j = min(range(len(C)), key=lambda k: abs(C[k]['s'] - t))
        if abs(C[j]['s'] - t) > 0.5:
            k = next((k for k in range(len(C)) if C[k]['s'] > t), len(C) - 1)
            a, b = C[max(k - 1, 0)], C[k]
            f = (t - a['s']) / (b['s'] - a['s']) if b['s'] > a['s'] else 0.0
            gl = round(a['gl'] + (b['gl'] - a['gl']) * f, 2)
            C.insert(k, dict(s=round(t, 2), x=o['x'], y=o['y'], vtx=0, gl=gl, gl_raw=gl,
                             src='지장물', obs=None,
                             U=round(gl - COV_MIN - DIA_F, 4), L=round(gl - DEPTH_SOFT, 4),
                             LH=round(gl - DEPTH_MAX, 4)))
            j = k
        pb = PROT_BOX if str(o.get('표기') or '').strip().lower()[:1] in ('b', 'u', 'v') or 'x' in str(o.get('표기') or '').lower() else 0.0
        cap = round(float(o['외저']) - SEP - pb - DIA_F, 4)
        if cap < C[j]['U']:
            C[j]['U'] = cap
            C[j]['LH'] = min(C[j]['LH'], cap - 0.001)
            C[j]['L'] = min(C[j]['L'], cap - 0.001)
        C[j]['obs'] = '%s %s(외저 %.2f)' % (o.get('구분', ''), o.get('표기') or ('D%d' % (o.get('관경mm') or 0)), o['외저'])
        C[j]['obs_d'] = dict(규격=o.get('표기') or ('D%d' % (o.get('관경mm') or 0)),
                             외저=round(float(o['외저']), 3),
                             el=(round(float(o['관저']), 3) if o.get('관저') is not None else None),
                             kind=('BOX' if (o.get('형식') or '') not in ('', '원형관') else '원형관'),
                             dmm=float(o.get('관경mm') or 0),
                             w=o.get('규격폭m'), h=o.get('규격높이m'))

    def put(t):
        j = min(range(len(C)), key=lambda k: abs(C[k]['s'] - t))
        if abs(C[j]['s'] - t) <= 0.3:
            return j
        k = next((k for k in range(len(C)) if C[k]['s'] > t), len(C) - 1)
        a, b = C[max(k - 1, 0)], C[k]
        f = (t - a['s']) / (b['s'] - a['s']) if b['s'] > a['s'] else 0.0
        gl = round(a['gl'] + (b['gl'] - a['gl']) * f, 2)
        C.insert(k, dict(s=round(t, 2), x=round(a['x'] + (b['x'] - a['x']) * f, 3),
                         y=round(a['y'] + (b['y'] - a['y']) * f, 3), vtx=1,
                         gl=gl, gl_raw=gl, src='보간', obs=None,
                         U=round(gl - COV_MIN - DIA_F, 4), L=round(gl - DEPTH_SOFT, 4),
                         LH=round(gl - DEPTH_MAX, 4)))
        return k

    if spec:
        if spec.get('mode') == '하월':
            for rv in spec.get('하천', []):
                z = rv.get('하상')
                if z is None:
                    continue
                a, b = put(rv['a']), put(rv['b'])
                cap = round(z - COV_RIV - PROT_BOX - DIA_F, 4)
                for k in range(min(a, b), max(a, b) + 1):
                    C[k]['U'] = min(C[k]['U'], cap)
                    C[k]['L'] = min(C[k]['L'], cap - 0.001)
                    C[k]['LH'] = min(C[k]['LH'], cap - 0.001)
                    C[k]['zone'] = '하월(하상 %.2f)' % z
        elif spec.get('mode') == '매달기':
            for a0, b0 in spec.get('교량', []):
                a, b = put(a0), put(b0)
                a, b = min(a, b), max(a, b)
                va = round(C[a]['gl'] - HANG, 4)
                vb = round(C[b]['gl'] - HANG, 4)
                sa, sb = C[a]['s'], C[b]['s']
                for k in range(a, b + 1):
                    f = (C[k]['s'] - sa) / (sb - sa) if sb > sa else 0.0
                    v = va + (vb - va) * f
                    tol = 0.0 if k in (a, b) else 0.003
                    C[k]['U'] = v + tol
                    C[k]['L'] = C[k]['LH'] = v - tol
                    C[k]['zone'] = ('교량첨가 단부' if k in (a, b) else '교량첨가(관매달기)')
                for t0 in (a0 - TRANS, b0 + TRANS):
                    if 0.5 < t0 < C[-1]['s'] - 0.5:
                        k = put(t0)
                        if not C[k].get('zone'):
                            C[k]['U'] = C[k]['L'] = C[k]['LH'] = round(C[k]['gl'] - COV_MIN - DIA_F, 4)
                            C[k]['zone'] = '교대 전이점'
    return C, spikes


def fit_k(C, k):
    """IP 개수 k-1 로 통로를 만족하는 해 중 최대굴착 최소 (전수탐색)"""
    n = len(C)
    must = [i for i in range(1, n - 1) if C[i]['U'] == C[i]['L']]
    free = [i for i in range(1, n - 1) if i not in must]
    need = (k - 1) - len(must)
    if need < 0:
        return None
    best = None
    for comb in itertools.combinations(free, need):
        idx = sorted(set([0] + must + list(comb) + [n - 1]))
        V = [[i, (C[i]['U'] if C[i]['U'] < 1e8 else -1e9)] for i in idx]
        V[0][1] = C[0]['U']
        V[-1][1] = C[-1]['U']
        for q in range(1, len(V) - 1):
            V[q][1] = -1e9 if C[V[q][0]]['U'] > 1e8 else min(C[V[q][0]]['U'], 1e8)
        lowest = min(c['U'] for c in C if c['U'] < 1e8) - 60.0
        for q in range(1, len(V) - 1):
            if C[V[q][0]]['U'] != C[V[q][0]]['L']:
                V[q][1] = lowest
        V = relax_up(C, V)
        okk, dep = True, []
        for t in range(n):
            il = interp(C, V, t)
            if il > C[t]['U'] + 1e-6:
                okk = False
                break
            dep.append(C[t]['gl'] - il)
        if okk and (best is None or max(dep) < best[0]):
            best = (max(dep), [list(v) for v in V])
    return best


def reach(C, i, yi, key='L'):
    lo, hi, j, plo, phi = -1e9, 1e9, i, -1e9, 1e9
    for k in range(i + 1, len(C)):
        ds = C[k]['s'] - C[i]['s']
        if ds < EPS:
            continue
        lo = max(lo, (C[k][key] - yi) / ds)
        hi = min(hi, (C[k]['U'] - yi) / ds)
        if lo > hi + 1e-9:
            return j, plo, phi
        j, plo, phi = k, lo, hi
    return j, plo, phi


def greedy(C):
    V = [[0, C[0]['U']]]
    i, yi = 0, C[0]['U']
    while i < len(C) - 1:
        j, lo, hi = reach(C, i, yi)
        if j == i:
            j, lo, hi = reach(C, i, yi, 'LH')
        if j == i:
            j = i + 1
            lo = hi = (min(C[j]['U'], max(C[j]['LH'], yi)) - yi) / (C[j]['s'] - C[i]['s'])
        best = None
        for f in (1.0, 0.75, 0.5, 0.25, 0.0):
            m = lo + (hi - lo) * f
            yj = min(yi + m * (C[j]['s'] - C[i]['s']), C[j]['U'])
            j2, _, _ = reach(C, j, yj)
            if best is None or (j2, yj) > best[0]:
                best = ((j2, yj), yj)
        V.append([j, best[1]])
        i, yi = j, best[1]
    V[-1] = [len(C) - 1, C[-1]['U']]
    return V


def interp(C, V, t):
    for k in range(len(V) - 1):
        if V[k][0] <= t <= V[k + 1][0]:
            ds = C[V[k + 1][0]]['s'] - C[V[k][0]]['s']
            f = (C[t]['s'] - C[V[k][0]]['s']) / ds if ds else 0.0
            return V[k][1] + (V[k + 1][1] - V[k][1]) * f
    return V[-1][1]


def merge_short(C, V):
    ch = True
    while ch and len(V) > 2:
        ch = False
        for k in range(1, len(V) - 1):
            La = C[V[k][0]]['s'] - C[V[k - 1][0]]['s']
            Lb = C[V[k + 1][0]]['s'] - C[V[k][0]]['s']
            if min(La, Lb) >= LMIN_SEG:
                continue
            W = V[:k] + V[k + 1:]
            if all(C[t]['LH'] - 1e-6 <= interp(C, W, t) <= C[t]['U'] + 1e-6 for t in range(len(C))):
                V, ch = W, True
                break
    return V


def relax_up(C, V, rounds=80):
    for _ in range(rounds):
        moved = 0.0
        for k in range(1, len(V) - 1):
            ik, cap = V[k][0], C[V[k][0]]['U']
            for nb in (k - 1, k + 1):
                i0, y0 = V[nb]
                for t in range(min(ik, i0) + 1, max(ik, i0)):
                    f = (C[t]['s'] - C[i0]['s']) / (C[ik]['s'] - C[i0]['s'])
                    if f > 1e-9:
                        cap = min(cap, y0 + (C[t]['U'] - y0) / f)
            if cap > V[k][1] + 1e-4:
                moved = max(moved, cap - V[k][1])
                V[k][1] = cap
        if moved < 1e-4:
            break
    return V


def repair(C, V):
    """상한(최소토피) 위반이 남으면 최대위반 지점을 정점으로 분할"""
    for _ in range(60):
        worst, wt = 0.0, None
        for t in range(len(C)):
            d = interp(C, V, t) - C[t]['U']
            if d > worst + 1e-9:
                worst, wt = d, t
        if wt is None or worst <= 1e-6:
            break
        V.append([wt, C[wt]['U']])
        V.sort(key=lambda v: v[0])
        V = [v for i, v in enumerate(V) if i == 0 or v[0] != V[i - 1][0]]
    return V


def solve(nm, ch, FOB, SPEC, FIXPROF, MAXSEG, BR):
    C, spikes = ground(ch['pts'], FOB.get(nm, []), SPEC.get(nm))
    V = greedy(C)
    n0 = len(V) - 1
    V = repair(C, merge_short(C, relax_up(C, merge_short(C, V))))
    V = relax_up(C, V)
    tgt = MAXSEG.get(nm)
    if tgt and len(V) - 1 != tgt:
        alt = fit_k(C, tgt)
        if alt:
            V = alt[1]
            print('   [지정] %s 구배구간 %d개로 고정 → 최대굴착 %.2f m' % (nm, tgt, alt[0]))
        else:
            print('   [지정] %s 구배구간 %d개 해 없음 — 최소꺾임안 유지' % (nm, tgt))
    FIXP = FIXPROF.get(nm)
    if FIXP:
        FIXP = [list(q) for q in FIXP]
        for o in FOB.get(nm, []):                       # 지정 종단의 지장물 관통 방지
            if o.get('외저') is None:
                continue
            mk = str(o.get('표기') or '').strip().lower()
            pb = PROT_BOX if (mk[:1] in ('b', 'u', 'v') or 'x' in mk) else 0.0
            capo = round(float(o['외저']) - SEP - pb - DIA_F, 3)
            sv = float(o['s'])
            cur = None
            for q in range(1, len(FIXP)):
                if sv <= FIXP[q][0] + 1e-9:
                    a0, z0 = FIXP[q - 1]
                    a1, z1 = FIXP[q]
                    f = (sv - a0) / max(a1 - a0, 1e-9)
                    cur = z0 + (z1 - z0) * f
                    break
            if cur is None or cur <= capo + 1e-6:
                continue
            FIXP = [v for v in FIXP if abs(v[0] - sv) > 0.01]
            FIXP.append([round(sv, 2), capo])
            FIXP.sort()
            i0 = FIXP.index([round(sv, 2), capo])
            keep = []
            for k in range(i0):
                a0, z0 = FIXP[0]
                f = (FIXP[k][0] - a0) / max(round(sv, 2) - a0, 1e-9)
                zline = z0 + (capo - z0) * f
                if k == 0 or FIXP[k][1] > zline + 1e-6:
                    keep.append(FIXP[k])
            FIXP = keep + FIXP[i0:]
            print('   [지정] %s 누가 %.2f 지장물(%s) 하부통과 → 관저 %.3f (종전 %.3f, %.3f m 하향)'
                  % (nm, sv, o.get('표기'), capo, cur, cur - capo))

        def _fixil(sv):
            if sv <= FIXP[0][0]:
                return FIXP[0][1]
            for q in range(1, len(FIXP)):
                if sv <= FIXP[q][0]:
                    a0, z0 = FIXP[q - 1]
                    a1, z1 = FIXP[q]
                    f = (sv - a0) / max(a1 - a0, 1e-9)
                    return z0 + (z1 - z0) * f
            return FIXP[-1][1]
        V = []
        for a0, z0 in FIXP:
            k = min(range(len(C)), key=lambda q: abs(C[q]['s'] - a0))
            if not V or V[-1][0] != k:
                V.append((k, z0))
        print('   [지정] %s 종단 고정 : 지정 %d정점 (구배구간 %d)' % (nm, len(FIXP), len(V) - 1))

    P_ = []
    for t, c in enumerate(C):
        il = (math.floor(_fixil(c['s']) * 1000) / 1000) if FIXP \
            else (math.floor(interp(C, V, t) * 1000) / 1000)
        P_.append(dict(s=c['s'], x=c['x'], y=c['y'], gl=c['gl'], gl_raw=c['gl_raw'], src=c['src'],
                       obs=c.get('obs'), obs_d=c.get('obs_d'), zone=c.get('zone'),
                       il=round(il, 3), vtx=int(any(v[0] == t for v in V))))
    segs = []
    _VS = [(a0, z0) for a0, z0 in FIXP] if FIXP else [(C[v[0]]['s'], v[1]) for v in V]
    for k in range(len(_VS) - 1):
        a, b = _VS[k][0], _VS[k + 1][0]
        ia = round(math.floor(_VS[k][1] * 1000) / 1000, 3)
        ib = round(math.floor(_VS[k + 1][1] * 1000) / 1000, 3)
        L = round(b - a, 2)
        segs.append(dict(a=a, b=b, L=L, il_a=ia, il_b=ib, H=round(ib - ia, 2),
                         S=round((ib - ia) / L * 1000.0, 2) if L else 0.0))
    bur = [p for p in P_ if not (p.get('zone') or '').startswith('교량첨가')]
    cov = [round(p['gl'] - p['il'] - DIA_F, 3) for p in bur]
    dep = [round(p['gl'] - p['il'], 3) for p in bur]
    ils = [p['il'] for p in P_]
    sag = [P_[i]['s'] for i in range(1, len(P_) - 1) if ils[i] < ils[i - 1] - 0.005 and ils[i] <= ils[i + 1] - 0.005]
    hgh = [P_[i]['s'] for i in range(1, len(P_) - 1) if ils[i] > ils[i - 1] + 0.005 and ils[i] >= ils[i + 1] + 0.005]
    ob = [dict(s=p['s'], txt=p['obs'], il=p['il'], gl=p.get('gl'), **(p.get('obs_d') or {})) for p in P_ if p.get('obs')]
    br = BR.get(nm, {})
    _mode = SPEC.get(nm, {}).get('mode')
    spans = (dict(하월=br.get('하천', [])) if _mode == '하월' else dict(교량=br.get('교량', []), 하천=br.get('하천', [])))
    sp = SPEC.get(nm, {})
    res = dict(L=ch['L'], D=DIA_F, pts=P_, segs=segs, spikes=spikes, obs=ob, spans=spans,
               mode=sp.get('mode'), 하상=sp.get('하천', []), sag=sag, high=hgh, il_top=round(max(ils) + DIA_F, 3))
    print('== %-14s L=%7.2f m · 측점 %2d' % (nm, ch['L'], len(P_)))
    print('   구배구간 %2d개(직선화 전 지반추종 %d절점) · 토피 %.2f~%.2f · 최대굴착 %.2f'
          % (len(segs), len(P_) - 1, min(cov), max(cov), max(dep)))
    print('   국부저점(배니) %d · 국부고점(공기) %d · 토피위반 %d · 하천/교량 의심 %d'
          % (len(sag), len(hgh), sum(1 for c in cov if c < COV_MIN - 0.006), len(spikes)))
    for o in ob:
        print('      ◆ 누가 %.2f m 지장물 %s → 관저 %.3f (여유 %.3f m)'
              % (o['s'], o['txt'], o['il'], o['외저'] - o['il'] - DIA_F - SEP))
    for s_ in spikes:
        print('      ※ 누가 %.2f m 원지반 %.2f (%s) → 설계 %.2f : 하천·교량 횡단 검토' % (s_['s'], s_['gl_raw'], s_['src'], s_['gl_use']))
    return res, n0


# ---------- 맨홀펌프장 (원 계산서 방식) ----------
G_ = 9.8
ITEMS = [('흡입구', '3식', 50, None, 1, 0.5), ('직관', '2식', 50, None, 5, 0.05), ('엘보', '3식', 50, None, 1, 0.17),
         ('TEE', '3식', 50, None, 1, 0.8), ('CHK V/V', '3식', 50, None, 1, 1.5), ('GATE V/V', '3식', 50, None, 1, 0.175),
         ('ELBOW', '3식', 50, None, 1, 0.17), ('토출구', '3식', None, None, 1, 1.0), ('편락관', '편락', None, 50, 1, 0.8)]
MOTOR = [(0.349, 0.4), (0.749, 0.75), (1.499, 1.5), (2.199, 2.2), (3.699, 3.7), (5.499, 5.5),
         (7.499, 7.5), (10.999, 11.0), (14.999, 15.0), (21.999, 22.0)]


def vel(q, D):
    return q * 4 / math.pi / (D / 1000) ** 2 / 60


def hf_mech(q, Dback):
    h = 0.0
    for nm, f_, D1, D2, n_, f in ITEMS:
        D = D1 or Dback
        v = vel(q, D)
        if f_ == '2식':
            h += f * (n_ / (D / 1000)) * v * v / (2 * G_)
        elif f_ == '편락':
            h += f * (vel(q, Dback) - vel(q, D2)) ** 2 / (2 * G_) * n_
        else:
            h += f * v * v / (2 * G_) * n_
    return h


def hl_pipe(q, Dback, L, C=100):
    return 10.666 * C ** -1.85 * (Dback / 1000) ** -4.87 * (q / 60) ** 1.85 * L


def motor(kw):
    for hi, v in MOTOR:
        if kw <= hi:
            return v
    return MOTOR[-1][1]


def main():
    global DIA_F, COV_MIN, DEPTH_SOFT, DEPTH_MAX, LMIN_SEG, SPIKE, SEP, PROT_BOX, COV_RIV, HANG, TRANS
    ap = R.common_args(argparse.ArgumentParser(description='30 압송 종단·펌프장(force.json)'))
    ap.add_argument('--special', default=None, help='하월·관매달기 JSON {펌프장:{mode, 하천, 교량}}')
    ap.add_argument('--fixprof', default=None, help='지정 종단 JSON {펌프장:[[측점,관저],…]}')
    ap.add_argument('--maxseg', action='append', default=[], metavar='펌프장:k', help='구배구간 수 지정')
    ap.add_argument('--pump-q', choices=('velocity', 'inflow'), default='velocity',
                    help='신설 펌프 용량 기준(기본 velocity = 압송 최소유속 확보)')
    a = ap.parse_args()
    C = R.Ctx(a)
    P = C.P
    net, gnd = C.load('net.json'), C.load('ground.json')
    crs = C.load('cross.json', required=False) or {}
    FG = gnd.get('force_pts') or {}
    if not FG:
        print('압송 체인이 없다 — 건너뜀')
        C.save('force.json', dict(meta=dict(created=R.stamp(), step='r7_force'), chains={}, pumps=[]))
        return
    COV_MIN, DEPTH_SOFT, DEPTH_MAX = P['force_cover_m'], P['force_depth_m'], P['force_depth_m']
    LMIN_SEG, SPIKE, SEP, PROT_BOX = P['force_min_seg_m'], P['force_spike_m'], P['obstacle_sep_m'], P['box_protect_m']
    COV_RIV, HANG, TRANS = P['bed_cover_m'], P['hang_depth_m'], P['abutment_trans_m']
    print('설계기준:')
    R.print_params(P, C.src, ['force_dia_mm', 'force_cover_m', 'force_depth_m', 'force_min_seg_m', 'force_spike_m',
                              'obstacle_sep_m', 'box_protect_m', 'bed_cover_m', 'hang_depth_m', 'abutment_trans_m'])
    FOB = crs.get('force') or {}
    SPEC = R.load_any(a.special) if a.special else {}
    FIXPROF = {k: [tuple(q) for q in v] for k, v in (R.load_any(a.fixprof) if a.fixprof else {}).items()}
    MAXSEG = {k: int(v) for k, _, v in (m.partition(':') for m in a.maxseg)}
    BR = {}
    for nm, sps in (gnd.get('spans') or {}).items():
        if nm in FG:
            for sp in sps:
                BR.setdefault(nm, {}).setdefault(sp['kind'], []).append([sp['a'], sp['b']])

    RESULT = {}
    for nm, ch in FG.items():
        DIA_F = (net['force'].get(nm, {}).get('관경') or P['force_dia_mm']) / 1000.0
        RESULT[nm], _ = solve(nm, ch, FOB, SPEC, FIXPROF, MAXSEG, BR)

    # ── 맨홀펌프장 용량
    des = C.load('design.json', required=False)
    pro = C.load('profile.json', required=False)
    prow = []
    if des and pro:
        eta, margin, hvb = P.get('pump_eta', 0.23), P.get('pump_margin', 1.15), P.get('pump_hv_m', 1.5)
        qmin_ = P.get('pump_q_min', 0.10)
        vmin = P['min_velocity_mps']
        for nm, r in RESULT.items():
            fr = des['force_res'].get(nm, {})
            Qd = fr.get('유입누가_Lps', 0.0) * 86.4
            inIL = pro['IL'].get(nm)
            if inIL is None:
                continue
            Db = int(round(r['D'] * 1000))
            Lp = sum(s['L'] for s in r['segs'])
            bop = max(max(s['il_a'], s['il_b']) for s in r['segs'])
            hwl = round(inIL - 0.05, 2)
            lwl = round(hwl - 0.5, 2)
            bot = round(lwl - 0.55, 2)
            qm = Qd / 1440
            q_in = max(math.ceil(qm * 100) / 100, qmin_)
            q_v = math.ceil(vmin * math.pi * (Db / 1000) ** 2 / 4 * 60 * 100) / 100
            q = max(q_in, q_v) if a.pump_q == 'velocity' else q_in
            ha = round(bop - lwl, 2)
            hf = hf_mech(q, Db)
            hl = hl_pipe(q, Db, Lp)
            H = round(ha + hf + hl + hvb)
            kw = 0.163 * q * H / eta * margin
            prow.append(dict(펌프장=nm, 구분='신설', 토출대상=net['force'][nm]['하류맨홀'], 누가유량_m3d=round(Qd, 2),
                             시간최대_m3min=round(qm, 4), 유입관저고=round(inIL, 3), HWL=hwl, LWL=lwl, 저면고=bot,
                             압송관경=Db, 압송연장_m=round(Lp, 1), 압송최상단BOP=round(bop, 3),
                             실양정ha_m=ha, 기계손실hf_m=round(hf, 3), 관로손실hl_m=round(hl, 3),
                             여유양정hv_m=round(H - (ha + hf + hl), 3), 전양정H_m=H,
                             용량_유입기준=q_in, 용량_유속기준=q_v, 적용용량_m3min=q, 용량기준=a.pump_q,
                             압송유속_mps=round(vel(q, Db), 3), 축동력_kW=round(kw, 3), 적용동력_kW=motor(kw),
                             비고='hl 후단관경 D%d 기준(80A 면 hl 약 0.73배) · η=%.2f 여유율 %.2f' % (Db, eta, margin)))
        print('\n맨홀펌프장(신설) %d개소 — 용량 기준 %s' % (len(prow), a.pump_q))
        for p in prow:
            print('  %-14s Q %.2f㎥/일 · 유입관저 %.2f · ha %.2f hf %.2f hl %.2f → H %dm · q %.2f㎥/min(%.2fm/s) · %.2fkW → %.2fkW'
                  % (p['펌프장'], p['누가유량_m3d'], p['유입관저고'], p['실양정ha_m'], p['기계손실hf_m'], p['관로손실hl_m'],
                     p['전양정H_m'], p['적용용량_m3min'], p['압송유속_mps'], p['축동력_kW'], p['적용동력_kW']))
    else:
        print('design.json/profile.json 없음 — 펌프장 용량은 건너뜀')

    out = dict(meta=dict(created=R.stamp(), step='r7_force', special=a.special, fixprof=a.fixprof, maxseg=a.maxseg,
                         pump_q=a.pump_q,
                         params=R.params_table(P, C.src, ['force_dia_mm', 'force_cover_m', 'force_depth_m',
                                                          'force_min_seg_m', 'force_spike_m', 'obstacle_sep_m',
                                                          'box_protect_m', 'bed_cover_m', 'hang_depth_m'])),
               chains=RESULT, pumps=prow)
    print('\n→ %s  (최소토피 %.2f · 최대굴착 %.2f · 최소구간 %.0f m)'
          % (C.save('force.json', out), COV_MIN, DEPTH_MAX, LMIN_SEG))


if __name__ == '__main__':
    main()
