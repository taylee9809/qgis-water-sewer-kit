# -*- coding: utf-8 -*-
"""12 — '반쪽으로 쪼개진' 폴리곤 병합.

원인
  측량도가 관로를 **관벽 2선 + 중심선 = 3선**으로 그린 구간이 있다.
  polygonize 하면 중심선이 경계가 되어 한 관이 폭 절반짜리 두 개로 쪼개진다.
  (사례·사용자 확인: 반쪽 두 개 = 원도에선 하나의 D600.
   원도 3선 간격 0.30/0.30, 양끝 0.60 캡 → 실제 관은 0.60 폭 한 개)

판정
  ① 표기가 같고 서로 맞닿은(공유 경계 길이 ≥ 양쪽 장변의 60%) 두 폴리곤
  ② 각자의 실측폭이 표기폭의 절반에 가깝다(기본 ±25%)
  ③ 합치면 실측폭이 표기폭과 맞는다(±15%)
  → 하나로 병합(union). 번호는 작은 쪽을 쓰고, 지운 번호는 삭제 이력에 남긴다.

사용
  python -X utf8 12_merge_halves.py [--config sewer.config.json] [--gpkg ...] [--poly ...] [--cl ...]
        [--district ...] [--log <이력.csv>] [--note-field <필드>] [--write]
  결과: <산출>/12_merge_halves.csv · 흡수 삭제분은 <산출>/지장물_삭제이력.csv 에 누적
  ※ --write 는 병합 목록을 사람이 도면으로 확인한 뒤에(보정 기록에 '사용자 확인' 이 남는다).
"""
import os
import sys
import csv
import math
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

GPKG, POLY, CL = obstacle_src()
LOG = argval('--log') or out_path('지장물_삭제이력.csv')
CSV_OUT = out_path('12_merge_halves.csv')
STAMP = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
NOTE = note_field()


def wlab_of(pr):
    sp = parse_spec(str(pr.get('표기') or ''))
    if sp and sp[0] == 'C':
        return sp[1] / 1000.0
    if sp and sp[0] == 'R':
        return sp[1]
    return float(pr['규격_폭m']) if pr.get('규격_폭m') else None


def main():
    rows = [(pr, g) for pr, g in read_layer(GPKG, POLY) if g.geom_type == 'Polygon']
    tree = STRtree([g for _, g in rows])
    cur = route_lines(current=True)
    ctree = STRtree([g for _, g in cur])
    print('폴리곤 %d' % len(rows))

    pairs, used = [], set()
    for i, (pa, ga) in enumerate(rows):
        ka = str(pa.get('지장물번호'))
        if ka in used:
            continue
        wl = wlab_of(pa)
        La, wa = band_dims(ga)
        if not wl or not wa or not La:
            continue
        if abs(wa - wl / 2) > 0.25 * (wl / 2):          # ② 절반 폭인가
            continue
        for j in [int(x) for x in tree.query(ga.buffer(0.05))]:
            if j == i:
                continue
            pb, gb = rows[j]
            kb = str(pb.get('지장물번호'))
            if kb in used or str(pb.get('표기') or '') != str(pa.get('표기') or ''):
                continue
            Lb, wb = band_dims(gb)
            if not wb or not Lb:
                continue
            if abs(wb - wl / 2) > 0.25 * (wl / 2):
                continue
            share = ga.buffer(0.02).intersection(gb.buffer(0.02)).area / 0.04 if True else 0
            shared = ga.exterior.intersection(gb.buffer(0.03)).length
            if shared < 0.6 * min(La, Lb):              # ① 긴 변을 공유하나
                continue
            u = unary_union([ga, gb]).buffer(0)
            Lu, wu = band_dims(u)
            if not wu or abs(wu - wl) > 0.15 * wl:      # ③ 합치면 표기폭과 맞나
                continue
            d, kc = None, None
            for x in [int(y) for y in ctree.query(u.buffer(40))]:
                dd = cur[x][1].distance(u)
                if d is None or dd < d:
                    d, kc = dd, cur[x][0]
            keep, drop = sorted([ka, kb])
            pairs.append(dict(keep=keep, drop=drop, 표기=pa.get('표기'), 종류=pa.get('종류'),
                              wl=wl, wa=round(wa, 3), wb=round(wb, 3),
                              면적=round(u.area, 2), 폭=round(wu, 3), 길이=round(Lu, 2),
                              geom=u, 공유=round(shared, 2),
                              거리=round(d, 1) if d is not None else None, 관번호=kc,
                              pr=(pa if keep == ka else pb)))
            used.add(ka)
            used.add(kb)
            break

    near = [p for p in pairs if p['거리'] is not None and p['거리'] <= 10]
    print('\n반쪽 쌍 %d개 / 금회 노선 10 m 이내 %d개' % (len(pairs), len(near)))
    for p in sorted(pairs, key=lambda r: (r['거리'] is None, r['거리'])):
        print('  %-10s + %-10s %-8s %-12s 폭 %.2f+%.2f → %.2f (표기 %.2f) 길이 %6.2f | %s %s' % (
            p['keep'], p['drop'], p['종류'], p['표기'], p['wa'], p['wb'], p['폭'], p['wl'],
            p['길이'], p['관번호'] or '-',
            ('%.1fm' % p['거리']) if p['거리'] is not None else '>40m'))

    with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['유지번호', '삭제번호', '종류', '표기', '표기폭', '폭A', '폭B', '병합폭',
                    '병합면적', '병합길이', '공유경계', '금회관번호', '금회거리m'])
        for p in pairs:
            w.writerow([p['keep'], p['drop'], p['종류'], p['표기'], p['wl'], p['wa'], p['wb'],
                        p['폭'], p['면적'], p['길이'], p['공유'], p['관번호'], p['거리']])
    print('\n목록: %s' % CSV_OUT)

    if not pairs or not has('--write'):
        print('(쓰기 안 함 — 반영은 --write)')
        return
    backup(GPKG)
    add_column(GPKG, POLY, NOTE)
    add_column(GPKG, CL, NOTE)
    items, cls, recs = [], [], []
    for p in pairs:
        d = dict(p['pr'])
        d[NOTE] = ('반쪽 병합: 원도가 관벽 2선 + 중심선(3선)으로 그려져 폭 %.2f 두 개로 쪼개져 '
                   '있었음 → %s 와 합쳐 폭 %.2f 단일 폴리곤 (사용자 확인 %s) [12_merge_halves]'
                   % (p['wa'], p['drop'], p['폭'], stamp6()))
        d['규격_폭m'] = round(p['폭'], 3)
        items.append((p['keep'], p['geom'], d))
        cls.append((p['keep'], band_axis(p['geom']), d))
        recs.append(dict(일시=STAMP, 지장물번호=p['drop'], 종류=p['종류'], 표기=p['표기'],
                         면적=p['면적'], 실측폭=p['wb'], 구분=p['pr'].get('구분'),
                         이유='반쪽 병합으로 %s 에 흡수 (원도 3선 작도)' % p['keep'],
                         대표좌표='%.2f, %.2f' % (p['geom'].representative_point().x,
                                                p['geom'].representative_point().y)))
    replace_features(GPKG, POLY, items)
    try:
        replace_features(GPKG, CL, cls)
    except Exception as e:
        print('중심선 갱신 건너뜀:', e)
    con = sqlite3.connect(GPKG)
    c = con.cursor()
    n = 0
    for p in pairs:
        c.execute('delete from "%s" where 지장물번호=?' % POLY, (p['drop'],))
        n += c.rowcount
        try:
            c.execute('delete from "%s" where 지장물번호=?' % CL, (p['drop'],))
        except Exception:
            pass
    con.commit()
    con.close()
    cols = ['일시', '지장물번호', '종류', '표기', '면적', '실측폭', '구분', '이유', '대표좌표']
    new = not os.path.exists(LOG)
    with open(LOG, 'a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        for r in recs:
            w.writerow(r)
    print('병합 %d쌍 / 흡수 삭제 %d행 / 이력 기록' % (len(items), n))
    feature_counts(GPKG, [POLY, CL])


if __name__ == '__main__':
    main()
