# -*- coding: utf-8 -*-
"""10 — 가짜 폴리곤 삭제. 삭제 전 백업하고, 지운 내역을 이력 CSV에 남긴다.

왜 이력을 남기나: 폴리곤을 지우면 행이 사라져 보정 기록 필드에 근거를 남길 수 없다.
다음 회차에 같은 걸 또 만들어 올렸을 때 "이건 지난번에 지운 것"을 알 수 있어야 한다.

사용 (공통: [--config sewer.config.json] [--gpkg ...] [--poly ...] [--cl ...] [--log <이력.csv>])
  단건/수건   python -X utf8 10_delete_ghost.py --ids <번호1>,<번호2> --reason "경계선 표시(사용자 확인)" --write
  일괄(소형)  python -X utf8 10_delete_ghost.py --from-csv "<산출>/지장물_가짜후보.csv" --col 가짜번호
                                        --max-area 2.0 --reason "부속선 부스러기" --write
  --write 없으면 지울 목록만 출력.
이력: <산출>/지장물_삭제이력.csv (누적, 12 의 병합 흡수분도 같은 파일)
"""
import os
import sys
import csv
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

GPKG, POLY, CL = obstacle_src()
REASON = argval('--reason', '가짜 폴리곤(부속선·경계선)')
LOG = argval('--log') or out_path('지장물_삭제이력.csv')


def wanted():
    ids = []
    if '--ids' in sys.argv:
        ids += [x.strip() for x in argval('--ids').split(',') if x.strip()]
    if '--from-csv' in sys.argv:
        col = argval('--col', '가짜번호')
        mx = float(argval('--max-area', '1e9'))
        for r in csv.DictReader(open(argval('--from-csv'), encoding='utf-8-sig')):
            a = r.get('가짜면적') or r.get('면적') or '0'
            try:
                a = float(a)
            except ValueError:
                a = 0.0
            if r.get(col) and a <= mx:
                ids.append(r[col].strip())
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def main():
    ids = wanted()
    if not ids:
        print(__doc__)
        return
    rows = {pr.get('지장물번호'): (pr, g) for pr, g in read_layer(GPKG, POLY)}
    hit = [i for i in ids if i in rows]
    miss = [i for i in ids if i not in rows]
    print('요청 %d건 / 레이어에 있음 %d / 없음 %d' % (len(ids), len(hit), len(miss)))
    if miss:
        print('  없음(이미 삭제됐거나 번호 오류):', miss[:10])
    print('\n삭제 대상')
    for i in hit:
        pr, g = rows[i]
        L, w = band_dims(g)
        print('  %-12s %-9s %-14s 면적 %7.2f ㎡  실측폭 %-6s' % (
            i, pr.get('종류'), pr.get('표기') or '-', g.area, round(w, 3) if w else '-'))
    if not has('--write'):
        print('\n(지우지 않음 — 실제 삭제는 --write)')
        return

    backup(GPKG)
    con = sqlite3.connect(GPKG)
    cur = con.cursor()
    n_p = n_c = 0
    recs = []
    for i in hit:
        pr, g = rows[i]
        L, w = band_dims(g)
        recs.append(dict(일시=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                         지장물번호=i, 종류=pr.get('종류'), 표기=pr.get('표기'),
                         면적=round(g.area, 2), 실측폭=round(w, 3) if w else '',
                         구분=pr.get('구분'), 이유=REASON,
                         대표좌표='%.2f, %.2f' % (g.representative_point().x,
                                               g.representative_point().y)))
        cur.execute('delete from "%s" where 지장물번호=?' % POLY, (i,))
        n_p += cur.rowcount
        try:
            cur.execute('delete from "%s" where 지장물번호=?' % CL, (i,))
            n_c += cur.rowcount
        except Exception:
            pass
    con.commit()
    con.close()
    print('\n폴리곤 %d행 / 중심선 %d행 삭제' % (n_p, n_c))

    cols = ['일시', '지장물번호', '종류', '표기', '면적', '실측폭', '구분', '이유', '대표좌표']
    new = not os.path.exists(LOG)
    with open(LOG, 'a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        for r in recs:
            w.writerow(r)
    print('이력 기록 → %s (누적)' % LOG)
    feature_counts(GPKG, [POLY, CL])


if __name__ == '__main__':
    main()
