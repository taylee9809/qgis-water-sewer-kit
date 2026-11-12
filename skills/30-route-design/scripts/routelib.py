# -*- coding: utf-8 -*-
"""30-route-design 공통 모듈 (qgis-water-sewer-kit).

단계 스크립트(r1~r8)가 함께 쓰는 것:
  - 설정(kitconfig) · 설계기준(design.*) 기본값과 그 출처(설정 / 인자 / 킷 기본=KDS 또는 자체 가정)
  - 실행 폴더 <outputs.root>/30-route/<run>/ 와 중간 JSON 읽기·쓰기
  - 레이어 읽기(GPKG·PostGIS, bbox 거르기, 폴리곤 맨홀 → 중심점)
  - 지반고 표본(측량점 → DEM 우선순위) — rasterio 는 kitconfig.fix_proj() 뒤에 import
  - 위상 정렬 · Manning 부분만관 수리

파라미터 스윕은 파일을 고치지 말고 `--set 키=값` 또는 환경변수 ROUTE_SET="키=값,키=값" 으로 준다
(SKILL.md 1절 — 파일 치환은 크래시 때 중간값이 남는다).
"""
import sys, os, io, json, math, collections, datetime, argparse
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents
                            if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

STEP_DIR = '30-route'

# ───────────────────────────────────────────── 설계기준
# 키: (기본값, 근거). 근거가 'KDS' 로 시작하면 기준값, 나머지는 **자체 가정**(보고서에 가정으로 표기할 것).
# sewer.config.json 의 design.<키> 가 있으면 그것을 쓰고, --set 이 있으면 그것이 이긴다.
# ★ 근거의 기준 코드(KDS 61 40 00 관로시설·57 65 00 배수시설·44 90 00 도로암거구조)는 있는 것을 확인했지만(2026-11),
#   절 번호는 KDS 61 40 00 2025 개정판과 대조하지 않았다. 보고서에 인용하기 전에 원문으로 확인한다
#   (korean-engineering-mcp 의 get_standard_detail — 30 SKILL.md 「참고 조문」).
DESIGN = collections.OrderedDict([
    ('min_cover_m',          (1.0,   'KDS 61 40 00 3.2 최소 흙두께 원칙 1 m')),
    ('min_slope_permil',     (5.0,   'KDS 61 40 00 1.3 (시점부) · 과업 하한')),
    ('manning_n',            (0.011, 'KDS 61 40 00 1.2 조도계수(PVC)')),
    ('min_dia_mm',           (200,   'KDS 61 40 00 2.3 오수 최소관경')),
    ('pressure_max_d_mm',    (100,   '킷 설정 — 이 관경 이하는 압송으로 본다')),
    ('drop_manhole_m',       (0.6,   'KDS 61 40 00 5.1 부관 기준 단차')),
    ('mh_max_spacing_m',     (75,    'KDS 61 40 00 7.1② D600 이하 최대간격')),
    ('min_velocity_mps',     (0.6,   'KDS 61 40 00 1.3 최소유속')),
    ('max_velocity_mps',     (3.0,   'KDS 61 40 00 1.3 최대유속')),
    ('road_cover_m',         (None,  '자체 가정 — 노폭이 넓은 도로의 토피(과업·조례). 비우면 미적용')),
    ('road_width_limit_m',   (4.0,   '자체 가정 — 노폭이 이 값을 넘으면 road_cover_m 적용')),
    ('max_depth_m',          (7.0,   '자체 가정 — 간선 직선화 매설심도 상한')),
    ('obstacle_sep_m',       (0.30,  'KDS 57 65 00 4.5(2) 교차 이격')),
    ('box_protect_m',        (0.15,  '자체 가정 — BOX·개거 하부 통과 관보호공 두께')),
    ('box_wall_m',           (0.30,  'KDS 44 90 00 4.5.2 부재 최소두께 — BOX 저판')),
    ('unknown_cover_m',      (0.8,   '자체 가정 — 관저 미상 원형 지장물 토피')),
    ('water_cover_assume_m', (1.2,   '자체 가정 — 상수관 토피(실측 없을 때)')),
    ('water_protect_m',      (0.15,  '자체 가정 — 상수관 교차부 관보호공')),
    ('cover_relax_max_m',    (0.5,   '자체 가정 — 중간 절점 하나 때문에 하류 관저를 더 낮추는 상한')),
    ('ground_step_m',        (2.0,   '자체 가정 — 맨홀 사이 지반고 표본 간격')),
    ('ground_spike_m',       (0.5,   '자체 가정 — 이만큼 낮은 단발 함몰은 측구·DEM 노이즈로 평활')),
    ('notch_m',              (1.5,   '자체 가정 — 주변보다 이만큼 낮으면 하천 노치(검사 제외)')),
    ('ground_mismatch_m',    (1.0,   '자체 가정 — 맨홀 지반고와 사이 표본이 양 끝에서 이만큼 어긋나면 출처 불일치(제약 제외)')),
    ('force_dia_mm',         (75,    '자체 가정 — 신설 압송관경')),
    ('force_cover_m',        (1.2,   '자체 가정 — 압송관 최소토피')),
    ('force_depth_m',        (3.0,   '자체 가정 — 압송관 최대굴착(관저 기준)')),
    ('force_min_seg_m',      (20.0,  '자체 가정 — 압송 최소 구배구간 연장')),
    ('force_spike_m',        (2.0,   '자체 가정 — 평활값보다 이만큼 낮은 원지반 = 하천·교량 의심')),
    ('force_station_m',      (20.0,  '자체 가정 — 압송 측점 간격')),
    ('bed_cover_m',          (1.0,   '자체 가정 — 하천 하월 하상 피복(하천점용 협의)')),
    ('hang_depth_m',         (1.2,   '자체 가정 — 관매달기 교면 − 관저')),
    ('abutment_trans_m',     (1.5,   '자체 가정 — 교대 전후 전이점 거리')),
    ('pump_smooth_n',        (7,     '자체 가정 — 펌프 위치 판단용 지반고 이동중앙값 노드 수')),
])


def _num(v):
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ('none', 'null', ''):
            return None
        try:
            return int(s) if s.lstrip('-').isdigit() else float(s)
        except ValueError:
            return s
    return v


def design_params(cfg, sets=()):
    """→ (P, src). P[키]=값, src[키]='설정'|'인자'|'KDS'|'가정'."""
    P, src = {}, {}
    over = {}
    env = os.environ.get('ROUTE_SET', '')
    for item in [x for x in env.split(',') if x.strip()] + list(sets or ()):
        k, _, v = item.partition('=')
        over[k.strip()] = _num(v)
    cd = cfg.get('design', {}) or {}
    for k, (dv, why) in DESIGN.items():
        if k in over:
            P[k], src[k] = over[k], '인자'
        elif k in cd and cd[k] is not None:
            P[k], src[k] = cd[k], '설정'
        else:
            P[k], src[k] = dv, ('KDS' if why.startswith('KDS') else '가정')
    for k, v in over.items():            # DESIGN 에 없는 키도 받는다(스크립트별 추가 파라미터)
        if k not in P:
            P[k], src[k] = v, '인자'
    return P, src


def params_table(P, src, keys):
    out = []
    for k in keys:
        why = DESIGN.get(k, (None, ''))[1]
        out.append(dict(키=k, 값=P.get(k), 출처=src.get(k), 근거=why))
    return out


def print_params(P, src, keys):
    for k in keys:
        tag = {'KDS': '기준값', '설정': '설정', '인자': '인자', '가정': '★자체가정'}.get(src.get(k), src.get(k))
        print('   %-22s %-8s %s' % (k, P.get(k), tag))


# ───────────────────────────────────────────── 실행 컨텍스트

def common_args(ap):
    kitconfig.add_config_arg(ap)
    ap.add_argument('--run', default='main',
                    help='실행 이름 — 산출 폴더 <outputs.root>/30-route/<run>/ (기본 main)')
    ap.add_argument('--set', action='append', default=[], metavar='키=값',
                    help='설계기준 덮어쓰기(여러 번). 예: --set cover_relax_max_m=2.0')
    return ap


class Ctx:
    def __init__(self, args):
        self.args = args
        self.cfg = kitconfig.load(getattr(args, 'config', None))
        self.P, self.src = design_params(self.cfg, getattr(args, 'set', []))
        self.dir = self.cfg.out_dir(STEP_DIR, getattr(args, 'run', 'main') or 'main')

    def path(self, name):
        return self.dir / name

    def save(self, name, obj):
        p = self.dir / name
        with io.open(p, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, default=_jdefault)
        return p

    def load(self, name, required=True):
        p = self.dir / name
        if not p.exists():
            if required:
                raise SystemExit('%s 가 없다 — 앞 단계를 먼저 돌릴 것 (%s)' % (name, self.dir))
            return None
        return json.load(io.open(p, encoding='utf-8'))


def _jdefault(o):
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
    except ImportError:
        pass
    return str(o)


def stamp():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


def load_any(path):
    """JSON(dict/list) 또는 CSV(헤더 있음) → 행 목록 또는 dict."""
    p = Path(path)
    if p.suffix.lower() == '.json':
        return json.load(io.open(p, encoding='utf-8'))
    import csv
    with io.open(p, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


# ───────────────────────────────────────────── 레이어

def layer_spec(cfg, key, src_gpkg=None, layer_name=None):
    L = cfg.layer(key) if cfg.has_layer(key) else None
    if src_gpkg:
        name = layer_name or (L['layer'] if L else None)
        if not name:
            raise SystemExit('--src-gpkg 를 쓸 때 layers.%s 가 없으면 레이어명을 인자로 줄 것' % key)
        return {'kind': 'gpkg', 'path': Path(src_gpkg), 'layer': name}
    if L is None:
        return None
    if layer_name:
        L = dict(L, layer=layer_name)
    return L


def read_layer(cfg, key, bbox=None, src_gpkg=None, layer_name=None, required=True):
    """레이어 → GeoDataFrame. bbox=(minx,miny,maxx,maxy) 면 그 범위만. 없는 레이어는 None(required=False)."""
    import geopandas as gpd
    L = layer_spec(cfg, key, src_gpkg, layer_name)
    if L is None:
        if required:
            raise kitconfig.ConfigError('layers.%s 가 설정에 없다' % key)
        return None
    if L['kind'] == 'gpkg':
        if not Path(L['path']).exists():
            raise kitconfig.ConfigError('layers.%s: GPKG 가 없다 → %s' % (key, L['path']))
        return gpd.read_file(L['path'], layer=L['layer'], bbox=bbox) if bbox else \
            gpd.read_file(L['path'], layer=L['layer'])
    sql = 'select * from "%s"."%s"' % (L['schema'], L['layer'])
    if bbox:
        sql += ' where "%s" && ST_MakeEnvelope(%f,%f,%f,%f, ST_SRID("%s"))' % (
            L['geom'], bbox[0], bbox[1], bbox[2], bbox[3], L['geom'])
    with cfg.pg_connect() as cn:
        return gpd.read_postgis(sql, cn, geom_col=L['geom'])


def fcol(cfg, key, logical, df=None, default=None):
    """논리 필드 → 실제 필드명. 설정에 없거나 df 에 없으면 default(None)."""
    f = cfg.get('fields.%s.%s' % (key, logical)) or default
    if f and df is not None and f not in df.columns:
        return None
    return f


def val(row, col, default=None):
    if not col:
        return default
    v = row.get(col) if hasattr(row, 'get') else row[col]
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
    except TypeError:
        pass
    return v


def point_of(geom):
    """맨홀 기하(점·폴리곤) → (x, y). 폴리곤이면 중심점."""
    if geom is None or geom.is_empty:
        return None
    g = geom if geom.geom_type == 'Point' else geom.centroid
    return (g.x, g.y)


def lines_of(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == 'LineString':
        return [list(geom.coords)]
    if geom.geom_type == 'MultiLineString':
        return [list(g.coords) for g in geom.geoms]
    return []


# ───────────────────────────────────────────── 지반고 표본

class Ground:
    """지반고 표본기. 우선순위: 측량점(반경 내 중앙값) → DEM 목록(앞에서부터).
    측량점 = layers.survey_point (fields.survey_point.elev, 선택 outlier 필드 1 은 제외)."""

    def __init__(self, cfg, bbox=None, radius=5.0, use_survey=True, use_dem=True):
        self.cfg, self.radius = cfg, radius
        self.SA = None
        if use_survey and cfg.has_layer('survey_point'):
            import numpy as np
            df = read_layer(cfg, 'survey_point', bbox=bbox)
            ez = fcol(cfg, 'survey_point', 'elev', df, 'elev')
            oc = fcol(cfg, 'survey_point', 'outlier', df, 'outlier')
            if oc:
                df = df[df[oc].fillna(0).astype(float) == 0]
            pts = [(g.x, g.y, float(z)) for g, z in zip(df.geometry, df[ez])
                   if g is not None and not g.is_empty and z is not None and not math.isnan(float(z))]
            self.SA = np.array(pts) if pts else None
        self._tree = None
        if self.SA is not None:
            try:
                from scipy.spatial import cKDTree
                self._tree = cKDTree(self.SA[:, :2])
            except ImportError:
                pass
        self.dems = []
        if use_dem:
            for d in (cfg.get('dem') or []):
                p = cfg.path_of(d)
                if p and Path(p).exists():
                    self.dems.append(Path(p))
                else:
                    print('  ! DEM 없음(건너뜀): %s' % p)
        self._ds = None

    @property
    def n_survey(self):
        return 0 if self.SA is None else len(self.SA)

    def survey_at(self, x, y, r=None):
        """측량점 반경 r 안 중앙값(소수 2자리). 원 파이프라인과 같은 방식."""
        if self.SA is None:
            return None
        import numpy as np
        r = r or self.radius
        SA = self.SA
        if self._tree is not None:
            s = SA[self._tree.query_ball_point((x, y), r + 1.5)]
        else:
            m = (np.abs(SA[:, 0] - x) < r + 1) & (np.abs(SA[:, 1] - y) < r + 1)
            s = SA[m]
        if not len(s):
            return None
        k = np.hypot(s[:, 0] - x, s[:, 1] - y) <= r
        return round(float(np.median(s[k, 2])), 2) if k.sum() else None

    def _open(self):
        if self._ds is None:
            kitconfig.fix_proj()
            import rasterio
            self._ds = [(rasterio.open(p), 'DEM:' + p.stem) for p in self.dems]
        return self._ds

    def dem_many(self, xy):
        """[(x,y)] → [(z, tag) | (None, None)]. 앞 DEM 에 값이 없으면 다음 DEM."""
        import numpy as np
        out = [(None, None)] * len(xy)
        todo = list(range(len(xy)))
        for ds, tag in self._open():
            if not todo:
                break
            vals = list(ds.sample([xy[i] for i in todo]))
            nod = ds.nodata
            left = []
            for i, v in zip(todo, vals):
                z = float(v[0])
                if np.isfinite(z) and z > -100 and (nod is None or abs(z - nod) > 1e-6):
                    out[i] = (round(z, 2), tag)
                else:
                    left.append(i)
            todo = left
        return out

    def at(self, x, y):
        z = self.survey_at(x, y)
        if z is not None:
            return z, '측량'
        if self.dems:
            return self.dem_many([(x, y)])[0]
        return None, None


# ───────────────────────────────────────────── 망·수리

def topo_order(nodes, edges):
    """edges = [(u, d)] → 상류 먼저 노드 순서. 순환이면 SystemExit."""
    downs = collections.defaultdict(list)
    indeg = {n: 0 for n in nodes}
    for u, d in edges:
        downs[u].append(d)
        indeg[d] = indeg.get(d, 0) + 1
        indeg.setdefault(u, 0)
    q = collections.deque(sorted(n for n, k in indeg.items() if k == 0))
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for d in downs[n]:
            indeg[d] -= 1
            if indeg[d] == 0:
                q.append(d)
    if len(order) != len(indeg):
        bad = sorted(n for n, k in indeg.items() if k > 0)[:10]
        raise SystemExit('망에 순환이 있다: %s …' % bad)
    return order


def manning_partial(D_mm, S_permil, q_Lps, n):
    """원형관 Manning 부분만관. → (만관유속, 만관유량 L/s, 실유속, 수심 m, 수심비, 유량비율 %).
    원 파이프라인(종단·수리 단계) 식 그대로 — 이분법 80회."""
    Dm = D_mm / 1000.0
    I = max(S_permil, 1e-6) / 1000.0
    Vf = (1 / n) * (Dm / 4) ** (2 / 3) * math.sqrt(I)
    A = math.pi * Dm * Dm / 4
    Qf = Vf * A * 1000
    if Qf <= 0 or not q_Lps:
        return Vf, Qf, 0, 0, 0, 0
    t = min(q_Lps / Qf, 0.999)
    lo, hi = 1e-9, 1.0
    for _ in range(80):
        m = (lo + hi) / 2
        th = 2 * math.acos(1 - 2 * m)
        if ((th - math.sin(th)) / (2 * math.pi)) * (1 - math.sin(th) / th) ** (2 / 3) < t:
            lo = m
        else:
            hi = m
    hd = (lo + hi) / 2
    th = 2 * math.acos(1 - 2 * hd)
    return Vf, Qf, Vf * (1 - math.sin(th) / th) ** (2 / 3), hd * Dm, hd, t * 100


def judge_velocity(v, P):
    if v > P['max_velocity_mps']:
        return 'OVER%.1f' % P['max_velocity_mps']
    if v >= P['min_velocity_mps']:
        return 'GOOD'
    return 'LESS%.1f' % P['min_velocity_mps']


def floor3(v):
    return math.floor(v * 1000) / 1000


def route_of(pid):
    """관번호 → 노선(계통) 이름. 'XX-100-001' → 'XX-100'. 노선 필드가 없을 때만 쓴다."""
    return pid.rsplit('-', 1)[0] if '-' in pid else pid


# KS F 4403:2019 원심력 철근 콘크리트관 관두께(호칭 mm → T mm). 지장물 외저 = 관저 − T.
KS_F4403_T = [(150, 26), (200, 27), (250, 28), (300, 30), (350, 32), (400, 35), (450, 38), (500, 42),
              (600, 50), (700, 58), (800, 66), (900, 75), (1000, 82), (1100, 88), (1200, 95),
              (1350, 103), (1500, 112), (1650, 120), (1800, 127)]


PVC_OUTER_M = {200: 0.22, 150: 0.17, 80: 0.09, 75: 0.085}   # 이중벽 PVC 외경(원 파이프라인 표)


def pvc_outer_m(d_mm):
    return PVC_OUTER_M.get(int(d_mm), d_mm / 1000.0 + 0.02)


def pipe_wall_m(d_mm):
    if not d_mm:
        return 0.0
    for dn, t in KS_F4403_T:
        if d_mm <= dn:
            return t / 1000.0
    return KS_F4403_T[-1][1] / 1000.0
