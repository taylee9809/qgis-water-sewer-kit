"""00-onboard 조사기 — 그 팀 데이터(GPKG · PostGIS · QGIS 프로젝트)의 레이어·필드·표본값을 한 번에 훑는다.

읽기 전용이다. 원본에 아무것도 쓰지 않는다.

    python -X utf8 probe.py <경로…> [--postgis host:port/dbname --user postgres --password-env SEWER_PG_PASSWORD]
                            [--schema public] [--out _산출/00-onboard]

<경로> 는 .gpkg / .qgz / .qgs 파일 또는 폴더(안의 *.gpkg *.qgz *.qgs 를 재귀 탐색).
산출: inventory.json(전체) · inventory.md(Claude 가 대응표 만들 때 읽는 요약).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

SAMPLE_N = 5
NUMERIC = ('REAL', 'DOUBLE', 'FLOAT', 'INTEGER', 'INT', 'NUMERIC', 'MEDIUMINT', 'SMALLINT', 'BIGINT',
           'double precision', 'integer', 'numeric', 'real', 'bigint', 'smallint')


def _short(v, n=40):
    s = str(v)
    return s if len(s) <= n else s[:n] + '…'


# ---------------- GPKG ----------------
def probe_gpkg(path: Path):
    out = []
    cn = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    try:
        rows = cn.execute(
            'select c.table_name, c.data_type, g.column_name, g.geometry_type_name, c.srs_id '
            'from gpkg_contents c left join gpkg_geometry_columns g on c.table_name = g.table_name').fetchall()
        for t, dt, gcol, gtype, srs in rows:
            try:
                n = cn.execute(f'select count(*) from "{t}"').fetchone()[0]
                cols = cn.execute(f'pragma table_info("{t}")').fetchall()
            except sqlite3.Error as e:
                out.append({'source': str(path), 'kind': 'gpkg', 'layer': t, 'error': str(e)})
                continue
            fields = []
            for _cid, name, ctype, _nn, _d, pk in cols:
                if name == gcol or pk:
                    continue
                q = f'"{name}" is not null and cast("{name}" as text) != \'\''
                filled = cn.execute(f'select count(*) from "{t}" where {q}').fetchone()[0]
                samples = [r[0] for r in cn.execute(f'select distinct "{name}" from "{t}" where {q} limit {SAMPLE_N}')]
                info = {'name': name, 'type': ctype, 'filled': round(filled / n, 3) if n else 0,
                        'samples': [_short(s) for s in samples]}
                if (ctype or '').upper() in NUMERIC and filled:
                    info['range'] = list(cn.execute(f'select min("{name}"), max("{name}") from "{t}"').fetchone())
                fields.append(info)
            out.append({'source': str(path), 'kind': 'gpkg', 'layer': t, 'geometry': gtype or dt,
                        'srs': srs, 'count': n, 'fields': fields})
    finally:
        cn.close()
    return out


# ---------------- PostGIS ----------------
def probe_postgis(dsn: str, user: str, pw_env: str, schema: str):
    import psycopg2
    m = re.match(r'^([^:/]+)(?::(\d+))?/(.+)$', dsn)
    if not m:
        sys.exit('--postgis 형식: host:port/dbname')
    pw = os.environ.get(pw_env)
    if pw is None:
        sys.exit(f'비밀번호 환경변수 {pw_env} 가 없다. 사용자에게 설정을 부탁할 것(값을 대화에 붙여 넣게 하지 말 것).')
    cn = psycopg2.connect(host=m.group(1), port=int(m.group(2) or 5432), dbname=m.group(3), user=user, password=pw)
    cn.set_client_encoding('UTF8')
    cn.autocommit = True
    cur = cn.cursor()
    cur.execute("set statement_timeout = '30s'")
    cur.execute('select f_table_name, f_geometry_column, type, srid from geometry_columns where f_table_schema = %s', (schema,))
    geo = {r[0]: r for r in cur.fetchall()}
    cur.execute("select table_name, table_type from information_schema.tables where table_schema = %s "
                "and table_type in ('BASE TABLE','VIEW') order by table_name", (schema,))
    out = []
    for t, ttype in cur.fetchall():
        g = geo.get(t)
        try:
            cur.execute('select column_name, data_type from information_schema.columns '
                        'where table_schema = %s and table_name = %s order by ordinal_position', (schema, t))
            cols = cur.fetchall()
            cur.execute(f'select count(*) from "{schema}"."{t}"')
            n = cur.fetchone()[0]
            fields = []
            for name, dtype in cols:
                if g and name == g[1]:
                    continue
                q = f'"{name}" is not null and "{name}"::text <> \'\''
                cur.execute(f'select count(*) from "{schema}"."{t}" where {q}')
                filled = cur.fetchone()[0]
                cur.execute(f'select distinct "{name}"::text from "{schema}"."{t}" where {q} limit {SAMPLE_N}')
                info = {'name': name, 'type': dtype, 'filled': round(filled / n, 3) if n else 0,
                        'samples': [_short(r[0]) for r in cur.fetchall()]}
                if dtype in NUMERIC and filled:
                    cur.execute(f'select min("{name}"), max("{name}") from "{schema}"."{t}"')
                    info['range'] = [float(x) if x is not None else None for x in cur.fetchone()]
                fields.append(info)
            out.append({'source': f'postgis:{m.group(3)}', 'kind': 'postgis', 'schema': schema, 'layer': t,
                        'table_type': ttype, 'geometry': g[2] if g else None, 'geom_col': g[1] if g else None,
                        'srs': g[3] if g else None, 'count': n, 'fields': fields})
        except Exception as e:  # 권한·타임아웃 등 — 한 테이블 실패로 전체를 멈추지 않는다
            out.append({'source': f'postgis:{m.group(3)}', 'kind': 'postgis', 'layer': t, 'error': str(e)[:200]})
    cn.close()
    return out


# ---------------- QGIS 프로젝트 ----------------
def probe_qgis_project(path: Path):
    if path.suffix.lower() == '.qgz':
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith('.qgs'))
            root = ET.fromstring(z.read(name))
    else:
        root = ET.parse(path).getroot()
    groups = {}

    def walk(node, trail):
        for ch in node:
            if ch.tag == 'layer-tree-group':
                walk(ch, trail + [ch.get('name') or ''])
            elif ch.tag == 'layer-tree-layer':
                groups[ch.get('id')] = ' / '.join(t for t in trail if t)

    lt = root.find('layer-tree-group')
    if lt is not None:
        walk(lt, [])
    layers = []
    for ml in root.iter('maplayer'):
        ds = ml.findtext('datasource') or ''
        ds = re.sub(r"password='[^']*'", "password='***'", ds)
        ds = re.sub(r'password=\S+', 'password=***', ds)
        layers.append({'name': ml.findtext('layername'), 'group': groups.get(ml.findtext('id'), ''),
                       'provider': ml.findtext('provider'), 'type': ml.get('type'),
                       'geometry': ml.get('geometry'), 'datasource': ds})
    return {'project': str(path), 'layout_count': len(list(root.iter('Layout'))), 'layers': layers}


# ---------------- 요약 md ----------------
def to_md(inv):
    L = ['# 데이터 조사 결과 (00-onboard probe)', '']
    for p in inv['projects']:
        L += [f'## QGIS 프로젝트 `{Path(p["project"]).name}` — 레이어 {len(p["layers"])} · 조판 {p["layout_count"]}', '',
              '| 그룹 | 레이어 | 공급자 | 기하 |', '|---|---|---|---|']
        for x in p['layers']:
            L.append(f'| {x["group"]} | {x["name"]} | {x["provider"]} | {x["geometry"] or x["type"]} |')
        L.append('')
    L += ['## 레이어별 필드 (채움률 · 표본 · 범위)', '']
    for x in inv['layers']:
        if 'error' in x:
            L.append(f'### {x["layer"]} — 읽기 실패: {x["error"]}')
            continue
        L.append(f'### {x["layer"]}  ({x["geometry"]}, {x["count"]}건, srs {x["srs"]}) — {Path(x["source"]).name if x["kind"] == "gpkg" else x["source"]}')
        for f in x['fields']:
            rng = f' · 범위 {f["range"][0]}~{f["range"][1]}' if f.get('range') else ''
            L.append(f'- `{f["name"]}` {int(f["filled"] * 100)}% · {", ".join(f["samples"][:3])}{rng}')
        L.append('')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('paths', nargs='*', help='.gpkg/.qgz/.qgs 파일 또는 폴더')
    ap.add_argument('--postgis', help='host:port/dbname')
    ap.add_argument('--user', default='postgres')
    ap.add_argument('--password-env', default='SEWER_PG_PASSWORD')
    ap.add_argument('--schema', default='public')
    ap.add_argument('--out', default='_산출/00-onboard')
    a = ap.parse_args()

    files = []
    for p in map(Path, a.paths):
        if p.is_dir():
            files += [q for q in p.rglob('*') if q.suffix.lower() in ('.gpkg', '.qgz', '.qgs')]
        elif p.exists():
            files.append(p)
        else:
            print(f'[건너뜀] 없음: {p}')
    inv = {'layers': [], 'projects': []}
    for f in sorted(set(files)):
        try:
            if f.suffix.lower() == '.gpkg':
                inv['layers'] += probe_gpkg(f)
            else:
                inv['projects'].append(probe_qgis_project(f))
            print(f'[읽음] {f}')
        except Exception as e:
            print(f'[실패] {f}: {e}')
    if a.postgis:
        inv['layers'] += probe_postgis(a.postgis, a.user, a.password_env, a.schema)
        print(f'[읽음] PostGIS {a.postgis} ({a.schema})')

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'inventory.json').write_text(json.dumps(inv, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    (out / 'inventory.md').write_text(to_md(inv), encoding='utf-8')
    print(f'레이어 {len(inv["layers"])} · QGIS 프로젝트 {len(inv["projects"])} → {out / "inventory.md"}')


if __name__ == '__main__':
    main()
