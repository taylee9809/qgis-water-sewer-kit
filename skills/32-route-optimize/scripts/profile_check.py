# -*- coding: utf-8 -*-
"""C3 — 대안 노선 종단 성립성 검증 (DEM 샘플링 토피).

출발점 → 도착 맨홀 직선을 DEM 으로 STEP m 간격 샘플링해서 경사안별 관저·토피를 찍는다.
    시작관저 = 도착 유출관저 + 경사 × 직선거리,  토피 = 지반 − 관저 − 관경
제약 지점(암거 등)은 --check 측점 설명 으로 넣으면 그 지점 관저·관상단을 따로 표시.

좌표는 직접(--from X Y / --to X Y) 주거나 설정 레이어에서 찾는다:
  --from-pipe 관번호 up|dn   → layers.plan_pipe 끝점
  --to-mh 맨홀번호           → layers.asbuilt_manhole 좌표, 유출관저 = 지반고 − 맨홀깊이 (--il-b 가 없을 때)
DEM = sewer.config.json dem 목록(앞의 것 우선, 셀 값 그대로). 기본 경사안 = design.min_slope_permil,
관경 = design.min_dia_mm, 토피 기준 = design.min_cover_m.

예)
  python -X utf8 profile_check.py --from-pipe A-023 dn --to-mh M09 --district A지구 ^
      --plan 5.0 --plan "7.6:7.6‰ 여유안" --check 3.2 "박스 내공저면 59.60"
DB·GPKG·DEM 은 읽기만 한다.
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


# ───── DEM (앞의 것 우선, 셀 값 그대로 — 보간 없음) ─────────
class Dem:
    def __init__(self, paths):
        kitconfig.fix_proj()          # rasterio import 전에
        import rasterio
        self.ds = []
        for p in paths:
            if Path(p).exists():
                self.ds.append(rasterio.open(p))
            else:
                print('  (DEM 없음, 건너뜀: %s)' % p)

    def z(self, x, y):
        for d in self.ds:
            r, c = d.index(x, y)
            if 0 <= r < d.height and 0 <= c < d.width:
                v = d.read(1, window=((r, r + 1), (c, c + 1)))[0, 0]
                if d.nodata is not None and v == d.nodata:
                    continue
                v = float(v)
                if v == v and v > -1000:
                    return v
        return None


def permil(s):
    return float(Decimal(str(s)) / 1000)


def run(A, B, IL_B, PLANS, DIA, dem, CHECKS, STEP, MIN_COVER):
    L = math.hypot(B[0] - A[0], B[1] - A[1])
    print('출발(%.2f, %.2f) → 도착(%.2f, %.2f)  직선 %.1f m  도착 유출IL %.2f\n'
          % (A[0], A[1], B[0], B[1], L, IL_B))
    stations = sorted(set([0] + [c[0] for c in CHECKS] +
                          list(range(STEP, int(L), STEP)) + [round(L, 1)]))
    for slope, tag in PLANS:
        IL0 = IL_B + slope * L
        print('=== %s · 시작관저 %.2f (실경사 %.2f‰) ===' % (tag, IL0, slope * 1000))
        print('%8s %9s %9s %9s  %s' % ('측점', '지반고', '관저', '토피', '비고'))
        worst = (99, None)
        for s in stations:
            t = s / L
            gl = dem.z(A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t)
            il = IL0 - slope * s
            if gl is None:
                print('%8.1f %9s %9.2f %9s' % (s, '-', il, '-')); continue
            cov = gl - il - DIA
            note = ''
            for cs, cd in CHECKS:
                if abs(s - cs) < 0.05:
                    note = '← %s / 관상단 %.2f' % (cd, il + DIA)
            if cov < worst[0]:
                worst = (cov, s)
            print('%8.1f %9.2f %9.2f %9.2f  %s' % (s, gl, il, cov, note))
        print('  최소토피 %.2f m (측점 %.1f)' % worst if worst[1] is not None else '  최소토피 - (DEM 값 없음)')
        if worst[1] is not None and MIN_COVER is not None:
            print('  → 최소토피 기준 %.2f m %s' % (MIN_COVER, '이상 OK' if worst[0] >= MIN_COVER else '★ 미달'))
        print()


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
    ap = argparse.ArgumentParser(description='대안 노선 종단 성립성 — 출발→도착 직선 DEM 샘플링 토피 (읽기 전용)')
    g1 = ap.add_mutually_exclusive_group(required=True)
    g1.add_argument('--from', dest='frm', nargs=2, type=float, metavar=('X', 'Y'), help='출발 노드 좌표')
    g1.add_argument('--from-pipe', nargs=2, metavar=('관번호', 'up|dn'), help='계획 노선(layers.plan_pipe) 끝점을 출발점으로')
    g2 = ap.add_mutually_exclusive_group(required=True)
    g2.add_argument('--to', nargs=2, type=float, metavar=('X', 'Y'), help='도착 맨홀 좌표')
    g2.add_argument('--to-mh', metavar='맨홀번호', help='도착 맨홀 (layers.asbuilt_manhole, --mh-key 로 바꿈)')
    ap.add_argument('--il-b', type=float, help='도착 맨홀 유출관저 (--to-mh 면 지반고 − 맨홀깊이 로 자동)')
    ap.add_argument('--district', help='--to-mh 검색을 이 지구로 한정')
    ap.add_argument('--mh-key', default='asbuilt_manhole', help='--to-mh 를 찾을 논리 레이어 (기본 asbuilt_manhole)')
    ap.add_argument('--plan', action='append', metavar='‰[:라벨]',
                    help="경사안. 예 5.0 / '7.6:7.6‰ 여유안'. 기본 design.min_slope_permil 하나")
    ap.add_argument('--dia', type=float, help='관경 m (기본 design.min_dia_mm/1000, 없으면 0.20)')
    ap.add_argument('--check', nargs=2, action='append', default=[], metavar=('측점m', '설명'),
                    help='제약 지점(암거 등) — 그 측점의 관저·관상단을 따로 표시')
    ap.add_argument('--step', type=int, default=10, help='샘플 간격 m (기본 10)')
    ap.add_argument('--dem', nargs='+', metavar='TIF', help='DEM 경로 (기본 sewer.config.json dem, 앞의 것 우선)')
    ap.add_argument('--min-cover', type=float, help='최소토피 기준 m (기본 design.min_cover_m)')
    ap.add_argument('--save', nargs='?', const='', metavar='파일명',
                    help='콘솔 출력을 <outputs.root>/32-route/ 에도 저장 (파일명 생략 시 profile_check_YYMMDD_HHMM.txt)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = kitconfig.load(a.config)
    kitconfig.fix_proj()
    # 출발점
    if a.frm:
        A = tuple(a.frm)
    else:
        pno, side = a.from_pipe
        if side not in ('up', 'dn'):
            ap.error('--from-pipe 두 번째 값은 up 또는 dn')
        no = cfg.read_layer('plan_pipe')
        hit = no[no[cfg.field('plan_pipe', 'pipe_id')] == pno]
        if hit.empty:
            raise SystemExit('계획 노선에 관번호 %s 가 없다' % pno)
        g = hit.iloc[-1].geometry
        if g.geom_type == 'MultiLineString':
            g = g.geoms[0]
        c = list(g.coords)
        A = tuple((c[0] if side == 'up' else c[-1])[:2])
    # 도착점
    IL_B = a.il_b
    if a.to:
        B = tuple(a.to)
        if IL_B is None:
            ap.error('--to 로 줄 때는 --il-b (도착 유출관저) 가 필요하다')
    else:
        r = fetch(cfg, a.mh_key, ['mh_id', 'ground', 'depth'],
                  where={'mh_id': a.to_mh, 'district': a.district}, xy=True)
        if not r:
            raise SystemExit('맨홀 %s 를 %s 에서 못 찾음' % (a.to_mh, a.mh_key))
        if len(r) > 1:
            print('  (맨홀 %s 가 %d건 — --district 로 한정할 것. 첫 건 사용)' % (a.to_mh, len(r)))
        _, gl, h, x, y = r[0]
        B = (x, y)
        if IL_B is None:
            if gl is None or h is None:
                raise SystemExit('맨홀 %s 의 지반고/깊이가 비었다 — --il-b 로 줄 것' % a.to_mh)
            IL_B = round(gl - h, 2)
    # 경사안
    if a.plan:
        PLANS = []
        for p in a.plan:
            v, _, lab = p.partition(':')
            PLANS.append((permil(v), lab or '%s‰' % v))
    else:
        v = cfg.require('design.min_slope_permil')
        PLANS = [(permil(v), '%.1f‰ 최소' % float(v))]
    mind = cfg.get('design.min_dia_mm')
    DIA = a.dia if a.dia is not None else (mind / 1000 if mind else 0.20)
    CHECKS = [(float(s), d) for s, d in a.check]
    MIN_COVER = a.min_cover if a.min_cover is not None else cfg.get('design.min_cover_m')
    if a.dem:
        dps = [Path(p) for p in a.dem]
    else:
        d = cfg.get('dem') or []
        dps = [cfg.path_of(p) for p in ([d] if isinstance(d, str) else d)]
    dem = Dem(dps)
    if not dem.ds:
        raise SystemExit('DEM 이 없다 — sewer.config.json dem 또는 --dem')
    fh = None
    if a.save is not None:
        import datetime
        sp = cfg.out_dir('32-route') / (a.save or 'profile_check_%s.txt' % datetime.datetime.now().strftime('%y%m%d_%H%M'))
        fh, con = tee_to(sp)
    run(A, B, IL_B, PLANS, DIA, dem, CHECKS, a.step, MIN_COVER)
    if fh:
        sys.stdout = con
        fh.close()
        print('저장: %s' % sp)


if __name__ == '__main__':
    main()
