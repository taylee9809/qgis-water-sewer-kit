# -*- coding: utf-8 -*-
"""r2 지반고 · 노폭 · 교량/하천 구간  →  ground.json

    python r2_ground.py
    python r2_ground.py --gl-table 설계사종단_지반고.csv     # name,gl 또는 x,y,gl — 가장 먼저 쓴다
    python r2_ground.py --road-width 노폭.csv               # 관번호,최소폭,평균폭 (도로경계면 대신)
    python r2_ground.py --prefer-dem                         # 레이어 지반고보다 DEM 을 먼저

노드 지반고 우선순위
  ① --gl-table(설계사 종단 측량 지반고 등, 좌표면 2 m 안 매칭) → ② 측량점(layers.survey_point, 반경 5 m 중앙값)
  → ③ 레이어 값(plan_manhole.ground / 준공 맨홀 지반고) → ④ DEM(설정 dem 목록, 앞에서부터)
구간 표본(맨홀 사이)  design.ground_step_m 간격, 양 끝 0.5 m 제외 — 측량점 → DEM. r5 중간 절점 검사·제약에 쓴다.
압송 측점            체인 정점 + design.force_station_m 간격 + 끝점 — 측량점 → DEM(원 파이프라인 방식).
노폭                 layers.road_boundary 가 있으면 관로 25/50/75 % 지점 직각 ±25 m 코드의 도로 안 길이.
교량·하천 구간        layers.bridge / layers.river 와 겹치는 관 구간(시점부터 m)과 하상 표고(측량·DEM 최소/10퍼센타일).
                     하월·관매달기 여부는 사람이 정한다 → r7 --special.
"""
import sys, os, math, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 지반고(ground.json)'))
    ap.add_argument('--gl-table', default=None, help='우선 지반고 표(CSV/JSON: name,gl 또는 x,y,gl)')
    ap.add_argument('--road-width', default=None, help='노폭 표(CSV 관번호,최소폭,평균폭 / JSON [[관번호,최소,평균,n]])')
    ap.add_argument('--prefer-dem', action='store_true', help='레이어 지반고보다 DEM 우선')
    ap.add_argument('--radius', type=float, default=5.0, help='측량점 반경 m (기본 5)')
    ap.add_argument('--no-spans', action='store_true', help='교량·하천 구간 생략')
    a = ap.parse_args()
    C = R.Ctx(a)
    cfg, P = C.cfg, C.P
    net = C.load('net.json')
    seg, force, node, MH, ASB = net['seg'], net['force'], net['node'], net['MH'], net.get('asbuilt', {})
    import numpy as np

    xs = [v[0] for v in node.values()] + [p[0] for c in force.values() for p in c['pts']]
    ys = [v[1] for v in node.values()] + [p[1] for c in force.values() for p in c['pts']]
    bbox = (min(xs) - 60, min(ys) - 60, max(xs) + 60, max(ys) + 60)
    G = R.Ground(cfg, bbox=bbox, radius=a.radius)
    print('측량점 %d · DEM %s' % (G.n_survey, [p.name for p in G.dems] or '없음'))

    # ── 노드 지반고
    nodes = list(dict.fromkeys([s['상류맨홀'] for s in seg.values()] + [s['하류맨홀'] for s in seg.values()]
                               + list(force) + [c['하류맨홀'] for c in force.values()]))
    TAB_N, TAB_P = {}, []
    if a.gl_table:
        rows = R.load_any(a.gl_table)
        if isinstance(rows, dict):
            rows = [dict(name=k, gl=v) for k, v in rows.items()]
        WG = collections.defaultdict(list)
        for r in rows:
            gl = r.get('gl') if r.get('gl') not in (None, '') else r.get('GL')
            if gl in (None, ''):
                continue
            if r.get('x') not in (None, '') and r.get('y') not in (None, ''):
                WG[(round(float(r['x']), 1), round(float(r['y']), 1))].append(float(gl))
            elif r.get('name'):
                TAB_N[str(r['name'])] = float(gl)
        TAB_P = [(k[0], k[1], sorted(v)[len(v) // 2]) for k, v in WG.items()]
        print('지반고 표: 이름 %d · 좌표 %d' % (len(TAB_N), len(TAB_P)))

    def layer_gl(n):
        if n in MH and MH[n].get('지반고') is not None:
            return float(MH[n]['지반고'])
        if n in ASB and ASB[n].get('GL') is not None:
            return float(ASB[n]['GL'])
        return None

    GL, GLS, srcc = {}, {}, collections.Counter()
    dem_need = []
    for n in nodes:
        x, y = node[n]
        w = [z for wx, wy, z in TAB_P if math.hypot(wx - x, wy - y) < 2.0]
        if n in TAB_N:
            GL[n], GLS[n] = round(TAB_N[n], 2), '표(이름)'
        elif w:
            GL[n], GLS[n] = round(w[0], 2), '표(좌표)'
        else:
            v = G.survey_at(x, y)
            lv = layer_gl(n)
            if v is not None:
                GL[n], GLS[n] = v, '측량'
            elif lv is not None and not a.prefer_dem:
                GL[n], GLS[n] = round(lv, 2), '레이어'
            else:
                dem_need.append(n)
    if dem_need and G.dems:
        for n, (z, tag) in zip(dem_need, G.dem_many([tuple(node[n]) for n in dem_need])):
            if z is None and layer_gl(n) is not None:
                z, tag = round(layer_gl(n), 2), '레이어'
            GL[n], GLS[n] = z, tag
    else:
        for n in dem_need:
            lv = layer_gl(n)
            GL[n], GLS[n] = (round(lv, 2), '레이어') if lv is not None else (None, '없음')
    srcc = collections.Counter(GLS.values())
    print('노드 %d 지반고 출처: %s' % (len(nodes), dict(srcc)))
    miss = [n for n in nodes if GL.get(n) is None]
    if miss:
        print('  ! 지반고 없음 %d: %s' % (len(miss), miss[:10]))

    # ── 구간 표본(맨홀 사이)
    from shapely.geometry import LineString
    step = float(P['ground_step_m'])
    prof, allp = {}, []
    for pid, s in seg.items():
        g = LineString(s['coords'])
        L = g.length
        x = step
        while x < L - 0.5:
            p = g.interpolate(x)
            allp.append((pid, round(x, 2), p.x, p.y))
            x += step
    zs = []
    dneed = []
    for i, (pid, t, x, y) in enumerate(allp):
        v = G.survey_at(x, y)
        zs.append([v, '측량'] if v is not None else [None, None])
        if v is None:
            dneed.append(i)
    if dneed and G.dems:
        for i, (z, tag) in zip(dneed, G.dem_many([(allp[i][2], allp[i][3]) for i in dneed])):
            zs[i] = [z, tag]
    for (pid, t, x, y), (z, tag) in zip(allp, zs):
        if z is not None:
            prof.setdefault(pid, []).append([t, z, tag])
    print('구간 표본 %d점 (간격 %.1f m, 값 없음 %d)' % (len(allp), step, sum(1 for z in zs if z[0] is None)))

    # ── 압송 측점
    fpts = {}
    st = float(P['force_station_m'])
    for nm, ch in force.items():
        Pp = ch['pts']
        sl = [math.dist(Pp[i], Pp[i + 1]) for i in range(len(Pp) - 1)]
        acc = np.cumsum([0] + sl)
        L = float(acc[-1])
        ss = sorted(set([round(float(v), 2) for v in acc] + [float(t) for t in np.arange(0, L, st)] + [round(L, 2)]))
        out = []
        for t in ss:
            j = max(0, min(len(sl) - 1, int(np.searchsorted(acc, t) - 1)))
            f = (t - acc[j]) / sl[j] if sl[j] else 0
            out.append([round(t, 2), round(Pp[j][0] + (Pp[j + 1][0] - Pp[j][0]) * f, 3),
                        round(Pp[j][1] + (Pp[j + 1][1] - Pp[j][1]) * f, 3),
                        1 if any(abs(t - float(v)) < 0.01 for v in acc) else 0])
        fpts[nm] = dict(L=round(L, 2), pts=out)
    allf = [(nm, i, p[1], p[2]) for nm, ch in fpts.items() for i, p in enumerate(ch['pts'])]
    Z = {}
    if G.dems and allf:
        for q, (z, tag) in zip(allf, G.dem_many([(q[2], q[3]) for q in allf])):
            if z is not None:
                Z[q[:2]] = (z, tag)
    fcnt = collections.Counter()
    for nm, ch in fpts.items():
        for i, p in enumerate(ch['pts']):
            z = G.survey_at(p[1], p[2])
            src = '측량'
            if z is None:
                zz = Z.get((nm, i))
                z, src = (zz[0], zz[1]) if zz else (None, '없음')
            fcnt[src] += 1
            p += [z, src]
    if fpts:
        print('압송 측점 %d (출처 %s)' % (sum(len(c['pts']) for c in fpts.values()), dict(fcnt)))

    # ── 노폭
    road_w = {}
    if a.road_width:
        rows = R.load_any(a.road_width)
        for r in rows:
            if isinstance(r, list):
                road_w[str(r[0])] = [r[1], r[2], r[3] if len(r) > 3 else None]
            else:
                f = lambda k: float(r[k]) if r.get(k) not in (None, '') else None
                road_w[str(r.get('관번호') or r.get('pipe_id'))] = [f('최소폭'), f('평균폭'), None]
        print('노폭 표 %d구간' % len(road_w))
    elif cfg.has_layer('road_boundary') and seg:
        from shapely.ops import unary_union
        rd = R.read_layer(cfg, 'road_boundary', bbox=bbox)
        road = unary_union([g for g in rd.geometry if g is not None])
        for pid, s in seg.items():
            g = LineString(s['coords'])
            L = g.length
            ws = []
            for frac in (0.25, 0.5, 0.75):
                d = L * frac
                p = g.interpolate(d)
                p1, p2 = g.interpolate(max(d - 1, 0)), g.interpolate(min(d + 1, L))
                t = np.array([p2.x - p1.x, p2.y - p1.y])
                nv = np.array([-t[1], t[0]]) / (np.linalg.norm(t) or 1)
                if not road.contains(p):
                    continue
                ch_ = LineString([(p.x - 25 * nv[0], p.y - 25 * nv[1]), (p.x + 25 * nv[0], p.y + 25 * nv[1])]).intersection(road)
                parts = [ch_] if ch_.geom_type == 'LineString' else list(getattr(ch_, 'geoms', []))
                hit = [c for c in parts if c.distance(p) < 0.05]
                if hit:
                    ws.append(hit[0].length)
            road_w[pid] = [round(min(ws), 1) if ws else None, round(sum(ws) / len(ws), 1) if ws else None, len(ws)]
        w = [v[0] for v in road_w.values() if v[0]]
        print('노폭 산출 %d구간 (도로경계면 밖 %d): ≤%.0fm %d / 그 외 %d'
              % (len(road_w), sum(1 for v in road_w.values() if v[0] is None), P['road_width_limit_m'],
                 sum(x <= P['road_width_limit_m'] for x in w), sum(x > P['road_width_limit_m'] for x in w)))

    # ── 교량·하천 구간
    spans = {}
    if not a.no_spans:
        from shapely.geometry import Point
        from shapely.prepared import prep
        lay =[(k, nm) for k, nm in (('bridge', '교량'), ('river', '하천')) if cfg.has_layer(k)]
        lines = [(pid, LineString(s['coords'])) for pid, s in seg.items()] + \
                [(nm, LineString(c['pts'])) for nm, c in force.items()]
        from shapely.ops import unary_union
        from shapely.strtree import STRtree
        for key, label in lay:
            df = R.read_layer(cfg, key, bbox=bbox)
            geoms = [g for g in df.geometry if g is not None and not g.is_empty]
            if not geoms:
                continue
            tree = STRtree(geoms)
            for pid, g in lines:
                ivs = []
                for i in tree.query(g, predicate='intersects'):
                    inter = g.intersection(geoms[int(i)])
                    parts = [inter] if inter.geom_type in ('LineString', 'Point') else list(getattr(inter, 'geoms', []))
                    for q in parts:
                        if q.is_empty:
                            continue
                        if q.geom_type == 'Point':
                            aa = bb = g.project(q)
                        else:
                            cs = list(q.coords)
                            aa, bb = sorted((g.project(Point(cs[0])), g.project(Point(cs[-1]))))
                        ivs.append([aa, bb, {int(i)}])
                # 겹치거나 잘게 나뉜 면(타일·중복)이 만든 구간은 하나로 잇는다(0.5 m 안)
                ivs.sort(key=lambda v: v[0])
                merged = []
                for aa, bb, ids in ivs:
                    if merged and aa <= merged[-1][1] + 0.5:
                        merged[-1][1] = max(merged[-1][1], bb)
                        merged[-1][2] |= ids
                    else:
                        merged.append([aa, bb, set(ids)])
                for aa, bb, ids in merged:
                    rec = dict(kind=label, a=round(aa, 2), b=round(bb, 2))
                    polys = [geoms[i] for i in ids if geoms[i].geom_type in ('Polygon', 'MultiPolygon')]
                    if label == '하천' and polys:
                        # 하상 = 하천 면 ∩ 관 20 m 안의 1.5 m 격자 DEM / 측량점 (원 하월 지점 검토 방식)
                        gb = g.buffer(20)
                        area = unary_union([q.intersection(gb) for q in polys])     # 먼저 자르고 합친다(큰 면 합치기는 느리다)
                        bx = area.bounds
                        pa = prep(area)
                        grid = [(x, y) for x in np.arange(bx[0], bx[2], 1.5) for y in np.arange(bx[1], bx[3], 1.5)
                                if pa.contains(Point(x, y))]
                        dz = [z for z, _ in G.dem_many(grid)] if (grid and G.dems) else []
                        dz = [z for z in dz if z is not None]
                        sv = []
                        if G.SA is not None:
                            m = ((G.SA[:, 0] >= bx[0]) & (G.SA[:, 0] <= bx[2]) &
                                 (G.SA[:, 1] >= bx[1]) & (G.SA[:, 1] <= bx[3]))
                            sv = [float(z) for x, y, z in G.SA[m] if pa.contains(Point(x, y))]
                        if dz:
                            rec.update(DEM최소=round(min(dz), 2), DEM_p10=round(float(np.percentile(dz, 10)), 2), DEM점=len(dz))
                        if sv:
                            rec.update(측량최소=round(min(sv), 2), 측량_p10=round(float(np.percentile(sv, 10)), 2), 측량점=len(sv))
                    spans.setdefault(pid, []).append(rec)
        if lay:
            print('교량·하천 구간: %d개 관로/체인 %d곳' % (len(spans), sum(len(v) for v in spans.values())))

    out = dict(meta=dict(created=R.stamp(), step='r2_ground', gl_table=a.gl_table, prefer_dem=a.prefer_dem,
                         dem=[str(p) for p in G.dems], survey=G.n_survey, step_m=step),
               GL=GL, GL_src=GLS, prof=prof, force_pts=fpts, road_width=road_w, spans=spans)
    print('→ %s' % C.save('ground.json', out))


if __name__ == '__main__':
    main()
