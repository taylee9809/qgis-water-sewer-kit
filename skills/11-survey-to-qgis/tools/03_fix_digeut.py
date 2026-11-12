# -*- coding: utf-8 -*-
"""03 — ㄷ자 외곽선을 단선으로 보고 버퍼해 '두께가 2배'가 된 폴리곤 전수 보정.

왜 생기나
  측량도가 관·측구를 '두 변 + 막힌 끝 한 변'(ㄷ자)으로만 그린 구간이 있다.
  02 빌더의 C 단계는 그걸 한 줄짜리 선으로 보고 규격폭으로 버퍼하므로
  외곽선 전체가 두꺼워진 ㄷ자 폴리곤이 나온다 → 간섭 검토에서 가짜 교차 발생.

판정 (거리로 짝지으면 옆 폴리곤에 오배정된다 — 반드시 within 으로)
  ① 폴리곤 '안에' 원도 체인이 완전히 들어 있다(= 그 체인을 버퍼해 만든 것)
  ② 그 체인이 ㄷ자다: 정점 ≥4, 연결변 0.1~--wmax(기본 6.5) m, 양옆 세그먼트 역평행 ≥160°, 양변 ≥1 m
  ③ Polygon(체인좌표) 면적이 현재 면적의 ratio 이하(기본 0.75)

보정
  시점·종점을 이어 폐합한 밴드로 교체. 폭(규격_폭m)은 표기와 20% 넘게 다를 때만
  ㄷ자 연결변 길이(= 실측 외폭)로 바꾼다.
  자기교차로 무효면 중심선 버퍼로 대체하고 목록에 남긴다.

사용
  python -X utf8 03_fix_digeut.py [--config sewer.config.json] [--src <경량.dxf>]
                          [--gpkg <지장물.gpkg>] [--poly <폴리곤 레이어>] [--cl <중심선 레이어>]
                          [--review] [--ratio 0.75] [--wmax 6.5] [--note-field <필드>] [--write]
  대상 기본: layers.obstacle_poly / obstacle_line. --review 면 obstacle_review(_line).
  --write 없으면 검출만. 결과 CSV: <산출>/03_digeut.csv (--review 면 03_digeut_확인필요.csv)
"""
import os
import sys
import csv
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

SRC = light_dxf()
REVIEW = has('--review')
GPKG, POLY, CL = obstacle_src(review=REVIEW, required=not REVIEW)
RATIO = float(argval('--ratio', '0.75'))
WMAX = float(argval('--wmax', '6.5'))   # 2.0 이던 때 폭 2 m 넘는 박스 ㄷ자를 놓쳤다
NOTE_FIELD = note_field()
CSV_OUT = out_path('03_digeut%s.csv' % ('_확인필요' if REVIEW else ''))


def main():
    if REVIEW and (GPKG is None or not os.path.exists(GPKG)):
        print('확인필요 레이어(layers.obstacle_review)가 없다 — 건너뜀 (%s)' % (GPKG or '설정 없음'))
        return
    print('원도   %s' % SRC)
    print('대상   %s | %s' % (os.path.basename(GPKG), POLY))
    doc = load_dxf(SRC)
    ch = chains(dxf_lines(doc, PIPE_LAYERS))
    ctree = STRtree(ch)
    rows = read_layer(GPKG, POLY)
    print('원도 체인 %d / 폴리곤 %d' % (len(ch), len(rows)))

    hits = []
    for pr, g in rows:
        if g.geom_type != 'Polygon':
            continue
        inside = [ch[int(i)] for i in ctree.query(g)
                  if ch[int(i)].length > 0.3 and ch[int(i)].within(g.buffer(0.03))]
        if not inside:
            continue
        c0 = max(inside, key=lambda x: x.length)
        dg = digeut(list(c0.coords), w_max=WMAX)
        if not dg:
            continue
        try:
            fix = Polygon([tuple(p[:2]) for p in c0.coords]).buffer(0)
        except Exception:
            fix = None
        if fix is None or fix.is_empty:
            fix, how = band_axis(g).buffer(dg['w'] / 2, cap_style=2, join_style=2), '중심선버퍼(대체)'
        elif fix.geom_type != 'Polygon' or not fix.is_valid:
            fix, how = band_axis(g).buffer(dg['w'] / 2, cap_style=2, join_style=2), '중심선버퍼(자기교차)'
        else:
            how = '폐합'
        if fix.area > g.area * RATIO:
            continue                      # 이미 폐합 형상 = 정상
        hits.append((pr, g, fix, dg, how))

    print('\n[두께 2배 ㄷ자 %d건]' % len(hits))
    with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['지장물번호', '종류', '표기', '종전폭m', '보정폭m', '종전면적', '보정면적',
                    '보정방식', '체인길이', '역평행도'])
        for pr, g, fix, dg, how in hits:
            L = sum(math.dist(c, d) for c, d in zip(list(g.exterior.coords),
                                                    list(g.exterior.coords)[1:]))
            print('  %-12s %-8s %-14s 폭 %-5s→%.2f  면적 %7.2f→%7.2f  %s' % (
                pr.get('지장물번호'), pr.get('종류'), pr.get('표기'),
                pr.get('규격_폭m'), dg['w'], g.area, fix.area, how))
            w.writerow([pr.get('지장물번호'), pr.get('종류'), pr.get('표기'),
                        pr.get('규격_폭m'), round(dg['w'], 3), round(g.area, 2),
                        round(fix.area, 2), how, round(L, 2), round(dg['dev'], 1)])
    print('목록: %s' % CSV_OUT)

    if not hits or not has('--write'):
        print('\n(쓰기 안 함 — 반영하려면 --write)')
        return
    backup(GPKG)
    add_column(GPKG, POLY, NOTE_FIELD)
    add_column(GPKG, CL, NOTE_FIELD)
    items, cls = [], []
    for pr, g, fix, dg, how in hits:
        d = dict(pr)
        try:
            w0 = float(pr.get('규격_폭m'))
        except (TypeError, ValueError):
            w0 = None
        if not w0 or abs(dg['w'] - w0) / w0 > 0.2:      # 표기와 20% 이내면 표기 규격 유지
            d['규격_폭m'] = round(dg['w'], 3)             # (BOX 3.0 을 실측 2.91 로 덮지 않게)
        d[NOTE_FIELD] = ('ㄷ자 외곽선(두 변+막힌 끝)을 단선으로 보고 버퍼해 두께 2배였음 → '
                         '%s 형상으로 교체(면적 %.2f→%.2f ㎡, 실측 외폭 %.2f m) [03_fix_digeut]'
                         % (how, g.area, fix.area, dg['w']))
        items.append((pr['지장물번호'], fix, d))
        cls.append((pr['지장물번호'], band_axis(fix), d))
    replace_features(GPKG, POLY, items)
    try:
        replace_features(GPKG, CL, cls)
    except Exception as e:
        print('  중심선 갱신 건너뜀:', e)
    print('\n반영 %d건' % len(items))
    feature_counts(GPKG, [POLY, CL])


if __name__ == '__main__':
    main()
