"""킷 표준 스키마(templates/db_schema.json)로 빈 DB 를 만든다 — DB 를 처음 잡는 프로젝트용.

    python -X utf8 make_empty_db.py --gpkg data\\sewer.gpkg [--write-config sewer.config.json]
    python -X utf8 make_empty_db.py --sql schema.sql [--schema public]     # PostGIS DDL (실행은 사람이 확인 후)

--gpkg          빈 GPKG(레이어·필드·유일성 인덱스). 이미 있으면 멈춘다.
--write-config  예시 설정을 바탕으로, 모든 레이어가 이 GPKG 를 가리키는 sewer.config.json 을 쓴다.
                이 GPKG 에 없는 레이어(상수관·도로·교량·지적 — 외부 자료)는 키를 지운다. 들여온 뒤 다시 적는다.
--sql           PostGIS 용 CREATE TABLE 스크립트(NOT NULL·코드값 CHECK·UNIQUE·GIST 인덱스). DB 에 직접 쓰지 않는다.
설계 근거: standard/db-design.md
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((KIT / 'templates' / 'db_schema.json').read_text(encoding='utf-8'))

# 지구 안에서 유일해야 하는 열 묶음 — 우리 DB 에서 관번호 중복이 실제로 생겼던 곳
UNIQUE = {
    'plan_pipe': ['지구', '관번호'], 'plan_manhole': ['지구', '맨홀번호'], 'plan_pump': ['지구', '펌프장'],
    'asbuilt_pipe': ['지구', '관번호'], 'asbuilt_manhole': ['지구', '맨홀번호'],
    'flow_basis': ['처리분구', '목표연도'], 'hyd_result': ['회차', '지구', '관번호'], 'delivery': ['회차'],
}
FIONA_TYPE = {'TEXT': 'str', 'REAL': 'float', 'INTEGER': 'int', 'DATE': 'str'}
PG_TYPE = {'TEXT': 'text', 'REAL': 'double precision', 'INTEGER': 'integer', 'DATE': 'date'}


def code_of(desc):
    m = re.search(r'코드 (\S+)', desc or '')
    return m.group(1) if m and m.group(1) in SCHEMA['codes'] else None


def make_gpkg(path: Path):
    import fiona
    from fiona.crs import CRS
    if path.exists():
        raise SystemExit(f'이미 있다: {path} — 덮어쓰지 않는다')
    path.parent.mkdir(parents=True, exist_ok=True)
    crs = CRS.from_string(SCHEMA['crs'])
    for t in SCHEMA['tables']:
        props = {f[0]: FIONA_TYPE[f[1]] for f in t['fields']}
        schema = {'geometry': t['geom'] or 'None', 'properties': props}
        with fiona.open(path, 'w', driver='GPKG', layer=t['name'], schema=schema, crs=crs if t['geom'] else None):
            pass
    cn = sqlite3.connect(path)
    for t in SCHEMA['tables']:
        cols = UNIQUE.get(t['key'])
        if cols:
            idx = f'ux_{t["key"]}'
            cn.execute(f'create unique index "{idx}" on "{t["name"]}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
    cn.commit()
    n = cn.execute("select count(*) from gpkg_contents").fetchone()[0]
    cn.close()
    print(f'GPKG 생성: {path} — 레이어·표 {n}개')


def make_sql(path: Path, schema_name: str):
    srid = int(SCHEMA['crs'].split(':')[1])
    L = [f'-- qgis-water-sewer-kit 표준 스키마 (templates/db_schema.json 에서 생성)',
         f'-- 실행 전에 확인할 것: 스키마 이름 "{schema_name}", 좌표계 EPSG:{srid}. 기존 테이블이 있으면 실패한다(덮어쓰지 않음).',
         'CREATE EXTENSION IF NOT EXISTS postgis;', f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";', '']
    for t in SCHEMA['tables']:
        q = f'"{schema_name}"."{t["name"]}"'
        cols = ['  fid serial PRIMARY KEY']
        for name, typ, req, _logical, desc in t['fields']:
            c = f'  "{name}" {PG_TYPE[typ]}' + (' NOT NULL' if req else '')
            code = code_of(desc)
            if code:
                vals = ', '.join("'" + v + "'" for v in SCHEMA['codes'][code])
                c += f' CHECK ("{name}" IN ({vals}))'
            cols.append(c)
        if t['geom']:
            cols.append(f'  geom geometry({t["geom"]}, {srid})')
        u = UNIQUE.get(t['key'])
        if u:
            cols.append('  UNIQUE (' + ', '.join(f'"{c}"' for c in u) + ')')
        L += [f'-- {t["key"]}: {t["desc"]}', f'CREATE TABLE {q} (', ',\n'.join(cols), ');']
        if t['geom']:
            L.append(f'CREATE INDEX ON {q} USING gist (geom);')
        L.append('')
    path.write_text('\n'.join(L), encoding='utf-8')
    print(f'PostGIS DDL: {path} — 표 {len(SCHEMA["tables"])}개 (DB 에 실행하지 않았다)')


def write_config(cfg_path: Path, gpkg: Path):
    if cfg_path.exists():
        raise SystemExit(f'이미 있다: {cfg_path} — 덮어쓰지 않는다')
    ex = json.loads((KIT / 'templates' / 'sewer.config.example.json').read_text(encoding='utf-8'))
    try:
        rel = gpkg.resolve().relative_to(cfg_path.resolve().parent).as_posix()
    except ValueError:
        rel = gpkg.resolve().as_posix()
    keys = {t['key']: t['name'] for t in SCHEMA['tables']}
    layers = {}
    for k, spec in ex['layers'].items():
        if k in keys:
            layers[k] = {'src': rel, 'layer': keys[k]}
    for k, name in keys.items():                       # 예시 설정에 없는 표준 표도 넣는다
        layers.setdefault(k, {'src': rel, 'layer': name})
    ex['layers'] = layers
    ex['_layers'] = ('make_empty_db.py 가 만든 빈 GPKG 를 가리킨다. 상수관·도로·교량·지적 같은 외부 자료는 들여온 뒤 '
                     'water_pipe·road_boundary·bridge·parcel 키를 더한다.')
    ex.pop('db', None)
    ex['dem'] = []
    ex['_dem'] = 'DEM(1 m 실측 → 5 m 순)을 넣으면 경로를 적는다. 비어 있으면 30·32 의 지반고 계산이 안 된다.'
    ex['project']['name'] = '새 프로젝트'
    ex['project']['root'] = '.'
    cfg_path.write_text(json.dumps(ex, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'설정: {cfg_path} — 레이어 {len(layers)}개가 {rel} 를 가리킴. 지구·설계기준·납품 폴더 규칙은 00-onboard 로 채울 것')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--gpkg', type=Path)
    ap.add_argument('--write-config', type=Path)
    ap.add_argument('--sql', type=Path)
    ap.add_argument('--schema', default='public')
    a = ap.parse_args()
    if not (a.gpkg or a.sql):
        ap.error('--gpkg 또는 --sql 중 하나는 줘야 한다')
    if a.write_config and not a.gpkg:
        ap.error('--write-config 는 --gpkg 와 같이 쓴다')
    if a.gpkg:
        make_gpkg(a.gpkg)
        if a.write_config:
            write_config(a.write_config, a.gpkg)
    if a.sql:
        make_sql(a.sql, a.schema)


if __name__ == '__main__':
    main()
