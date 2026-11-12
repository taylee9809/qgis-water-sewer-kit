# -*- coding: utf-8 -*-
"""가짜(부속선) 폴리곤 탐지.

사례: BOX3.0X2.0 자리에 실제 지장물은 하나(실측 3.007 = 표기 3.0)뿐인데,
BOX 윙월·도로접속부의 짧은 선들로 폴리곤 4개(폭 1.46·0.25·0.30·0.24 m)가
더 만들어지고 라벨까지 인접 승계받았다. 사용자 확인: "원본에 그 위치엔 지장물이 없다".

규칙 후보
  같은 표기(규격 라벨)를 가진 폴리곤이 반경 R 안에 뭉쳐 있고,
  그 안에 실측폭이 표기폭과 ±TOL(기본 12%) 로 맞는 '본체'가 있으면,
  나머지(실측폭이 안 맞고 면적도 본체보다 작은 것)는 부속선에서 생긴 가짜로 본다.

사용
  python -X utf8 08_find_ghost.py [--config sewer.config.json] [--gpkg ...] [--poly ...]
        [--r 6] [--tol 0.12] [--district 지구1,지구2] [--route ...] [--route-layer ...]
  결과: <산출>/지장물_가짜후보.csv  (10_delete_ghost.py --from-csv 입력)
"""
import os
import sys
import csv
import math
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

GP, PL, _ = obstacle_src(need_cl=False)
R = float(argval('--r', '6'))
TOL = float(argval('--tol', '0.12'))
CSV_OUT = out_path('지장물_가짜후보.csv')

cur = route_lines(current=True)
ctree = STRtree([g for _, g in cur])

rows = [(pr, g) for pr, g in read_layer(GP, PL) if g.geom_type == 'Polygon']
tree = STRtree([g for _, g in rows])


def wlab_of(pr):
    sp = parse_spec(str(pr.get('표기') or ''))
    if sp and sp[0] == 'C':
        return sp[1] / 1000.0
    if sp and sp[0] == 'R':
        return sp[1]
    return float(pr['규격_폭m']) if pr.get('규격_폭m') else None


info = []
for pr, g in rows:
    L, w = band_dims(g)
    info.append(dict(pr=pr, g=g, w=w, L=L, wl=wlab_of(pr),
                     표기=str(pr.get('표기') or '').strip(), 번호=pr.get('지장물번호')))

ghosts, groups = [], []
done = set()
for i, a in enumerate(info):
    if a['번호'] in done or not a['표기']:
        continue
    ids = [int(x) for x in tree.query(a['g'].buffer(R))]
    grp = [info[j] for j in ids
           if info[j]['표기'] == a['표기'] and info[j]['g'].distance(a['g']) <= R]
    if len(grp) < 2:
        continue
    ok = [x for x in grp if x['wl'] and x['w'] and abs(x['w'] - x['wl']) / x['wl'] <= TOL]
    if not ok:
        continue
    main = max(ok, key=lambda x: x['g'].area)
    bad = [x for x in grp if x['번호'] != main['번호']
           and not (x['wl'] and x['w'] and abs(x['w'] - x['wl']) / x['wl'] <= TOL)
           and x['g'].area < main['g'].area]
    if not bad:
        continue
    for x in grp:
        done.add(x['번호'])
    d = None
    for k in [int(y) for y in ctree.query(main['g'].buffer(40))]:
        dd = cur[k][1].distance(main['g'])
        d = dd if d is None else min(d, dd)
    kc = None
    for k in [int(y) for y in ctree.query(main['g'].buffer((d or 0) + 0.1))]:
        if abs(cur[k][1].distance(main['g']) - (d or 0)) < 0.05:
            kc = cur[k][0]
            break
    groups.append(dict(표기=a['표기'], main=main, bad=bad,
                       거리=round(d, 1) if d is not None else None, 관번호=kc))
    ghosts += bad

near = [gr for gr in groups if gr['거리'] is not None and gr['거리'] <= 10]
print('같은 표기 뭉치 %d곳 / 가짜 후보 %d개 (전체)' % (len(groups), len(ghosts)))
print('  그중 금회 노선 10 m 이내 뭉치 %d곳 / 가짜 %d개' % (
    len(near), sum(len(gr['bad']) for gr in near)))
if ghosts:                                   # 빈 결과에서 IndexError 방지
    print('  가짜 후보 면적: 중앙 %.2f ㎡ / 최대 %.2f ㎡' % (
        sorted(x['g'].area for x in ghosts)[len(ghosts) // 2],
        max(x['g'].area for x in ghosts)))

print('\n[금회 노선 10 m 이내 뭉치]')
for gr in sorted(near, key=lambda r: r['거리']):
    m = gr['main']
    print('  %-14s 본체 %-10s 실측 %.3f 면적 %6.2f  | 가짜 %d개: %s  (%s %.1fm)' % (
        gr['표기'], m['번호'], m['w'] or 0, m['g'].area, len(gr['bad']),
        ', '.join('%s(%.2f㎡·폭%.2f)' % (x['번호'], x['g'].area, x['w'] or 0)
                  for x in gr['bad']), gr['관번호'] or '-', gr['거리']))

with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['표기', '본체번호', '본체실측폭', '본체면적', '가짜번호', '가짜면적', '가짜실측폭',
                '금회관번호', '금회거리m'])
    for gr in groups:
        for x in gr['bad']:
            w.writerow([gr['표기'], gr['main']['번호'], round(gr['main']['w'] or 0, 3),
                        round(gr['main']['g'].area, 2), x['번호'], round(x['g'].area, 2),
                        round(x['w'] or 0, 3), gr['관번호'], gr['거리']])
print()
print('목록: %s  (허용오차 %.0f%%)' % (CSV_OUT, TOL * 100))
