# -*- coding: utf-8 -*-
"""15 중심선 정합 — 폴리곤 밖으로 벗어난 `지장물_중심선` 을 폴리곤에서 다시 만든다.

왜 필요한가
-----------
중심선 레이어에 **지장물이 없는 자리에 선만 길게 남아 있는 곳**이 있다(사례·사용자 지적).
원인은 02 빌더가 중심선을 만들 때 쓰는 축 추정이 굽은 띠에서 실패해, 폴리곤으로 채택되지
않은 **원도 체인 전체**가 그대로 중심선으로 남은 것이다. 사례 회차에서 폴리곤 1392 중
**84건**이 제 폴리곤 밖으로 0.3 m 넘게(최대 135 m) 뻗어 있고, 1건은 중심선이 아예 없었다.

중심선은 폴리곤의 축이어야 한다. 폴리곤을 고치면 중심선도 같이 가야 하고(14 가 그렇게
한다), 애초에 어긋나 있던 것도 여기서 맞춘다.

핵심: `_common.band_axis` 를 쓰지 말 것
---------------------------------------
band_axis 는 '가장 긴 변 두 개의 중점을 잇는' 방식이라 **정점이 잘게 쪼개진 굽은 띠**에서
엉뚱한 현(chord)이 된다(사례: 실제 23.1 m → band_axis 6.2 m). 여기서는 양 끝(최소
회전사각형의 짧은 변)에서 경계를 둘로 갈라 **호장 비례로 평균**낸다.

사용
    python -X utf8 15_fix_centerline.py [--config sewer.config.json] [--gpkg ...] [--poly ...]
        [--cl ...] [--tol 0.3] [--write] [--export]
출력
    <산출>/15_centerline.csv · (--export) <산출>/지장물_15중심선_<날짜>.gpkg
"""
import os
import sys
import csv
import math
import datetime
import collections

TOOL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL)
from _common import *                                      # noqa: E402,F403

from shapely.geometry import LineString, Point             # noqa: E402
from shapely.ops import substring                          # noqa: E402

GP, DEF_POLY, DEF_CL = obstacle_src()
OUT = out_path('15_centerline.csv')


def poly_centerline(poly, n=60):
    """띠 폴리곤의 중심선 — 양 끝에서 경계를 둘로 갈라 호장 비례로 평균."""
    try:
        ring = LineString(poly.exterior.coords)
        cs = list(poly.minimum_rotated_rectangle.exterior.coords)[:-1]
        if len(cs) < 4:
            return band_axis(poly)
        edges = sorted(((math.dist(cs[i], cs[(i + 1) % 4]),
                         ((cs[i][0] + cs[(i + 1) % 4][0]) / 2,
                          (cs[i][1] + cs[(i + 1) % 4][1]) / 2))
                        for i in range(4)), key=lambda e: e[0])
        a, b = sorted((ring.project(Point(edges[0][1])),
                       ring.project(Point(edges[1][1]))))
        if b - a < 1e-6 or ring.length - (b - a) < 1e-6:
            return band_axis(poly)
        c1 = substring(ring, a, b)
        c2 = substring(ring, b, ring.length)
        tail = substring(ring, 0, a)
        if tail.length > 1e-9:
            c2 = LineString(list(c2.coords) + list(tail.coords))
        pts = []
        for k in range(n + 1):
            f = k / n
            q1 = c1.interpolate(f, normalized=True)
            q2 = c2.interpolate(1 - f, normalized=True)
            pts.append(((q1.x + q2.x) / 2, (q1.y + q2.y) / 2))
        out = [pts[0]]
        for q in pts[1:]:
            if math.dist(out[-1], q) > 1e-9:
                out.append(q)
        return LineString(out) if len(out) > 1 else band_axis(poly)
    except Exception:
        return band_axis(poly)


def main():
    tol = float(argval('--tol', 0.3))
    polys = {pr.get('지장물번호'): (pr, g) for pr, g in read_layer(GP, DEF_POLY)
             if pr.get('지장물번호')}
    cls = {pr.get('지장물번호'): (pr, g) for pr, g in read_layer(GP, DEF_CL)
           if pr.get('지장물번호')}
    print('폴리곤 %d · 중심선 %d' % (len(polys), len(cls)))

    rows, fix = [], []
    tally = collections.Counter()
    for key, (pr, g) in polys.items():
        if g is None or g.is_empty or g.area <= 0:
            continue
        cur = cls.get(key)
        out_len = (cur[1].difference(g.buffer(0.05)).length if cur else None)
        why = ''
        if cur is None:
            why = '중심선 없음'
        elif out_len > tol:
            why = '폴리곤 밖 %.2f m' % out_len
        if not why:
            tally['정상'] += 1
            continue
        new = poly_centerline(g)
        after = new.difference(g.buffer(0.05)).length
        L, w = band_dims(g)
        ok = after <= tol
        tally['고침' if ok else '고쳐도 벗어남'] += 1
        rows.append(dict(지장물번호=key, 종류=pr.get('종류') or '', 표기=pr.get('표기') or '',
                         사유=why, 폴리곤면적=round(g.area, 2),
                         폴리곤폭=round(w, 2) if w else '',
                         기대길이=round(L, 1) if L else '',
                         종전길이=round(cur[1].length, 1) if cur else '',
                         종전_폴밖=round(out_len, 2) if out_len is not None else '',
                         새길이=round(new.length, 1), 새_폴밖=round(after, 2),
                         판정='고침' if ok else '확인필요'))
        # ★ 기준을 통과한 것만 쓴다. 통과 못 한 건 지금보다 나빠질 수 있어 손대지 않고
        #   목록(판정=확인필요)으로만 남긴다 — 교량 면처럼 '띠'가 아닌 폴리곤은 중심선
        #   자체가 의미 없다(사례: 교량면 573 ㎡).
        if ok:
            base = dict(cur[0]) if cur else dict(pr)
            base['지장물번호'] = key
            fix.append((key, new, base))

    rows.sort(key=lambda r: -(float(r['종전_폴밖']) if r['종전_폴밖'] != '' else 0))
    if rows:                                       # 빈 결과(전부 정상)에서 IndexError 방지
        with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
            w_ = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w_.writeheader()
            w_.writerows(rows)
    print()
    for k, v in tally.most_common():
        print('   %-14s %d' % (k, v))
    print('→ %s' % OUT)

    if has('--export'):
        dst = out_path('지장물_15중심선_%s.gpkg' % datetime.datetime.now().strftime('%y%m%d'))
        if os.path.exists(dst):
            os.remove(dst)
        props = collections.OrderedDict([('지장물번호', 'str:32'), ('표기', 'str:64'),
                                         ('사유', 'str:64'), ('길이', 'float')])
        with fiona.open(dst, 'w', driver='GPKG', layer='새중심선',
                        schema={'geometry': 'LineString', 'properties': props},
                        crs=CRS.from_epsg(epsg())) as d:
            d.writerecords([{'geometry': mapping(gg), 'properties': {
                '지장물번호': k, '표기': (polys[k][0].get('표기') or ''),
                '사유': next((r['사유'] for r in rows if r['지장물번호'] == k), ''),
                '길이': round(gg.length, 2)}} for k, gg, _ in fix])
        print('→ %s' % dst)

    if has('--write'):
        backup(GP)
        n = replace_features(GP, DEF_CL, fix)
        print('중심선 반영 %d건' % n)
        feature_counts(GP, [DEF_POLY, DEF_CL])
    else:
        print('※ 미반영 — 반영하려면 --write')


if __name__ == '__main__':
    main()
