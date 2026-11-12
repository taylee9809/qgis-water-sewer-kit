# -*- coding: utf-8 -*-
"""진단 — 신설 자연유하 관로 × 상수관 근접(0.3 m) 병행/사교차 분류 + 도로경계면 밖 연장  →  상수관_근접.csv

    python diag_water.py
    python diag_water.py --sep 0.3 --angle 15

상수관(layers.water_pipe, fields.water_pipe.status == values.water_status_active 만)과 관로 버퍼(--sep)의 겹침이
1 m 넘는 곳을 교차각으로 나눈다: < --angle° 실제 병행(직선 관로로는 못 피함 → 이설·노선 이동 대상) / 그 밖 사교차.
상수관 이격 0.3 m 근거: KDS 57 65 00 4.5(2), KDS 57 70 00 2.6(1). road_boundary 가 있으면 도로(+0.3 m) 밖 연장도 낸다.
"""
import sys, os, io, csv, math, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R


def main():
    ap = R.common_args(argparse.ArgumentParser(description='상수관 병행·사교차 진단'))
    ap.add_argument('--sep', type=float, default=0.3)
    ap.add_argument('--angle', type=float, default=15.0)
    a = ap.parse_args()
    C = R.Ctx(a)
    cfg = C.cfg
    net = C.load('net.json')
    import numpy as np
    from shapely.geometry import LineString
    from shapely.ops import unary_union, nearest_points
    from shapely.strtree import STRtree
    lines = [(pid, LineString(s['coords'])) for pid, s in net['seg'].items()]
    if not lines:
        raise SystemExit('자연유하 구간이 없다')
    aoi = unary_union([g for _, g in lines]).buffer(30)
    bb = aoi.bounds
    if not cfg.has_layer('water_pipe'):
        raise SystemExit('layers.water_pipe 가 없다')
    wat = R.read_layer(cfg, 'water_pipe', bbox=bb)
    c_st = R.fcol(cfg, 'water_pipe', 'status', wat)
    c_d = R.fcol(cfg, 'water_pipe', 'dia_mm', wat)
    act = cfg.value('water_status_active', None)
    if c_st and act:
        wat = wat[wat[c_st] == act]
    wat = wat[wat.geometry.intersects(aoi)].reset_index(drop=True)
    wg = list(wat.geometry.values)
    tree = STRtree(wg)

    def sdir(line, pt):
        d = line.project(pt)
        p1, p2 = line.interpolate(max(d - 1, 0)), line.interpolate(min(d + 1, line.length))
        v = np.array([p2.x - p1.x, p2.y - p1.y])
        n = np.linalg.norm(v)
        return v / n if n else v

    rows = []
    for pid, g in lines:
        buf = g.buffer(a.sep)
        for j in tree.query(buf):
            w = wg[int(j)]
            inter = w.intersection(buf)
            if inter.length < 1.0:
                continue
            c = inter.centroid
            ps, pw = nearest_points(g, c)[0], nearest_points(w, c)[0]
            ang = math.degrees(math.acos(min(1.0, abs(float(np.dot(sdir(g, ps), sdir(w, pw)))))))
            rows.append(dict(관번호=pid, 근접연장=round(inter.length, 1), 교차각=round(ang), 분류='병행' if ang < a.angle else '사교차',
                             상수관경=R.val(wat.iloc[int(j)], c_d), 최소이격=round(w.distance(g), 2)))
    par = [r for r in rows if r['분류'] == '병행']
    print('상수관 %d · %.1f m 이내 근접(1 m 초과) %d건 %.0f m = 사교차 %d건 %.0f m + 실제 병행 %d건 %.0f m'
          % (len(wat), a.sep, len(rows), sum(r['근접연장'] for r in rows), len(rows) - len(par),
             sum(r['근접연장'] for r in rows) - sum(r['근접연장'] for r in par), len(par), sum(r['근접연장'] for r in par)))
    if cfg.has_layer('road_boundary'):
        rd = R.read_layer(cfg, 'road_boundary', bbox=bb)
        road = unary_union([x for x in rd.geometry if x is not None]).buffer(0.3)
        out = sum(g.difference(road).length for _, g in lines)
        print('도로경계면(+0.3 m) 밖 연장 %.0f m / %.0f m' % (out, sum(g.length for _, g in lines)))
    for r in sorted(par, key=lambda r: -r['근접연장'])[:12]:
        print('  병행 %-14s %5.1f m  각 %3d°  이격 %.2f' % (r['관번호'], r['근접연장'], r['교차각'], r['최소이격']))
    with io.open(C.path('상수관_근접.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print('→ %s' % C.path('상수관_근접.csv'))


if __name__ == '__main__':
    main()
