# -*- coding: utf-8 -*-
"""40-review-vendor 공통 모듈 (qgis-water-sewer-kit 판).

설계 원칙: **회차가 바뀔 때 고칠 것이 없어야 한다.**
  - 설정은 프로젝트 루트의 `sewer.config.json` 하나다(kitconfig). 이 스킬이 보는 곳:
      review_vendor.*            납품 폴더 위치·이름 규칙·입력 패턴·종단 서식·메모·허용차·준공 기준일
      layers.plan_pipe / plan_manhole / plan_pump / asbuilt_manhole   (GPKG 또는 PostGIS)
      fields.<레이어>.<논리필드>  실제 필드명
      values.kind_pressure       '압송' 코드값
      design.pressure_max_d_mm   이 관경 이하면 압송
      outputs.root               산출물 → <outputs.root>/40-review/<납품폴더명>/
  - 납품 폴더는 폴더명 일부만 주면 찾고, 생략하면 가장 최신 `YYMMDD … from …` 폴더를 쓴다.
  - 납품물(평면도/종단면도/.flo/.OUT)은 폴더 안을 재귀 탐색해서 찾는다(중첩 중복 폴더 대응).

쓰는 쪽:
    import vendorlib as V
    ctx = V.context()                       # 최신 납품 폴더
    ctx = V.context('YYMMDD')               # 날짜로
    ctx = V.context('지구명 … 2차')        # 폴더명 일부로
    ctx = V.context(r'<경로>\\그 폴더')       # 경로 직접
"""
import os, sys, io, re, json, glob, sqlite3, struct, math, collections, decimal
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
else:                                        # 구형 파이썬/리다이렉트 대비
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

STEP_DIR = '40-review'                       # 산출 = <outputs.root>/40-review/<납품폴더명>
PLAN_KEYS = ('plan_pipe', 'plan_manhole', 'plan_pump')   # --gpkg 로 갈아끼우는 설계 레이어

# 설정에 키가 빠졌을 때 쓰는 값(원래 서식 기준). 실제 값은 sewer.config.json review_vendor 절.
DEFAULT_DELIVERY = r'^(\d{6})\s*\(?\d*\)?\s*from'
DEFAULT_PATTERNS = {'plan': ['평면도.dxf', '*평면*.dxf'], 'profile': ['종단면도.dxf', '*종단*.dxf'],
                    'flo': ['*.flo'], 'out': ['*.OUT'], 'dwg': ['*.dwg']}
DEFAULT_PROFILE = dict(row_pitch=1000, label_y_lo=100, label_y_hi=160,
                       bar_layer='j_table3', bar_points=4, bar_flat_tol=20,
                       name_pattern=r'^[A-Z]+[-A-Z0-9]*-\d+$',
                       len_label_pattern=r'^L=([0-9.]+)M$', len_label_y_lo=123, len_label_y_hi=128,
                       pressure_word='압송')
DEFAULT_MEMO = dict(layers=['j_none'], min_text_height=20)
DEFAULT_TOL = dict(length_m=0.06, invert_m=0.05, node_snap_m=12.0)
DEFAULT_ASBUILT_NOTE = ('기준 DB 에 이 지구 준공 맨홀이 없을 수 있다. 다른 준공 레이어에 있으면 '
                        'review_vendor.asbuilt_asof 를 비우고 layers.asbuilt_manhole 로 대조하거나, '
                        '12-asbuilt-ingest 로 먼저 적재할 것.')


# ───────────────────────────────────────────── 설정 · 경로

def load_config(path=None):
    """sewer.config.json → kitconfig.Config. 찾는 순서: --config → SEWER_CONFIG → 현재 폴더에서 위로."""
    cfg = kitconfig.load(path)
    if not cfg.root.is_dir():
        raise SystemExit('프로젝트 루트를 못 찾았다: %s\n'
                         '%s 의 project.root 를 확인할 것.' % (cfg.root, cfg.path))
    cfg.require('review_vendor.exchange_dirs')
    return cfg


def rv(cfg, key, default=None):
    """review_vendor.<key>"""
    return cfg.get('review_vendor.' + key, default)


def profile_conf(cfg):
    P = dict(DEFAULT_PROFILE)
    P.update(rv(cfg, 'profile') or {})
    return P


def memo_conf(cfg):
    M = dict(DEFAULT_MEMO)
    M.update(rv(cfg, 'memo') or {})
    return M


def tol(cfg, key):
    return (rv(cfg, 'tolerance') or {}).get(key, DEFAULT_TOL[key])


def pressure_dmax(cfg):
    return cfg.get('design.pressure_max_d_mm', 100)


def kind_pressure(cfg):
    return cfg.value('kind_pressure', '압송')


def _norm(s):
    return re.sub(r'[\s,\.()]+', '', s).lower()


def deliveries(cfg):
    """납품(수신) 폴더 목록을 최신순으로."""
    pat = re.compile(rv(cfg, 'delivery_pattern') or DEFAULT_DELIVERY)
    out = []
    for ex in rv(cfg, 'exchange_dirs') or []:
        d = cfg.path_of(ex)
        if not d or not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_dir():
                continue
            m = pat.match(p.name)
            if m:
                out.append((m.group(1), p))
    out.sort(key=lambda t: (t[0], t[1].name), reverse=True)
    return [p for _, p in out]


def find_delivery(key=None, cfg=None):
    cfg = cfg or load_config()
    cands = deliveries(cfg)
    if key:
        p = Path(key)
        if p.is_dir():
            return p.resolve()
        hit = [c for c in cands if _norm(key) in _norm(c.name)]
        if not hit:
            raise SystemExit('납품 폴더를 못 찾았다: %r\n현재 목록:\n  %s'
                             % (key, '\n  '.join(c.name for c in cands[:15])))
        return hit[0]
    if not cands:
        raise SystemExit('납품 폴더가 없다. %s 의 review_vendor.exchange_dirs·delivery_pattern 확인.\n'
                         '  exchange_dirs = %s' % (cfg.path.name, rv(cfg, 'exchange_dirs')))
    return cands[0]


def collect_inputs(delivery, cfg=None):
    """납품 폴더 안에서 평면도/종단면도/.flo/.OUT 을 재귀 탐색.

    파일명은 회차·지구마다 다르다 → review_vendor.input_patterns 의 **앞 패턴부터**
    찾아보고 처음 걸리는 것을 쓴다. 중첩 중복 폴더는 경로가 짧은 쪽.
    """
    pats = (rv(cfg, 'input_patterns') if cfg is not None else None) or DEFAULT_PATTERNS

    def pick(key):
        for pattern in pats.get(key, DEFAULT_PATTERNS.get(key, [])):
            hits = [p for p in delivery.rglob(pattern) if p.is_file()]
            best = {}
            for p in sorted(hits, key=lambda q: (len(q.parts), str(q))):
                best.setdefault((p.name.lower(), p.stat().st_size), p)
            if best:
                return sorted(best.values(), key=lambda q: (len(q.parts), str(q)))
        return []

    plan, prof = pick('plan'), pick('profile')
    return {
        'delivery': delivery,
        'plan': plan[0] if plan else None,
        'profile': prof[0] if prof else None,
        'plan_all': plan,
        'profile_all': prof,
        'flo': pick('flo'),
        'out': pick('out'),
        'dwg': pick('dwg'),
    }


def resolve_gpkg(g, cfg):
    """--gpkg 인자 → 절대경로. 상대경로는 현재 폴더, 없으면 프로젝트 루트 기준."""
    if not g:
        return None
    p = Path(g)
    if not p.is_absolute() and not p.exists():
        p = cfg.path_of(g)
    p = p.resolve()
    if not p.is_file():
        raise SystemExit('--gpkg 파일이 없다: %s' % p)
    return p


def context(key=None, cfg_path=None, out_sub=None, gpkg=None):
    cfg = load_config(cfg_path)
    delivery = find_delivery(key, cfg)
    ctx = collect_inputs(delivery, cfg)
    ctx['cfg'] = cfg
    ctx['gpkg_override'] = resolve_gpkg(gpkg, cfg)
    ctx['plan_src'] = Layer(ctx, 'plan_pipe').label
    ctx['outdir'] = cfg.out_dir(STEP_DIR, out_sub or delivery.name)
    return ctx


def argctx(단계, extra=None):
    """공통 CLI. 모든 A* 스크립트가 같은 인자를 받는다.

        python a2_len.py                      # 최신 납품 폴더
        python a2_len.py -d YYMMDD            # 날짜·폴더명 일부·경로
        python a2_len.py --gpkg <백업.gpkg>    # 설계 레이어를 그 회차 백업에서
        python a2_len.py --config <sewer.config.json>
    """
    import argparse
    ap = argparse.ArgumentParser(description=단계)
    ap.add_argument('-d', '--delivery', default=None,
                    help='납품 폴더(날짜 6자리·폴더명 일부·경로). 생략하면 최신 from 폴더')
    ap.add_argument('--gpkg', default=None,
                    help='설계 레이어(plan_pipe/plan_manhole/plan_pump)를 이 GPKG 에서 같은 '
                         '레이어명으로 읽는다 — 그 회차 백업과 대조할 때')
    kitconfig.add_config_arg(ap)
    ap.add_argument('--outdir', default=None, help='산출 하위폴더 이름(기본: 납품 폴더명)')
    if extra:
        extra(ap)
    a = ap.parse_args()
    ctx = context(a.delivery, a.config, a.outdir, a.gpkg)
    ctx['args'] = a
    banner(ctx, 단계)
    return ctx


def banner(ctx, 단계):
    print('=' * 78)
    print('[%s]  %s' % (단계, ctx['delivery'].name))
    print('  평면도   : %s' % (ctx['plan'].name if ctx['plan'] else '없음'))
    print('  종단면도 : %s' % (ctx['profile'].name if ctx['profile'] else '없음'))
    print('  flo/OUT  : %s / %s' % (', '.join(p.name for p in ctx['flo']) or '없음',
                                    ', '.join(p.name for p in ctx['out']) or '없음'))
    print('  설계DB   : %s%s' % (ctx['plan_src'], '  (--gpkg)' if ctx.get('gpkg_override') else ''))
    print('  산출      : %s' % ctx['outdir'])
    print('=' * 78)


def save(ctx, name, obj):
    p = ctx['outdir'] / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n저장 %s' % p)
    return p


# ───────────────────────────────────────────── 관번호

def rowof(y, pitch):
    """종단면도 행 번호. 행 기준선 y = row*pitch, 그 위로 도면과 메모 띠가 올라간다.
    → **floor** 로 잡아야 메모가 다음 행으로 밀리지 않는다. 기준선의 -0.0 오차는 eps 로 흡수."""
    import math
    return int(math.floor(y / pitch + 1e-6))


def mother(no):
    """압송 세분구간(-00801) → 모관번호(-008). 그 외는 그대로."""
    m = re.match(r'^(.*-)(\d{3})(\d{2,})$', no or '')
    return m.group(1) + m.group(2) if m else no


# ───────────────────────────────────────────── DXF

def walk(doc, layout, handler, ox=0.0, oy=0.0, depth=0, maxdepth=3):
    """블록 INSERT 를 재귀 전개하며 (엔티티, 누적 오프셋) 을 handler 에 넘긴다."""
    for e in layout:
        try:
            t = e.dxftype()
            handler(e, t, ox, oy)
            if t == 'INSERT':
                for a in getattr(e, 'attribs', []):
                    handler(a, 'ATTRIB', ox, oy)
                if depth < maxdepth:
                    walk(doc, doc.blocks[e.dxf.name], handler, ox + e.dxf.insert[0],
                         oy + e.dxf.insert[1], depth + 1, maxdepth)
        except Exception:
            continue


def profile_bars(dxf_path, cfg, verbose=True):
    """종단면도 관번호 막대 → {관번호: 연장}, 그리고 행별 진단.

    막대 = bar_layer 의 4점 LWPOLYLINE(국소 y 가 label_y_lo~hi, 납작한 것).
    라벨 = 같은 y 띠의 TEXT/ATTRIB 중 관번호 패턴.
    행(row) = floor(y / row_pitch). 행 안에서 x 순으로 라벨↔막대를 1:1 짝짓는다.
    """
    import ezdxf
    P = profile_conf(cfg)
    pitch = P['row_pitch']
    pat = re.compile(P['name_pattern'])
    lpat = re.compile(P.get('len_label_pattern') or r'^L=([0-9.]+)M$')
    LLO = P.get('len_label_y_lo', 123)
    LHI = P.get('len_label_y_hi', 128)
    texts, bars, llab = [], [], []

    def h(e, t, ox, oy):
        if t in ('TEXT', 'ATTRIB'):
            y = e.dxf.insert[1] + oy
            s = (e.dxf.text or '').strip()
            if P['label_y_lo'] < (y % pitch) < P['label_y_hi'] and pat.match(s):
                texts.append((rowof(y, pitch), e.dxf.insert[0] + ox, s))
            elif LLO <= (y % pitch) <= LHI:
                m = lpat.match(s)
                if m:
                    llab.append((rowof(y, pitch), e.dxf.insert[0] + ox, float(m.group(1))))
        elif t == 'LWPOLYLINE' and e.dxf.layer == P['bar_layer']:
            pts = e.get_points('xy')
            if len(pts) == P['bar_points']:
                ys = [p[1] + oy for p in pts]
                if max(ys) - min(ys) < P['bar_flat_tol'] \
                        and P['label_y_lo'] < (min(ys) % pitch) < P['label_y_hi']:
                    xs = sorted(p[0] + ox for p in pts)
                    bars.append((rowof(min(ys), pitch), round(xs[0], 2), round(xs[-1], 2)))

    doc = ezdxf.readfile(str(dxf_path))
    walk(doc, doc.modelspace(), h)
    bars = sorted(set(bars))
    texts = sorted(set(texts))
    llab = sorted(set(llab))
    rows = sorted(set(t[0] for t in texts) | set(b[0] for b in bars))
    length, label_len, diag, unpaired = {}, {}, [], []
    for r in rows:
        tt = sorted([t for t in texts if t[0] == r], key=lambda t: t[1])
        bb = sorted([b for b in bars if b[0] == r], key=lambda b: b[1])
        ll = sorted([q for q in llab if q[0] == r], key=lambda q: q[1])
        ok = len(tt) == len(bb)
        diag.append(dict(row=r, 라벨=len(tt), 막대=len(bb), L라벨=len(ll),
                         짝짓기='OK' if ok else '불일치'))
        if ok:
            for t, b in zip(tt, bb):
                length[t[2]] = round(b[2] - b[1], 2)
            if len(ll) == len(tt):          # 도면에 글자로 적힌 연장
                for t, q in zip(tt, ll):
                    label_len[t[2]] = q[2]
        else:
            unpaired.append(r)
    if verbose:
        print('종단 라벨 %d / 막대 %d / L라벨 %d / 행 %d'
              % (len(texts), len(bars), len(llab), len(rows)))
        for d in diag:
            print('   row %d : 라벨 %3d / 막대 %3d / L라벨 %3d  %s'
                  % (d['row'], d['라벨'], d['막대'], d['L라벨'], d['짝짓기']))
        if unpaired:
            print('   ★ 행 %s 은 라벨·막대 수가 달라 연장을 못 뽑았다 '
                  '(시트 끝 잘림·이중작도 확인).' % unpaired)
        print('종단 연장 확보 %d구간 (도면 기재 L= 라벨 %d구간)' % (len(length), len(label_len)))
    return dict(length=length, label_len=label_len, diag=diag, unpaired=unpaired,
                texts=texts, bars=bars, llab=llab, rows=rows)


# ───────────────────────────────────────────── flo / OUT

def read_flo(path):
    """makesw .flo → [{up,dn,L,ILu,ILd,D,Q,GL,tokens,raw}]"""
    rows = []
    for ln in Path(path).read_text(encoding='cp949', errors='replace').splitlines()[2:]:
        if not ln.strip():
            continue
        tk = ln.split()
        if len(tk) < 9:
            continue
        try:
            L, ILu, ILd = float(tk[2]), float(tk[5]), float(tk[6])
        except ValueError:
            continue
        md = re.search(r'\b0\s+(\d{2,4})\b', ln)
        mq = re.search(r'Q=([\d.]+)', ln)
        try:
            GL = float(tk[7])
        except (ValueError, IndexError):
            GL = None
        rows.append(dict(up=tk[0], dn=tk[1], L=L, ILu=ILu, ILd=ILd, GL=GL,
                         D=md.group(1) if md else None,
                         Q=float(mq.group(1)) if mq else None,
                         tokens=[(p, float(a), float(b), float(c)) for p, a, b, c in
                                 re.findall(r'([xXG])=([\d.]+),([\d.-]+),([\d.]+)', ln)],
                         tail=tk[8:], raw=ln.rstrip()))
    return rows


def read_all_flo(ctx):
    return {p.name: read_flo(p) for p in ctx['flo']}


def read_out(path):
    """makesw .OUT → {관번호: {head, 판정, nums, 2nd}}

    2행 숫자 인덱스: 0 누가면적 1 평균 2 관저고 3 지반고 4 경사 5 강우강도
                    6 만관유속 7 유속max 8 허용유속 9 실유속 10 오수량 11 수심
    1행 6번째 실수 = 만관유량.
    """
    NUM = re.compile(r'(-?\d+\.\d+)')
    rows, cur = {}, None
    for ln in Path(path).read_text(encoding='cp949', errors='replace').splitlines():
        m = re.match(r'\s+([A-Z]+-[A-Z0-9]+-\d+)\s+([A-Z]+-[A-Z0-9]+-\d+|방류-\d+)(.*)$', ln)
        if m:
            cur = m.group(1)
            rest = m.group(3)
            jm = re.search(r'(LESS0\.6|NO GOOD|UPPR3\.0|GOOD|FULL)', rest)
            rows[cur] = dict(head=rest.strip(), 판정=jm.group(1) if jm else None,
                             head_nums=NUM.findall(rest))
        elif cur and re.match(r'\s+([A-Z]+-[A-Z0-9]+-\d+|방류-\d+)\s', ln):
            rows[cur]['2nd'] = ln.strip()
            rows[cur]['nums'] = NUM.findall(ln)
            cur = None
    return rows


def read_all_out(ctx):
    return {p.name: read_out(p) for p in ctx['out']}


# ───────────────────────────────────────────── 도형 (GPKG BLOB · WKB)

_ENV = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _wkb(buf, off):
    """WKB(ISO·EWKB, 2D/Z/M) 한 도형 → (정점 리스트, 다음 오프셋). 모르는 타입은 (None, off)."""
    end = '<' if buf[off] == 1 else '>'
    typ = struct.unpack_from(end + 'I', buf, off + 1)[0]
    o = off + 5
    dims = 2
    if typ & 0x20000000:                     # EWKB SRID
        o += 4
    if typ & 0x80000000:                     # EWKB Z
        dims += 1
    if typ & 0x40000000:                     # EWKB M
        dims += 1
    typ &= 0x0FFFFFFF
    if typ >= 1000:                          # ISO Z/M/ZM
        dims += {1: 1, 2: 1, 3: 2}.get(typ // 1000, 0)
        typ %= 1000
    step = 8 * dims
    if typ == 1:
        return [struct.unpack_from(end + 'dd', buf, o)], o + step
    if typ == 2:
        n = struct.unpack_from(end + 'I', buf, o)[0]
        o += 4
        return [struct.unpack_from(end + 'dd', buf, o + step * i) for i in range(n)], o + step * n
    if typ == 3:
        nr = struct.unpack_from(end + 'I', buf, o)[0]
        o, pts = o + 4, []
        for _ in range(nr):
            n = struct.unpack_from(end + 'I', buf, o)[0]
            o += 4
            pts += [struct.unpack_from(end + 'dd', buf, o + step * i) for i in range(n)]
            o += step * n
        return pts, o
    if typ in (4, 5, 6, 7):                  # Multi* / GeometryCollection → 정점을 이어 붙인다
        n = struct.unpack_from(end + 'I', buf, o)[0]
        o, pts = o + 4, []
        for _ in range(n):
            p, o = _wkb(buf, o)
            if p is None:
                return None, o
            pts += p
        return pts, o
    return None, o


def wkb_points(buf, off=0):
    """WKB → 정점 리스트 (Point/LineString/Polygon 과 그 Multi*)."""
    if buf is None:
        return None
    try:
        return _wkb(bytes(buf), off)[0]
    except (struct.error, IndexError):
        return None


def gpkg_points(blob):
    """GPKG geometry BLOB → 정점 리스트."""
    if blob is None:
        return None
    env = (blob[3] >> 1) & 7
    if env not in _ENV:
        return None
    return wkb_points(blob, 8 + _ENV[env])


def centroid(pts):
    """정점 리스트의 외접사각형 중심."""
    if not pts:
        return None
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def polyline_length(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) if pts else None


# ───────────────────────────────────────────── 설계·준공 레이어 (GPKG / PostGIS)

def _q(name):
    return '"%s"' % str(name).replace('"', '""')


def _num(v):
    """PostGIS numeric(Decimal) → float. 그 외는 그대로."""
    return float(v) if isinstance(v, decimal.Decimal) else v


def col(cfg, key, logical, required=True):
    """fields.<key>.<logical> → SQL 열 표현. 필수가 아니고 매핑이 없으면 NULL."""
    if required:
        return _q(cfg.field(key, logical))
    f = cfg.get('fields.%s.%s' % (key, logical))
    return _q(f) if f else 'NULL'


def fname(cfg, key, logical):
    """매핑된 실제 필드명 또는 None."""
    return cfg.get('fields.%s.%s' % (key, logical)) or None


class Layer:
    """layers.<key> 읽기 — GPKG 는 sqlite3, PostGIS 는 psycopg2(kitconfig.pg_connect).

    SQL 틀:  {T} = 테이블, {G} = 도형열(GPKG BLOB / PostGIS WKB), '?' = 자리표시자.
    --gpkg 가 주어지면 plan_* 레이어는 그 GPKG 에서 같은 레이어명으로 읽는다.
    """

    def __init__(self, ctx, key):
        cfg = ctx['cfg']
        L = dict(cfg.layer(key))
        ov = ctx.get('gpkg_override')
        if ov and key in PLAN_KEYS:
            L = {'kind': 'gpkg', 'path': Path(ov), 'layer': L['layer']}
        self.cfg, self.key, self.spec = cfg, key, L
        self.kind, self.name = L['kind'], L['layer']
        if self.kind == 'gpkg':
            self.label = '%s · %s' % (Path(L['path']).name, self.name)
        else:
            self.label = 'PostGIS %s.%s' % (L['schema'], self.name)

    def rows(self, tmpl, params=()):
        if self.kind == 'gpkg':
            p = Path(self.spec['path'])
            if not p.is_file():
                raise SystemExit('layers.%s: GPKG 가 없다 → %s' % (self.key, p))
            con = sqlite3.connect(str(p))
            try:
                g = con.execute('select column_name from gpkg_geometry_columns where table_name=?',
                                (self.name,)).fetchone()
                sql = tmpl.format(T=_q(self.name), G=_q(g[0] if g else 'geom'))
                return con.execute(sql, params).fetchall()
            finally:
                con.close()
        sql = tmpl.format(T='%s.%s' % (_q(self.spec['schema']), _q(self.name)),
                          G='ST_AsBinary(%s)' % _q(self.spec['geom']))
        sql = sql.replace('%', '%%').replace('?', '%s')
        cn = self.cfg.pg_connect()
        try:
            cur = cn.cursor()
            cur.execute(sql, tuple(params))
            return cur.fetchall()
        finally:
            cn.close()

    def points(self, g):
        if g is None:
            return None
        return gpkg_points(g) if self.kind == 'gpkg' else wkb_points(g)


def plan_routes(ctx):
    """계획관로 → {관번호: dict(L, D, ILu, ILd, GL, 구분, 지구, 자료기준일, A, B)}

    필수 필드: pipe_id. length_m 매핑이 없으면 도형 길이를 쓴다. 나머지는 없으면 None.
    """
    cfg, k = ctx['cfg'], 'plan_pipe'
    has_len = bool(fname(cfg, k, 'length_m'))
    cols = [col(cfg, k, 'pipe_id'), col(cfg, k, 'length_m', False), col(cfg, k, 'dia_mm', False),
            col(cfg, k, 'us_invert', False), col(cfg, k, 'ds_invert', False),
            col(cfg, k, 'us_ground', False), col(cfg, k, 'kind', False),
            col(cfg, k, 'district', False), col(cfg, k, 'asof', False)]
    L = Layer(ctx, k)
    out = {}
    for no, Ln, D, ILu, ILd, GL, gb, zone, asof, geom in L.rows(
            'select %s, {G} from {T}' % ', '.join(cols)):
        p = L.points(geom) or []
        if not has_len:
            Ln = round(polyline_length(p), 2) if len(p) > 1 else None
        Ln, D, ILu, ILd, GL = map(_num, (Ln, D, ILu, ILd, GL))
        out[no] = dict(L=Ln, D=D, ILu=ILu, ILd=ILd, GL=GL, 구분=gb, 지구=zone,
                       자료기준일=asof, A=p[0] if p else None, B=p[-1] if p else None)
    return out


def asbuilt_manholes(ctx):
    """A4 기준 DB → ([(이름, (x,y), GL, 유출관저고, 심도)], 설명)

    review_vendor.asbuilt_asof 가 있으면 계획맨홀 레이어(plan_manhole)에서 그 자료기준일로
    준공 편입분을 거른다. 비어 있으면 layers.asbuilt_manhole 을 쓴다 — 거기에 out_invert 매핑이
    없으면 유출관저고 = 지반고 − 맨홀깊이.
    """
    cfg = ctx['cfg']
    asof = rv(cfg, 'asbuilt_asof') or ''
    out = []
    if asof:
        k = 'plan_manhole'
        L = Layer(ctx, k)
        sql = 'select %s, {G}, %s, %s, %s from {T} where %s=?' % (
            col(cfg, k, 'mh_id'), col(cfg, k, 'ground', False), col(cfg, k, 'out_invert'),
            col(cfg, k, 'depth', False), col(cfg, k, 'asof'))
        for nm, geom, GL, IL, dep in L.rows(sql, (asof,)):
            GL, IL, dep = map(_num, (GL, IL, dep))
            c = centroid(L.points(geom))
            if c:
                out.append((nm, c, GL, IL, dep))
        return out, '%s 중 %s=%r' % (L.label, fname(cfg, k, 'asof'), asof)

    k = 'asbuilt_manhole'
    if not cfg.has_layer(k):
        raise SystemExit('A4 기준 DB 가 없다 — review_vendor.asbuilt_asof 도 layers.asbuilt_manhole 도 '
                         '비어 있다 (%s).' % cfg.path.name)
    L = Layer(ctx, k)
    has_il = bool(fname(cfg, k, 'out_invert'))
    sql = 'select %s, {G}, %s, %s, %s from {T}' % (
        col(cfg, k, 'mh_id'), col(cfg, k, 'ground', False),
        col(cfg, k, 'out_invert', False), col(cfg, k, 'depth', False))
    for nm, geom, GL, IL, dep in L.rows(sql):
        GL, IL, dep = map(_num, (GL, IL, dep))
        if not has_il:
            IL = round(GL - dep, 3) if (GL is not None and dep is not None) else None
        c = centroid(L.points(geom))
        if c:
            out.append((nm, c, GL, IL, dep))
    return out, '%s%s' % (L.label, '' if has_il else ' (유출관저고 = 지반고 − 맨홀깊이)')


def nearest(pt, items, limit=3):
    """items = [(이름, (x,y), …)] → 거리순 상위."""
    return sorted(((math.dist(pt, it[1]),) + tuple(it) for it in items))[:limit]
