# -*- coding: utf-8 -*-
"""r5 종단(관저고)  →  profile.json

    python r5_profile.py
    python r5_profile.py --fixed-il 경계관저.csv          # 맨홀,IL — 기존 접속 맨홀·설계사 채택 관저 고정
    python r5_profile.py --straighten XX-100-005:2        # 간선 직선화(시점맨홀:최소 구배구간 수)
    python r5_profile.py --mid off                        # 중간 절점 제약 끔(맨홀 노드만, 검사는 한다)
    python r5_profile.py --special 하월.json               # 자연유하 하천 하월·관매달기 지정

알고리즘(SKILL.md 3절) — 원 파이프라인 종단 단계를 그대로 옮기고 설정·인자만 뺐다
  1. 노드 상한 cap = 지반고 − 토피 − D. 토피 = design.min_cover_m, 노폭 > road_width_limit_m 이면 road_cover_m.
     지장물 교차는 경사가 아니라 **상류 노드 심도**로 푼다: cap ≤ 외저 − 이격 − (BOX 관보호공) − D + S·x
     (S = 직전 반복해의 실제 구간경사, 첫 회는 최소경사). 이 cap → 관저 → 직선화를 --iter(기본 5)회 반복 수렴.
     ★ 지반경사로 한 번에 완화하면 지장물 위반이 생긴다(원 파이프라인에서 12건) — 반복해를 유지할 것.
  2. 위상순(상류 먼저) 순산: 도착관저 = min(cap[하류], 상류관저 − S_min × L). 노드 관저 = min(cap, 도착관저들).
     도착관저 ≠ 노드 관저 = 단차(부관). 고정 관저(--fixed-il · 준공 경계)는 그 값.
     자식이 낮게 도착하면 노드 관저가 따라 내려가므로 부모가 자동으로 수용한다(펌프가 하나 줄어드는 경우).
  3. ★ 맨홀 사이 절점 제약(--mid constrain, 기본): 절점 k(거리비 t)의 상한
        IL_b ≤ IL_a + (GL_k − 토피 − D − IL_a) / t
     t 가 작으면 폭주하므로 ① 지반고 3점 이동중앙값 평활(design.ground_spike_m 이상 단발 함몰만)
     ② 완화 상한 design.cover_relax_max_m — 그보다 더 낮춰야 하면 낮추지 않고 **관 보호공**으로 보고
     ③ 토피제어 맨홀 후보 보고(r1 --split 으로 넣고 다시 돌린다). 검사 제외: 교차 제어점 ±12 m,
     교량 ±25 m, 하천 구간·노치(주변보다 design.notch_m 넘게 낮음) ±10 m.
  4. 간선 직선화(--straighten): 고정 종점·최소경사·노드 상한 아래에서 구배구간 수 k 를 늘려 가며
     design.max_depth_m 를 지키는 최소 k 를 IP 조합 전수로 찾는다.
  5. 경계에 못 닿는 계통(도착관저 < 고정 관저) = 자연유하 불가 → 부족 높이와 **펌프 후보**
     (design.pump_smooth_n 노드 이동중앙값 지반고의 최저점 — 원지반 최저점은 하천 노치에 앉는다)를 보고.
판정: 경사 미달은 ±0.02‰ 허용(관저 mm 반올림 때문에 4.98‰ 로 찍힌다).
"""
import sys, os, math, json, itertools, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R

floor3 = R.floor3


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 종단(profile.json)'))
    ap.add_argument('--fixed-il', action='append', default=[], help='고정 관저 CSV/JSON(맨홀,IL) — 여러 개 가능')
    ap.add_argument('--straighten', action='append', default=[], metavar='시점맨홀:k', help='간선 직선화')
    ap.add_argument('--mid', choices=('constrain', 'check', 'off'), default='constrain',
                    help='맨홀 사이 절점: constrain=제약+검사(기본) / check=검사만 / off=안 봄')
    ap.add_argument('--iter', type=int, default=5, help='교차 제약 반복 수렴 횟수(기본 5)')
    ap.add_argument('--special', default=None, help='자연유하 하월·관매달기 JSON {관번호:{mode,하천|교량}}')
    ap.add_argument('--absorb', action='store_true', help='단차 흡수(유입 계통을 눕혀 부관 낙차를 줄임) — 기본 끔')
    ap.add_argument('--absorb-slope', type=float, default=0.100, help='흡수 최대경사(기본 100‰)')
    ap.add_argument('--absorb-depth', type=float, default=5.0, help='흡수로 내릴 수 있는 지선 최대심도(기본 5 m)')
    a = ap.parse_args()
    C = R.Ctx(a)
    P = C.P
    net, gnd = C.load('net.json'), C.load('ground.json')
    crs = C.load('cross.json', required=False) or {}
    seg, GL = net['seg'], gnd['GL']
    S_MIN = P['min_slope_permil'] / 1000.0
    SEP, PROT_BOX = P['obstacle_sep_m'], P['box_protect_m']
    COV_BASE = P['min_cover_m']
    print('설계기준:')
    R.print_params(P, C.src, ['min_cover_m', 'road_cover_m', 'road_width_limit_m', 'min_slope_permil',
                              'obstacle_sep_m', 'box_protect_m', 'max_depth_m', 'cover_relax_max_m',
                              'ground_spike_m', 'notch_m', 'ground_mismatch_m', 'pump_smooth_n'])

    # ── 노폭 → 토피 기준
    RW = {pid: max([v for v in (w[0], w[1]) if v is not None] or [0]) for pid, w in gnd.get('road_width', {}).items()}

    def cover(pid):
        if P['road_cover_m'] is not None and RW.get(pid, 0) > P['road_width_limit_m']:
            return P['road_cover_m']
        return COV_BASE

    DIA = {pid: s['관경'] / 1000.0 for pid, s in seg.items()}

    # ── 교차(지장물·상수관) + 자연유하 하월
    OBS = {k: [dict(o) for o in v] for k, v in (crs.get('gravity') or {}).items() if k in seg}
    SPEC = R.load_any(a.special) if a.special else {}
    hang = collections.defaultdict(list)
    for pid, sp in SPEC.items():
        if pid not in seg:
            continue
        if sp.get('mode') == '하월':
            for rv in sp.get('하천', []):
                if rv.get('하상') is None:
                    continue
                dout = R.pvc_outer_m(seg[pid]['관경'])
                bot_eff = rv['하상'] - P['bed_cover_m'] - PROT_BOX - (dout - DIA[pid])
                OBS.setdefault(pid, []).append(dict(x=float(rv['b']), kind='하천', 규격='하월(하상 %.2f)' % rv['하상'],
                                                    el=None, bot=round(bot_eff, 3), sep=0.0, prot=0.0, dmm=0,
                                                    w=None, h=None, 구분='하천하월', 출처=rv.get('출처', '--special')))
        elif sp.get('mode') == '매달기':
            for ab in sp.get('교량', []):
                hang[pid].append((float(ab[0]), float(ab[1])))

    def o_sep(o):
        return o['sep'] if 'sep' in o else SEP

    def o_prot(o):
        if 'prot' in o:
            return o['prot']
        return PROT_BOX if o['kind'] == 'BOX' else 0.0

    # ── 고정 관저(경계)
    FIXED_IL, fsrc = {}, {}
    for n, b in (net.get('boundary') or {}).items():
        if b.get('IL') is not None:
            FIXED_IL[n], fsrc[n] = float(b['IL']), b.get('src')
    for fp in a.fixed_il:
        rows = R.load_any(fp)
        items = rows.items() if isinstance(rows, dict) else \
            [((r.get('맨홀') or r.get('name') or r.get('mh_id')), (r.get('IL') or r.get('il') or r.get('관저고'))) for r in rows]
        for n, v in items:
            if n and v not in (None, ''):
                FIXED_IL[str(n)], fsrc[str(n)] = float(v), os.path.basename(fp)
    print('고정 관저 %d개: %s' % (len(FIXED_IL), ', '.join('%s=%.3f' % kv for kv in list(FIXED_IL.items())[:8])))

    # ── 위상
    downs, ups, nodes = collections.defaultdict(list), collections.defaultdict(list), set()
    for pid, s in seg.items():
        downs[s['상류맨홀']].append(pid)
        ups[s['하류맨홀']].append(pid)
        nodes |= {s['상류맨홀'], s['하류맨홀']}
    order = R.topo_order(nodes, [(s['상류맨홀'], s['하류맨홀']) for s in seg.values()])
    free_term = [n for n in nodes if not downs[n] and n not in FIXED_IL and n not in net['force']]
    if free_term:
        print('  ! 고정 관저 없는 자연유하 말단 %d: %s (자유 말단으로 계산)' % (len(free_term), free_term[:8]))

    # ── 맨홀 사이 절점(평활·제외구간)
    spike, notch = P['ground_spike_m'], P['notch_m']
    MIDP, MISMATCH = {}, {}
    for pid, pts in (gnd.get('prof') or {}).items():
        if pid not in seg or not pts:
            continue
        L = seg[pid]['연장']
        raw = [p[1] for p in pts]
        ss = [p[0] for p in pts]
        # 맨홀 지반고(측량·표)와 사이 표본(DEM)의 출처가 달라 양 끝에서 크게 어긋나면 그 관은 제약에서 뺀다(검사만)
        mis = 0.0
        for gn, s0 in ((GL.get(seg[pid]['상류맨홀']), 0.0), (GL.get(seg[pid]['하류맨홀']), L)):
            near = [p[1] for p in pts if abs(p[0] - s0) <= 4.0]
            if gn is not None and near:
                mis = max(mis, min(abs(z - gn) for z in near))
        if mis > P['ground_mismatch_m']:
            MISMATCH[pid] = round(mis, 2)
        use = raw[:]
        for i in range(1, len(raw) - 1):
            med = sorted(raw[i - 1:i + 2])[1]
            if raw[i] < med - spike:
                use[i] = med
        ex_ranges = [(o['x'] - 12, o['x'] + 12, '교차제어') for o in OBS.get(pid, [])]
        for sp in (gnd.get('spans') or {}).get(pid, []):
            pad = 25 if sp['kind'] == '교량' else 10
            ex_ranges.append((sp['a'] - pad, sp['b'] + pad, sp['kind']))
        for a0, b0 in hang.get(pid, []):
            ex_ranges.append((a0 - 25, b0 + 25, '관매달기'))
        if pid in MISMATCH:
            ex_ranges.append((-1, L + 1, '지반출처차'))
        for i, s in enumerate(ss):
            nb = [raw[j] for j in range(len(ss)) if 4 <= abs(ss[j] - s) <= 12]
            if len(nb) >= 2 and raw[i] < sorted(nb)[len(nb) // 2] - notch:
                ex_ranges.append((s - 10, s + 10, '하천노치'))
        rows = []
        for s, r0, u in zip(ss, raw, use):
            ex = next((t for a0, b0, t in ex_ranges if a0 <= s <= b0), None)
            if 0 < s < L:
                rows.append((s, r0, u, ex))
        MIDP[pid] = rows

    relax = P['cover_relax_max_m']
    MIDLOG = {}

    def mid_arrival(pid, ILu, a0):
        """맨홀 사이 절점 토피 → 하류 관저 상한. 완화 상한 넘는 부분은 낮추지 않는다(관 보호공)."""
        if a.mid != 'constrain' or pid not in MIDP or ILu is None:
            return a0
        L, D, cv = seg[pid]['연장'], DIA[pid], cover(pid)
        ub, bind = a0, None
        for s, r0, u, ex in MIDP[pid]:
            if ex:
                continue
            t = s / L
            lim = ILu + (u - cv - D - ILu) / t
            if lim < ub:
                ub, bind = lim, s
        if bind is None:
            return a0
        lo = a0 - relax
        if ub < lo:
            MIDLOG[pid] = dict(종류='완화상한', 측점=round(bind, 2), 필요하향=round(a0 - ub, 3), 적용하향=round(relax, 3))
            return floor3(lo)
        MIDLOG[pid] = dict(종류='중간제약', 측점=round(bind, 2), 필요하향=round(a0 - ub, 3), 적용하향=round(a0 - ub, 3))
        return floor3(ub)

    STRAIGHTEN = collections.OrderedDict()
    for it in a.straighten:
        k, _, v = it.partition(':')
        STRAIGHTEN[k] = int(v or 2)
    DEPTH_MAX = P['max_depth_m']
    ITER_N = max(1, a.iter)
    SLOPE_PREV = {}
    STRAIGHT_LOG = []
    ARR_NAT = {}
    for _ITER in range(ITER_N):
        MIDLOG.clear()
        cap = {}
        for n in nodes:
            gl = GL.get(n)
            cs = [cover(p) for p in downs[n] + ups[n]]
            Dn = max([DIA[p] for p in downs[n] + ups[n]] or [P['min_dia_mm'] / 1000.0])
            v = (gl - (max(cs) if cs else COV_BASE) - Dn) if gl is not None else 99.0
            for pid in downs[n]:
                # 교차 제약은 상류 노드 심도로 — 교차점까지의 하강은 직전 반복해의 실제 구간경사를 인정
                sg_ = max(S_MIN, SLOPE_PREV.get(pid, 0.0))
                for o in OBS.get(pid, []):
                    v = min(v, o['bot'] - o_sep(o) - o_prot(o) - DIA[pid] + sg_ * o['x'])
            cap[n] = math.floor(v * 1000) / 1000
        IL, ARR = {}, {}
        for n in order:
            if n in FIXED_IL:
                IL[n] = FIXED_IL[n]
                for pid in ups[n]:
                    u = seg[pid]['상류맨홀']
                    if IL.get(u) is not None:     # 진단용: 최소경사로 내려왔을 때 도착 관저(지반·지장물 상한 무시)
                        ARR_NAT[pid] = floor3(IL[u] - S_MIN * seg[pid]['연장'])
                    ARR[pid] = FIXED_IL[n]
                continue
            cands = []
            for pid in ups[n]:
                u = seg[pid]['상류맨홀']
                aa = cap[n] if IL.get(u) is None else min(cap[n], IL[u] - S_MIN * seg[pid]['연장'])
                aa = math.floor(aa * 1000) / 1000
                aa = mid_arrival(pid, IL.get(u), aa)
                ARR[pid] = aa
                cands.append(aa)
            IL[n] = math.floor(min([cap[n]] + cands) * 1000) / 1000

        # ── 간선 직선화 (원 방식: 고정 종점 · 최소경사 · 노드 상한, 심도상한 지키는 최소 구배구간 수)
        STRAIGHT_NODES = set()
        for start, KSEG in STRAIGHTEN.items():
            if start not in IL:
                continue
            rt0 = next((sg['노선'] for pid, sg in seg.items() if sg['상류맨홀'] == start), None)
            chain, cur_n = [], start
            while True:
                nx = [p for p in downs.get(cur_n, []) if seg[p]['노선'] == rt0 and p not in chain]
                if not nx:
                    break
                chain.append(nx[0])
                cur_n = seg[nx[0]]['하류맨홀']
            if len(chain) < 2:
                continue
            S, U, NM = [], [], []
            acc = 0.0
            for pid in chain:
                NM.append(seg[pid]['상류맨홀'])
                S.append(acc)
                U.append(cap[seg[pid]['상류맨홀']])
                acc += seg[pid]['연장']
            NM.append(cur_n)
            S.append(acc)
            U.append(IL[cur_n])
            U[0] = min(U[0], IL[NM[0]])
            y0, yN, LT = U[0], U[-1], S[-1]

            def prof(ips):
                idx = [0] + list(ips) + [len(S) - 1]
                y = [y0] + [None] * (len(idx) - 2) + [yN]
                for q in range(1, len(idx) - 1):
                    y[q] = U[idx[q]]
                for _ in range(200):
                    moved = False
                    for q in range(1, len(idx) - 1):
                        cap2 = U[idx[q]]
                        for nb in (q - 1, q + 1):
                            i0, y00 = idx[nb], y[nb]
                            for t in range(min(idx[q], i0) + 1, max(idx[q], i0)):
                                f = (S[t] - S[i0]) / (S[idx[q]] - S[i0])
                                if f > 1e-9:
                                    cap2 = min(cap2, y00 + (U[t] - y00) / f)
                        if cap2 < y[q] - 1e-6:
                            y[q] = cap2
                            moved = True
                    if not moved:
                        break
                out = []
                for t in range(len(S)):
                    il = None
                    for q in range(len(idx) - 1):
                        if idx[q] <= t <= idx[q + 1]:
                            f = ((S[t] - S[idx[q]]) / (S[idx[q + 1]] - S[idx[q]])) if idx[q + 1] > idx[q] else 0.0
                            il = y[q] + (y[q + 1] - y[q]) * f
                            break
                    if il > U[t] + 1e-6:
                        return None
                    out.append(il)
                for q in range(len(idx) - 1):
                    ds = S[idx[q + 1]] - S[idx[q]]
                    if ds > 0 and (y[q] - y[q + 1]) / ds < S_MIN - 1e-9:
                        return None
                return out

            best, used_k = None, KSEG
            for k in range(KSEG, KSEG + 3):
                cand = None
                for ips in itertools.combinations(range(1, len(S) - 1), k - 1):
                    r = prof(list(ips))
                    if r is None:
                        continue
                    d = max(GL[NM[t]] - r[t] for t in range(len(S)) if GL.get(NM[t]))
                    if cand is None or d < cand[0]:
                        cand = (d, list(ips), r)
                if cand is None:
                    continue
                if best is None:
                    best, used_k = cand, k
                if cand[0] <= DEPTH_MAX + 1e-6:
                    best, used_k = cand, k
                    break
            if best is None:
                if _ITER == ITER_N - 1:
                    print('[직선화] %s : 해 없음 — 현행 유지' % start)
                continue
            for t, n in enumerate(NM):
                IL[n] = math.floor(best[2][t] * 1000) / 1000
            STRAIGHT_NODES.update(NM)
            cset = set(chain)
            for pid, sg in seg.items():
                u, d = sg['상류맨홀'], sg['하류맨홀']
                if pid in cset:
                    ARR[pid] = IL[d]
                elif d in FIXED_IL:
                    ARR[pid] = FIXED_IL[d]
                elif d in IL and u in IL:
                    aa = math.floor(min(cap[d], IL[u] - S_MIN * sg['연장']) * 1000) / 1000
                    ARR[pid] = mid_arrival(pid, IL[u], aa)
            if _ITER == ITER_N - 1:
                msg = '[직선화] %s→%s  %d구간 %.1fm → 구배구간 %d개(IP %s) · 심도 최대 %.2f m%s' % (
                    start, NM[-1], len(chain), LT, used_k, ', '.join(NM[i] for i in best[1]), best[0],
                    '' if best[0] <= DEPTH_MAX + 1e-6 else '  ★심도상한 %.2f 초과' % DEPTH_MAX)
                print(msg)
                STRAIGHT_LOG.append(dict(시점=start, 종점=NM[-1], 구간=len(chain), 구배구간=used_k,
                                         IP=[NM[i] for i in best[1]], 최대심도=round(best[0], 3)))
        _new = {}
        for pid, sg in seg.items():
            u = sg['상류맨홀']
            if u in IL and pid in ARR and sg['연장']:
                _new[pid] = max(0.0, (IL[u] - ARR[pid]) / sg['연장'])
        SLOPE_PREV = _new

    # ── 단차 흡수(선택, 원 파이프라인 기본 끔)
    if a.absorb:
        S_ABS, DEPTH_LIM = a.absorb_slope, a.absorb_depth
        for _ in range(6):
            moved = False
            for n in reversed(order):
                if n in FIXED_IL or n not in IL:
                    continue
                need = IL[n]
                for pid in downs.get(n, []):
                    d = seg[pid]['하류맨홀']
                    if d in IL:
                        need = min(need, IL[d] + S_ABS * seg[pid]['연장'])
                if n not in STRAIGHT_NODES and GL.get(n) is not None:
                    need = max(need, GL[n] - DEPTH_LIM)
                need = math.floor(need * 1000) / 1000
                if need < IL[n] - 1e-6:
                    IL[n] = need
                    moved = True
            if not moved:
                break
        for pid, sg in seg.items():
            u, d = sg['상류맨홀'], sg['하류맨홀']
            if u not in IL or d not in IL or pid not in ARR:
                continue
            low = max(IL[d], IL[u] - S_ABS * sg['연장'])
            if low < ARR[pid] - 1e-6:
                ARR[pid] = math.floor(low * 1000) / 1000

    # ── 점검
    tolS = 0.02
    rows = {}
    for pid, s in seg.items():
        u, dn = s['상류맨홀'], s['하류맨홀']
        ilu, ild, L = IL[u], ARR[pid], s['연장']
        Sp = (ilu - ild) / L * 1000.0
        mids = []
        prot = []
        for x, r0, use, ex in MIDP.get(pid, []):
            il = ilu + (ild - ilu) * x / L
            cvr = round(r0 - il - DIA[pid], 3)
            cvs = round(use - il - DIA[pid], 3)
            mids.append((x, cvr, cvs, ex))
            if not ex and cvs < cover(pid) - 0.005:
                prot.append(dict(측점=x, 토피=cvs, 원지반토피=cvr, t=round(x / L, 3)))
        rows[pid] = dict(경사=Sp, 중간최소토피=min([m[2] for m in mids if not m[3]], default=None),
                         중간최소토피_원지반=min([m[1] for m in mids], default=None), 보호공=prot,
                         제외=sorted({m[3] for m in mids if m[3]}))
    low = [p for p, r in rows.items() if r['경사'] < P['min_slope_permil'] - tolS]
    covb = [p for p, s in seg.items() if GL.get(s['하류맨홀']) is not None
            and GL[s['하류맨홀']] - ARR[p] - DIA[p] < cover(p) - 0.005]
    bad = []
    for pid, s in seg.items():
        L = s['연장']
        for o in OBS.get(pid, []):
            il = IL[s['상류맨홀']] + (ARR[pid] - IL[s['상류맨홀']]) * (o['x'] / L if L else 0)
            marg = o['bot'] - o_sep(o) - o_prot(o) - (il + DIA[pid])
            if marg < -0.005:
                bad.append(dict(관번호=pid, 측점=o['x'], 규격=o.get('규격'), 여유=round(marg, 3)))
    dep = sorted(((round(GL[n] - IL[n], 2), n) for n in nodes if GL.get(n) is not None and n in IL), reverse=True)
    drop = sorted((round(ARR[p] - IL[s['하류맨홀']], 3), p, s['하류맨홀']) for p, s in seg.items()
                  if ARR[p] - IL[s['하류맨홀']] > 0.05)
    protect = [dict(관번호=p, **q) for p, r in rows.items() for q in r['보호공']]
    ctrl = [q for q in protect if q['t'] < 0.5]

    # ── 고정 관저가 노드 상한(토피·지장물)보다 높은 곳 — 설계사 채택값·준공값과 기준의 충돌
    fixed_over = [dict(맨홀=n, 고정관저=v, 상한=cap[n], 초과=round(v - cap[n], 3))
                  for n, v in FIXED_IL.items() if n in cap and v > cap[n] + 0.005]

    # ── 자연유하 불가 진단 + 펌프 후보
    pump_diag = []
    nsm = int(P['pump_smooth_n'])
    for pid, an in ARR_NAT.items():
        n = seg[pid]['하류맨홀']
        if an < FIXED_IL[n] - 0.005:
            path, cur = [], seg[pid]['상류맨홀']
            path.append(n)
            while True:
                path.append(cur)
                inc = ups.get(cur, [])
                if not inc:
                    break
                cur = max(inc, key=lambda q: seg[q]['연장'])
                cur = seg[cur]['상류맨홀']
                if cur in path:
                    break
            path = path[::-1]
            g = [GL.get(x) for x in path]
            sm = []
            for i in range(len(path)):
                w = [v for v in g[max(0, i - nsm // 2):i + nsm // 2 + 1] if v is not None]
                sm.append(sorted(w)[len(w) // 2] if w else None)
            iv = [i for i in range(len(path) - 1) if sm[i] is not None]
            imin = min(iv, key=lambda i: sm[i]) if iv else None
            graw = [v for v in g if v is not None]
            rmin = min(range(len(g)), key=lambda i: g[i] if g[i] is not None else 1e9)
            pump_diag.append(dict(경계=n, 유입관=pid, 고정관저=FIXED_IL[n], 자연도착=an,
                                  부족_m=round(FIXED_IL[n] - an, 3),
                                  지반최저=dict(맨홀=path[rmin], GL=g[rmin]),
                                  최저후상승_m=round(max(graw[rmin:] or [0]) - (g[rmin] or 0), 2) if graw else None,
                                  펌프후보=dict(맨홀=path[imin], 평활GL=sm[imin]) if imin is not None else None,
                                  경로=path))

    print('=== 종단 ===')
    S_all = sorted(r['경사'] for r in rows.values())
    if S_all:
        print(' 구간 %d  경사 최소 %.2f / 중앙 %.2f / 최대 %.1f ‰' % (len(rows), S_all[0], S_all[len(S_all) // 2], S_all[-1]))
    print(' 경사 %.0f‰ 미만(−0.02 허용) %d · 하류토피 미달 %d · 지장물 위반 %d' % (P['min_slope_permil'], len(low), len(covb), len(bad)))
    if dep:
        print(' 심도 최대 %.2f (%s) 평균 %.2f · 6m 초과 %d' % (dep[0][0], dep[0][1], sum(d[0] for d in dep) / len(dep),
                                                      sum(1 for d in dep if d[0] > 6)))
    print(' 부관/단차 %d개소 (%.1fm 이상 %d) 최대 %.2fm' % (len(drop), P['drop_manhole_m'],
                                                    sum(1 for d in drop if d[0] >= P['drop_manhole_m']),
                                                    drop[-1][0] if drop else 0.0))
    if MIDP:
        mm = [r['중간최소토피'] for r in rows.values() if r['중간최소토피'] is not None]
        if a.mid == 'constrain':
            print(' 맨홀 사이 최소토피 %.2f · 중간제약 %d구간 · 완화상한 걸림 %d구간 → 토피 미달 절점 %d'
                  '(관 보호공 %d구간, 토피제어 맨홀 후보 %d점 — r1 --split)'
                  % (min(mm) if mm else float('nan'), sum(1 for v in MIDLOG.values() if v['종류'] == '중간제약'),
                     sum(1 for v in MIDLOG.values() if v['종류'] == '완화상한'), len(protect),
                     len({q['관번호'] for q in protect}), len(ctrl)))
        else:
            print(' 맨홀 사이(검사만, --mid %s) 최소토피 %.2f · 토피 미달 절점 %d (%d구간)'
                  % (a.mid, min(mm) if mm else float('nan'), len(protect), len({q['관번호'] for q in protect})))
    if MISMATCH:
        print(' ! 맨홀 지반고 ↔ 사이 표본 출처 불일치(>%.1f m) %d구간 — 제약·미달 집계에서 뺐다(측량 종단으로 확인): %s'
              % (P['ground_mismatch_m'], len(MISMATCH), ', '.join('%s(%.1f)' % kv for kv in list(MISMATCH.items())[:6])))
    for f in fixed_over:
        print(' ★ 고정 관저가 상한보다 높음: %s 고정 %.3f > 상한 %.3f (+%.3f m) — 토피·지장물 기준과 충돌'
              % (f['맨홀'], f['고정관저'], f['상한'], f['초과']))
    for d in pump_diag:
        print(' ★ 자연유하 불가: %s ← %s  부족 %.2f m · 지반 최저 %s(%.2f) 이후 +%s m · 펌프 후보 %s'
              % (d['경계'], d['유입관'], d['부족_m'], d['지반최저']['맨홀'], d['지반최저']['GL'] or 0,
                 d['최저후상승_m'], d['펌프후보']))

    out = dict(meta=dict(created=R.stamp(), step='r5_profile', mid=a.mid, iter=ITER_N, straighten=a.straighten,
                         fixed_il=a.fixed_il, special=a.special, absorb=a.absorb,
                         params=R.params_table(P, C.src, ['min_cover_m', 'road_cover_m', 'road_width_limit_m',
                                                          'min_slope_permil', 'obstacle_sep_m', 'box_protect_m',
                                                          'max_depth_m', 'cover_relax_max_m', 'ground_spike_m',
                                                          'notch_m', 'ground_mismatch_m', 'bed_cover_m'])),
               IL=IL, ARR=ARR, cap=cap, FIXED_IL=FIXED_IL, FIXED_src=fsrc,
               cover={p: cover(p) for p in seg}, OBS=OBS, mid=rows, midlog=MIDLOG,
               checks=dict(경사미달=low, 하류토피미달=covb, 지장물위반=bad, 단차=drop,
                           심도최대=dep[0] if dep else None, 보호공=protect, 토피제어맨홀후보=ctrl,
                           고정관저_상한초과=fixed_over, 지반출처차=MISMATCH),
               straighten=STRAIGHT_LOG, pump_diag=pump_diag, ARR_nat=ARR_NAT)
    print('→ %s' % C.save('profile.json', out))


if __name__ == '__main__':
    main()
