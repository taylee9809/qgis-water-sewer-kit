# -*- coding: utf-8 -*-
"""C2 — 최소경사 기준 수용 가능 맨홀 전수 탐색.

각 기존 맨홀(유출관저 = 지반고 − 맨홀깊이)에 대해
    필요 시작관저 = 유출관저 + 최소경사 × 직선거리,   가능 ⟺ 필요 시작관저 ≤ 출발 관저
를 가까운 순으로 찍는다.

출발 노드 = 계획 노선(layers.plan_pipe)의 관번호 + 상류/하류 끝점. 관저는 fields.plan_pipe.us_invert/ds_invert.
수용 후보 = layers.asbuilt_manhole (기본, --mh-key 로 바꿈) 의 mh_id·ground·depth (fields.<key>.*).
최소경사 = design.min_slope_permil (--min-slope 로 덮어씀).

예)
  python -X utf8 gravity_scan.py --district A지구 ^
      --start 펌프장노드 A-024 dn --start 상류단차제거노드 A-023 dn
  python -X utf8 gravity_scan.py --start 펌프장노드 A-024 dn --min-slope 7
DB·GPKG 는 읽기만 한다.
"""
import argparse
import math
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')


# ───── 레이어 읽기 (PostGIS / GPKG 공통) ─────────────────
def _py(v):
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError, ImportError):
        pass
    return v.item() if hasattr(v, 'item') else v


def fetch(cfg, key, logical, where=None, notnull=(), xy=False):
    """논리 레이어 key 의 logical 필드(+ 점 x, y) 를 행 튜플 목록으로. where={논리필드: 값}."""
    L = cfg.layer(key)
    cols = [cfg.field(key, c) for c in logical]
    wh = {cfg.field(key, k): v for k, v in (where or {}).items() if v is not None}
    nn = [cfg.field(key, c) for c in notnull]
    if L['kind'] == 'postgis':
        sel = ', '.join('"%s"' % c for c in cols)
        if xy:
            sel += ', ST_X("%s"), ST_Y("%s")' % (L['geom'], L['geom'])
        cond = ['"%s" = %%s' % c for c in wh] + ['"%s" is not null' % c for c in nn]
        sql = 'select %s from "%s"."%s"' % (sel, L['schema'], L['layer'])
        if cond:
            sql += ' where ' + ' and '.join(cond)
        cn = cfg.pg_connect()
        try:
            cu = cn.cursor()
            cu.execute(sql, tuple(wh.values()))
            return [tuple(r) for r in cu.fetchall()]
        finally:
            cn.close()
    gdf = cfg.read_layer(key)
    for c, v in wh.items():
        gdf = gdf[gdf[c] == v]
    for c in nn:
        gdf = gdf[gdf[c].notna()]
    rows = []
    for _, r in gdf.iterrows():
        t = tuple(_py(r[c]) for c in cols)
        if xy:
            g = r.geometry
            if g is not None and g.geom_type == 'MultiPoint':
                g = g.geoms[0]
            t += (g.x, g.y) if g is not None else (None, None)
        rows.append(t)
    return rows


def _ends(g):
    """선형의 첫 점·끝 점 (멀티라인이면 첫 파트)."""
    if g.geom_type == 'MultiLineString':
        g = g.geoms[0]
    c = list(g.coords)
    return c[0][:2], c[-1][:2]


def run(cfg, STARTS, JIGU, MINSLP, TOPN, MH_KEY):
    kitconfig.fix_proj()
    no = cfg.read_layer('plan_pipe')
    c_id = cfg.field('plan_pipe', 'pipe_id')
    c_up = cfg.field('plan_pipe', 'us_invert')
    c_dn = cfg.field('plan_pipe', 'ds_invert')
    pts = {}
    for _, ft in no.iterrows():
        n = ft[c_id]
        for _, pno, side in STARTS:
            if n == pno:
                a, b = _ends(ft.geometry)
                pts[pno] = (a if side == 'up' else b,
                            _py(ft[c_up]) if side == 'up' else _py(ft[c_dn]))
    mh = [(r[0], r[1], r[2], round(r[1] - r[2], 2), r[3], r[4]) for r in
          fetch(cfg, MH_KEY, ['mh_id', 'ground', 'depth'], where={'district': JIGU},
                notnull=['mh_id', 'ground', 'depth'], xy=True)]
    print('수용 후보 맨홀 %d개 · 최소경사 %.1f‰\n' % (len(mh), MINSLP * 1000))

    for tag, pno, side in STARTS:
        if pno not in pts:
            print('!! %s (%s) 못 찾음' % (tag, pno)); continue
        S, IL0 = pts[pno]
        print('===== 출발 %s : %s %s단  (%.2f, %.2f)  IL=%.2f =====' %
              (tag, pno, '상류' if side == 'up' else '하류', S[0], S[1], IL0))
        rows = sorted((math.hypot(x - S[0], y - S[1]), n, il) for n, gl, h, il, x, y in mh)
        print('%-16s %8s %8s %10s %9s %8s %s' %
              ('맨홀', '직선m', '유출IL', '필요시작IL', '여유', '실경사‰', '판정'))
        for L, n, il in rows[:TOPN]:
            need = il + MINSLP * L
            s = (IL0 - il) / L * 1000 if L else 0
            print('%-16s %8.1f %8.2f %10.2f %9.2f %8.2f  %s' %
                  (n, L, il, need, IL0 - need, s, 'OK' if need <= IL0 else '불가'))
        ok = [(L, n, il) for L, n, il in rows if il + MINSLP * L <= IL0]
        print('→ 가장 가까운 가능 맨홀: %s\n' %
              ('%s (%.1f m, 유출IL %.2f)' % (ok[0][1], ok[0][0], ok[0][2]) if ok else '없음'))


def tee_to(path):
    """--save: 콘솔 출력을 산출 폴더 파일에도 같이 쓴다."""
    f = open(path, 'w', encoding='utf-8')
    con = sys.stdout

    class _Tee:
        def write(self, s):
            con.write(s)
            f.write(s)

        def flush(self):
            con.flush()
            f.flush()
    sys.stdout = _Tee()
    return f, con


def main():
    ap = argparse.ArgumentParser(description='최소경사 기준 수용 가능 맨홀 전수 탐색 (가까운 순, 읽기 전용)')
    ap.add_argument('--start', nargs=3, action='append', required=True, metavar=('라벨', '관번호', 'up|dn'),
                    help='출발 노드 = 계획 노선 관번호의 상류(up)/하류(dn) 끝점. 여러 번 줄 수 있다 '
                         '(예: 펌프장 노드, 상류 단차 제거 노드)')
    ap.add_argument('--district', help='수용 후보 맨홀을 이 지구로 한정 (fields.<mh-key>.district)')
    ap.add_argument('--min-slope', type=str, default=None, metavar='‰',
                    help='최소경사 ‰ (기본 design.min_slope_permil). 여유를 보려면 7 등으로도 돌려 본다')
    ap.add_argument('--top', type=int, default=14, help='출발점별로 찍을 가까운 맨홀 수 (기본 14)')
    ap.add_argument('--mh-key', default='asbuilt_manhole',
                    help='수용 후보 맨홀 논리 레이어 (기본 asbuilt_manhole). fields 에 mh_id·ground·depth 필요')
    ap.add_argument('--save', nargs='?', const='', metavar='파일명',
                    help='콘솔 출력을 <outputs.root>/32-route/ 에도 저장 (파일명 생략 시 gravity_scan_YYMMDD_HHMM.txt)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()
    for _, _, side in a.start:
        if side not in ('up', 'dn'):
            ap.error('--start 세 번째 값은 up 또는 dn: %s' % side)

    cfg = kitconfig.load(a.config)
    permil = a.min_slope if a.min_slope is not None else cfg.require('design.min_slope_permil')
    MINSLP = float(Decimal(str(permil)) / 1000)
    fh = None
    if a.save is not None:
        import datetime
        sp = cfg.out_dir('32-route') / (a.save or 'gravity_scan_%s.txt' % datetime.datetime.now().strftime('%y%m%d_%H%M'))
        fh, con = tee_to(sp)
    run(cfg, [tuple(s) for s in a.start], a.district, MINSLP, a.top, a.mh_key)
    if fh:
        sys.stdout = con
        fh.close()
        print('저장: %s' % sp)


if __name__ == '__main__':
    main()
