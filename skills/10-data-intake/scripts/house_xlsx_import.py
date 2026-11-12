# -*- coding: utf-8 -*-
"""배수설비 가구 반영/제외 관리 ② — 엑셀 편집표 → 가구 레이어(설정 layers.house) + 맨홀별 자체유량.

엑셀(house_xlsx_export.py 가 만든 표)의 반영여부·제외사유·점유량_m3d·배정맨홀 을 레이어에 되돌려 넣는다.
  - fid 로 대조하되 가구 이름(fields.house.house_id)이 어긋나면 그 행은 건너뛰고 보고한다(안전장치).
  - 반영여부가 '포함' 이 아니거나 점유량이 있으면 검토일 = 오늘.
  - 맨홀별 자체유량 = 포함된 가구의 (점유량_m3d 가 있으면 그것, 없으면 Q) 를 배정맨홀별로 합친다 → 수리계산 입력.
기본은 **보고만**(바뀔 값·집계). --write 일 때 레이어를 고친다:
  - 쓰기 전 백업(GPKG 파일째 <이름>_bak_YYMMDD.gpkg / PostGIS <표>_bak_YYMMDD)
  - 관리 필드(반영여부·제외사유·검토일·점유량_m3d)가 없으면 더한다
  - 바뀐 가구만 fid 로 UPDATE 한다(다른 필드·도형은 건드리지 않는다)
산출: <outputs.root>/10-intake/맨홀별_자체유량_YYMMDD.csv (보고만일 때도 — 엑셀 값 기준 집계)

예)
  python -X utf8 house_xlsx_import.py 배수설비가구_반영관리_YYMMDD.xlsx
  python -X utf8 house_xlsx_import.py 배수설비가구_반영관리_YYMMDD.xlsx --write
"""
import argparse
import collections
import csv
import datetime
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')
from house_xlsx_export import MGMT, read_house  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('xlsx', help='편집한 엑셀 (house_xlsx_export.py 산출)')
    ap.add_argument('--sheet', default='배수설비가구', help='시트 이름 (기본 배수설비가구)')
    ap.add_argument('--write', action='store_true', help='레이어를 고친다 (없으면 보고만)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = kitconfig.load(a.config)
    f_id = cfg.field('house', 'house_id', default='배수설비명')
    f_q = cfg.field('house', 'q_m3d', default='Q_m3d')
    f_mh = cfg.field('house', 'assigned_mh', default='배정맨홀')
    upd_cols = ('반영여부', '제외사유', '점유량_m3d', f_mh)
    today = datetime.date.today().isoformat()

    from openpyxl import load_workbook
    ws = load_workbook(a.xlsx, data_only=True)[a.sheet]
    hdr = [c.value for c in ws[1]]
    ix = {h: i for i, h in enumerate(hdr)}
    if 'fid' not in ix or f_id not in ix:
        raise SystemExit('엑셀 머리글에 fid 와 %s 가 있어야 한다' % f_id)
    xl = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[ix['fid']] is None:
            continue
        xl[int(row[ix['fid']])] = row
    print('엑셀 %d행 읽음' % len(xl))

    fields, rows = read_house(cfg)
    L = cfg.layer('house')
    print('레이어 %s %d건' % (L['layer'], len(rows)))
    chg = collections.Counter()
    mismatch, changed = [], {}
    for fid, p, _xy in rows:
        x = xl.get(fid)
        if x is None:
            continue
        if str(x[ix[f_id]]) != str(p.get(f_id)):
            mismatch.append((fid, p.get(f_id), x[ix[f_id]]))
            continue
        before = dict(p)
        for col in upd_cols:
            if col not in ix:
                continue
            v = x[ix[col]]
            if isinstance(v, str):
                v = v.strip() or None
            if v != p.get(col):
                p[col] = v
                chg[col] += 1
        if (p.get('반영여부') or '포함') != '포함' or p.get('점유량_m3d'):
            p['검토일'] = today
        d = {c: p.get(c) for c in upd_cols + ('검토일',) if p.get(c) != before.get(c)}
        if d:
            changed[fid] = d
    print('변경: %s (가구 %d)' % (dict(chg) or '없음', len(changed)))
    if mismatch:
        print('★ %s 불일치로 건너뛴 행 %d건: %s' % (f_id, len(mismatch), mismatch[:5]))

    # 반영 현황 · 맨홀별 자체유량
    excl = [p for _f, p, _xy in rows if (p.get('반영여부') or '포함') != '포함']
    print('\n=== 반영 현황 ===\n   포함 %d / 제외 %d' % (len(rows) - len(excl), len(excl)))
    for k, v in collections.Counter(str(p.get('제외사유') or '(사유없음)') for p in excl).most_common(10):
        print('     %-28s %d건' % (k, v))
    load = collections.defaultdict(float)
    cnt = collections.Counter()
    noassign = []
    for _f, p, _xy in rows:
        if (p.get('반영여부') or '포함') != '포함':
            continue
        q = (p.get('점유량_m3d') or 0) or (p.get(f_q) or 0)
        if not q:
            continue
        m = (p.get(f_mh) or '').strip()
        if not m:
            noassign.append((p.get(f_id), q))
            continue
        load[m] += q
        cnt[m] += 1
    print('\n=== 맨홀별 자체유량 ===')
    print('   배정 맨홀 %d개 / 합계 %.2f ㎥/일' % (len(load), sum(load.values())))
    print('   배정맨홀 없는 가구 %d건 (유량 %.2f)' % (len(noassign), sum(q for _, q in noassign)))
    for m, q in sorted(load.items(), key=lambda kv: -kv[1])[:10]:
        print('     %-16s %8.2f ㎥/일 (%d가구)' % (m, q, cnt[m]))
    out = cfg.out_dir('10-intake') / ('맨홀별_자체유량_%s.csv' % datetime.date.today().strftime('%y%m%d'))
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow([f_mh, '자체유량_m3d', '가구수'])
        for m in sorted(load):
            w.writerow([m, round(load[m], 4), cnt[m]])
    print('\n집계 CSV: %s' % out)

    if not a.write:
        print('(보고만 — 레이어는 바꾸지 않았다. 확인 후 --write)')
        return
    if not changed:
        print('바뀐 값이 없어 레이어를 고치지 않는다')
        return
    add = [(m, t) for m, t in MGMT if m not in fields]
    stamp = datetime.datetime.now().strftime('%y%m%d')
    if L['kind'] == 'gpkg':
        p = Path(L['path'])
        bak = p.with_name('%s_bak_%s%s' % (p.stem, stamp, p.suffix))
        if bak.exists():
            bak = p.with_name('%s_bak_%s%s' % (p.stem, datetime.datetime.now().strftime('%y%m%d_%H%M%S'), p.suffix))
        shutil.copy2(p, bak)
        print('백업: %s' % bak)
        # GDAL 이 만든 R-tree 갱신 트리거는 ST_* 함수를 불러 표준 sqlite3 에서 UPDATE 가 막힌다.
        # 도형은 안 바꾸므로 한 트랜잭션 안에서 그 트리거만 잠시 내렸다가 원문 그대로 다시 만든다.
        cn = sqlite3.connect(p, isolation_level=None)
        try:
            trig = [(n, s) for n, s in cn.execute("select name, sql from sqlite_master where type='trigger' and tbl_name=?",
                                                  (L['layer'],)).fetchall() if s and 'ST_' in s]
            cn.execute('begin')
            for n, _s in trig:
                cn.execute('drop trigger "%s"' % n)
            for m, t in add:
                cn.execute('alter table "%s" add column "%s" %s' % (L['layer'], m, 'REAL' if t == 'float' else 'TEXT'))
            for fid, d in changed.items():
                cn.execute('update "%s" set %s where fid=?' % (L['layer'], ', '.join('"%s"=?' % c for c in d)),
                           list(d.values()) + [fid])
            for _n, s in trig:
                cn.execute(s)
            cn.execute('commit')
        except BaseException:
            cn.execute('rollback')
            raise
        finally:
            cn.close()
    else:
        cn = cfg.pg_connect()
        sch, tb = L['schema'], L['layer']
        try:
            cu = cn.cursor()
            bak = '%s_bak_%s' % (tb, stamp)
            cu.execute('select to_regclass(%s)', ('"%s"."%s"' % (sch, bak),))
            if cu.fetchone()[0]:
                bak = '%s_bak_%s' % (tb, datetime.datetime.now().strftime('%y%m%d_%H%M%S'))
            cu.execute('create table "%s"."%s" as table "%s"."%s"' % (sch, bak, sch, tb))
            print('백업: %s.%s' % (sch, bak))
            for m, t in add:
                cu.execute('alter table "%s"."%s" add column if not exists "%s" %s'
                           % (sch, tb, m, 'double precision' if t == 'float' else 'text'))
            for fid, d in changed.items():
                cu.execute('update "%s"."%s" set %s where fid=%%s' % (sch, tb, ', '.join('"%s"=%%s' % c for c in d)),
                           list(d.values()) + [fid])
            cn.commit()
        except BaseException:
            cn.rollback()
            raise
        finally:
            cn.close()
    print('레이어 %s: 관리 필드 추가 %s · 가구 %d건 갱신' % (L['layer'], [m for m, _ in add] or '없음', len(changed)))


if __name__ == '__main__':
    main()
