# -*- coding: utf-8 -*-
"""측량 표고점 → 1 m TIN DEM (+ 거친 DEM 에 실측을 덮어쓴 새 파일).

알고리즘
  1) 같은 좌표(0.01 m 반올림) 중복 제거 → Delaunay TIN 선형보간
  2) 점에서 --maxd(30 m) 넘게 떨어진 셀은 nodata. 1024×1024 블록으로 나눠 쓰고, 블록 전체가 점에서 멀면 건너뜀
  3) --merge-into 를 주면 거친 DEM(5 m 등) 셀 중심 기준 1 m DEM 5×5 창 평균(유효 13셀 이상)으로 덮어쓴 **새 파일**을 만든다
     (원본 거친 DEM 은 고치지 않는다)

점 입력 (셋 중 하나)
  --layer-key 키        설정 layers.<키> (PostGIS·GPKG). 표고 필드 = --z-field 또는 fields.<키>.elev
  --points 파일         GPKG·SHP (점 도형) 또는 CSV (--xyz 열 이름 3개)
  --dxf 측량도.dxf       측량현황도의 표고점 POINT(--point-layer) + 옆 표고 글자(--height-layer, --text-dist 안 최근접).
                        POINT 에 z 가 있으면 그것을 쓴다. ※ 이상치 걸러내기는 하지 않는다 — 결과를 보고 --filter 로 뺀다
  --filter "outlier == 0"   점 속성으로 거르기 (pandas query 문법, 한글 열은 `열` 처럼 백틱)

기본은 **보고만**(점 수·범위·격자 크기). --write 일 때 래스터를 만든다.
산출: --out (기본 <outputs.root>/10-intake/DEM_실측_1m.tif). 이미 있으면 멈추고, --replace 면 <이름>_bak_YYMMDD.tif 로 옮긴 뒤 새로 만든다.
만든 뒤 sewer.config.json 의 "dem" 목록 맨 앞에 1 m, 그다음에 거친 DEM(또는 실측 반영본)을 적는다.

예)
  python -X utf8 build_dem.py --layer-key survey_point --filter "outlier == 0"
  python -X utf8 build_dem.py --points 표고점.gpkg --z-field elev --write
  python -X utf8 build_dem.py --dxf 측량현황도.dxf --point-layer point --height-layer height --write ^
         --merge-into data/DEM_5m.tif
"""
import argparse
import datetime
import math
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')


def load_cfg(path):
    try:
        return kitconfig.load(path)
    except kitconfig.ConfigError:
        if path:
            raise
        print('(sewer.config.json 없음 — 인자만 사용, 산출은 현재 폴더)', file=sys.stderr)
        return None


def read_points(a, cfg):
    """→ numpy (N, 3) x, y, z"""
    import numpy as np
    if a.dxf:
        import ezdxf
        from scipy.spatial import cKDTree
        doc = ezdxf.readfile(a.dxf)
        msp = doc.modelspace()
        pts = [e.dxf.location for e in msp if e.dxftype() == 'POINT' and e.dxf.layer in a.point_layer]
        txt = []
        for e in msp:
            if e.dxftype() == 'TEXT' and e.dxf.layer in (a.height_layer or []):
                try:
                    txt.append((e.dxf.insert[0], e.dxf.insert[1], float(e.dxf.text.strip())))
                except ValueError:
                    pass
        out, miss = [], 0
        tree = cKDTree([(t[0], t[1]) for t in txt]) if txt else None
        for p in pts:
            if p[2]:
                out.append((p[0], p[1], p[2]))
                continue
            if tree is None:
                miss += 1
                continue
            d, i = tree.query((p[0], p[1]))
            if d <= a.text_dist:
                out.append((p[0], p[1], txt[i][2]))
            else:
                miss += 1
        print('DXF 표고점 %d (z 있음·글자 짝 %d, 표고 못 찾음 %d)' % (len(pts), len(out), miss))
        import pandas as pd
        df = pd.DataFrame(out, columns=['x', 'y', 'z'])
    else:
        import geopandas as gpd
        import pandas as pd
        if a.layer_key:
            if not cfg:
                raise SystemExit('--layer-key 는 sewer.config.json 이 있어야 한다')
            gdf = cfg.read_layer(a.layer_key)
            zf = a.z_field or cfg.field(a.layer_key, 'elev', default='elev')
        elif a.points and a.points.lower().endswith('.csv'):
            gdf = pd.read_csv(a.points)
            zf = a.xyz[2]
        elif a.points:
            gdf = gpd.read_file(a.points, layer=a.layer) if a.layer else gpd.read_file(a.points)
            zf = a.z_field or 'elev'
        else:
            raise SystemExit('점 입력이 없다 — --layer-key / --points / --dxf 중 하나')
        n0 = len(gdf)
        if a.filter:
            gdf = gdf.query(a.filter)
        if a.points and a.points.lower().endswith('.csv'):
            df = pd.DataFrame({'x': gdf[a.xyz[0]], 'y': gdf[a.xyz[1]], 'z': gdf[zf]})
        else:
            if zf not in gdf.columns:
                raise SystemExit('표고 필드 %s 가 없다 — --z-field 로 줄 것 (있는 열: %s)' % (zf, list(gdf.columns)))
            df = pd.DataFrame({'x': gdf.geometry.x, 'y': gdf.geometry.y, 'z': gdf[zf]})
        print('점 %d → 거르기 후 %d' % (n0, len(df)))
    df = df[df['z'].notna()]
    return df[['x', 'y', 'z']].to_numpy(dtype=float)


def build_1m(P, out, crs, RES, MAXD, NOD, BS, bbox):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.windows import Window
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import Delaunay, cKDTree
    xmin, ymin, xmax, ymax = bbox
    W, H = int((xmax - xmin) / RES), int((ymax - ymin) / RES)
    t0 = time.time()
    tri = Delaunay(P[:, :2])
    interp = LinearNDInterpolator(tri, P[:, 2], fill_value=NOD)
    tree = cKDTree(P[:, :2])
    print('TIN', round(time.time() - t0, 1), 's')
    prof = dict(driver='GTiff', width=W, height=H, count=1, dtype='float32', crs=crs,
                transform=from_origin(xmin, ymax, RES, RES), nodata=NOD, tiled=True, blockxsize=512, blockysize=512,
                compress='LZW', predictor=2, BIGTIFF='IF_SAFER')
    nvalid = 0
    with rasterio.open(out, 'w', **prof) as dst:
        for r0 in range(0, H, BS):
            for c0 in range(0, W, BS):
                h = min(BS, H - r0)
                w = min(BS, W - c0)
                xs = xmin + (c0 + np.arange(w) + 0.5) * RES
                ys = ymax - (r0 + np.arange(h) + 0.5) * RES
                gx, gy = np.meshgrid(xs, ys)
                q = np.column_stack([gx.ravel(), gy.ravel()])
                d, _ = tree.query(q[::37], k=1)          # 블록 전체가 점에서 멀면 건너뜀
                if d.min() > MAXD + BS * RES * 1.5:
                    dst.write(np.full((h, w), NOD, dtype='float32'), 1, window=Window(c0, r0, w, h))
                    continue
                d, _ = tree.query(q, k=1)
                z = interp(q)
                z[d > MAXD] = NOD
                z = np.where(np.isnan(z), NOD, z).astype('float32').reshape(h, w)
                nvalid += int((z != NOD).sum())
                dst.write(z, 1, window=Window(c0, r0, w, h))
            print('  행 %d/%d 완료 %ds' % (r0, H, round(time.time() - t0)), flush=True)
    print('1 m 완료, 유효 셀 %d = %.1f km²' % (nvalid, nvalid * RES * RES / 1e6))
    return W, H


def merge_into(coarse, out1, merged, bbox, RES, W, H, NOD):
    """거친 DEM 사본에 1 m 유효 셀 5×5 평균을 덮어쓴다(유효 13셀 이상)."""
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    xmin, ymin, xmax, ymax = bbox
    shutil.copy(coarse, merged)
    with rasterio.open(merged, 'r+') as d5, rasterio.open(out1) as d1:
        a5 = d5.read(1)
        t5 = d5.transform
        n5 = d5.nodata
        changed = 0
        for r in range(d5.height):
            ys = t5.f + (r + 0.5) * t5.e
            if ys < ymin or ys > ymax:
                continue
            xs = t5.c + (np.arange(d5.width) + 0.5) * t5.a
            m = (xs >= xmin) & (xs <= xmax)
            if not m.any():
                continue
            rows1 = int((ymax - ys) / RES)
            cols1 = ((xs[m] - xmin) / RES).astype(int)
            r1a, r1b = max(0, rows1 - 2), min(H, rows1 + 3)
            blk = d1.read(1, window=Window(0, r1a, W, r1b - r1a))
            vals = []
            for c in cols1:
                sub = blk[:, max(0, c - 2):min(W, c + 3)]
                v = sub[sub != NOD]
                vals.append(v.mean() if v.size >= 13 else n5)
            vals = np.array(vals, dtype='float32')
            sel = vals != n5
            if sel.any():
                idxs = np.where(m)[0][sel]
                a5[r, idxs] = vals[sel]
                changed += int(sel.sum())
        d5.write(a5, 1)
    print('실측 반영 셀 %d → %s' % (changed, merged))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group('점 입력 (하나)')
    g.add_argument('--layer-key', help='설정 layers.<키> 의 점 레이어 (예: survey_point)')
    g.add_argument('--points', help='점 파일 (GPKG·SHP·CSV)')
    g.add_argument('--layer', help='--points GPKG 안 레이어 이름')
    g.add_argument('--xyz', nargs=3, default=['x', 'y', 'z'], metavar=('X', 'Y', 'Z'), help='CSV 열 이름 (기본 x y z)')
    g.add_argument('--z-field', help='표고 필드 (기본 fields.<키>.elev 또는 elev)')
    g.add_argument('--dxf', help='측량현황도 DXF')
    g.add_argument('--point-layer', nargs='+', default=['point'], help='DXF 표고점 POINT 레이어 (기본 point)')
    g.add_argument('--height-layer', nargs='+', default=['height'], help='DXF 표고 글자 레이어 (기본 height)')
    g.add_argument('--text-dist', type=float, default=2.0, help='표고점 ↔ 표고 글자 허용 거리 m (기본 2)')
    g.add_argument('--filter', help='점 거르기 (pandas query, 예: "outlier == 0")')
    g.add_argument('--offset', nargs=2, type=float, metavar=('DX', 'DY'), help='점 좌표에 더할 값 (도면 좌표가 프로젝트 좌표와 다를 때)')
    g = ap.add_argument_group('격자')
    g.add_argument('--res', type=float, default=1.0, help='해상도 m (기본 1)')
    g.add_argument('--maxd', type=float, default=30.0, help='점에서 이보다 먼 셀은 비움 m (기본 30)')
    g.add_argument('--pad', type=float, default=20.0, help='점 범위 바깥 여유 m (기본 20)')
    g.add_argument('--bbox', nargs=4, type=float, metavar=('XMIN', 'YMIN', 'XMAX', 'YMAX'), help='이 범위만 (점도 이 범위로 자름)')
    g.add_argument('--crs', help='좌표계 (기본 project.crs 또는 EPSG:5186)')
    g = ap.add_argument_group('산출')
    g.add_argument('--out', help='1 m DEM 경로 (기본 <outputs.root>/10-intake/DEM_실측_1m.tif)')
    g.add_argument('--merge-into', help='거친 DEM — 이 파일의 사본에 실측을 덮어써 새 파일로 낸다')
    g.add_argument('--merge-out', help='실측 반영본 경로 (기본 <out 폴더>/<거친 DEM 이름>_실측반영.tif)')
    g.add_argument('--write', action='store_true', help='실제로 래스터를 만든다 (없으면 보고만)')
    g.add_argument('--replace', action='store_true', help='--out 이 있으면 _bak_YYMMDD 로 옮기고 새로 만든다')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()
    if sum(bool(v) for v in (a.layer_key, a.points, a.dxf)) != 1:
        ap.error('점 입력은 --layer-key / --points / --dxf 중 하나만')

    cfg = load_cfg(a.config)
    kitconfig.fix_proj()          # rasterio 전에
    import numpy as np
    crs = a.crs or (cfg.get('project.crs', 'EPSG:5186') if cfg else 'EPSG:5186')
    odir = cfg.out_dir('10-intake') if cfg else Path.cwd()
    out = Path(a.out) if a.out else odir / 'DEM_실측_1m.tif'
    RES, MAXD, NOD, BS = a.res, a.maxd, -9999.0, 1024

    P = read_points(a, cfg)
    if a.offset:
        P[:, 0] += a.offset[0]
        P[:, 1] += a.offset[1]
    if a.bbox:
        x0, y0, x1, y1 = a.bbox
        P = P[(P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1)]
    print('점', len(P))
    _, idx = np.unique(np.round(P[:, :2], 2), axis=0, return_index=True)   # 같은 좌표 중복 제거(Delaunay 안정)
    P = P[np.sort(idx)]
    print('중복제거 후', len(P))
    if len(P) < 3:
        raise SystemExit('점이 3개 미만 — 입력·거르기를 확인할 것')
    if a.bbox:
        xmin, ymin, xmax, ymax = a.bbox
    else:
        xmin, ymin = np.floor(P[:, 0].min() - a.pad), np.floor(P[:, 1].min() - a.pad)
        xmax, ymax = np.ceil(P[:, 0].max() + a.pad), np.ceil(P[:, 1].max() + a.pad)
    W, H = int((xmax - xmin) / RES), int((ymax - ymin) / RES)
    print('격자 %d x %d = %d Mpx  범위 %s  좌표계 %s' % (W, H, round(W * H / 1e6), [float(v) for v in (xmin, ymin, xmax, ymax)], crs))
    print('표고 %.2f ~ %.2f' % (P[:, 2].min(), P[:, 2].max()))
    if not a.write:
        print('\n(보고만 — 래스터를 만들지 않았다. --write 로 생성: %s)' % out)
        return
    if out.exists():
        if not a.replace:
            raise SystemExit('이미 있다: %s — 다시 만들려면 --replace (기존 파일은 _bak_YYMMDD 로 옮김)' % out)
        bak = out.with_name('%s_bak_%s%s' % (out.stem, datetime.datetime.now().strftime('%y%m%d_%H%M'), out.suffix))
        shutil.move(str(out), bak)
        print('기존 파일 → %s' % bak)
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = build_1m(P, str(out), crs, RES, MAXD, NOD, BS, (xmin, ymin, xmax, ymax))
    print('저장: %s' % out)
    if a.merge_into:
        mo = Path(a.merge_out) if a.merge_out else out.parent / ('%s_실측반영.tif' % Path(a.merge_into).stem)
        if mo.resolve() == Path(a.merge_into).resolve():
            raise SystemExit('--merge-out 이 원본 거친 DEM 과 같다 — 원본은 고치지 않는다')
        merge_into(a.merge_into, str(out), str(mo), (xmin, ymin, xmax, ymax), RES, W, H, NOD)
    print('\n설정: sewer.config.json "dem" 맨 앞에 %s%s 를 적는다(앞의 것 우선).'
          % (out.name, (' , 다음에 실측 반영본' if a.merge_into else '')))


if __name__ == '__main__':
    main()
