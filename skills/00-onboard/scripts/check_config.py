"""00-onboard 설정 검사기 — sewer.config.json 이 실제 데이터와 맞는지 확인한다. 읽기 전용.

    python -X utf8 check_config.py [--config <sewer.config.json>] [--no-geom]

확인하는 것
  1. 레이어마다: 열리는가 · 좌표계가 project.crs 와 같은가 · 매핑한 필드가 실제로 있는가 · 필수 논리 필드가 매핑됐는가 · 채움률
  2. 코드값: plan_pipe 의 kind 필드에 values.kind_gravity / kind_pressure 가 실제로 나오는가
  3. 관로 방향(geopandas 있을 때): 끝점 그래프의 '갈라지는 노드'·'마주 보는 노드' 수, 자연유하 역경사 건수, 관번호 중복
  4. DEM 파일 존재, design 기준값
산출: <outputs.root>/00-onboard/config_check.md. 치명 문제가 있으면 종료코드 1.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

REQUIRED = {
    'plan_pipe': ['pipe_id', 'dia_mm', 'us_invert', 'ds_invert', 'kind'],
    'plan_manhole': ['mh_id', 'ground', 'out_invert'],
    'plan_pump': ['pump_id', 'in_invert'],
    'house': ['house_id', 'q_m3d'],
    'asbuilt_pipe': ['pipe_id', 'dia_mm', 'us_invert', 'ds_invert', 'status'],
    'asbuilt_manhole': ['mh_id', 'ground', 'depth'],
}
DESIGN_KEYS = ['min_cover_m', 'min_slope_permil', 'manning_n', 'min_dia_mm', 'pressure_max_d_mm']


def table_info(cfg, key):
    """→ (columns:list, count:int, srid:int|None, sql_runner) 또는 예외"""
    L = cfg.layer(key)
    if L['kind'] == 'gpkg':
        if not L['path'] or not L['path'].exists():
            raise FileNotFoundError(f'GPKG 없음: {L["path"]}')
        cn = sqlite3.connect(f'file:{L["path"].as_posix()}?mode=ro', uri=True)
        t = L['layer']
        if not cn.execute('select 1 from gpkg_contents where table_name = ?', (t,)).fetchone():
            raise LookupError(f'GPKG 에 레이어 "{t}" 없음')
        cols = [r[1] for r in cn.execute(f'pragma table_info("{t}")')]
        n = cn.execute(f'select count(*) from "{t}"').fetchone()[0]
        srs = cn.execute('select srs_id from gpkg_contents where table_name = ?', (t,)).fetchone()[0]

        def run(sql):
            return cn.execute(sql).fetchall()
        return cols, n, srs, run, f'"{t}"'
    cn = cfg.pg_connect()
    cn.autocommit = True
    cur = cn.cursor()
    s, t = L['schema'], L['layer']
    cur.execute('select column_name from information_schema.columns where table_schema=%s and table_name=%s', (s, t))
    cols = [r[0] for r in cur.fetchall()]
    if not cols:
        raise LookupError(f'PostGIS 에 "{s}"."{t}" 없음')
    cur.execute(f'select count(*) from "{s}"."{t}"')
    n = cur.fetchone()[0]
    cur.execute('select srid from geometry_columns where f_table_schema=%s and f_table_name=%s', (s, t))
    r = cur.fetchone()

    def run(sql):
        cur.execute(sql)
        return cur.fetchall()
    return cols, n, (r[0] if r else None), run, f'"{s}"."{t}"'


def geom_checks(cfg, key, out):
    """끝점 그래프로 방향 의심 노드를 센다. geopandas 없으면 건너뛴다."""
    try:
        kitconfig.fix_proj()
        from shapely.ops import linemerge
        gdf = cfg.read_layer(key)
    except Exception as e:
        out.append(f'  - 방향 검사 건너뜀({type(e).__name__}: {str(e)[:80]})')
        return
    starts, ends = Counter(), Counter()
    for g in gdf.geometry:
        if g is None or g.is_empty:
            continue
        if g.geom_type == 'MultiLineString':
            g = linemerge(g)
        parts = list(g.geoms) if g.geom_type == 'MultiLineString' else [g]
        a, b = parts[0].coords[0], parts[-1].coords[-1]
        starts[(round(a[0] / 0.05), round(a[1] / 0.05))] += 1
        ends[(round(b[0] / 0.05), round(b[1] / 0.05))] += 1
    split = sum(1 for k, v in starts.items() if v >= 2)            # 한 점에서 두 관 이상이 '출발' = 갈라짐
    facing = sum(1 for k, v in ends.items() if v >= 2 and k not in starts)  # 두 관 끝이 마주보고 나가는 관 없음
    out.append(f'  - 끝점 그래프: 갈라지는 노드 {split} · 마주보는 끝(유출 없음) {facing}  '
               '→ 하수관은 갈라지지 않으므로 둘 다 많으면 도형 방향 역전 의심')
    try:
        us, ds, kind = cfg.field(key, 'us_invert'), cfg.field(key, 'ds_invert'), cfg.get(f'fields.{key}.kind') or cfg.get(f'fields.{key}.flow_kind')
        sub = gdf
        if kind and cfg.value('kind_gravity'):
            sub = gdf[gdf[kind] == cfg.value('kind_gravity')]
        adverse = ((sub[us].astype(float) + 0.001) < sub[ds].astype(float)).sum()
        out.append(f'  - 자연유하 역경사(상류관저 < 하류관저) {adverse} / {len(sub)}건 — 많으면 필드 상·하류가 뒤바뀌었거나 도형 역전')
    except Exception as e:
        out.append(f'  - 역경사 검사 건너뜀({str(e)[:80]})')
    try:
        pid = cfg.field(key, 'pipe_id')
        dup = gdf[pid].dropna().astype(str)
        dup = dup[dup.duplicated()].unique()
        out.append(f'  - 관번호 중복 {len(dup)}종' + (f' (예: {", ".join(dup[:5])})' if len(dup) else ''))
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    kitconfig.add_config_arg(ap)
    ap.add_argument('--no-geom', action='store_true', help='방향 검사 생략(빠름)')
    a = ap.parse_args()
    cfg = kitconfig.load(a.config)

    rep = [f'# 설정 검사 — {cfg.path}', '', f'프로젝트 루트: `{cfg.root}`', '']
    fatal = 0
    want_srid = None
    crs = str(cfg.get('project.crs', ''))
    if crs.upper().startswith('EPSG:'):
        want_srid = int(crs.split(':')[1])

    rep += ['## 레이어', '']
    for key in (cfg.get('layers') or {}):
        try:
            cols, n, srid, run, tname = table_info(cfg, key)
        except Exception as e:
            rep.append(f'- ❌ **{key}** — {e}')
            fatal += 1
            continue
        mapped = cfg.fields(key)
        missing_cols = [f'{lg}→{col}' for lg, col in mapped.items() if col not in cols]
        need = [lg for lg in REQUIRED.get(key, []) if lg not in mapped]
        bad = bool(missing_cols or need)
        srs_txt = '' if (want_srid is None or srid in (None, want_srid)) else f' · ⚠ 좌표계 {srid} ≠ {want_srid}'
        rep.append(f'- {"❌" if bad else "✅"} **{key}** = {tname} · {n}건{srs_txt}')
        if missing_cols:
            rep.append(f'  - 매핑했지만 실제로 없는 필드: {", ".join(missing_cols)}')
        if need:
            rep.append(f'  - 필수인데 매핑 안 된 논리 필드: {", ".join(need)}')
        fatal += bad
        # 채움률 (필수 필드만)
        for lg in REQUIRED.get(key, []):
            col = mapped.get(lg)
            if col and col in cols and n:
                filled = run(f'select count(*) from {tname} where "{col}" is not null')[0][0]
                if filled / n < 0.9:
                    rep.append(f'  - ⚠ {lg}(`{col}`) 채움률 {filled * 100 // n}% — 비어 있는 행은 판정에서 빠진다')
        # 코드값 (관로 레이어만 — 맨홀·펌프장의 '구분'은 뜻이 다르다)
        kcol = {'plan_pipe': mapped.get('kind'), 'asbuilt_pipe': mapped.get('flow_kind')}.get(key)
        if kcol and kcol in cols:
            vals = [str(r[0]) for r in run(f'select distinct "{kcol}" from {tname} where "{kcol}" is not null limit 20')]
            exp = [cfg.value('kind_gravity'), cfg.value('kind_pressure')]
            miss = [v for v in exp if v and v not in vals]
            if miss:
                rep.append(f'  - ⚠ 코드값 {miss} 이(가) `{kcol}` 에 없음. 실제 값: {vals[:10]} → values 절을 고칠 것')
        scol = mapped.get('status')
        if scol and scol in cols:
            vals = [str(r[0]) for r in run(f'select distinct "{scol}" from {tname} where "{scol}" is not null limit 20')]
            rep.append(f'  - 상태값 `{scol}`: {vals[:10]}')
        if key in ('plan_pipe', 'asbuilt_pipe') and not a.no_geom and not bad:
            geom_checks(cfg, key, rep)

    rep += ['', '## DEM', '']
    for d in cfg.get('dem') or []:
        p = cfg.path_of(d)
        rep.append(f'- {"✅" if p.exists() else "❌"} {p}')
        fatal += not p.exists()
    if not cfg.get('dem'):
        rep.append('- ⚠ DEM 없음 — 지반고·토피 계산(30·32)이 안 된다')

    rep += ['', '## 설계기준', '']
    for k in DESIGN_KEYS:
        v = cfg.get(f'design.{k}')
        rep.append(f'- {"✅" if v is not None else "⚠"} {k} = {v}')

    rep += ['', f'**치명 문제 {fatal}건**' + (' — 고친 뒤 다시 돌릴 것.' if fatal else ' — 통과.')]
    out = cfg.out_dir('00-onboard') / 'config_check.md'
    out.write_text('\n'.join(rep), encoding='utf-8')
    print('\n'.join(rep))
    print(f'\n→ {out}')
    sys.exit(1 if fatal else 0)


if __name__ == '__main__':
    main()
