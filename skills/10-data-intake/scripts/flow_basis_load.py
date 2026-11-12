# -*- coding: utf-8 -*-
"""기본계획 유량표(엑셀) → 표준 스키마 「유량기준」 표 — 분구별 계획하수량의 단일 원천.

수리계산은 이 표만 읽는다. 엑셀을 고쳤으면 이 스크립트를 다시 돌린다(엑셀 사본을 원천으로 쓰지 않는다).

읽는 법 (시트 구조가 같으면 인자 없이 돈다)
  - 위에서부터 읽는다. 분구 열(--zone-col, 기본 A)은 병합 셀이라 빈칸이면 위 값을 잇고, 줄바꿈은 지운다.
  - 구분 열(--gubun-col, 기본 B)이 전체·기존·계획 인 행 = 분구 유량 행.
  - 첫 열이 --stop-word(기본 '비고')로 시작하는 행 아래는 점유량 표(이름 · 면적 ha · 시간최대 m³/일)로 읽는다.
  - 열 → 표준 필드 (--col 필드=열[+열…], 엑셀 열 문자. 기본 = 한 기본계획 서식):
        일최대_m3d   = C+D+E   (생활오수 + 지하수사용오수 + 연계처리)
        시간최대_m3d = J       (수리계산유량 = 오수 시간최대(일최대×1.5) + 지하수 + 관광 + 기타)
        지하수_m3d   = G       (지하수 유입 — 첨두 미적용)
    계획인구·원단위_Lpcd·일평균_m3d 도 --col 로 줄 수 있다(서식에 있으면).
  - 처리분구 이름: 전체 행 = 분구 이름, 기존·계획 행 = '분구(기존)'·'분구(계획)' — 표준 표에 구분 열이 없어서다.
    --gubun 으로 넣을 구분을 고른다(기본 전체 기존 계획). --zone-name code 면 분구코드 열(--code-col, 기본 K) 값을 쓴다.
  - 목표연도 = --target-year (엑셀에 없다, 쓰기 때 필수). 자료기준일 = 엑셀 수정일(또는 --asof).
    근거 = '파일명 시트 N행 (md5 앞 8자리)' — 같은 원본인지 이것으로 가린다.

산출: <outputs.root>/10-intake/유량기준_미리보기.csv(적재할 행 + 읽은 열 값) · 유량기준_점유량.csv(점유량 표, 표준 표에 자리가 없음)
쓰기 (--write): 대상 = layers.flow_basis (GPKG 속성표 또는 PostGIS)
  - 같은 원본(md5)이 이미 들어가 있으면 건너뛴다.
  - 같은 (처리분구, 목표연도) 행이 있으면 멈춘다. --replace 면 그 행만 지우고 넣는다.
  - 쓰기 전 백업: GPKG 는 파일째 <이름>_bak_YYMMDD.gpkg, PostGIS 는 <표>_bak_YYMMDD.
엑셀은 읽기만 한다.

예)
  python -X utf8 flow_basis_load.py --xlsx "기본계획 유량.xlsx" --sheet 수리계산유량
  python -X utf8 flow_basis_load.py --xlsx "기본계획 유량.xlsx" --sheet 수리계산유량 --target-year 2040 --write
"""
import argparse
import csv
import datetime
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

DEF_COLS = {'일최대_m3d': 'C+D+E', '시간최대_m3d': 'J', '지하수_m3d': 'G'}
STD = ['처리분구', '목표연도', '계획인구', '원단위_Lpcd', '일평균_m3d', '일최대_m3d', '시간최대_m3d', '지하수_m3d', '근거', '자료기준일']
GUBUN = ('전체', '기존', '계획')


def col_idx(letter):
    n = 0
    for ch in letter.strip().upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


def parse(xlsx, sheet, zone_c, gubun_c, code_c, stop_word, num_cols):
    """원본 파서와 같은 규칙: 분구 행(구분 = 전체·기존·계획) / stop_word 아래 점유량 표."""
    from openpyxl import load_workbook
    ws = load_workbook(xlsx, data_only=True)[sheet]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    basis, point = [], []
    cur_zone = None
    mode = 'basis'
    for rn, r in enumerate(rows, start=1):
        c = [('' if v is None else v) for v in r]
        a = str(c[zone_c]).strip() if len(c) > zone_c else ''
        b = str(c[gubun_c]).strip() if len(c) > gubun_c else ''
        if a.startswith(stop_word):
            mode = 'point'
            continue
        if mode == 'basis':
            if a and a not in ('구분',):
                cur_zone = a.replace('\n', '')
            if b in GUBUN and cur_zone:
                vals = {L: fnum(c[col_idx(L)]) if len(c) > col_idx(L) else None for L in num_cols}
                code = str(c[code_c]).strip() if len(c) > code_c and c[code_c] else None
                basis.append(dict(row=rn, zone=cur_zone, gubun=b, code=code, vals=vals))
        else:
            if a and a not in ('구분', '계') and len(c) > 2:
                try:
                    area = float(c[1])
                    q = float(c[2])
                except Exception:
                    continue
                point.append(dict(row=rn, name=a, area_ha=area, q_max=q))
    return basis, point


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--xlsx', required=True, help='기본계획 유량 엑셀')
    ap.add_argument('--sheet', required=True, help='시트 이름')
    ap.add_argument('--zone-col', default='A', help='분구 열 (기본 A)')
    ap.add_argument('--gubun-col', default='B', help='구분(전체·기존·계획) 열 (기본 B)')
    ap.add_argument('--code-col', default='K', help='분구코드 열 (기본 K)')
    ap.add_argument('--zone-name', choices=('label', 'code'), default='label', help='처리분구 이름 출처 (기본 label = 분구 열)')
    ap.add_argument('--stop-word', default='비고', help='이 글자로 시작하는 행 아래는 점유량 표 (기본 비고)')
    ap.add_argument('--col', action='append', metavar='필드=열[+열]', help='표준 필드 ← 엑셀 열 합 (기본 %s)'
                    % ', '.join('%s=%s' % kv for kv in DEF_COLS.items()))
    ap.add_argument('--gubun', nargs='+', choices=GUBUN, default=list(GUBUN), help='넣을 구분 (기본 전체 기존 계획)')
    ap.add_argument('--target-year', type=int, help='목표연도 (쓰기 때 필수)')
    ap.add_argument('--asof', help='자료기준일 YYYY-MM-DD (기본 엑셀 수정일)')
    ap.add_argument('--write', action='store_true', help='layers.flow_basis 에 쓴다 (없으면 보고·미리보기만)')
    ap.add_argument('--replace', action='store_true', help='같은 (처리분구, 목표연도) 행을 지우고 넣는다')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = kitconfig.load(a.config)
    cols = dict(DEF_COLS)
    for s in a.col or []:
        k, v = s.split('=', 1)
        if k.strip() not in STD:
            ap.error('--col 필드는 표준 필드 중 하나: %s' % STD)
        cols[k.strip()] = v.strip()
    letters = sorted({L for v in cols.values() for L in v.split('+')}, key=col_idx)

    xl = Path(a.xlsx)
    md5 = hashlib.md5(xl.read_bytes()).hexdigest()
    asof = a.asof or datetime.date.fromtimestamp(xl.stat().st_mtime).isoformat()
    print('원본 %s  시트 %s\n  md5 %s  수정일 %s' % (xl.name, a.sheet, md5, asof))
    basis, point = parse(xl, a.sheet, col_idx(a.zone_col), col_idx(a.gubun_col), col_idx(a.code_col), a.stop_word, letters)
    print('분구 유량 행 %d / 점유량 행 %d' % (len(basis), len(point)))

    out_rows = []
    for b in basis:
        if b['gubun'] not in a.gubun:
            continue
        nm = b['code'] if (a.zone_name == 'code' and b['code']) else b['zone']
        nm = nm if b['gubun'] == '전체' else '%s(%s)' % (nm, b['gubun'])
        r = {k: None for k in STD}
        r.update(처리분구=nm, 목표연도=a.target_year, 자료기준일=asof,
                 근거='%s %s 시트 %d행 (md5 %s)' % (xl.name, a.sheet, b['row'], md5[:8]))
        for f, expr in cols.items():
            vs = [b['vals'].get(L) for L in expr.split('+')]
            r[f] = sum(v for v in vs if v is not None) if any(v is not None for v in vs) else None
        r['_raw'] = b
        out_rows.append(r)
    names = [r['처리분구'] for r in out_rows]
    dup = sorted({n for n in names if names.count(n) > 1})
    print('\n%-16s %10s %10s %10s' % ('처리분구', '일최대', '시간최대', '지하수'))
    for r in out_rows:
        print('%-16s %10s %10s %10s' % (r['처리분구'], r['일최대_m3d'], r['시간최대_m3d'], r['지하수_m3d']))
    if dup:
        print('★ 처리분구 이름 중복 %s — --zone-name 또는 엑셀 분구 이름을 확인 (같은 목표연도에 두 번 못 넣는다)' % dup)
    if point:
        print('\n점유량 %d건 (합계 %.1f ㎥/일) — 표준 유량기준 표에 자리가 없어 CSV 로만 낸다' % (len(point), sum(p['q_max'] for p in point)))

    od = cfg.out_dir('10-intake')
    with open(od / '유량기준_미리보기.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(STD + ['엑셀행', '분구', '구분', '분구코드'] + letters)
        for r in out_rows:
            b = r['_raw']
            w.writerow([r[k] for k in STD] + [b['row'], b['zone'], b['gubun'], b['code']] + [b['vals'][L] for L in letters])
    with open(od / '유량기준_점유량.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['구분', '면적_ha', '시간최대_m3d', '엑셀행', '근거'])
        for p in point:
            w.writerow([p['name'], p['area_ha'], p['q_max'], p['row'], '%s %s 시트 (md5 %s)' % (xl.name, a.sheet, md5[:8])])
    print('\n미리보기: %s\n점유량:   %s' % (od / '유량기준_미리보기.csv', od / '유량기준_점유량.csv'))

    if not a.write:
        print('(보고만 — DB 는 바꾸지 않았다. --target-year 와 --write 로 적재)')
        return
    if not a.target_year:
        ap.error('--write 에는 --target-year 가 필요하다 (목표연도는 필수 필드)')
    if dup:
        raise SystemExit('처리분구 이름이 겹쳐 넣지 않는다: %s' % dup)
    write(cfg, out_rows, md5, a.target_year, a.replace)


def write(cfg, rows, md5, year, replace):
    L = cfg.layer('flow_basis')
    tag = 'md5 %s' % md5[:8]
    stamp = datetime.datetime.now().strftime('%y%m%d')
    keys = [r['처리분구'] for r in rows]
    if L['kind'] == 'gpkg':
        p = Path(L['path'])
        if not p.exists():
            raise SystemExit('대상 GPKG 가 없다: %s' % p)
        cn = sqlite3.connect(p)
        try:
            have = [r[1] for r in cn.execute('pragma table_info("%s")' % L['layer'])]
            if not have:
                raise SystemExit('GPKG 에 표 %s 가 없다' % L['layer'])
            if cn.execute('select count(*) from "%s" where "근거" like ?' % L['layer'], ('%' + tag + '%',)).fetchone()[0] and not replace:
                print('같은 원본(%s)이 이미 들어가 있다 — 건너뜀 (다시 넣으려면 --replace)' % tag)
                return
            q = ','.join('?' * len(keys))
            n = cn.execute('select count(*) from "%s" where "목표연도"=? and "처리분구" in (%s)' % (L['layer'], q),
                           [year] + keys).fetchone()[0]
            if n and not replace:
                raise SystemExit('같은 (처리분구, 목표연도 %d) 행이 %d건 있다 — 바꾸려면 --replace' % (year, n))
        finally:
            cn.close()
        bak = p.with_name('%s_bak_%s%s' % (p.stem, stamp, p.suffix))
        if bak.exists():
            bak = p.with_name('%s_bak_%s%s' % (p.stem, datetime.datetime.now().strftime('%y%m%d_%H%M%S'), p.suffix))
        shutil.copy2(p, bak)
        print('백업: %s' % bak)
        cn = sqlite3.connect(p)
        try:
            cols = [c for c in STD if c in have]
            if replace:
                k = cn.execute('delete from "%s" where "목표연도"=? and "처리분구" in (%s)' % (L['layer'], ','.join('?' * len(keys))),
                               [year] + keys).rowcount
                print('기존 %d행 삭제' % k)
            cn.executemany('insert into "%s" (%s) values (%s)' % (L['layer'], ','.join('"%s"' % c for c in cols), ','.join('?' * len(cols))),
                           [[r[c] for c in cols] for r in rows])
            cn.commit()
            print('%s ← %d행 (빠진 열: %s)' % (L['layer'], len(rows), [c for c in STD if c not in have] or '없음'))
        finally:
            cn.close()
        return
    cn = cfg.pg_connect()
    sch, tb = L['schema'], L['layer']
    try:
        cu = cn.cursor()
        cu.execute('select column_name from information_schema.columns where table_schema=%s and table_name=%s', (sch, tb))
        have = [r[0] for r in cu.fetchall()]
        if not have:
            raise SystemExit('PostGIS 에 %s.%s 가 없다' % (sch, tb))
        cu.execute('select count(*) from "%s"."%s" where "근거" like %%s' % (sch, tb), ('%' + tag + '%',))
        if cu.fetchone()[0] and not replace:
            print('같은 원본(%s)이 이미 들어가 있다 — 건너뜀 (다시 넣으려면 --replace)' % tag)
            return
        cu.execute('select count(*) from "%s"."%s" where "목표연도"=%%s and "처리분구" = any(%%s)' % (sch, tb), (year, keys))
        n = cu.fetchone()[0]
        if n and not replace:
            raise SystemExit('같은 (처리분구, 목표연도 %d) 행이 %d건 있다 — 바꾸려면 --replace' % (year, n))
        bak = '%s_bak_%s' % (tb, stamp)
        cu.execute('select to_regclass(%s)', ('"%s"."%s"' % (sch, bak),))
        if cu.fetchone()[0]:
            bak = '%s_bak_%s' % (tb, datetime.datetime.now().strftime('%y%m%d_%H%M%S'))
        cu.execute('create table "%s"."%s" as table "%s"."%s"' % (sch, bak, sch, tb))
        print('백업: %s.%s' % (sch, bak))
        if replace:
            cu.execute('delete from "%s"."%s" where "목표연도"=%%s and "처리분구" = any(%%s)' % (sch, tb), (year, keys))
            print('기존 %d행 삭제' % cu.rowcount)
        cols = [c for c in STD if c in have]
        for r in rows:
            cu.execute('insert into "%s"."%s" (%s) values (%s)' % (sch, tb, ','.join('"%s"' % c for c in cols), ','.join(['%s'] * len(cols))),
                       [r[c] for c in cols])
        cn.commit()
        print('%s.%s ← %d행' % (sch, tb, len(rows)))
    except BaseException:
        cn.rollback()
        raise
    finally:
        cn.close()


if __name__ == '__main__':
    main()
