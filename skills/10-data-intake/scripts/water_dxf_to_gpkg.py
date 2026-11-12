# -*- coding: utf-8 -*-
"""상수관망도 DXF(지자체 상수도 GIS 도면) → 상수관로 GPKG 레이어 (상수관 이격 검토용 water_pipe).

알고리즘
  1) 관로 = LWPOLYLINE·LINE. 레이어 이름 '시설코드(D관경)_사업명' 을 --layer-re 로 나눈다 (예: SAA004(D100)_○○사업)
  2) 라벨(--label-layer 의 TEXT) '연도/관종/관경/L연장/D토피' 를 --label-re 로 읽는다
  3) 라벨 → 관로: 반경 --radius(40 m) 안 후보쌍 전수, 점수 = 연장차×2 + 거리×0.5 + (관경 다르면 25),
     점수순 전역 그리디(라벨 하나 = 관로 하나)
  4) 라벨에 토피가 없으면 토피 글자 레이어(--depth-prefix·--depth-layer)의 숫자 중 관 중점 --depth-radius(15 m) 안 최근접 → 토피_GIS
  5) 상태 = 레이어 이름에 '폐관'·'삭제' 가 있으면 그것, 아니면 '사용'. 구분 = 시설코드 → --code-map
  출력 필드: dxf_레이어 구분 시설코드 상태 관종 관경 레이어관경 실연장 표기연장 설치연도 토피_라벨 토피_GIS 사업명 라벨원문 라벨거리 연장오차
  (설정 예시의 fields.water_pipe = {dia_mm: 관경, material: 관종, status: 상태, cover: 토피_GIS} 와 맞는다)

기본은 **보고만**(관로·라벨·매칭 통계). --write 일 때 GPKG 를 쓴다.
산출: --out (기본 <outputs.root>/10-intake/상수관망.gpkg), 레이어 --out-layer(기본 상수관로).
     이미 있으면 멈추고, --replace 면 <이름>_bak_YYMMDD.gpkg 로 옮긴 뒤 새로 쓴다.
쓴 뒤 sewer.config.json 에 "water_pipe": {"src": "<그 GPKG 상대경로>", "layer": "상수관로"} 를 더한다.
레이어 이름·라벨 형식은 지자체 도면마다 다르다 — 처음엔 보고만 돌려 매칭률을 보고 정규식을 맞춘다.
DXF 는 읽기만 한다.

예)
  python -X utf8 water_dxf_to_gpkg.py --dxf "상수도 관망도.dxf"
  python -X utf8 water_dxf_to_gpkg.py --dxf "상수도 관망도.dxf" --write
"""
import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

# 한 지자체 상수도 GIS 도면 기준 예시(시설코드는 상수도 지형지물 코드 체계). 인자·설정 intake.water.* 로 바꾼다.
DEF = {
    'layer_re': r'^(SA[A-Z]?\d+)\((D[\d.\-가-힣]+)\)(?:_(.+))?$',
    'label_layer': '라벨',
    'label_re': r'^(\d{4}|-+)/([^/]*)/(?:[ØøΦD]?)([\d.]+|-+)/L([\d.]+)\s*/?\s*D?([\d.]*|-+)\s*$',
    'depth_prefix': ['SA912'],
    'depth_layer': ['삭제_GIS'],
    'code_map': {'SAA004': '배수관', 'SAA005': '급수관', 'SAA020': '도수/송수관',
                 'SAA003': '관로(SAA003)', 'SAA010': '관로(SAA010)'},
    'status_words': ['폐관', '삭제'],
}
COLS = ['dxf_레이어', '구분', '시설코드', '상태', '관종', '관경', '레이어관경', '실연장',
        '표기연장', '설치연도', '토피_라벨', '토피_GIS', '사업명', '라벨원문',
        '라벨거리', '연장오차', 'geometry']


def load_cfg(path):
    try:
        return kitconfig.load(path)
    except kitconfig.ConfigError:
        if path:
            raise
        print('(sewer.config.json 없음 — 인자·기본값만 사용, 산출은 현재 폴더)', file=sys.stderr)
        return None


def num(s):
    try:
        return float(s)
    except Exception:
        return None


def convert(dxf, o):
    import ezdxf
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
    doc = ezdxf.readfile(dxf)
    msp = doc.modelspace()

    # ---------- 1. 라인 수집 ----------
    LYR_RE = re.compile(o['layer_re'])
    rows = []
    for e in msp:
        t = e.dxftype()
        if t == 'LWPOLYLINE':
            pts = [(v[0], v[1]) for v in e.get_points()]
        elif t == 'LINE':
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        else:
            continue
        pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        if len(pts) < 2:
            continue
        lyr = e.dxf.layer
        m = LYR_RE.match(lyr)
        rows.append(dict(dxf_레이어=lyr, 시설코드=m.group(1) if m else None, 레이어관경=m.group(2)[1:] if m else None,
                         사업명=m.group(3) if m else None, geometry=LineString(pts)))
    print('라인 %d개' % len(rows))

    # ---------- 2. 라벨 파싱 ----------
    LAB_RE = re.compile(o['label_re'])
    labels = []
    for e in msp:
        if e.dxftype() != 'TEXT' or e.dxf.layer != o['label_layer']:
            continue
        txt = e.dxf.text.strip()
        m = LAB_RE.match(txt)
        if not m:
            continue
        yr, mat, dia, L, dep = m.groups()
        labels.append(dict(pt=Point(e.dxf.insert.x, e.dxf.insert.y), txt=txt,
                           설치연도=(int(yr) if yr.isdigit() else None), 관종=(mat if mat and mat != '-' else None),
                           라벨관경=num(dia), 라벨연장=num(L), 토피=num(dep)))
    print('라벨 %d개 파싱' % len(labels))

    # ---------- 3. 라벨 → 라인 매칭 ----------
    geoms = [r['geometry'] for r in rows]
    tree = STRtree(geoms)
    for r in rows:
        r['_len'] = r['geometry'].length
    R = o['radius']
    pairs = []
    nocand = 0
    for li, lb in enumerate(labels):
        got = 0
        for i in tree.query(lb['pt'].buffer(R)):
            i = int(i)
            r = rows[i]
            d = geoms[i].distance(lb['pt'])
            if d > R:
                continue
            dl = abs(r['_len'] - lb['라벨연장']) if lb['라벨연장'] else 999
            dia_ok = (r['레이어관경'] and lb['라벨관경'] and r['레이어관경'].replace('.0', '') == str(int(lb['라벨관경'])))
            pairs.append((dl * 2 + d * 0.5 + (0 if dia_ok else 25), li, i, d, dl))
            got += 1
        if not got:
            nocand += 1
    pairs.sort(key=lambda x: x[0])
    assigned, used_lab = {}, set()
    for score, li, i, d, dl in pairs:
        if li in used_lab or i in assigned:
            continue
        used_lab.add(li)
        assigned[i] = (score, labels[li], d, dl)
    print('라벨 매칭: 배정 %d / 반경내 후보없음 %d / 경합탈락 %d'
          % (len(assigned), nocand, len(labels) - nocand - len(assigned)))
    dls = [v[3] for v in assigned.values()]
    print('연장 오차: 0.1m이내 %d / 1m이내 %d / 그외 %d'
          % (sum(1 for x in dls if x <= 0.1), sum(1 for x in dls if 0.1 < x <= 1.0), sum(1 for x in dls if x > 1.0)))

    # ---------- 4. 토피 글자 최근접 ----------
    depth_pts, depth_val = [], []
    for e in msp:
        if e.dxftype() != 'TEXT':
            continue
        lay = e.dxf.layer
        if not (any(lay.startswith(p) for p in o['depth_prefix']) or lay in o['depth_layer']):
            continue
        try:
            v = float(e.dxf.text.strip())
        except Exception:
            continue
        depth_pts.append(Point(e.dxf.insert.x, e.dxf.insert.y))
        depth_val.append(v)
    print('토피 텍스트 %d개' % len(depth_pts))
    dtree = STRtree(depth_pts) if depth_pts else None

    # ---------- 5. 속성 조립 ----------
    DR = o['depth_radius']
    for i, r in enumerate(rows):
        a = assigned.get(i)
        lb = a[1] if a else None
        r['라벨원문'] = lb['txt'] if lb else None
        r['설치연도'] = lb['설치연도'] if lb else None
        r['관종'] = lb['관종'] if lb else None
        r['관경'] = (lb['라벨관경'] if lb and lb['라벨관경'] else
                   (float(r['레이어관경']) if r['레이어관경'] and re.match(r'^[\d.]+$', r['레이어관경']) else None))
        r['표기연장'] = lb['라벨연장'] if lb else None
        r['토피_라벨'] = lb['토피'] if lb else None
        r['라벨거리'] = round(a[2], 2) if a else None
        r['연장오차'] = round(a[3], 2) if a else None
        r['실연장'] = round(r['_len'], 2)
        lyr = r['dxf_레이어']
        r['상태'] = next((w for w in o['status_words'] if w in lyr), '사용')
        r['구분'] = o['code_map'].get(r['시설코드'], '기타')
        if dtree is not None and r['토피_라벨'] is None:
            c = r['geometry'].interpolate(0.5, normalized=True)
            cand = dtree.query(c.buffer(DR))
            if len(cand):
                j = min((int(k) for k in cand), key=lambda k: depth_pts[k].distance(c))
                if depth_pts[j].distance(c) <= DR:
                    r['토피_GIS'] = depth_val[j]
        r.pop('_len')
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dxf', required=True, help='상수관망도 DXF')
    ap.add_argument('--layer-re', help='관로 레이어 이름 정규식(1 시설코드, 2 D관경, 3 사업명) (기본 intake.water.layer_re)')
    ap.add_argument('--label-layer', help='라벨 TEXT 레이어 (기본 intake.water.label_layer 또는 "라벨")')
    ap.add_argument('--label-re', help='라벨 정규식(1 연도, 2 관종, 3 관경, 4 연장, 5 토피) (기본 intake.water.label_re)')
    ap.add_argument('--depth-prefix', nargs='+', help='토피 글자 레이어 접두 (기본 intake.water.depth_prefix 또는 SA912)')
    ap.add_argument('--depth-layer', nargs='+', help='토피 글자 레이어 이름 (기본 intake.water.depth_layer)')
    ap.add_argument('--code-map', help='시설코드→구분 JSON (기본 intake.water.code_map)')
    ap.add_argument('--radius', type=float, default=40.0, help='라벨 ↔ 관로 탐색 반경 m (기본 40)')
    ap.add_argument('--depth-radius', type=float, default=15.0, help='관 중점 ↔ 토피 글자 반경 m (기본 15)')
    ap.add_argument('--crs', help='좌표계 (기본 project.crs 또는 EPSG:5186) — 도면 좌표계와 같아야 한다')
    ap.add_argument('--out', help='산출 GPKG (기본 <outputs.root>/10-intake/상수관망.gpkg)')
    ap.add_argument('--out-layer', default='상수관로', help='산출 레이어 이름 (기본 상수관로)')
    ap.add_argument('--write', action='store_true', help='GPKG 를 쓴다 (없으면 보고만)')
    ap.add_argument('--replace', action='store_true', help='--out 이 있으면 _bak_ 로 옮기고 새로 쓴다')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    get = (lambda k, d: cfg.get(k, d)) if cfg else (lambda k, d: d)
    o = {k: get('intake.water.' + k, v) for k, v in DEF.items()}
    for k in ('layer_re', 'label_layer', 'label_re', 'depth_prefix', 'depth_layer'):
        v = getattr(a, k)
        if v:
            o[k] = v
    if a.code_map:
        o['code_map'] = json.loads(a.code_map)
    o['radius'], o['depth_radius'] = a.radius, a.depth_radius
    crs = a.crs or get('project.crs', 'EPSG:5186')
    out = Path(a.out) if a.out else ((cfg.out_dir('10-intake') if cfg else Path.cwd()) / '상수관망.gpkg')

    rows = convert(a.dxf, o)
    import geopandas as gpd
    gdf = gpd.GeoDataFrame(rows, crs=crs)
    for c in COLS:
        if c not in gdf.columns:
            gdf[c] = None
    gdf = gdf[COLS]
    print(gdf['구분'].value_counts().to_dict())
    print('상태', gdf['상태'].value_counts().to_dict())
    print('라벨보유 %d / 전체 %d · 관경 있음 %d · 토피(라벨 또는 GIS) %d'
          % (gdf['라벨원문'].notna().sum(), len(gdf), gdf['관경'].notna().sum(),
             (gdf['토피_라벨'].notna() | gdf['토피_GIS'].notna()).sum()))
    if not a.write:
        print('\n(보고만 — 파일을 쓰지 않았다. --write 로 저장: %s 레이어 %s)' % (out, a.out_layer))
        return
    if out.exists():
        if not a.replace:
            raise SystemExit('이미 있다: %s — 다시 쓰려면 --replace (기존 파일은 _bak_ 로 옮김)' % out)
        bak = out.with_name('%s_bak_%s%s' % (out.stem, datetime.datetime.now().strftime('%y%m%d_%H%M'), out.suffix))
        shutil.move(str(out), bak)
        print('기존 파일 → %s' % bak)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver='GPKG', layer=a.out_layer, engine='pyogrio')
    print('저장: %s  레이어 %s  %d개' % (out, a.out_layer, len(gdf)))
    print('설정: layers.water_pipe = {"src": "<위 GPKG, 프로젝트 루트 기준 상대경로>", "layer": "%s"}' % a.out_layer)


if __name__ == '__main__':
    main()
