# -*- coding: utf-8 -*-
"""측량현황도 → QGIS 지장물 변환 도구 공통 모듈.

이 파일을 먼저 import 해야 한다(fiona 보다 먼저 PROJ 경로를 덮어써야 하므로).
    from _common import *
경로·레이어·필드는 _kit(설정 배관)에서 온다 — 여기에 프로젝트 경로를 적지 않는다.
"""
import os
import sys
import re
import math
import json
import shutil
import sqlite3
import datetime
import importlib.util
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kit import *                                         # noqa: E402,F403

# ── PROJ 충돌 회피: QGIS·PostGIS 를 깐 PC 는 PROJ_DATA 가 그쪽으로 잡혀 있어
#    pip 로 깐 fiona 가 EPSG 조회에 실패한다. fiona import 전에 번들 proj_data 강제.
#    (kitconfig.fix_proj 는 rasterio·pyproj 만 본다 — fiona 번들은 여기서 처리)
_spec = importlib.util.find_spec('fiona')
if _spec is not None:
    _pd = os.path.join(os.path.dirname(_spec.origin), 'proj_data')
    if os.path.isdir(_pd):
        os.environ['PROJ_DATA'] = os.environ['PROJ_LIB'] = _pd
        os.environ.pop('GDAL_DATA', None)

import fiona                                     # noqa: E402
from fiona.crs import CRS                        # noqa: E402
import ezdxf                                     # noqa: E402
from shapely.geometry import (shape, mapping, Point, LineString,  # noqa: E402
                              Polygon, MultiLineString)
from shapely.ops import linemerge, unary_union   # noqa: E402
from shapely.strtree import STRtree              # noqa: E402
from shapely import minimum_rotated_rectangle    # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 측량현황도 DXF 레이어 규약 (측량사 작도 규약 — 다르면 여기와 01·02 의 목록을 고친다)
PIPE_LAYERS = ('배수시설물', '배수시설물-매립', '배수시설물-추진')
TEXT_LAYERS = ('TEXT', '상세', '배수시설물', '배수시설물-매립')
STD = [100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800,
       900, 1000, 1100, 1200, 1350, 1500, 1650, 1800]

# 프로젝트 경로·레이어는 _kit 의 obstacle_src() / route_spec() / out_path() 로 얻는다.


# ────────────────────────────────────────────────────────── DXF
def load_dxf(path):
    """AC1032 DXF 는 $DWGCODEPAGE 가 ANSI_949 여도 실제 UTF-8 이다."""
    return ezdxf.read(open(path, encoding='utf-8', errors='replace'))


def dxf_lines(doc, layers=PIPE_LAYERS, min_len=0.02):
    """지정 레이어의 LINE/LWPOLYLINE 을 LineString 목록으로."""
    out = []
    for e in doc.modelspace():
        lay = e.dxf.layer
        if not any(k in lay for k in layers):
            continue
        t = e.dxftype()
        if t == 'LINE':
            g = LineString([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)])
        elif t == 'LWPOLYLINE':
            p = [(a[0], a[1]) for a in e.get_points()]
            if getattr(e, 'closed', False) and len(p) > 2:
                p = p + [p[0]]
            if len(p) < 2:
                continue
            g = LineString(p)
        else:
            continue
        if g.length >= min_len:
            out.append((lay, g))
    return out


def dxf_texts(doc, layers=TEXT_LAYERS):
    out = []
    for e in doc.modelspace():
        if e.dxftype() not in ('TEXT', 'MTEXT'):
            continue
        lay = e.dxf.layer
        if layers and not any(k in lay for k in layers):
            continue
        try:
            t = e.plain_text() if e.dxftype() == 'MTEXT' else e.dxf.text
        except Exception:
            t = ''
        t = (t or '').strip()
        if t:
            out.append((lay, t, float(e.dxf.insert[0]), float(e.dxf.insert[1])))
    return out


def chains(lines, min_len=0.3):
    """선분을 linemerge 로 체인화."""
    m = linemerge(unary_union([g for _, g in lines] if lines and
                              isinstance(lines[0], tuple) else lines))
    gs = list(m.geoms) if hasattr(m, 'geoms') else [m]
    return [g for g in gs if g.length >= min_len]


# ────────────────────────────────────────────────────────── 규격 텍스트
_NUM = r'\d+(?:\.\d+)?'
RE_D = re.compile(r'^(?:%%C|D)(\d{2,4})(?:[Hh]=?(%s))?$' % _NUM, re.I)
RE_WH = re.compile(r'^(U형측구|U형수로|U형|J형측구|J형|V형측구|V형|BOX|박스|암거|라멘교|교량|U|J|V)?'
                   r'(?:(\d)@)?(%s)[xX*×](%s)(?:[xX*×](%s))?$' % (_NUM, _NUM, _NUM), re.I)
_KIND = [('U형수로', 'U형수로'), ('U형측구', 'U형측구'), ('J형측구', 'J형측구'),
         ('V형측구', 'V형측구'), ('U형', 'U형측구'), ('J형', 'J형측구'), ('V형', 'V형측구'),
         ('라멘교', '암거/교량'), ('교량', '암거/교량'), ('암거', '암거/교량'),
         ('박스', '박스'), ('BOX', '박스'), ('U', 'U형측구'), ('J', 'J형측구'), ('V', 'V형측구')]


def _n(s):
    """'0.6'→0.6, '06'→0.6 (소수점 없는 2자리 0N 은 0.N m 표기)"""
    if s is None:
        return None
    s = str(s).strip()
    if '.' in s:
        return float(s)
    if len(s) == 2 and s[0] == '0':
        return float(s) / 10.0
    return float(s)


def parse_spec(t):
    """규격 텍스트 → ('C', mm) | ('R', 폭m, 깊이m, 연수) | None"""
    if not t:
        return None
    s = str(t).strip().replace(' ', '').replace('..', '.')
    m = RE_D.match(s)
    if m:
        return ('C', float(m.group(1)))
    m = RE_WH.match(s)
    if m:
        head, n, a, b = m.group(1), m.group(2), _n(m.group(3)), _n(m.group(4))
        cnt = int(n) if n else 1
        return ('R', a * cnt, b, cnt)
    return None


def kind_of(head):
    if not head:
        return '미상'
    h = str(head).upper()
    for k, v in _KIND:
        if h.startswith(k.upper()):
            return v
    return '미상'


def snap_std(w_m, tol_ratio=0.12, tol_abs=40):
    """실측폭(m) → 표준관경(mm). 허용오차 밖이면 None."""
    if not w_m:
        return None
    mm = w_m * 1000.0
    b = min(STD, key=lambda s: abs(s - mm))
    return b if abs(b - mm) <= max(tol_abs, tol_ratio * b) else None


def spec_eq(a, b, tol_mm=1.0, tol_m=0.06):
    """두 규격이 같은가. ('C',mm) / ('R',w,h,n)"""
    if not a or not b:
        return False
    if a[0] == 'C' and b[0] == 'C':
        return abs(a[1] - b[1]) <= tol_mm
    if a[0] == 'R' and b[0] == 'R':
        return ((abs(a[1] - b[1]) <= tol_m and abs(a[2] - b[2]) <= tol_m) or
                (abs(a[1] - b[2]) <= tol_m and abs(a[2] - b[1]) <= tol_m))
    return False


def specs_of_row(pr):
    """폴리곤 속성에서 가능한 규격 표현들(표기·관경mm·폭/깊이)."""
    out = []
    표기 = str(pr.get('표기') or '')
    s = parse_spec(표기)
    if s:
        out.append(s)
    dmm = pr.get('관경mm')
    if dmm:
        out.append(('C', float(dmm)))
    w, h = pr.get('규격_폭m'), pr.get('규격_깊이m')
    if w and h:
        out.append(('R', float(w), float(h), 1))
    if w and not h and '원형' in str(pr.get('종류') or ''):
        out.append(('C', float(w) * 1000))
    return out


# ────────────────────────────────────────────────────────── 형상 판정
def _ang(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def digeut(coords, w_min=0.1, w_max=6.5, dev_min=160.0, arm_min=1.0):
    """ㄷ자(두 변 + 막힌 끝 한 변) 체인인가.
       w_max 는 02 체인쌍 폭 상한(6.5 m)과 맞춘다 — 2.0 이던 때 BOX 2.5X2.0(사례,
       연결변 2.52 m)이 빠져 U자 폴리곤으로 남았다.
       → dict(i=연결변 시작index, w=연결변 길이, L1/L2=양변 길이, dev=역평행도) | None"""
    cs = [tuple(c[:2]) for c in coords]
    if len(cs) < 4:
        return None
    for i in range(1, len(cs) - 2):
        w = math.dist(cs[i], cs[i + 1])
        if not (w_min <= w <= w_max):
            continue
        dev = abs((_ang(cs[i - 1], cs[i]) - _ang(cs[i + 1], cs[i + 2]) + 180) % 360 - 180)
        L1, L2 = math.dist(cs[i - 1], cs[i]), math.dist(cs[i + 1], cs[i + 2])
        if dev >= dev_min and min(L1, L2) >= arm_min:
            return dict(i=i, w=w, L1=L1, L2=L2, dev=dev)
    return None


def band_dims(poly):
    """띠 모양 폴리곤의 (길이 L, 폭 w). P = 2L + 2w, A = L*w 역산.
       L/w 가 3 미만이면 '띠'가 아니다(뭉툭한 면·맨홀·교량면) → 폭 판정에 쓰지 말 것."""
    if poly is None or poly.is_empty or poly.area <= 0:
        return (None, None)
    P, A = poly.exterior.length, poly.area
    disc = P * P - 16 * A
    if disc < 0:
        return (None, None)
    r = math.sqrt(disc)
    w, L = (P - r) / 4.0, (P + r) / 4.0
    return (L, w) if w > 0 else (None, None)


def band_width(poly):
    """띠 모양 폴리곤의 폭(m). 굽은 띠는 최소회전사각형 단변이 틀리므로
       둘레·면적에서 역산한다: P = 2L + 2w, A = L*w  ->  w = (P - sqrt(P^2 - 16A)) / 4.
       (굽은 L자 U1.0 측구가 MRR 로는 17 m 로 나오던 문제)"""
    if poly is None or poly.is_empty or poly.area <= 0:
        return None
    P, A = poly.exterior.length, poly.area
    disc = P * P - 16 * A
    if disc < 0:
        return None
    w = (P - math.sqrt(disc)) / 4.0
    return w if w > 0 else None


def band_axis(poly):
    """띠 모양 폴리곤의 중심선(장변 두 개의 중점 연결)."""
    cs = list(poly.exterior.coords)[:-1]
    if len(cs) < 4:
        mrr = minimum_rotated_rectangle(poly)
        cs = list(mrr.exterior.coords)[:-1]
    e = sorted(((math.dist(cs[i], cs[(i + 1) % len(cs)]), cs[i], cs[(i + 1) % len(cs)])
                for i in range(len(cs))), reverse=True)
    (L1, A1, B1), (L2, A2, B2) = e[0], e[1]
    return LineString([((A1[0] + B2[0]) / 2, (A1[1] + B2[1]) / 2),
                       ((B1[0] + A2[0]) / 2, (B1[1] + A2[1]) / 2)])


# ────────────────────────────────────────────────────────── GPKG
def read_layer(gpkg, layer):
    """[(props, geom)] 목록으로 읽기."""
    out = []
    with fiona.open(gpkg, layer=layer) as src:
        for ft in src:
            g = shape(ft['geometry']) if ft['geometry'] else None
            if g is not None and not g.is_empty:
                out.append((dict(ft['properties']), g))
    return out


def read_spec(L, bbox=None, encoding=None):
    """kitconfig 레이어 spec({'kind':'gpkg'|'postgis', ...}) → [(props, geom)].
       gpkg 는 .shp 도 받는다(path 가 .shp 면 레이어명 무시). bbox=(x0,y0,x1,y1) 이면 그 안만."""
    if L['kind'] == 'postgis':
        return _read_postgis(L, bbox)
    path = str(L['path'])
    kw = {}
    if not path.lower().endswith('.shp'):
        kw['layer'] = L['layer']
    elif encoding:
        kw['encoding'] = encoding
    out = []
    with fiona.open(path, **kw) as src:
        it = src.filter(bbox=bbox) if bbox else src
        for ft in it:
            g = shape(ft['geometry']) if ft['geometry'] else None
            if g is not None and not g.is_empty:
                out.append((dict(ft['properties']), g))
    return out


def _read_postgis(L, bbox=None):
    from shapely import wkb
    s, t, gc = L['schema'], L['layer'], L['geom']
    sql = 'select *, ST_AsBinary("%s") as "__wkb" from "%s"."%s"' % (gc, s, t)
    args = None
    if bbox:
        sql += (' where "%s" && ST_MakeEnvelope(%%s,%%s,%%s,%%s, Find_SRID(%%s,%%s,%%s))' % gc)
        args = tuple(bbox) + (s, t, gc)
    out = []
    with cfg().pg_connect() as cn:
        cur = cn.cursor()
        cur.execute(sql, args)
        names = [d[0] for d in cur.description]
        for row in cur.fetchall():
            pr = dict(zip(names, row))
            b = pr.pop('__wkb', None)
            pr.pop(gc, None)
            g = wkb.loads(bytes(b)) if b is not None else None
            if g is not None and not g.is_empty:
                out.append((pr, g))
    return out


def route_lines(current=False):
    """설계 노선 → [(관번호, LineString)]. current=True 면 '금회' 지구만
       (--district / survey.current_districts — 비어 있으면 전체 노선을 금회로 본다)."""
    kid = fld('plan_pipe', 'pipe_id', '관번호')
    kd = fld('plan_pipe', 'district', '지구')
    ds = districts() if current else ()
    return [(pr.get(kid), g) for pr, g in read_spec(route_spec())
            if g.geom_type == 'LineString' and (not ds or pr.get(kd) in ds)]


def method_map(src=None):
    """생성방식(관경산출) 참조표. 통합 '정본' 레이어에는 관경산출 필드가 없다 →
       02 빌드 GPKG 의 지장물_폴리곤_<접미> 레이어에서 끌어온다.
       정본 번호는 '<접미>-J0001' 처럼 접두가 붙는다(빌드마다 J 번호가 겹치므로 접두로 구분)."""
    src = src or build_gpkg()
    m = {}
    if not src or not os.path.exists(src):
        return m, src
    for lyr in fiona.listlayers(src):
        mm = re.match(r'^지장물_폴리곤_(.+)$', lyr)
        if not mm:
            continue
        pre = mm.group(1) + '-'
        try:
            with fiona.open(src, layer=lyr) as sc:
                for ft in sc:
                    m[pre + str(ft['properties'].get('지장물번호'))] = \
                        ft['properties'].get('관경산출')
        except Exception:
            pass
    return m, src


def columns(gpkg, table):
    con = sqlite3.connect(gpkg)
    try:
        return [r[1] for r in con.execute('PRAGMA table_info("%s")' % table)]
    finally:
        con.close()


def add_column(gpkg, table, col, typ='TEXT'):
    if col in columns(gpkg, table):
        return False
    con = sqlite3.connect(gpkg)
    try:
        con.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (table, col, typ))
        con.commit()
        return True
    finally:
        con.close()


def _update_triggers(cur, table):
    """fid 가 안 바뀌는 UPDATE 에도 WHEN 절이 평가되는 'AFTER UPDATE ON' 트리거.
       이 트리거들이 ST_IsEmpty 를 호출해서 순수 sqlite3 UPDATE 가 막힌다.
       번호(update3/4 또는 4/5)는 GPKG 작성기마다 다르므로 SQL 패턴으로 찾는다."""
    rows = list(cur.execute(
        "select name,sql from sqlite_master where type='trigger' and tbl_name=?", (table,)))
    return [(n, s) for n, s in rows
            if s and 'AFTER UPDATE ON' in s.upper().replace('"', '').replace("'", '')]


def update_attrs(gpkg, table, updates, key='지장물번호'):
    """updates = [(키값, {필드: 값})]  — 속성만 수정(도형 불변).
       ※ 같은 커서로 select 를 돌면서 update 하면 첫 행만 반영된다. 호출자는 list 로 넘길 것."""
    con = sqlite3.connect(gpkg)
    cur = con.cursor()
    trg = _update_triggers(cur, table)
    for n, _ in trg:
        cur.execute('DROP TRIGGER "%s"' % n)
    n_done = 0
    try:
        for k, vals in updates:
            cols = ','.join('"%s"=?' % c for c in vals)
            cur.execute('update "%s" set %s where "%s"=?' % (table, cols, key),
                        tuple(vals.values()) + (k,))
            n_done += cur.rowcount
        con.commit()
    finally:
        for n, s in trg:
            cur.execute(s)
        con.commit()
        con.close()
    return n_done


def replace_features(gpkg, table, items, key='지장물번호'):
    """items = [(키값, geom, props)] — 도형 교체(삭제 후 재삽입).
       ※ 도형 UPDATE 는 rtree 트리거(ST_MinX)가 막으므로 DELETE + fiona append 로 한다."""
    con = sqlite3.connect(gpkg)
    cur = con.cursor()
    for k, _, _ in items:
        cur.execute('delete from "%s" where "%s"=?' % (table, key), (k,))
    con.commit()
    con.close()
    with fiona.open(gpkg, 'a', layer=table) as dst:
        keys = set(dst.schema['properties'])
        for k, g, pr in items:
            dst.write({'geometry': mapping(g),
                       'properties': {c: pr.get(c) for c in keys}})
    return len(items)


def append_features(gpkg, table, items):
    """items = [(geom, props)] — 신규 추가."""
    with fiona.open(gpkg, 'a', layer=table) as dst:
        keys = set(dst.schema['properties'])
        for g, pr in items:
            dst.write({'geometry': mapping(g), 'properties': {c: pr.get(c) for c in keys}})
    return len(items)


def write_points(gpkg_out, layer, rows, schema_props):
    """검토 결과 점 레이어 새로 쓰기. rows = [(geom, props)]"""
    schema = {'geometry': 'Point', 'properties': schema_props}
    with fiona.open(gpkg_out, 'w', driver='GPKG', layer=layer,
                    schema=schema, crs=CRS.from_epsg(epsg())) as dst:
        for g, pr in rows:
            dst.write({'geometry': mapping(g),
                       'properties': {k: pr.get(k) for k in schema_props}})
    return len(rows)


def backup(path, tag=None):
    tag = tag or datetime.datetime.now().strftime('%y%m%d_%H%M')
    b = '%s_bak_%s%s' % (os.path.splitext(path)[0], tag, os.path.splitext(path)[1])
    if not os.path.exists(b):
        shutil.copy2(path, b)
        print('백업 %s' % os.path.basename(b))
    return b


def feature_counts(gpkg, tables):
    con = sqlite3.connect(gpkg)
    try:
        for t in tables:
            n = con.execute('select count(*) from "%s"' % t).fetchone()[0]
            try:
                fc = con.execute('select feature_count from gpkg_ogr_contents where table_name=?',
                                 (t,)).fetchone()
                fc = fc[0] if fc else '-'
            except Exception:
                fc = '-'
            try:
                rt = con.execute('select count(*) from "rtree_%s_geom"' % t).fetchone()[0]
            except Exception:
                rt = '-'
            print('  %-24s %6d (ogr %s / rtree %s)' % (t, n, fc, rt))
    finally:
        con.close()


# ────────────────────────────────────────────────────────── 설계사 .flo 지장물 토큰
def flo_tokens(path, encoding='cp949'):
    """makesw .flo 의 지장물 토큰.
       x= 원형관 / X= 사각(측구·BOX) / G= 지반 보조점(지장물 아님 → 제외)
       반환: {상류관번호: [dict(pre, 측점, 저면EL, 크기)]}
       ※ 저면EL + 0.30 ≈ 도면 관저고(원형), 사각은 0.45~0.70 로 깊이마다 다르다."""
    out = collections.defaultdict(list)
    txt = open(path, encoding=encoding, errors='replace').read().splitlines()
    for ln in txt[2:]:
        if not ln.strip():
            continue
        up = ln.split()[0]
        for m in re.finditer(r'([xXG])=([\d.]+),([\d.-]+),([\d.]+)', ln):
            pre, sta, el, sz = m.groups()
            if pre == 'G':
                continue
            out[up].append(dict(pre=pre, 측점=float(sta), 저면EL=float(el), 크기=float(sz)))
    return dict(out)


# argval / has / positionals 는 _kit 에 있다.
