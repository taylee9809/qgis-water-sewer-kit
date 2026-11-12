# -*- coding: utf-8 -*-
"""06 — 설계사 종단 지장물(.flo 의 x=/X= 토큰) ↔ QGIS 지장물 폴리곤 교차 검증.

왜 하나
  설계사 종단은 현장 조사값이라 측량현황도와 **독립된 검증원**이다.
  (사례: 한 회차 토큰 118건을 이렇게 대조해 19건의 폴리곤 오류를 찾아냈다.)

토큰 규약 (makesw .flo)
  x= 원형관 / X= 사각(측구·BOX) / G= 지반 보조점 → 지장물 아님, 제외
  저면EL + 0.30 ≈ 도면 관저고(원형). 사각은 0.45~0.70 으로 깊이마다 달라 키로 쓰지 않는다.
  크기 = 관경 + 0.60 규약인 파일이 있고 그대로인 파일이 있다 → 파일마다 확인할 것.

판정
  측점을 관로(상류맨홀→하류맨홀 직선) 따라 보간해 평면 좌표를 만들고,
  반경 5 m 안의 폴리곤 '후보 전체'와 규격(원형 D mm / 사각 폭×깊이)을 비교한다.
  최근접 1개만 보면 겹친 지장물에서 짝이 바뀐다.
    일치      : 규격까지 맞는 폴리곤이 있다
    규격상이  : 위치는 겹치는데 규격이 다르다 → 원도 실측폭·라벨로 어느 쪽이 맞는지 판단
    없음      : 5 m 안에 후보가 없다 → 상수관이면 상수관망 레이어에서 확인(측량도 대상 아님)

사용
  python -X utf8 06_check_profile.py [--config sewer.config.json] [--flo "A.flo" "B.flo"]
        [--gpkg ...] [--poly ...] [--review] [--route <노선.gpkg>] [--route-layer <레이어>]
        [--radius 5] [--out <결과.gpkg>]
  --flo   기본 survey.flo (설정의 .flo 목록)
  노선    --route > survey.design_check_gpkg > layers.plan_pipe (관번호 = fields.plan_pipe.pipe_id)
  --review 면 확인필요 레이어를 대조하고 결과 이름이 06_확인필요_check.* 가 된다(11 --promote 입력).
  결과: <산출>/06_profile_check.csv + 점 레이어 06_profile_check.gpkg (판정별 색 구분해 보면 된다)
"""
import os
import sys
import csv
import math
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

REVIEW = has('--review')
GPKG, POLY, _ = obstacle_src(review=REVIEW, need_cl=False, required=not REVIEW)
RSPEC = route_spec()
RAD = float(argval('--radius', '5'))
BASE = '06_확인필요_check' if has('--review') else '06_profile_check'
OUT = argval('--out', out_path(BASE + '.gpkg'))
CSV_OUT = out_path(BASE + '.csv')


def flo_paths():
    if '--flo' not in sys.argv:
        return flo_from_cfg()
    out = []
    for a in sys.argv[sys.argv.index('--flo') + 1:]:
        if a.startswith('--'):
            break
        out.append(a)
    return out


def main():
    floes = flo_paths()
    if not floes:
        print(__doc__)
        return
    if REVIEW and (GPKG is None or not os.path.exists(GPKG)):
        print('확인필요 레이어(layers.obstacle_review)가 없다 — 건너뜀 (%s)' % (GPKG or '설정 없음'))
        return
    route = {}
    kid = fld('plan_pipe', 'pipe_id', '관번호')
    for pr, g in read_spec(RSPEC):
        k = pr.get(kid)
        if k and g.geom_type == 'LineString':
            route[k] = g
    src_name = (os.path.basename(str(RSPEC['path'])) if RSPEC['kind'] == 'gpkg'
                else 'PostGIS %s' % RSPEC['schema'])
    print('노선 %d구간 (%s | %s)' % (len(route), src_name, RSPEC['layer']))

    rows = read_layer(GPKG, POLY)
    tree = STRtree([g for _, g in rows])
    print('지장물 폴리곤 %d' % len(rows))

    pts, miss = [], []
    for fp in floes:
        tok = flo_tokens(fp)
        n = sum(len(v) for v in tok.values())
        print('%s : 지장물 토큰 %d (구간 %d)' % (os.path.basename(fp), n, len(tok)))
        for up, items in tok.items():
            ln = route.get(up)
            if ln is None:
                miss.append(up)
                continue
            for it in items:
                q = ln.interpolate(min(max(it['측점'], 0.0), ln.length))
                pts.append(dict(파일=os.path.basename(fp), 관번호=up, 측점=it['측점'],
                                저면EL=it['저면EL'], 크기=it['크기'], pre=it['pre'], q=q))
    if miss:
        print('  ! 노선 레이어에 없는 관번호 %d개: %s' % (len(set(miss)), sorted(set(miss))[:8]))
    print('대조 대상 %d개' % len(pts))

    # 토큰 크기 → 규격 추정: 원형은 (크기 또는 크기−0.60), 사각은 비교 제외
    res = []
    for p in pts:
        cand = {}
        for i in [int(x) for x in tree.query(p['q'].buffer(RAD))]:
            d = rows[i][1].distance(p['q'])
            if d <= RAD:
                cand[i] = d
        hit = None
        if p['pre'] == 'x':
            guesses = [('C', p['크기'] * 1000), ('C', (p['크기'] - 0.60) * 1000)]
        else:
            guesses = []
        for i, d in sorted(cand.items(), key=lambda kv: kv[1]):
            ss = specs_of_row(rows[i][0])
            if guesses and any(spec_eq(gg, s) for gg in guesses for s in ss):
                hit = (i, d)
                break
        if hit:
            판정 = '일치'
        elif cand:
            판정 = '규격상이' if guesses else '위치일치(사각-규격비교제외)'
        else:
            판정 = '없음'
        i0 = (hit[0] if hit else (min(cand, key=cand.get) if cand else None))
        pr0 = rows[i0][0] if i0 is not None else {}
        res.append(dict(p, 판정=판정, 거리=(hit[1] if hit else (min(cand.values()) if cand else None)),
                        폴리곤=pr0.get('지장물번호'), QGIS종류=pr0.get('종류'),
                        QGIS표기=pr0.get('표기'), 후보수=len(cand)))

    cnt = collections.Counter(r['판정'] for r in res)
    print('\n[판정]', dict(cnt))
    print('\n규격상이·없음 목록(앞 30):')
    for r in res:
        if r['판정'] in ('일치', '위치일치(사각-규격비교제외)'):
            continue
        print('  %-14s 측점%6.1f %s=%.2f 저면 %6.2f → %-8s %-12s %s (후보 %d, %s)' % (
            r['관번호'], r['측점'], r['pre'], r['크기'], r['저면EL'],
            r['판정'], r['폴리곤'] or '-', r['QGIS표기'] or '-', r['후보수'],
            ('%.2f m' % r['거리']) if r['거리'] is not None else '-'))

    with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['파일', '관번호', '측점', '토큰', '크기', '저면EL', '판정', '폴리곤',
                    'QGIS종류', 'QGIS표기', '거리m', '후보수'])
        for r in res:
            w.writerow([r['파일'], r['관번호'], r['측점'], r['pre'], r['크기'], r['저면EL'],
                        r['판정'], r['폴리곤'], r['QGIS종류'], r['QGIS표기'],
                        round(r['거리'], 2) if r['거리'] is not None else '', r['후보수']])
    schema = dict([('파일', 'str'), ('관번호', 'str'), ('측점', 'float'), ('토큰', 'str'),
                   ('크기', 'float'), ('저면EL', 'float'), ('판정', 'str'), ('폴리곤', 'str'),
                   ('QGIS종류', 'str'), ('QGIS표기', 'str'), ('거리m', 'float'),
                   ('후보수', 'int')])
    write_points(OUT, '지장물_종단대조', [(r['q'], dict(
        파일=r['파일'], 관번호=r['관번호'], 측점=r['측점'], 토큰=r['pre'], 크기=r['크기'],
        저면EL=r['저면EL'], 판정=r['판정'], 폴리곤=r['폴리곤'], QGIS종류=r['QGIS종류'],
        QGIS표기=r['QGIS표기'], 거리m=r['거리'], 후보수=r['후보수'])) for r in res], schema)
    print('\n결과: %s / %s (레이어 지장물_종단대조)' % (CSV_OUT, OUT))


if __name__ == '__main__':
    main()
