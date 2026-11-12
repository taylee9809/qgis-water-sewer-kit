# -*- coding: utf-8 -*-
"""05 — 품질 플래그 집계. '추측으로 만든 폴리곤'이 몇 개 남았는지 센다.

변환은 완전 자동이 될 수 없다(도면이 사람 보는 그림이라서). 그래서 목표는
'오류 0' 이 아니라 **추측한 것이 어디인지 아는 상태**다. 이 스크립트가 그 목록이다.

세는 항목
  · 생성방식(관경산출): 실측폭 / 도면표기 / 도면표기(단선버퍼) / 추정(단선 기본폭) / (인접승계)
  · 종류='미상' 또는 표기 공백
  · 무효·자기교차 폴리곤
  · 면적/길이가 비정상(폭 <0.05 m 또는 >6.6 m)
  · 서로 0.3 이상 겹치는 중복 쌍(누적본 납품에서 생긴다)
  · 03/04 가 남긴 보정 기록(보정_YYMMDD 또는 fields.obstacle_poly.fix_note) 건수

합격 기준(권장): 추정·미상 0건, 무효 0건, 중복 0건, 04 라벨 오귀속 의심 0건.
남으면 그 목록만 사람이 도면으로 확인하면 된다.

사용
  python -X utf8 05_qc_flags.py [--config sewer.config.json] [--gpkg ...] [--poly ...]
                                [--review] [--dup 0.3] [--srcgpkg <빌드.gpkg>]
  --srcgpkg 기본 survey.build_gpkg (정본에 관경산출이 없을 때 생성방식 참조원)
  결과: <산출>/05_qc_flags.csv
"""
import os
import sys
import csv
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

REVIEW = has('--review')
GPKG, POLY, _ = obstacle_src(review=REVIEW, need_cl=False, required=not REVIEW)
DUP = float(argval('--dup', '0.3'))
CSV_OUT = out_path('05_qc_flags%s.csv' % ('_확인필요' if REVIEW else ''))


def main():
    if REVIEW and (GPKG is None or not os.path.exists(GPKG)):
        print('확인필요 레이어(layers.obstacle_review)가 없다 — 건너뜀 (%s)' % (GPKG or '설정 없음'))
        return
    rows = read_layer(GPKG, POLY)
    cols = columns(GPKG, POLY)
    NOTES = note_fields(cols)
    # 통합 '정본' 레이어에는 생성방식(관경산출)이 없다 — 02 빌드 GPKG 의 지장물_폴리곤_<접미> 에서 끌어온다.
    # 정본은 '<접미>-J0001' 처럼 접두가 붙고 빌드 레이어는 'J0001' 이다.
    # 빌드마다 같은 번호를 쓰므로 접두를 붙여 구분해야 한다(안 하면 섞인다).
    방식맵 = {}
    if '관경산출' not in cols:
        방식맵, src2 = method_map(argval('--srcgpkg'))
        if 방식맵:
            print('생성방식 참조: %s (%d건)' % (os.path.basename(src2), len(방식맵)))
    print('%s | %s : %d 폴리곤' % (os.path.basename(GPKG), POLY, len(rows)))
    print('필드:', ', '.join(cols))

    flags = collections.defaultdict(list)
    for pr, g in rows:
        k = pr.get('지장물번호')
        k_raw = str(pr.get('지장물번호') or '')
        방식 = str(pr.get('관경산출') or 방식맵.get(k_raw) or '')
        if '추정' in 방식:
            flags['추정(기본폭)'].append(k)
        elif '단선버퍼' in 방식:
            flags['단선버퍼'].append(k)
        if '인접승계' in 방식:
            flags['라벨 인접승계'].append(k)
        if str(pr.get('종류') or '') in ('미상', '', 'None') or not str(pr.get('표기') or '').strip():
            flags['미상·표기없음'].append(k)
        if not g.is_valid:
            flags['무효 폴리곤'].append(k)
        if g.geom_type == 'Polygon' and g.area > 0:
            w = band_width(g) or 0
            if w < 0.05 or w > 6.6:
                flags['폭 비정상(%.2f)' % w if False else '폭 비정상'].append(k)
        if note_text(pr, NOTES):
            flags['보정 기록 있음'].append(k)

    tree = STRtree([g for _, g in rows])
    dups = set()
    for i, (pr, g) in enumerate(rows):
        for j in [int(x) for x in tree.query(g)]:
            if j <= i:
                continue
            h = rows[j][1]
            inter = g.intersection(h).area
            if inter <= 0:
                continue
            if inter / min(g.area, h.area) > DUP:
                dups.add((pr.get('지장물번호'), rows[j][0].get('지장물번호')))

    print('\n[플래그]')
    for k in sorted(flags, key=lambda k: -len(flags[k])):
        print('  %-16s %5d' % (k, len(flags[k])))
    print('  %-16s %5d  (겹침비 > %.2f)' % ('중복 쌍', len(dups), DUP))

    gate = sum(len(flags[k]) for k in ('추정(기본폭)', '미상·표기없음', '무효 폴리곤', '폭 비정상')) \
        + len(dups)
    print('\n합격 판정: %s  (추정+미상+무효+폭이상+중복 = %d)' % ('통과' if gate == 0 else '확인 필요', gate))

    with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['플래그', '지장물번호'])
        for k in sorted(flags):
            for v in flags[k]:
                w.writerow([k, v])
        for a, b in sorted(dups):
            w.writerow(['중복쌍', '%s / %s' % (a, b)])
    print('목록: %s' % CSV_OUT)


if __name__ == '__main__':
    main()
