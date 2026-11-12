# -*- coding: utf-8 -*-
"""남은 지장물 문제건 — 성격별로 갈라 사용자 확인용 목록/레이어 생성 (v2).

갈래
  A. 라벨 오귀속 의심 : 실측폭이 표기보다 훨씬 작다(큰 규격 라벨이 작은 지물에 붙음)
                      또는 원형관인데 실측이 표기보다 크다
  B. 미상 — 원형관/측구 판별 필요 : 규격 라벨이 없다. 실측폭은 있으니 종류만 알면 채울 수 있다
  C. 중복 쌍 : 누적본 납품으로 같은 지물이 두 번 그려졌다. 어느 쪽을 남길지
  D. 폭 추정·비정상 : 도면에 규격이 없어 기본 0.3 으로 버퍼 / 폭이 0.05 미만·6.6 초과
  E. (참고, 확인 불필요) 측구·BOX 의 표기(내공) ↔ 실측(외곽) 차이로 설명되는 것

'금회' = --district 또는 survey.current_districts 의 지구 노선(비우면 전체 노선).

사용
  python -X utf8 07_review_list.py [--config sewer.config.json] [--gpkg ...] [--poly ...]
        [--district 지구1,지구2] [--route <노선.gpkg>] [--route-layer ...] [--srcgpkg <빌드.gpkg>]
결과
  <산출>/지장물_확인목록.csv · 지장물_확인목록.gpkg (레이어 지장물_확인목록, 점)
"""
import os
import sys
import csv
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

GP, PL, _ = obstacle_src(need_cl=False)
OUT = out_path('지장물_확인목록.gpkg')
CSV_OUT = out_path('지장물_확인목록.csv')
NEAR = 10.0

cur = route_lines(current=True)
allr = route_lines()
ctree, atree = STRtree([g for _, g in cur]), STRtree([g for _, g in allr])

rows = read_layer(GP, PL)
방식맵, _ = method_map(argval('--srcgpkg'))

SAGAK = ('측구', '박스', '암거', '수로')


def dist(tree, arr, g):
    best = None
    for i in [int(x) for x in tree.query(g.buffer(40))]:
        d = arr[i][1].distance(g)
        if best is None or d < best[0]:
            best = (d, arr[i][0])
    return best or (None, None)


items = []
for pr, g in rows:
    if g.geom_type != 'Polygon' or g.area <= 0:
        continue
    k = str(pr.get('지장물번호') or '')
    방식 = str(pr.get('관경산출') or 방식맵.get(k) or '')
    L, w = band_dims(g)
    band = bool(L and w and L / w >= 3.0)
    표기 = str(pr.get('표기') or '').strip()
    종류 = str(pr.get('종류') or '')
    미상 = 종류 in ('미상', '', 'None') or not 표기
    사각 = any(s in 종류 for s in SAGAK)
    sp = parse_spec(표기)
    wlab = (sp[1] / 1000.0 if sp and sp[0] == 'C' else
            (sp[1] if sp and sp[0] == 'R' else
             (float(pr['규격_폭m']) if pr.get('규격_폭m') else None)))

    cls = chk = None
    sliver = bool(g.area < 1.0 and (L or 0) < 3.0)
    if sliver:
        cls, chk = 'F 슬리버(부스러기)', ('면적 %.2f㎡·길이 %.1fm — 선 교차부에서 생긴 조각. '
                                   '삭제 또는 인접 본체 병합 대상' % (g.area, L or 0))
    elif 미상:
        if band and snap_std(w):
            cls, chk = 'B 미상(판별 필요)', '실측폭 %.2f m → 원형관 D%d 인가, U%.1f 측구인가' % (
                w, snap_std(w), round(w, 1))
        else:
            cls, chk = 'D 폭 산정 불가', '규격 없음·뭉툭한 면(세장비 %.1f) — 도면 확인' % (
                (L / w) if (L and w) else 0)
    elif '추정' in 방식:
        cls, chk = 'D 폭 추정(기본 0.3)', '도면에 규격 없음 — 실제 규격 확인'
    elif wlab and w and band:
        r = w / wlab
        if r < 0.8:
            cls, chk = 'A 라벨 오귀속 의심', '표기 %s(폭 %.2f) 인데 실측 %.2f — 작은 지물에 큰 라벨?' % (
                표기, wlab, w)
        elif r > 1.2 and not 사각:
            cls, chk = 'A 라벨 오귀속 의심', '원형관 %s(폭 %.2f) 인데 실측 %.2f — 라벨 귀속 확인' % (
                표기, wlab, w)
        elif r > 1.2:
            cls, chk = 'E 측구 내공↔외곽(참고)', '표기 %.2f(내공) ↔ 실측 %.2f(외곽), 벽 %.2f m' % (
                wlab, w, (w - wlab) / 2)
    if w and (w < 0.05 or w > 6.6) and not cls:
        cls, chk = 'D 폭 비정상', '실측폭 %.2f m' % w
    if not cls:
        continue
    dc, kc = dist(ctree, cur, g)
    da, ka = dist(atree, allr, g)
    items.append(dict(구분=cls, 번호=k, 종류=종류, 표기=표기, 폭=pr.get('규격_폭m'),
                      실측폭=round(w, 3) if w else None, 세장비=round(L / w, 1) if (L and w) else None,
                      면적=round(g.area, 2), 생성방식=방식, 확인사항=chk,
                      금회거리=round(dc, 1) if dc is not None else None, 금회관번호=kc,
                      전체거리=round(da, 1) if da is not None else None, 전체관번호=ka,
                      geom=g))

# C 중복 쌍
tree = STRtree([g for _, g in rows])
dups, seen = [], set()
for i, (pr, g) in enumerate(rows):
    for j in [int(x) for x in tree.query(g)]:
        if j <= i:
            continue
        h = rows[j][1]
        it = g.intersection(h).area
        if it > 0 and it / min(g.area, h.area) > 0.3:
            a, b = pr.get('지장물번호'), rows[j][0].get('지장물번호')
            if (a, b) in seen:
                continue
            seen.add((a, b))
            dc, kc = dist(ctree, cur, g)
            dups.append(dict(a=a, b=b, k1=pr.get('종류'), t1=pr.get('표기'),
                             k2=rows[j][0].get('종류'), t2=rows[j][0].get('표기'),
                             a1=round(g.area, 2), a2=round(h.area, 2),
                             ov=round(it / min(g.area, h.area), 2),
                             거리=round(dc, 1) if dc is not None else None, 관번호=kc, geom=g))

print('=' * 100)
t = collections.defaultdict(lambda: [0, 0])
for it in items:
    t[it['구분']][0 if (it['금회거리'] is not None and it['금회거리'] <= NEAR) else 1] += 1
print('%-26s %8s %8s' % ('구분', '금회노선 10m내', '그밖'))
for k in sorted(t):
    print('%-26s %8d %8d' % (k, *t[k]))
print('%-26s %8d %8d' % ('C 중복 쌍', sum(1 for d in dups if d['거리'] is not None and d['거리'] <= NEAR),
                         sum(1 for d in dups if not (d['거리'] is not None and d['거리'] <= NEAR))))

for grp in ('A 라벨 오귀속 의심', 'B 미상(판별 필요)', 'D 폭 추정(기본 0.3)', 'D 폭 비정상',
            'D 폭 산정 불가', 'F 슬리버(부스러기)'):
    sub = [it for it in items if it['구분'] == grp and it['금회거리'] is not None
           and it['금회거리'] <= NEAR]
    if not sub:
        continue
    sub.sort(key=lambda r: r['금회거리'])
    print('\n' + '─' * 100)
    print('[%s] 금회 노선 10 m 이내 %d건' % (grp, len(sub)))
    for it in (sub if not grp.startswith('F') else sub[:12]):
        print('  %-10s %-8s %-13s 실측 %-6s | %-58s | %s %.1fm' % (
            it['번호'], it['종류'], it['표기'] or '-', it['실측폭'], it['확인사항'],
            it['금회관번호'], it['금회거리']))

print('\n' + '─' * 100)
print('[C 중복 쌍] %d건' % len(dups))
for d in sorted(dups, key=lambda r: (r['거리'] is None, r['거리'])):
    print('  %-10s %-8s %-12s(%6.1f㎡) ↔ %-10s %-8s %-12s(%6.1f㎡) 겹침 %.2f | %s %s' % (
        d['a'], d['k1'], d['t1'] or '-', d['a1'], d['b'], d['k2'], d['t2'] or '-', d['a2'],
        d['ov'], d['관번호'] or '', ('%.1fm' % d['거리']) if d['거리'] is not None else '>40m'))

e = [it for it in items if it['구분'].startswith('E')]
print('\n[E 참고 — 확인 불필요] %d건: 측구·BOX 표기(내공) ↔ 실측(외곽) 차이. 벽두께 중앙 %.2f m' % (
    len(e), sorted((it['실측폭'] - (it['폭'] or 0)) / 2 for it in e)[len(e) // 2] if e else 0))

cols = ['구분', '번호', '종류', '표기', '폭', '실측폭', '세장비', '면적', '생성방식', '확인사항',
        '금회거리', '금회관번호', '전체거리', '전체관번호']
with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(cols)
    for it in sorted(items, key=lambda r: (r['구분'], r['금회거리'] is None, r['금회거리'])):
        w.writerow([it[c] for c in cols])
    for d in dups:
        w.writerow(['C 중복 쌍', '%s / %s' % (d['a'], d['b']), '%s / %s' % (d['k1'], d['k2']),
                    '%s / %s' % (d['t1'], d['t2']), '', '', '',
                    '%s / %s' % (d['a1'], d['a2']), '',
                    '겹침비 %.2f — 어느 쪽을 남길지' % d['ov'], d['거리'], d['관번호'], '', ''])

schema = dict([('구분', 'str'), ('지장물번호', 'str'), ('종류', 'str'), ('표기', 'str'),
               ('폭m', 'float'), ('실측폭m', 'float'), ('면적', 'float'),
               ('확인사항', 'str'), ('금회노선거리m', 'float'), ('금회관번호', 'str')])
pts = [(it['geom'].representative_point(),
        dict(구분=it['구분'], 지장물번호=it['번호'], 종류=it['종류'], 표기=it['표기'],
             폭m=it['폭'], 실측폭m=it['실측폭'], 면적=it['면적'], 확인사항=it['확인사항'],
             금회노선거리m=it['금회거리'], 금회관번호=it['금회관번호'])) for it in items]
pts += [(d['geom'].representative_point(),
         dict(구분='C 중복 쌍', 지장물번호='%s / %s' % (d['a'], d['b']),
              종류='%s / %s' % (d['k1'], d['k2']), 표기='%s / %s' % (d['t1'], d['t2']),
              폭m=None, 실측폭m=None, 면적=d['a1'],
              확인사항='겹침비 %.2f — 어느 쪽을 남길지' % d['ov'],
              금회노선거리m=d['거리'], 금회관번호=d['관번호'])) for d in dups]
print('\n레이어 %d점 → %s' % (write_points(OUT, '지장물_확인목록', pts, schema), OUT))
print('CSV: %s' % CSV_OUT)
