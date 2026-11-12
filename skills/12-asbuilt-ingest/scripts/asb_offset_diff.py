# -*- coding: utf-8 -*-
"""B2+B4 — 준공도 좌표 오프셋 산정·검증  +  DB diff  +  지반고 vs DEM 이상치.

1) 기지 맨홀(또는 관로 끝점) 3점 이상으로 오프셋 산정 → 수렴 0.2 m 이내 확인
2) 원본 제원표(asb_spec.py 산출 JSON)와 준공 DB(asbuilt_manhole·asbuilt_pipe) 를 diff
3) 맨홀 지반고 vs DEM 이상치 탐지 (번호 뒤바뀜 신호)

준공 DB 는 sewer.config.json 의 layers.asbuilt_manhole / asbuilt_pipe (PostGIS 또는 GPKG),
필드는 fields.asbuilt_manhole.{mh_id,district,ground,depth} / fields.asbuilt_pipe.{pipe_id,district,length_m,dia_mm,status},
DEM 은 dem 목록(앞의 것 우선). PostGIS 비밀번호는 db.postgis.password_env 환경변수.

예)
  python -X utf8 asb_offset_diff.py --pair 1000.0 2000.0 1071.45 102305.77 --pair ... --pair ...
  python -X utf8 asb_offset_diff.py --district A지구 --spec A지구_asb_spec.json
  (둘 다 한 번에 줘도 된다)
DB·DEM 은 읽기만 한다.
"""
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')


# ───── 준공 DB 읽기 (PostGIS / GPKG 공통) ─────────────────
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


def dem_paths(cfg, override):
    if override:
        return [Path(p) for p in override]
    d = cfg.get('dem') or []
    if isinstance(d, str):
        d = [d]
    return [cfg.path_of(p) for p in d]


# ───── 1) 오프셋 ─────────────────────────────────────────
def offset(PAIRS):
    ds = [(q[0] - s[0], q[1] - s[1]) for s, q in PAIRS]
    ox = statistics.mean(d[0] for d in ds); oy = statistics.mean(d[1] for d in ds)
    spr = max(math.hypot(d[0] - ox, d[1] - oy) for d in ds)
    print('■ 좌표 오프셋 = (%+.2f, %+.2f)   편차 %.2f m  → %s'
          % (ox, oy, spr, 'OK' if spr <= 0.2 else '★ 재확인 (0.2m 초과)'))
    for (s, q), d in zip(PAIRS, ds):
        print('    (%.1f,%.1f)→(%.1f,%.1f)  (%+.2f, %+.2f)' % (s[0], s[1], q[0], q[1], d[0], d[1]))
    if len(PAIRS) < 3:
        print('    ★ 기지점 %d점 — 3점 이상으로 산정할 것' % len(PAIRS))
    return ox, oy


# ───── 2)·3) DB diff + DEM 이상치 ───────────────────────
def read_json(p):
    for enc in ('utf-8', 'cp949'):   # 예전 산출물은 cp949 로 저장된 것이 있다
        try:
            with open(p, encoding=enc) as f:
                return json.load(f)
        except UnicodeDecodeError:
            continue
    raise SystemExit('JSON 인코딩을 읽지 못했다: %s' % p)


def diff(cfg, JIGU, SPEC_JSON, DEMS, DEM_TOL):
    spec = read_json(SPEC_JSON)
    qm = {r[0]: (r[1], r[2], r[3], r[4]) for r in
          fetch(cfg, 'asbuilt_manhole', ['mh_id', 'ground', 'depth'],
                where={'district': JIGU}, notnull=['mh_id'], xy=True)}
    qp = {r[0]: (r[1], r[2], r[3]) for r in
          fetch(cfg, 'asbuilt_pipe', ['pipe_id', 'length_m', 'dia_mm', 'status'],
                where={'district': JIGU}, notnull=['pipe_id'])}
    MAN, PIPE = spec['man'], spec['pipe']
    print('\n■ 맨홀  원본 %d ↔ DB %d' % (len(MAN), len(qm)))
    print('   원본에만:', sorted(set(MAN) - set(qm)))
    print('   DB에만:', sorted(set(qm) - set(MAN)))
    for k in sorted(set(qm) & set(MAN)):
        if qm[k][1] is None or MAN[k] is None or abs(qm[k][1] - MAN[k]) > 0.005:
            print('   ! %-16s 깊이 DB %-6s ↔ 원본 %s' % (k, qm[k][1], MAN[k]))
    print('\n■ 관로  원본 %d ↔ DB %d' % (len(PIPE), len(qp)))
    print('   원본에만:', sorted(set(PIPE) - set(qp)), ' ← %s 여부 확인' % cfg.value('status_abandon', '기존폐쇄'))
    print('   DB에만:', sorted(set(qp) - set(PIPE)))
    for k in sorted(set(qp) & set(PIPE)):
        L, D = PIPE[k]
        if L and qp[k][0] and abs(qp[k][0] - L) > 0.05:
            print('   ! %-14s 연장 DB %-7s ↔ 원본 %s' % (k, qp[k][0], L))
    # DEM 이상치
    dem = Dem(DEMS)
    print('\n■ 지반고 vs DEM 이상치 (|차| > %.1f m — 번호 뒤바뀜 신호)' % DEM_TOL)
    if not dem.ds:
        print('   DEM 없음 — 건너뜀 (sewer.config.json dem 또는 --dem)')
        return
    bad = 0
    for k, (gl, h, x, y) in sorted(qm.items()):
        if gl is None:
            continue
        v = dem.z(x, y)
        if v and abs(gl - v) > DEM_TOL:
            print('   ! %-16s GL %-7s DEM %-7.2f  차 %+.2f' % (k, gl, v, gl - v)); bad += 1
    print('   이상치 %d건' % bad)


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
    ap = argparse.ArgumentParser(description='준공도 좌표 오프셋 산정 + 준공 DB diff + 지반고·DEM 이상치 (읽기 전용)')
    ap.add_argument('--pair', nargs=4, type=float, action='append', metavar=('SX', 'SY', 'QX', 'QY'),
                    help='오프셋 산정용 (원본도면 x y, 프로젝트 실좌표 x y). 3번 이상 준다')
    ap.add_argument('--district', help='diff 할 지구 (fields.*.district 값). 없으면 diff 생략')
    ap.add_argument('--spec', help='asb_spec.py 산출 JSON (기본 <outputs.root>/12-asbuilt/asb_spec.json)')
    ap.add_argument('--dem', nargs='+', metavar='TIF', help='DEM 경로 (기본 sewer.config.json dem, 앞의 것 우선)')
    ap.add_argument('--dem-tol', type=float, default=1.5, help='지반고 − DEM 이 이보다 크면 이상치 (기본 1.5 m)')
    ap.add_argument('--save', nargs='?', const='', metavar='파일명',
                    help='콘솔 출력을 <outputs.root>/12-asbuilt/ 에도 저장 (파일명 생략 시 asb_offset_diff_YYMMDD_HHMM.txt)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()
    if not a.pair and not a.district:
        ap.error('--pair (오프셋 산정) 또는 --district (DB diff) 중 하나는 줘야 한다')

    cfg = kitconfig.load(a.config) if (a.district or a.save is not None) else None
    fh = None
    if a.save is not None:
        import datetime
        out = cfg.out_dir('12-asbuilt') / (a.save or 'asb_offset_diff_%s.txt' % datetime.datetime.now().strftime('%y%m%d_%H%M'))
        fh, con = tee_to(out)
    if a.pair:
        ox, oy = offset([((p[0], p[1]), (p[2], p[3])) for p in a.pair])
        print('    → sewer.config.json asbuilt.offsets.%s = [%.2f, %.2f]' % (a.district or '<지구>', ox, oy))
    if a.district:
        sp = Path(a.spec) if a.spec else cfg.out_dir('12-asbuilt') / 'asb_spec.json'
        if not sp.exists():
            raise SystemExit('asb_spec.json 이 없다: %s — asb_spec.py 를 먼저 돌릴 것' % sp)
        diff(cfg, a.district, sp, dem_paths(cfg, a.dem), a.dem_tol)
    if fh:
        sys.stdout = con
        fh.close()
        print('저장: %s' % out)


if __name__ == '__main__':
    main()
