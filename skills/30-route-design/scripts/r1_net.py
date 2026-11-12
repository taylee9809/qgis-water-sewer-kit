# -*- coding: utf-8 -*-
"""r1 노선 → 망(맨홀 노드 · 자연유하 구간 · 압송 체인)  →  net.json

    python r1_net.py                                   # layers.plan_pipe 전부
    python r1_net.py --where 지구=OO                    # 필드=값 으로 거르기(여러 번 = AND)
    python r1_net.py --src-gpkg <다른.gpkg>              # plan_* 레이어를 다른 GPKG 에서(같은 레이어명)
    python r1_net.py --join 방류-102=XX-104-009          # 다른 지구 말단을 접속 맨홀로 이어 붙이기(병합)
    python r1_net.py --split XX-110-012:18.0             # 토피제어 맨홀 삽입(관번호:상류에서 거리 m)

입력
  layers.plan_pipe    (필수) 관로. 방향 = 상류 → 하류, 관번호 = 상류 맨홀번호(킷 규약)
                      필드 pipe_id(필수) · dia_mm · kind · us_mh/ds_mh(선택 — 없으면 끝점 연결로 찾는다) · route(선택)
  layers.plan_manhole (선택) 좌표·호수·펌프장 판별(kind = values.kind_pump_mh, 기본 '맨홀펌프장')
  layers.plan_pump    (선택) 펌프장 점(pump_id) — 압송 체인의 시점
  layers.asbuilt_manhole (선택) 말단·압송 토출 맨홀 매칭, 경계 관저고(out_invert 또는 ground − depth)

압송 판별: kind == values.kind_pressure 이거나 관경 ≤ design.pressure_max_d_mm.
압송 조각은 펌프장 점(3 m 이내)에서 시작해 끝점이 1.5 m 이내로 이어지는 조각을 차례로 붙인다(원 파이프라인 방식).
"""
import sys, os, math, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R


def parse_kv(items, sep='='):
    out = []
    for it in items or []:
        k, _, v = it.partition(sep)
        out.append((k.strip(), v.strip()))
    return out


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 노선 → 망(net.json)'))
    ap.add_argument('--src-gpkg', default=None, help='plan_pipe/plan_manhole/plan_pump 를 이 GPKG 에서 읽는다')
    ap.add_argument('--pipe-layer', default=None, help='관로 레이어명 덮어쓰기')
    ap.add_argument('--mh-layer', default=None, help='맨홀 레이어명 덮어쓰기')
    ap.add_argument('--where', action='append', default=[], metavar='필드=값', help='관로 거르기(AND)')
    ap.add_argument('--join', action='append', default=[], metavar='A=B', help='노드 A 를 B 로 부른다')
    ap.add_argument('--split', action='append', default=[], metavar='관번호:거리', help='맨홀 삽입')
    ap.add_argument('--pump', action='append', default=[], help='펌프장 노드명 추가 지정')
    ap.add_argument('--snap', type=float, default=3.0, help='펌프장 점 ↔ 압송 조각 시점 매칭 반경 m (기본 3)')
    ap.add_argument('--snap-end', type=float, default=None,
                    help='말단·압송 토출 → 맨홀(계획·준공) 매칭 반경 m (기본 --snap). 준공 맨홀 좌표가 몇 m 어긋난 지구용')
    ap.add_argument('--link', type=float, default=1.5, help='압송 조각 잇기 허용 m (기본 1.5)')
    a = ap.parse_args()
    if a.snap_end is None:
        a.snap_end = a.snap
    C = R.Ctx(a)
    cfg, P = C.cfg, C.P
    warn = []

    # ── 관로
    pipe = R.read_layer(cfg, 'plan_pipe', src_gpkg=a.src_gpkg, layer_name=a.pipe_layer)
    for k, v in parse_kv(a.where):
        if k not in pipe.columns:
            raise SystemExit('--where 필드 없음: %s' % k)
        pipe = pipe[pipe[k].astype(str) == v]
    c_id = R.fcol(cfg, 'plan_pipe', 'pipe_id', pipe)
    if not c_id:
        raise SystemExit('fields.plan_pipe.pipe_id 가 없다')
    c_kind = R.fcol(cfg, 'plan_pipe', 'kind', pipe)
    c_dia = R.fcol(cfg, 'plan_pipe', 'dia_mm', pipe)
    c_us = R.fcol(cfg, 'plan_pipe', 'us_mh', pipe)
    c_ds = R.fcol(cfg, 'plan_pipe', 'ds_mh', pipe)
    c_rt = R.fcol(cfg, 'plan_pipe', 'route', pipe)
    c_q = R.fcol(cfg, 'plan_pipe', 'flow_m3d', pipe)          # r6 --flow field 용(누가유량)
    K_P = cfg.value('kind_pressure', '압송')
    DMAX = P['pressure_max_d_mm']
    print('관로 %d건 (%s)' % (len(pipe), R.layer_spec(cfg, 'plan_pipe', a.src_gpkg, a.pipe_layer)['layer']))

    # ── 맨홀
    MH = collections.OrderedDict()
    mh = R.read_layer(cfg, 'plan_manhole', src_gpkg=a.src_gpkg, layer_name=a.mh_layer, required=False)
    pumps = collections.OrderedDict()
    K_PUMP = cfg.value('kind_pump_mh', '맨홀펌프장')
    T_PUMP = cfg.value('mh_type_pump', None)
    if mh is not None:
        m_id = R.fcol(cfg, 'plan_manhole', 'mh_id', mh)
        m_kind = R.fcol(cfg, 'plan_manhole', 'kind', mh)
        m_type = R.fcol(cfg, 'plan_manhole', 'mh_type', mh)
        m_gl = R.fcol(cfg, 'plan_manhole', 'ground', mh)
        for _, r in mh.iterrows():
            xy = R.point_of(r.geometry)
            nm = R.val(r, m_id)
            if xy is None or nm is None:
                continue
            kind = R.val(r, m_kind)
            typ = R.val(r, m_type)
            is_pump = (kind == K_PUMP) or (T_PUMP is not None and typ == T_PUMP)
            MH[nm] = dict(x=xy[0], y=xy[1], 호수=typ, 구분=kind, 펌프장=bool(is_pump),
                          지반고=R.val(r, m_gl))
            if is_pump:
                pumps[nm] = xy
    pp = R.read_layer(cfg, 'plan_pump', src_gpkg=a.src_gpkg, required=False)
    if pp is not None:
        p_id = R.fcol(cfg, 'plan_pump', 'pump_id', pp)
        for _, r in pp.iterrows():
            xy = R.point_of(r.geometry)
            if xy and R.val(r, p_id):
                pumps.setdefault(R.val(r, p_id), xy)
    print('맨홀 %d · 펌프장 %d' % (len(MH), len(pumps)))

    # ── 자연유하 / 압송 분리
    seg = collections.OrderedDict()
    force_geoms = []
    starts = {}
    for _, r in pipe.iterrows():
        parts = R.lines_of(r.geometry)
        if not parts:
            continue
        if len(parts) > 1:
            from shapely.ops import linemerge
            from shapely.geometry import MultiLineString
            mg = linemerge(MultiLineString(parts))
            parts = R.lines_of(mg)
            if len(parts) > 1:
                warn.append('다중 도형을 하나로 못 이음: %s (첫 조각만)' % R.val(r, c_id))
        co = [(c[0], c[1]) for c in parts[0]]
        dia = R.val(r, c_dia)
        dia = float(dia) if dia is not None else None
        kind = R.val(r, c_kind)
        is_force = (kind == K_P) or (dia is not None and dia <= DMAX)
        if is_force:
            force_geoms.append(dict(co=co, dia=dia))
            continue
        pid = str(R.val(r, c_id))
        if pid in seg:
            warn.append('관번호 중복 — 뒤엣것 무시: %s' % pid)
            continue
        from shapely.geometry import LineString
        g = LineString(co)
        us = R.val(r, c_us) or pid
        ds = R.val(r, c_ds)
        if us in MH and ds in MH:                       # 방향 검사(맨홀 좌표가 있을 때만)
            pu, pdn = (MH[us]['x'], MH[us]['y']), (MH[ds]['x'], MH[ds]['y'])
            if math.dist(co[0], pdn) + 1.0 < math.dist(co[0], pu) and math.dist(co[-1], pu) + 1.0 < math.dist(co[-1], pdn):
                co = co[::-1]
                g = LineString(co)
                warn.append('도형 방향 반대 → 뒤집음: %s' % pid)
        seg[pid] = dict(관번호=pid, 노선=R.val(r, c_rt) or R.route_of(pid), 상류맨홀=str(us),
                        하류맨홀=str(ds) if ds is not None else None,
                        관경=int(round(dia)) if dia is not None else int(P['min_dia_mm']),
                        연장=round(g.length, 2), x0=co[0][0], y0=co[0][1], x1=co[-1][0], y1=co[-1][1],
                        coords=[[round(x, 3), round(y, 3)] for x, y in co])
        if c_q and R.val(r, c_q) is not None:
            seg[pid]['입력누가_m3d'] = float(R.val(r, c_q))
        starts[pid] = co[0]
    print('자연유하 %d구간 / 총 %.1f m · 압송 조각 %d' % (len(seg), sum(s['연장'] for s in seg.values()), len(force_geoms)))

    # ── 하류맨홀이 없으면 끝점 연결로 찾는다
    asb = None
    if cfg.has_layer('asbuilt_manhole'):
        try:
            xs = [s['x0'] for s in seg.values()] + [s['x1'] for s in seg.values()]
            ys = [s['y0'] for s in seg.values()] + [s['y1'] for s in seg.values()]
            bb = (min(xs) - 200, min(ys) - 200, max(xs) + 200, max(ys) + 200) if xs else None
            asb = R.read_layer(cfg, 'asbuilt_manhole', bbox=bb)
        except SystemExit as e:
            warn.append('asbuilt_manhole 읽기 실패: %s' % e)
    ASB = {}
    if asb is not None:
        a_id = R.fcol(cfg, 'asbuilt_manhole', 'mh_id', asb)
        a_gl = R.fcol(cfg, 'asbuilt_manhole', 'ground', asb)
        a_dp = R.fcol(cfg, 'asbuilt_manhole', 'depth', asb)
        a_il = R.fcol(cfg, 'asbuilt_manhole', 'out_invert', asb)
        for _, r in asb.iterrows():
            xy = R.point_of(r.geometry)
            nm = R.val(r, a_id)
            if xy is None or nm is None:
                continue
            il = R.val(r, a_il)
            src = 'asbuilt.out_invert'
            if il is None and R.val(r, a_gl) is not None and R.val(r, a_dp) is not None:
                il = float(R.val(r, a_gl)) - float(R.val(r, a_dp))
                src = 'asbuilt 지반고−깊이'
            ASB[str(nm)] = dict(x=xy[0], y=xy[1], IL=None if il is None else round(float(il), 3),
                                GL=R.val(r, a_gl), src=src)

    def nearest(pt, cands, tol):
        best = None
        for nm, (x, y) in cands:
            d = math.dist(pt, (x, y))
            if d <= tol and (best is None or d < best[0]):
                best = (d, nm)
        return best

    need = [p for p, s in seg.items() if not s['하류맨홀']]
    if need:
        st_idx = [(p, starts[p]) for p in seg]
        mh_c = [(k, (v['x'], v['y'])) for k, v in MH.items()]
        as_c = [(k, (v['x'], v['y'])) for k, v in ASB.items()]
        for p in need:
            end = (seg[p]['x1'], seg[p]['y1'])
            hit = nearest(end, [(q, xy) for q, xy in st_idx if q != p], 1.0)
            if hit:
                seg[p]['하류맨홀'] = seg[hit[1]]['상류맨홀']
                continue
            hit = nearest(end, mh_c, a.snap_end) or nearest(end, as_c, a.snap_end)
            seg[p]['하류맨홀'] = hit[1] if hit else p + '_말단'
            if not hit:
                warn.append('하류맨홀 못 찾음(%.1f m 안) → %s_말단 (--snap-end 로 넓히거나 --join)' % (a.snap_end, p))
        print('하류맨홀 끝점 연결로 결정 %d구간' % len(need))

    # ── 맨홀 삽입(토피제어 맨홀) — 한 관에 여러 개면 하류 쪽부터(인덱스 역순) 자른다
    if a.split:
        from shapely.geometry import LineString
        from shapely.ops import substring
        bypid = collections.defaultdict(list)
        for k, v in parse_kv(a.split, ':'):
            bypid[k].append(float(v))
        for pid, ss in bypid.items():
            if pid not in seg:
                warn.append('--split 관번호 없음: %s' % pid)
                continue
            base = seg.pop(pid)
            g = LineString(base['coords'])
            cuts = sorted(s for s in ss if 0.5 < s < g.length - 0.5)
            pieces, prev = [], 0.0
            for s in cuts + [g.length]:
                pieces.append(substring(g, prev, s))
                prev = s
            names = [base['상류맨홀']] + ['%s-C%d' % (pid, i + 1) for i in range(len(cuts))]
            for i, gg in enumerate(pieces):
                us = names[i]
                ds = names[i + 1] if i + 1 < len(names) else base['하류맨홀']
                q = pid if i == 0 else names[i]
                co = list(gg.coords)
                seg[q] = dict(base, 관번호=q, 상류맨홀=us, 하류맨홀=ds, 연장=round(gg.length, 2),
                              x0=co[0][0], y0=co[0][1], x1=co[-1][0], y1=co[-1][1],
                              coords=[[round(x, 3), round(y, 3)] for x, y in co[:]])
            print('맨홀 삽입 %s: %d개 %s' % (pid, len(cuts), names[1:]))

    # ── 노드 좌표 (상류는 처음 것, 하류는 마지막 것 — 원 방식)
    node = collections.OrderedDict()
    for pid, s in seg.items():
        node.setdefault(s['상류맨홀'], (s['x0'], s['y0']))
        node[s['하류맨홀']] = (s['x1'], s['y1'])
    for nm, p in pumps.items():
        node.setdefault(nm, p)
    for nm in a.pump:
        if nm not in pumps and nm in node:
            pumps[nm] = node[nm]

    # ── 압송 체인: 펌프장에서 시작해 끝점이 이어지는 조각을 붙인다
    d = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])
    used, chains = set(), collections.OrderedDict()
    for nm, p in pumps.items():
        cand = [i for i, g in enumerate(force_geoms) if i not in used
                and min(d(p, g['co'][0]), d(p, g['co'][-1])) < a.snap]
        if not cand:
            continue
        i0 = cand[0]
        co = list(force_geoms[i0]['co'])
        if d(p, co[-1]) < d(p, co[0]):
            co = co[::-1]
        used.add(i0)
        pts = co[:]
        dias = [force_geoms[i0]['dia']]
        while True:
            best = None
            for i, g in enumerate(force_geoms):
                if i in used:
                    continue
                c = g['co']
                for cc in (c, c[::-1]):
                    dd = d(pts[-1], cc[0])
                    if best is None or dd < best[0]:
                        best = (dd, i, cc)
            if best is None or best[0] > a.link:
                break
            used.add(best[1])
            pts += list(best[2][1:])
            dias.append(force_geoms[best[1]]['dia'])
        L = sum(d(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        dd = [x for x in dias if x]
        chains[nm] = dict(펌프장=nm, pts=[[round(x, 3), round(y, 3)] for x, y in pts], L=round(L, 2),
                          관경=int(round(max(dd))) if dd else int(P['force_dia_mm']))
    if len(used) < len(force_geoms):
        warn.append('펌프장에 안 이어진 압송 조각 %d개 — 펌프장 점·kind 확인' % (len(force_geoms) - len(used)))

    # 압송 토출 맨홀 = 체인 끝점 최근접(자연유하 노드 → 계획맨홀 → 준공맨홀)
    cand_nodes = [(k, v) for k, v in node.items() if k not in chains]
    cand_mh = [(k, (v['x'], v['y'])) for k, v in MH.items() if k not in chains]
    cand_as = [(k, (v['x'], v['y'])) for k, v in ASB.items()]
    for nm, ch in chains.items():
        end = tuple(ch['pts'][-1])
        hit = nearest(end, cand_nodes, a.snap_end) or nearest(end, cand_mh, a.snap_end) or nearest(end, cand_as, a.snap_end)
        ch['하류맨홀'] = hit[1] if hit else nm + '_토출'
        ch['토출이격_m'] = round(hit[0], 2) if hit else None
        if not hit:
            warn.append('압송 %s 토출 맨홀 못 찾음(%.1f m 안) → %s_토출 (--snap-end 로 넓히거나 --join)' % (nm, a.snap_end, nm))
        if ch['하류맨홀'] not in node:
            v = ASB.get(ch['하류맨홀']) or MH.get(ch['하류맨홀'])
            node[ch['하류맨홀']] = (v['x'], v['y']) if v else end
        print('  압송 %-14s L=%7.2fm  정점 %2d → %s (%s m)' % (nm, ch['L'], len(ch['pts']), ch['하류맨홀'],
                                                         ch['토출이격_m']))
    for k, v in ASB.items():
        if k in node:
            continue
        if any(s['하류맨홀'] == k for s in seg.values()) or any(c['하류맨홀'] == k for c in chains.values()):
            node[k] = (v['x'], v['y'])

    # ── 병합(--join): 노드 이름 바꾸기
    for A_, B_ in parse_kv(a.join):
        n = 0
        for s in seg.values():
            for k in ('상류맨홀', '하류맨홀'):
                if s[k] == A_:
                    s[k] = B_
                    n += 1
        for c in chains.values():
            if c['하류맨홀'] == A_:
                c['하류맨홀'] = B_
                n += 1
        if A_ in node:
            node.setdefault(B_, node[A_])
            node.pop(A_)
        print('병합 %s → %s (%d곳)' % (A_, B_, n))

    # ── 경계(말단) 관저고: 자연유하 말단 중 펌프장이 아닌 것
    ups = {s['상류맨홀'] for s in seg.values()}
    term = sorted({s['하류맨홀'] for s in seg.values()} - ups - set(chains))
    boundary = {}
    for t in term:
        if t in ASB and ASB[t]['IL'] is not None:
            boundary[t] = dict(IL=ASB[t]['IL'], src=ASB[t]['src'])
        else:
            boundary[t] = dict(IL=None, src='없음 — --fixed-il 로 줄 것(없으면 자유 말단)')
    print('자연유하 말단 %d: %s' % (len(term), ', '.join('%s(%s)' % (t, boundary[t]['IL']) for t in term)))

    net = dict(meta=dict(created=R.stamp(), step='r1_net', where=a.where, join=a.join, split=a.split,
                         src=str(R.layer_spec(cfg, 'plan_pipe', a.src_gpkg, a.pipe_layer).get('path', 'postgis'))),
               seg=seg, force=chains, node={k: list(v) for k, v in node.items()}, MH=MH,
               boundary=boundary, asbuilt={k: v for k, v in ASB.items() if k in node}, warnings=warn)
    p = C.save('net.json', net)
    for w in warn[:20]:
        print('  ! ' + w)
    if len(warn) > 20:
        print('  ! … 경고 %d건 더' % (len(warn) - 20))
    print('→ %s' % p)


if __name__ == '__main__':
    main()
