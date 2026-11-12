# -*- coding: utf-8 -*-
"""04 — 규격 라벨 오귀속 점검 + '미상' 폴리곤 규격 추정.

왜 필요한가
  측량도는 관로 1개에 규격 텍스트를 1개만 찍는다. 02 빌더는 조각난 폴리곤에
  '가까운 라벨'을 인접 승계로 전파하므로, 몇 m 떨어진 다른 지장물이 라벨을 받아간다.
  (사례: 노선 횡단 D600 관이 북쪽 ㄷ자 측구의 'U형측구03x03' 을 받아가 있었다.
   그때 실측폭 0.37 m ↔ 표기 U0.3 로 23% 차이가 나 있었다 — 이게 신호다)

판정 (강한 신호 → 약한 신호 순)
  A. 표기폭 vs 실측폭(둘레·면적 역산) 차이 > tol(기본 20%)
     ※ 세장비 L/w >= 3 인 '띠' 폴리곤만 본다(뭉툭한 면·맨홀·교량면은 폭 역산이 무의미)
     → 라벨이 잘못 붙었거나 폭이 잘못 계산된 것. 실측폭이 표준관경과 맞으면 그 값을 제안.
  B. 종류='미상' 또는 표기 공백인데 실측폭이 표준관경과 ±12% 안
     → 원형관 D{관경} 제안.
  ※ 주의: 측구도 띠 폭이 표준관경과 겹친다(U0.3 측구 폭 0.3 → D300 으로 스냅된다).
     B 를 통째로 --write 하지 말고 04_labels.csv 를 훑어 측구·BOX 가 섞였는지 먼저 볼 것.
  C. (참고) 내 표기와 같은 규격 텍스트가 내 선이 아닌 다른 선에 밀착
     → 조각난 관로의 정상적인 인접승계도 섞이므로 CSV 참고용으로만 낸다.

  --write 는 B 만 반영한다(안전한 보정). A·C 는 도면을 봐야 한다.

사용
  python -X utf8 04_check_labels.py [--config sewer.config.json] [--src <경량.dxf>]
                            [--gpkg ...] [--poly ...] [--tol 0.20] [--near 0.6]
                            [--note-field <필드>] [--write]
  결과: <산출>/04_labels.csv   ※ 03_fix_digeut.py 를 먼저 돌려야 실측폭이 제대로 나온다.
"""
import os
import sys
import csv
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

SRC = light_dxf()
GPKG, POLY, _ = obstacle_src(need_cl=False)
TOL = float(argval('--tol', '0.20'))
NEAR = float(argval('--near', '0.6'))
NOTE_FIELD = note_field()
CSV_OUT = out_path('04_labels.csv')


def label_width(pr):
    """표기에서 읽히는 폭(m). 원형은 관경, 사각은 폭."""
    s = parse_spec(pr.get('표기'))
    if s and s[0] == 'C':
        return s[1] / 1000.0
    if s and s[0] == 'R':
        return s[1]
    w = pr.get('규격_폭m')
    return float(w) if w else None


def main():
    doc = load_dxf(SRC)
    ch = chains(dxf_lines(doc, PIPE_LAYERS))
    ctree = STRtree(ch)
    specs = [(lay, t, x, y, parse_spec(t)) for lay, t, x, y in dxf_texts(doc)]
    specs = [r for r in specs if r[4]]
    stree = STRtree([Point(r[2], r[3]) for r in specs])
    rows = read_layer(GPKG, POLY)
    print('원도 체인 %d / 규격 텍스트 %d / 폴리곤 %d' % (len(ch), len(specs), len(rows)))

    A, Bl, C = [], [], []
    for pr, g in rows:
        if g.geom_type != 'Polygon' or g.area <= 0:
            continue
        Lm, wmeas = band_dims(g)
        bandlike = bool(Lm and wmeas and Lm / wmeas >= 3.0)
        wlab = label_width(pr)
        미상 = (str(pr.get('종류') or '') in ('미상', '', 'None')
              or not str(pr.get('표기') or '').strip())

        if 미상 and wmeas and bandlike:
            d = snap_std(wmeas)
            if d:
                Bl.append((pr, d, wmeas))
        elif wlab and wmeas and wlab > 0 and bandlike:
            diff = abs(wmeas - wlab) / wlab
            if diff > TOL:
                A.append((pr, wlab, wmeas, diff, snap_std(wmeas)))

        # C: 참고 — 같은 규격 텍스트가 내 선에 밀착해 있는가
        mine = specs_of_row(pr)
        if not mine or 미상:
            continue
        own = [ch[int(i)] for i in ctree.query(g.buffer(0.1))
               if ch[int(i)].intersects(g.buffer(0.05))]
        same = [specs[int(i)] for i in stree.query(g.buffer(12))
                if any(spec_eq(specs[int(i)][4], m) for m in mine)]
        if not same:
            continue
        ok = False
        other = None
        for lay, t, x, y, sp in same:
            P = Point(x, y)
            if own and min(c.distance(P) for c in own) <= NEAR:
                ok = True
                break
            near_ch = [ch[int(i)] for i in ctree.query(P.buffer(NEAR))
                       if ch[int(i)].distance(P) <= NEAR]
            if near_ch and not any(c.within(g.buffer(0.05)) for c in near_ch):
                other = (t, P.distance(g))
        if not ok and other:
            C.append((pr, other))

    print('\n[A. 표기폭 ↔ 실측폭 %d%% 초과 차이 — %d건] ★ 먼저 볼 것' % (TOL * 100, len(A)))
    for pr, wl, wm, diff, d in sorted(A, key=lambda r: -r[3])[:30]:
        print('  %-12s %-8s 표기 %-14s 표기폭 %.2f ↔ 실측 %.2f (%.0f%%)  제안 %s' % (
            pr.get('지장물번호'), pr.get('종류'), pr.get('표기'), wl, wm, diff * 100,
            ('D%d' % d) if d else '-'))
    print('\n[B. 미상 → 실측폭이 표준관경과 일치 — %d건] (--write 로 반영)' % len(Bl))
    for pr, d, wm in Bl[:20]:
        print('  %-12s 실측폭 %.3f → 원형관 D%d' % (pr.get('지장물번호'), wm, d))
    print('\n[C. 참고: 라벨이 내 선에 밀착 안 됨 — %d건] (조각난 관로의 정상 승계도 섞임)' % len(C))

    with open(CSV_OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['구분', '지장물번호', '종류', '표기', '표기폭m', '실측폭m', '차이%', '제안', '비고'])
        for pr, wl, wm, diff, d in sorted(A, key=lambda r: -r[3]):
            w.writerow(['A_폭불일치', pr.get('지장물번호'), pr.get('종류'), pr.get('표기'),
                        round(wl, 3), round(wm, 3), round(diff * 100, 1),
                        ('D%d' % d) if d else '', '라벨 오귀속 또는 폭 계산 오류 — 도면 확인'])
        for pr, d, wm in Bl:
            w.writerow(['B_미상규격제안', pr.get('지장물번호'), pr.get('종류'), pr.get('표기'),
                        '', round(wm, 3), '', 'D%d' % d, '실측폭을 표준관경으로 스냅(±12%)'])
        for pr, (t, dist) in C:
            w.writerow(['C_밀착실패(참고)', pr.get('지장물번호'), pr.get('종류'), pr.get('표기'),
                        '', '', '', '', '같은 규격 텍스트 %r 이 %.2f m 떨어진 다른 선에 밀착' % (t, dist)])
    print('목록: %s' % CSV_OUT)

    if not has('--write') or not Bl:
        print('\n(쓰기 안 함 — B 만 반영하려면 --write)')
        return
    backup(GPKG)
    add_column(GPKG, POLY, NOTE_FIELD)
    ups = [(pr['지장물번호'],
            {'종류': '원형관', '표기': 'D%d' % d, '규격_폭m': d / 1000.0, '관경mm': float(d),
             NOTE_FIELD: '미상 → 원도 실측폭 %.3f m 가 표준관경 D%d 와 일치해 채움 [04_check_labels]'
                         % (wm, d)}) for pr, d, wm in Bl]
    print('미상 규격 채움 %d행' % update_attrs(GPKG, POLY, ups))


if __name__ == '__main__':
    main()
