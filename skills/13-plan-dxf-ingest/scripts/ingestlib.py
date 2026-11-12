# -*- coding: utf-8 -*-
"""13-plan-dxf-ingest 공통 모듈 (qgis-water-sewer-kit 판).

설계사 납품물(평면 DXF · 종단 DXF · makesw .flo/.OUT) → 계획 노선·계획맨홀·맨홀펌프장 레이어.

설정은 프로젝트 루트 `sewer.config.json` 하나다(kitconfig). 이 스킬이 보는 곳:
    plan_ingest.*               DXF 작도 규약(레이어·블록·속성 태그) · 코드값 · 허용차 · 압송 처리 방식
    review_vendor.exchange_dirs / delivery_pattern / input_patterns   납품 폴더 찾기(40 스킬과 공유)
    layers.plan_pipe / plan_manhole / plan_pump      쓰기 대상
    fields.<레이어>.<논리필드>                         실제 필드명
    values.kind_gravity / kind_pressure / kind_existing   관로 구분 코드값
    design.pressure_max_d_mm / min_cover_m / min_slope_permil / drop_manhole_m
    outputs.root                → <outputs.root>/13-plan-ingest/<납품폴더명>/

아래 DEFAULTS 는 한 설계사 도면 규약 기준 **예시**다. 다른 설계사 도면이면 p1_scan.py 의 census 를 보고
plan_ingest 절에 그 도면의 레이어·블록·태그 이름을 적는다(코드는 고치지 않는다).
"""
import os, sys, io, re, json, csv, math, glob, copy, datetime, collections
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

_KIT_LIB = next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())
sys.path.insert(0, str(_KIT_LIB))
import kitconfig  # noqa: E402
import flospec    # noqa: E402

STEP_DIR = '13-plan-ingest'

# ───────────────────────────────────────────── 기본값 (설정 plan_ingest 로 덮어쓴다)
DEFAULTS = {
    'input_patterns': None,          # None 이면 review_vendor.input_patterns → 아래 DEFAULT_PATTERNS
    'encoding': 'auto',              # auto = AC1021(2007) 이상은 UTF-8, 그 아래는 $DWGCODEPAGE
    'dxf': {
        'pipe_layers': ['0계획관로_일반', '0계획관로_압송', 'd압송관로'],
        'pressure_layers': ['0계획관로_압송', 'd압송관로'],
        'mh_layers': {'0계획맨홀': 'plan', '0기존맨홀': 'existing', '+0-펌프장': 'pump'},
        'label_layers': ['0관로명', '0관로명_압송'],
        'attrib_block': '속성맨홀',
        'obstacle_block': '기타데이터',
    },
    'attrib': {                      # 속성맨홀 블록 ATTRIB 태그 → 의미
        'up': '관로명1',               # = 상류맨홀 = 관번호
        'down': ['관로명2A', '관로명2'],  # 앞에서부터 비어 있지 않은 것
        'ground': '지반고', 'us_invert': '관저고1', 'ds_invert': '관저고2',
        'dia': '관경', 'kind': '관종', 'mh_type': '맨홀',
    },
    'kind_map': {'신설오수': 'gravity', '신설압송': 'pressure', '기존오수': 'existing', '기존압송': 'existing'},
    'mh_types': {                    # ATTRIB '맨홀' 값 → 역할·호수·규격
        '1호맨홀': {'role': 'plan', 'type': '1호맨홀', 'inner_mm': 900, 'outer_mm': 1200},
        '맨홀펌프장': {'role': 'pump', 'type': '3호맨홀', 'inner_mm': 1500, 'outer_mm': 1800},
        '1호기존': {'role': 'existing', 'type': '1호기존', 'inner_mm': 900, 'outer_mm': 1200},
    },
    'mh_kind': {'plan': '계획맨홀', 'pump': '맨홀펌프장', 'existing': '기존맨홀'},
    'write_mh_roles': ['plan', 'pump'],
    'obstacle_tags': {'pipe': '관로명', 'kind': '관종', 'dia': '관경', 'box': 'BOX', 'invert': '관저고'},
    'out': {
        'name_regex': r'[A-Z0-9]+-[A-Z0-9]+-\d+',
        'outfall_regex': r'방류-?\d*',
        'judge_words': ['LESS0.6', 'NO GOOD', 'UPPR3.0', 'GOOD', 'FULL', 'Q-ZERO'],
    },
    'profile_name_regex': r'^[A-Z]+[-A-Z0-9]*-\d+$',
    'prefixes': [],
    'district': None, 'area': None,
    'district_by_prefix': {}, 'area_by_prefix': {},
    'pressure': 'fold',              # fold = 펌프장별 1행(압송_종단에 누가거리:관저고) / split = 세분구간 그대로
    'ext_node': 'polyline',          # 금회 밖 하류노드 좌표: polyline(폴리라인 방향+flo 연장→기존맨홀 심볼) / attrib
    'pressure_hyd': 'keep',          # 압송 행 OUT 수리값: keep / null(makesw 가 압송을 자연유하식으로 푼 값이라 비울 때)
    'pump': {'kind': '신설', 'spec': '3호', 'inner_mm': 1500,
             'basis_note': '미산정(용량계산서 후속)', 'peak_factor': 1.44},
    'tolerance': {
        'attr': 0.005,               # 평면 ATTRIB ↔ flo 관경·관저고·지반고
        'len_m': 0.5,                # 직선도형 ↔ flo 연장 (속성맨홀 경로)
        'node_m': 3.0,               # 폴리라인 끝점 → 노드 (상류 노드 찾기·기존맨홀 심볼 스냅)
        'snap_m': 3.0,               # 폴리라인 끝점 → 맨홀 심볼 (폴리라인 경로)
        'bend_m': 0.30,              # 중간 정점을 굴절로 볼 최소 수직편차(미만은 화살촉)
        'trim_m': 3.2,               # 자연유하 화살표 폴리라인이 맨홀마다 잘린 길이 합(양끝 1.6 m)
        'trim_pressure_m': 0.0,      # 압송 폴리라인은 맨홀까지 그려 트림이 없었다
        'len_poly_m': 2.5,           # 도면연장(트림 복원) ↔ flo 연장 (폴리라인 경로)
        'renumber_m': 0.5,           # 변경메모: 상류절점이 이 안이면 번호변경으로 추정
        'alias_il': 0.005,           # flo 번호 ↔ 도면 번호 별칭 후보(관저고 일치)
    },
}
DEFAULT_DELIVERY = r'^(\d{6})\s*\(?\d*\)?\s*from'
DEFAULT_PATTERNS = {'plan': ['평면도.dxf', '*평면*.dxf'], 'profile': ['종단면도.dxf', '*종단*.dxf'],
                    'flo': ['*.flo'], 'out': ['*.OUT']}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ('mh_layers', 'mh_types', 'kind_map',
                                                                              'district_by_prefix', 'area_by_prefix'):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(config=None):
    cfg = kitconfig.load(config)
    C = _merge(DEFAULTS, cfg.get('plan_ingest') or {})
    return cfg, C


# ───────────────────────────────────────────── 납품 폴더·입력 찾기 (40 스킬과 같은 규칙)

def _norm(s):
    return re.sub(r'[\s,\.()]+', '', s).lower()


def deliveries(cfg):
    pat = re.compile(cfg.get('review_vendor.delivery_pattern') or DEFAULT_DELIVERY)
    out = []
    for ex in cfg.get('review_vendor.exchange_dirs') or []:
        d = cfg.path_of(ex)
        if not d or not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            m = pat.match(p.name) if p.is_dir() else None
            if m:
                out.append((m.group(1), p))
    out.sort(key=lambda t: (t[0], t[1].name), reverse=True)
    return [p for _, p in out]


def find_delivery(cfg, key=None):
    p = Path(key) if key else None
    if p is not None and p.is_dir():
        return p.resolve()
    cands = deliveries(cfg)
    if key:
        hit = [c for c in cands if _norm(key) in _norm(c.name)]
        if not hit:
            raise SystemExit('납품 폴더를 못 찾았다: %r\n현재 목록:\n  %s'
                             % (key, '\n  '.join(c.name for c in cands[:15]) or '(없음 — review_vendor.exchange_dirs 확인)'))
        return hit[0]
    if not cands:
        raise SystemExit('납품 폴더가 없다. review_vendor.exchange_dirs·delivery_pattern 을 확인하거나 '
                         '--dxf / --flo 로 파일을 직접 줄 것.')
    return cands[0]


def collect_inputs(cfg, C, delivery):
    pats = C.get('input_patterns') or cfg.get('review_vendor.input_patterns') or DEFAULT_PATTERNS

    def pick(key):
        for pattern in pats.get(key, DEFAULT_PATTERNS.get(key, [])):
            hits = [p for p in Path(delivery).rglob(pattern) if p.is_file()]
            best = {}
            for p in sorted(hits, key=lambda q: (len(q.parts), str(q))):
                best.setdefault((p.name.lower(), p.stat().st_size), p)
            if best:
                return sorted(best.values(), key=lambda q: (len(q.parts), str(q)))
        return []

    return {'plan': pick('plan'), 'profile': pick('profile'), 'flo': pick('flo'), 'out': pick('out')}


def outdir(cfg, name, sub=None):
    return cfg.out_dir(STEP_DIR, sub or name)


# ───────────────────────────────────────────── DXF 스트리밍 스캔

def dxf_encoding(path, forced='auto'):
    """AC1021(R2007) 이상 DXF 는 $DWGCODEPAGE 선언과 무관하게 UTF-8 이다(ANSI_949 로 적혀 있어도).
    그 아래 버전만 코드페이지를 따른다."""
    if forced and forced != 'auto':
        return forced
    ver, cp = None, None
    with open(path, 'rb') as f:
        head = f.read(200000)
    lines = head.replace(b'\r\n', b'\n').split(b'\n')
    for i, l in enumerate(lines[:-1]):
        s = l.strip()
        if s == b'$ACADVER' and i + 2 < len(lines):
            ver = lines[i + 2].strip().decode('ascii', 'replace')
        elif s == b'$DWGCODEPAGE' and i + 2 < len(lines):
            cp = lines[i + 2].strip().decode('ascii', 'replace').upper()
        if ver and cp:
            break
    if ver and ver >= 'AC1021':
        return 'utf-8'
    if cp and cp.startswith('ANSI_'):
        return 'cp' + cp[5:]
    return 'utf-8'


def _entities(path, encoding, numcodes=('10', '20', '11', '21'), strcodes=('1', '2')):
    """ENTITIES 절을 group code 쌍으로 훑어 엔티티 dict 를 내놓는다(통째 로드 안 함 — 200 MB 대 평면도용)."""
    ent, in_ent = None, False
    with open(path, 'r', encoding=encoding, errors='replace') as f:
        it = iter(f)
        for code in it:
            try:
                val = next(it).rstrip('\r\n')
            except StopIteration:
                break
            code = code.strip()
            if code == '2' and val == 'ENTITIES' and not in_ent and ent is None:
                in_ent = True
                continue
            if code == '0':
                if in_ent and ent is not None:
                    yield ent
                    ent = None
                if val == 'ENDSEC' and in_ent:
                    return
                if in_ent:
                    ent = {'type': val}
            elif in_ent and ent is not None:
                if code in numcodes:
                    try:
                        ent.setdefault(code, []).append(float(val))
                    except ValueError:
                        pass
                elif code == '8':
                    ent['8'] = val.strip()
                elif code in strcodes:
                    ent.setdefault(code, []).append(val)


def scan_plan(path, C):
    """평면 DXF 1-pass 스캔 — 레이어 census / 관로 폴리라인 / 맨홀 심볼 INSERT / 관로명 TEXT / 속성 블록 ATTRIB."""
    D = C['dxf']
    PL = set(D['pipe_layers'])
    MH = set(D['mh_layers'])
    TX = set(D['label_layers'])
    AB = {b for b in (D.get('attrib_block'), D.get('obstacle_block')) if b}
    enc = dxf_encoding(path, C.get('encoding', 'auto'))
    layers, blockcnt, tagcount = collections.Counter(), collections.Counter(), collections.Counter()
    pl, mh, tx, attribs = [], [], [], []
    cur = None
    for e in _entities(path, enc):
        t, lay = e['type'], e.get('8', '')
        layers[(t, lay)] += 1
        if t == 'INSERT':
            nm = (e.get('2') or [''])[0]
            blockcnt[(nm, lay)] += 1
            cur = None
            if lay in MH or nm in AB:
                cur = dict(block=nm, layer=lay, x=(e.get('10') or [0])[0], y=(e.get('20') or [0])[0], attrs={})
                (attribs if nm in AB else mh).append(cur)
        elif t == 'ATTRIB' and cur is not None:
            tag = ((e.get('2') or [''])[0] or '').strip()
            val = ((e.get('1') or [''])[0] or '').strip()
            if tag:
                cur['attrs'][tag] = val
                tagcount[(cur['block'], tag)] += 1
        elif t in ('SEQEND', 'ENDBLK'):
            cur = None
        elif t == 'LWPOLYLINE' and lay in PL:
            xs, ys = e.get('10') or [], e.get('20') or []
            if len(xs) >= 2 and len(xs) == len(ys):
                pl.append(dict(layer=lay, pts=[(round(x, 4), round(y, 4)) for x, y in zip(xs, ys)]))
        elif t == 'TEXT' and lay in TX:
            s = ((e.get('1') or [''])[0] or '').strip()
            if s:
                ax, ay = (e.get('11') or [None])[0], (e.get('21') or [None])[0]
                ix, iy = (e.get('10') or [0])[0], (e.get('20') or [0])[0]
                x, y = (ax, ay) if (ax or ay) else (ix, iy)
                tx.append(dict(name=s, layer=lay, x=x, y=y))
    return dict(path=str(path), encoding=enc, dxf_conf=copy.deepcopy(D),
                census=[[t, l, c] for (t, l), c in layers.most_common()],
                blocks=[[n, l, c] for (n, l), c in blockcnt.most_common()],
                tags=[[b, t, c] for (b, t), c in tagcount.most_common()],
                pipes=pl, symbols=mh, labels=tx, attribs=[a for a in attribs if a['attrs']])


def scan_profile_names(path, C):
    """종단 DXF 의 TEXT/MTEXT 중 관번호 형식 글자 집합 — flo↔도면 번호 별칭 확인용."""
    rx = re.compile(C.get('profile_name_regex') or DEFAULTS['profile_name_regex'])
    enc = dxf_encoding(path, C.get('encoding', 'auto'))
    names = collections.Counter()
    for e in _entities(path, enc, numcodes=(), strcodes=('1',)):
        if e['type'] in ('TEXT', 'MTEXT'):
            for s in e.get('1') or []:
                s = re.sub(r'\\[A-Za-z][^;]*;', '', s).strip()
                if rx.match(s):
                    names[s] += 1
    return names


# ───────────────────────────────────────────── flo / OUT

def parse_rename(items):
    m = {}
    for it in items or []:
        if '=' not in it:
            raise SystemExit('--rename 형식은 flo번호=도면번호: %r' % it)
        a, b = it.split('=', 1)
        m[a.strip()] = b.strip()
    return m


def load_flo(paths, rename=None):
    """flospec 으로 읽는다(탭/무탭/TSV 방언 모두). 반환: ({상류맨홀: 레코드}, 오류목록, 파일별 상류목록)."""
    rename = rename or {}
    FLO, ERR, BYFILE = {}, [], collections.OrderedDict()
    for p in paths:
        F = flospec.read_flo(str(p))
        names = []
        for r in F['recs']:
            if r.get('_err'):
                ERR.append((Path(p).name, r.get('_line'), r['_err']))
                continue
            r = dict(r)
            r['flo_up'], r['flo_dn'] = r['up'], r['dn']
            r['up'] = rename.get(r['up'], r['up'])
            r['dn'] = rename.get(r['dn'], r['dn'])
            r['_file'] = Path(p).name
            FLO[r['up']] = r
            names.append(r['up'])
        BYFILE[Path(p).name] = names
    return FLO, ERR, BYFILE


NUM = re.compile(r'(-?\d+\.\d+)')


def parse_out(paths, C, rename=None):
    """makesw .OUT 2행 레코드. 1행 = 관로명 현재관로 … 만관유량(6번째 실수) … 판정,
    2행 = 다음관로 … 실유속(9) 오수량(10, ㎥/s) 수심(11) 사용률(%USE)."""
    rename = rename or {}
    O = C['out']
    name, outf = O['name_regex'], O['outfall_regex']
    r1 = re.compile(r'\s+(%s)\s+(%s|%s)(.*)$' % (name, name, outf))
    r2 = re.compile(r'\s+(%s|%s)\s' % (name, outf))
    rj = re.compile('(' + '|'.join(re.escape(w) for w in O['judge_words']) + ')')
    OUT = {}
    for p in paths:
        cur = None
        for ln in io.open(p, encoding='cp949', errors='replace').read().splitlines():
            m = r1.match(ln)
            if m:
                cur = rename.get(m.group(1), m.group(1))
                rest = m.group(3)
                mm = rj.search(rest)
                OUT[cur] = {'head': rest.strip(), '판정': mm.group(1) if mm else None}
            elif cur and r2.match(ln):
                OUT[cur]['2nd'] = ln.strip()
                OUT[cur]['nums'] = NUM.findall(ln)
                cur = None
    return OUT


def _nn(seq, i):
    try:
        return float(seq[i])
    except Exception:
        return None


def hyd(OUT, n, null=False):
    if null:
        return dict(velocity_mps=None, flow_m3d=None, full_flow_m3d=None, flow_depth_m=None,
                    depth_ratio_pct=None, judge=None)
    o = OUT.get(n, {})
    nums = o.get('nums') or []
    h = NUM.findall(o.get('head', '') or '')
    u = re.search(r'([\d.]+)%USE', o.get('2nd', '') or '')
    q, full = _nn(nums, 10), _nn(h, 6)
    return dict(velocity_mps=_nn(nums, 9),
                flow_m3d=round(q * 86400, 1) if q is not None else None,
                full_flow_m3d=round(full * 86400, 1) if full is not None else None,
                flow_depth_m=_nn(nums, 11),
                depth_ratio_pct=float(u.group(1)) if u else None,
                judge=o.get('판정'))


def flo_tokens(rec):
    """flo 꼬리의 지장물 토큰 x=/X=/G= (측점,저면EL,크기). 같은 토큰 중복 기재는 1개로."""
    out, seen = [], set()
    for mm in re.finditer(r'([xXG])=([\d.]+),([\d.-]+),([\d.]+)', rec.get('_raw') or ''):
        p, sta, el, sz = mm.groups()
        if (p, sta, el, sz) in seen:
            continue
        seen.add((p, sta, el, sz))
        out.append(dict(pre=p, 측점=float(sta), 저면EL=float(el), 크기=float(sz)))
    return out


# ───────────────────────────────────────────── 계획 레이어 읽기 (GPKG / PostGIS)

PLAN_KEYS = ('plan_pipe', 'plan_manhole', 'plan_pump')


def layer_spec(cfg, key, gpkg=None):
    """layers.<key> → spec. gpkg 를 주면 같은 레이어명을 그 GPKG 에서(백업·사본 대조·시험 쓰기용)."""
    if not cfg.has_layer(key):
        return None
    sp = cfg.layer(key)
    if gpkg:
        return {'kind': 'gpkg', 'path': Path(gpkg), 'layer': sp['layer']}
    return sp


def label(sp):
    return ('%s | %s' % (sp['path'], sp['layer'])) if sp['kind'] == 'gpkg' else \
        ('postgis %s.%s' % (sp['schema'], sp['layer']))


def _coords(geom):
    if geom is None:
        return None
    t, c = geom['type'], geom['coordinates']
    if t == 'Point':
        return [tuple(c[:2])]
    if t == 'LineString':
        return [tuple(p[:2]) for p in c]
    if t == 'Polygon':
        return [tuple(p[:2]) for p in c[0]]
    if t == 'MultiLineString':
        return [tuple(p[:2]) for part in c for p in part]
    if t == 'MultiPolygon':
        return [tuple(p[:2]) for p in c[0][0]]
    if t == 'MultiPoint':
        return [tuple(c[0][:2])]
    return None


def read_rows(cfg, sp):
    """→ (필드 [(이름, 형)], 기하형, 행 목록[dict + '_xy' 좌표열 + '_fid'])."""
    if sp['kind'] == 'gpkg':
        if not Path(sp['path']).exists():
            raise SystemExit('GPKG 가 없다: %s' % sp['path'])
        kitconfig.fix_proj()
        import fiona
        if sp['layer'] not in fiona.listlayers(str(sp['path'])):
            raise SystemExit('레이어가 없다: %s' % label(sp))
        with fiona.open(str(sp['path']), layer=sp['layer']) as c:
            fields = list(c.schema['properties'].items())
            gtype = c.schema['geometry']
            rows = []
            for f in c:
                d = dict(f['properties'])
                g = f['geometry']
                d['_xy'] = _coords({'type': g['type'], 'coordinates': g['coordinates']}) if g else None
                d['_fid'] = f.id
                rows.append(d)
        return fields, gtype, rows
    import geopandas as gpd
    sql = 'select * from "%s"."%s"' % (sp['schema'], sp['layer'])
    with cfg.pg_connect() as cn:
        gdf = gpd.read_postgis(sql, cn, geom_col=sp['geom'])
    from shapely.geometry import mapping
    geomcol = gdf.geometry.name
    fields = [(c, str(gdf[c].dtype)) for c in gdf.columns if c != geomcol]
    gtype = gdf.geom_type.dropna().iloc[0] if len(gdf) else 'Unknown'
    rows = []
    for i, r in gdf.iterrows():
        d = {c: (None if (r[c] is None or (isinstance(r[c], float) and math.isnan(r[c]))) else r[c])
             for c, _ in fields}
        g = r[geomcol]
        d['_xy'] = _coords(mapping(g)) if g is not None else None
        d['_fid'] = i
        rows.append(d)
    return fields, gtype, rows


def ro_connect(path):
    """GPKG 를 읽기 전용으로 연다(원본·백업 대조 때 저널 파일도 만들지 않게)."""
    import sqlite3
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def find_build(cfg, a):
    if getattr(a, 'build', None):
        return Path(a.build)
    if a.outdir or a.delivery:
        name = a.outdir or find_delivery(cfg, a.delivery).name
        p = cfg.path_of(cfg.get('outputs.root', '_산출')) / STEP_DIR / name / 'build.json'
        if p.exists():
            return p
        raise SystemExit('build.json 이 없다: %s — p2_build.py 를 먼저 돌릴 것' % p)
    raise SystemExit('--build 또는 -d/--outdir 로 build.json 위치를 줄 것')


# ───────────────────────────────────────────── 잡동사니

def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


def polylen(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def midpoint(pts):
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    tot = sum(math.dist(*s) for s in segs)
    half, acc = tot / 2, 0.0
    for a, b in segs:
        d = math.dist(a, b)
        if acc + d >= half:
            r = (half - acc) / d if d else 0
            return (a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r)
        acc += d
    return pts[-1]


def circle(x, y, d_mm, n=32):
    r = d_mm / 2000.0
    p = [(round(x + r * math.cos(2 * math.pi * i / n), 4), round(y + r * math.sin(2 * math.pi * i / n), 4))
         for i in range(n)]
    p.append(p[0])
    return [p]


def match_prefix(name, prefixes):
    return (not prefixes) or any((name or '').startswith(p) for p in prefixes)


def by_prefix(name, table, default=None):
    best = None
    for k in table or {}:
        if (name or '').startswith(k) and (best is None or len(k) > len(best)):
            best = k
    return table[best] if best is not None else default


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_csv(path, header, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


def common_args(ap):
    ap.add_argument('-d', '--delivery', default=None,
                    help='납품 폴더(날짜 6자리·폴더명 일부·경로). review_vendor.exchange_dirs 에서 찾는다')
    ap.add_argument('--outdir', default=None, help='산출 하위폴더 이름(기본: 납품 폴더명 또는 DXF 이름)')
    kitconfig.add_config_arg(ap)
    return ap
