# -*- coding: utf-8 -*-
"""02 — 경량 DXF → 지장물 폴리곤/중심선/맨홀 GPKG 빌드  (실무 회차에서 검증된 빌더 그대로)

  A. 도면상 이미 닫힌 경계 → 끝점 5 cm 스냅 + 끝점→선분 스냅 후 polygonize
  B. 열린 평행 2선(관벽) → 체인 쌍 매칭으로 관로 폴리곤 자동 폐합
  C. 남는 단선 → 규격 텍스트 최빈 폭(없으면 기본 0.3 m)으로 flat 버퍼
  규격 텍스트(D600 / %%C600 / U0.6x0.5 / U형측구06x06 / Box 2@3.0X2.0 ...) → 속성
  분류: 배수시설물 → '기타지장물', S.오수맨홀·S.정화조 → '하수(기존)'

사용
  python -X utf8 02_build_poly.py [--config sewer.config.json] [--src <경량.dxf>] [--out <빌드.gpkg>]
         [--prev <기존 접미>] [--suf <새 접미>] [--no-dedup] [--label <원본 도면명>] [--write]
    --src   기본 <outputs.root>/11-survey/survey_light.dxf (01 의 출력)
    --out   기본 survey.build_gpkg (없으면 <outputs.root>/11-survey/지장물_빌드.gpkg).
            '기존 빌드 레이어가 들어 있는 GPKG' — 그 안의 --prev 레이어와 겹치는 지물을 떨어내
            신규분만 남긴다(누적본 납품 대응). --prev 를 안 주면 중복제거를 건너뛴다.
    --suf   새로 쓸 레이어 접미(기본 v1) → 지장물_폴리곤_<suf> 등
    --label 속성 '출처' 에 남길 원본 도면 이름(기본: --src 파일명)
    --write 없으면 집계만 출력(미리보기).

★ 이 단계 결과에는 '추측'이 섞여 있다. 바로 쓰지 말고 03~06 을 돌릴 것.
   · C 단계의 단선 버퍼 → 폭이 틀릴 수 있다(03·04)
   · 라벨 인접 승계 → 엉뚱한 규격이 붙을 수 있다(04)
   · 생성방식은 `관경산출` 필드에 남는다(05)
"""
import ezdxf, io, sys, re, math, collections, os
import numpy as np
import geopandas as gpd
from shapely.geometry import Polygon, Point, LineString, MultiLineString
from shapely.ops import unary_union, polygonize_full, linemerge, snap, substring
from shapely.strtree import STRtree
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kit import argval, out_path, build_gpkg, epsg, LIGHT_DXF   # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')
CRS = 'EPSG:%d' % epsg()
SRC = None      # 기본: <산출>/survey_light.dxf
OUT = None      # 기본: survey.build_gpkg — 기존 레이어가 있는 GPKG(중복제거 기준)
PREV = None     # 보존할 기존(수작업 보정) 레이어 접미 — 이것과 겹치는 지물은 떨어낸다
SUF = 'v1'      # 새로 쓸 레이어 접미
# ── 인자 치환은 반드시 여기(사용 전)에서 한다
if '--src' in sys.argv: SRC = sys.argv[sys.argv.index('--src') + 1]
if '--out' in sys.argv: OUT = sys.argv[sys.argv.index('--out') + 1]
if '--prev' in sys.argv: PREV = sys.argv[sys.argv.index('--prev') + 1]
if '--suf' in sys.argv: SUF = sys.argv[sys.argv.index('--suf') + 1]
SRC = SRC or out_path(LIGHT_DXF)
OUT = OUT or build_gpkg()
LABEL = argval('--label') or os.path.basename(SRC)    # 속성 '출처'
NODEDUP = '--no-dedup' in sys.argv or not PREV
print('[경로] 원도 %s\n       빌드 GPKG %s (기존 %s → 새 %s)' % (SRC, OUT, PREV or '-', SUF))
SNAP = 0.05          # 끝점끼리 / 끝점->선분 스냅 허용오차
W_MIN, W_MAX = 0.10, 6.5
OVR = 0.25
PIPE_LAYERS = ('배수시설물', '배수시설물-매립')
STD = [100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800,
       900, 1000, 1100, 1200, 1350, 1500, 1650, 1800]


def load(path):
    """AC1032 DXF: 헤더는 ANSI_949 지만 실제 UTF-8. 310(바이너리 미리보기) 태그는 제거."""
    # 310 태그는 filter_dxf.py 에서 제거된 경량본 사용(메모리 절약)
    return ezdxf.readfile(path)


# ---------------------------------------------------------------- 규격 텍스트 파서 (v2 동일)
def _n(s):
    if s is None:
        return None
    s = s.strip()
    if '.' in s:
        return float(s)
    if len(s) == 2 and s[0] == '0':
        return float(s) / 10.0
    return float(s)


NUM = r'\d+(?:\.\d+)?'
RE_D = re.compile(r'^(?:%%%%C|D)(\d{2,4})(?:[Hh]=?(%s))?$' % NUM, re.I)
RE_WH = re.compile(r'^(U형측구|U형수로|U형|J형측구|J형|V형측구|V형|BOX|박스|암거|라멘교|교량|U|J|V)?'
                   r'(?:(\d)@)?(%s)[xX*×](%s)(?:[xX*×](%s))?(?:[Hh]=?(%s))?$'
                   % (NUM, NUM, NUM, NUM), re.I)
RE_H = re.compile(r'^([UJV]형측구|V\.H|[Hh])[Hh]?=?(%s)?$' % NUM, re.I)
RE_BR = re.compile(r'^\S{2,6}교$')
_KIND = [('U형수로', 'U형수로'), ('U형측구', 'U형측구'), ('J형측구', 'J형측구'),
         ('V형측구', 'V형측구'), ('U형', 'U형측구'), ('J형', 'J형측구'), ('V형', 'V형측구'),
         ('라멘교', '암거/교량'), ('교량', '암거/교량'), ('암거', '암거/교량'),
         ('박스', '박스'), ('BOX', '박스'), ('U', 'U형측구'), ('J', 'J형측구'), ('V', 'V형측구')]


def _kind_of(head):
    if not head:
        return '암거/교량'
    h = head.upper()
    for k, v in _KIND:
        if h.startswith(k.upper()):
            return v
    return '미상'


def parse_spec(t):
    if not t:
        return (None, None, None, None, None)
    s = t.strip().replace(' ', '').replace('..', '.')
    m = RE_D.match(s)
    if m:
        return ('원형관', int(m.group(1)) / 1000.0,
                _n(m.group(2)) if m.group(2) else None, int(m.group(1)), None)
    m = RE_WH.match(s)
    if m:
        kind = _kind_of(m.group(1))
        cells = int(m.group(2)) if m.group(2) else None
        w, h = _n(m.group(3)), _n(m.group(4))
        if m.group(5):
            h = _n(m.group(5))
        if m.group(6):
            h = _n(m.group(6))
        if cells:
            w = w * cells
        return (kind, w, h, None, cells)
    m = RE_H.match(s)
    if m:
        kind = _kind_of(m.group(1))
        if kind == '미상' or m.group(1).upper() == 'H':
            kind = '깊이표기'
        return (kind, None, _n(m.group(2)) if m.group(2) else None, None, None)
    if RE_BR.match(s):
        return ('암거/교량', None, None, None, None)
    return (None, None, None, None, None)


# ---------------------------------------------------------------- 0. DXF 로드
doc = load(SRC)
msp = doc.modelspace()
raw = []                                   # (layer, [pts])
for e in msp:
    lay = e.dxf.get('layer', '')
    if lay not in PIPE_LAYERS:
        continue
    if e.dxftype() == 'LINE':
        raw.append((lay, [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]))
    elif e.dxftype() == 'LWPOLYLINE':
        pts = [(p[0], p[1]) for p in e.get_points('xy')]
        if e.closed and len(pts) > 2:
            pts = pts + [pts[0]]
        if len(pts) > 1:
            raw.append((lay, pts))
print('[입력] 폴리라인/선 %d개, 연장 %.0fm' % (len(raw), sum(LineString(p).length for _, p in raw)))

# ---------------------------------------------------------------- 1. 끝점 스냅 (끝점끼리 -> 클러스터 평균)
ends = np.array([p for _, pts in raw for p in (pts[0], pts[-1])])
pairs = cKDTree(ends).query_pairs(SNAP, output_type='ndarray')
n = len(ends)
mtx = (coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
       if len(pairs) else coo_matrix((n, n)))
nc, lab = connected_components(mtx, directed=False)
cnt = np.bincount(lab, minlength=nc)
rep = np.stack([np.bincount(lab, weights=ends[:, k], minlength=nc) / cnt for k in range(2)], 1)
chains = []                                # (layer, LineString) 원본 폴리라인 단위
for i, (lay, pts) in enumerate(raw):
    pts = list(pts)
    pts[0] = tuple(rep[lab[2 * i]]); pts[-1] = tuple(rep[lab[2 * i + 1]])
    g = LineString(pts)
    if g.length > 1e-6:
        chains.append((lay, g))
merged_ends = int((cnt > 1).sum())
print('[스냅1] 끝점끼리 %.0fmm -> 병합 클러스터 %d' % (SNAP * 1000, merged_ends))

# ---- 끝점 -> 다른 선분(중간)에 스냅 : T 접합이 살짝 떠 있는 경우
all_u = unary_union([g for _, g in chains])
tree = STRtree([g for _, g in chains])
fixed = 0
chains2 = []
for lay, g in chains:
    pts = list(g.coords)
    for k in (0, -1):
        p = Point(pts[k])
        best, bd = None, SNAP
        for j in tree.query(p.buffer(SNAP)):
            h = chains[int(j)][1]
            if h.equals(g):
                continue
            d = h.distance(p)
            if 1e-9 < d < bd:
                best, bd = h, d
        if best is not None:
            q = best.interpolate(best.project(p))
            pts[k] = (q.x, q.y); fixed += 1
    chains2.append((lay, LineString(pts)))
chains = chains2
print('[스냅2] 끝점->선분 스냅 %d개' % fixed)
lay_union = {L: unary_union([g for l, g in chains if l == L]) for L in PIPE_LAYERS}

# ---------------------------------------------------------------- 2. 폐합 폴리곤 (A)
u = unary_union([g for _, g in chains])
polys, dangles, cuts, inv = polygonize_full(u)
rings = [p for p in polys.geoms if p.area >= 0.05]
print('[A] 폐합 폴리곤 %d개 (면적<0.05 슬리버 %d 제외)' % (len(rings), len(polys.geoms) - len(rings)))
rt = STRtree(rings)
drop = set()
for i, p in enumerate(rings):
    for j in rt.query(p):
        j = int(j)
        if j == i or j in drop:
            continue
        if p.contains(rings[j].buffer(-0.001)) and p.area > rings[j].area * 1.5:
            drop.add(i)
rings = [p for i, p in enumerate(rings) if i not in drop]
print('    중첩 외곽링 %d개 제거 -> %d개' % (len(drop), len(rings)))

# ---------------------------------------------------------------- 3. 링에 안 쓰인 선 -> 체인 병합 -> 쌍 매칭 (B)
used = unary_union([p.exterior for p in rings]).buffer(0.03)
rest = collections.defaultdict(list)
for lay, g in chains:
    d = g.difference(used)
    if d.is_empty:
        continue
    for part in (d.geoms if hasattr(d, 'geoms') else [d]):
        if part.length > 0.12:
            rest[lay].append(part)
rchains = []                                 # (layer, LineString) linemerge 결과
for lay, gs in rest.items():
    m = linemerge(unary_union(gs))
    for part in (m.geoms if hasattr(m, 'geoms') else [m]):
        if part.length > 0.12:
            rchains.append((lay, part))
print('[B] 링에 안 쓰인 선 -> 체인 %d개 / %.0fm' % (len(rchains), sum(g.length for _, g in rchains)))

rgeom = [g for _, g in rchains]
rtree = STRtree(rgeom)
STEP = 0.25


def samples(g):
    n_ = max(2, int(g.length / STEP) + 1)
    ds = np.linspace(0, g.length, n_)
    return ds, [g.interpolate(d) for d in ds]


cands = []
for i in sorted(range(len(rgeom)), key=lambda k: -rgeom[k].length):
    gi = rgeom[i]
    ds, ps = samples(gi)
    for j in rtree.query(gi.buffer(W_MAX + 0.2)):
        j = int(j)
        if j == i:
            continue
        gj = rgeom[j]
        if gj.length > gi.length + 1e-9 or (abs(gj.length - gi.length) < 1e-9 and j < i):
            continue                          # 긴 쪽을 기준(i)으로 한 번만
        dist = np.array([gj.distance(p) for p in ps])
        ok = (dist >= W_MIN) & (dist <= W_MAX)
        # 연속 구간(run) 찾기: 폭이 일정한 구간
        k = 0
        while k < len(ds):
            if not ok[k]:
                k += 1; continue
            s = k
            while k + 1 < len(ds) and ok[k + 1] and abs(dist[k + 1] - dist[k]) < 0.12:
                k += 1
            e = k; k += 1
            run_len = ds[e] - ds[s]
            if run_len < max(0.3, OVR * min(gi.length, gj.length)):
                continue
            w = float(np.median(dist[s:e + 1]))
            if np.std(dist[s:e + 1]) > max(0.06, 0.12 * w):
                continue
            # 기준 체인 i 의 구간, 상대 체인 j 는 투영 구간
            sub_i = substring(gi, ds[s], ds[e])
            tj = sorted([gj.project(Point(sub_i.coords[0])), gj.project(Point(sub_i.coords[-1]))])
            if tj[1] - tj[0] < 0.2:
                continue
            sub_j = substring(gj, tj[0], tj[1])
            ci, cj = list(sub_i.coords), list(sub_j.coords)
            # 방향 정렬: j 를 i 와 반대 방향으로 이어 붙임
            if Point(ci[-1]).distance(Point(cj[-1])) < Point(ci[-1]).distance(Point(cj[0])):
                cj = cj[::-1]
            poly = Polygon(ci + cj)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != 'Polygon' or poly.area < 0.05:
                continue
            # 폭 검증: 면적/길이 ≈ 폭
            if abs(poly.area / max(sub_i.length, 1e-6) - w) > 0.35 * w + 0.05:
                continue
            cands.append({'w': w, 'poly': poly, 'lay': rchains[i][0], 'score': run_len,
                          'li': sub_i, 'lj': sub_j})

cands.sort(key=lambda d: -d['score'])
kept = []
for c in cands:
    dup = False
    for k in kept:
        if k['poly'].intersects(c['poly']):
            ia = k['poly'].intersection(c['poly']).area
            if ia > 0.55 * min(k['poly'].area, c['poly'].area):
                dup = True; break
    if not dup:
        kept.append(c)
rr = STRtree(rings)
kept2 = []
for c in kept:
    ov = sum(c['poly'].intersection(rings[int(j)]).area for j in rr.query(c['poly']))
    if ov < 0.5 * c['poly'].area:
        kept2.append(c)
print('[B] 체인쌍 후보 %d -> 채택 %d (폐합폴리곤과 중복 %d 제외)' % (len(cands), len(kept2), len(kept) - len(kept2)))

# ---------------------------------------------------------------- 4. 속성 부여
texts = [t for t in msp.query('TEXT') if t.dxf.layer in ('TEXT', '배수시설물')]
tp = np.array([[t.dxf.insert.x, t.dxf.insert.y] for t in texts])
tt = [t.dxf.text.strip() for t in texts]
tspec = [parse_spec(s) for s in tt]
ok = [i for i, s in enumerate(tspec) if s[0]]
tp_ok, ti_ok = tp[ok], np.array(ok)
ktree = cKDTree(tp_ok)
print('[텍스트] 규격 파싱 성공 %d / %d' % (len(ok), len(tt)))


def short_long(pg):
    """굽은 띠 폴리곤도 맞도록 둘레·면적식으로 폭 추정 (P=2L+2w, A=Lw).
    식이 안 풀리면(면형 블록) MRR 단변으로 대체."""
    c = list(pg.minimum_rotated_rectangle.exterior.coords)
    e = [math.dist(c[i], c[i + 1]) for i in range(4)]
    P, A = pg.exterior.length, pg.area
    disc = P * P - 16 * A
    if disc >= 0:
        w = (P - math.sqrt(disc)) / 4
        L = A / w if w > 1e-6 else max(e)
        if w <= min(e) * 1.05 + 1e-6:
            return w, L
    return min(e), max(e)


def centerline(pg):
    c = list(pg.minimum_rotated_rectangle.exterior.coords)[:4]
    e = sorted([(math.dist(c[i], c[(i + 1) % 4]), i) for i in range(4)])
    i, j = e[-1][1], e[-2][1]
    a, b = c[i], c[(i + 1) % 4]
    a2, b2 = c[j], c[(j + 1) % 4]
    return LineString([((a[0] + b2[0]) / 2, (a[1] + b2[1]) / 2),
                       ((b[0] + a2[0]) / 2, (b[1] + a2[1]) / 2)])


def curve_centerline(c):
    li, lj = c['li'], c['lj']
    n_ = max(2, int(li.length / 0.5) + 1)
    pts = []
    for d in np.linspace(0, li.length, n_):
        p = li.interpolate(d); q = lj.interpolate(lj.project(p))
        pts.append(((p.x + q.x) / 2, (p.y + q.y) / 2))
    return LineString(pts)


def layer_of(pg):
    b = pg.exterior.buffer(0.05)
    best, bl = None, 0.0
    for L, gu in lay_union.items():
        try:
            l = gu.intersection(b).length
        except Exception:
            l = 0.0
        if l > bl:
            best, bl = L, l
    return best or '배수시설물'


def near_text(pg, w0, r=12.0):
    cen = pg.representative_point()
    idx = ktree.query_ball_point([cen.x, cen.y], r)
    if not idx:
        return None, (None, None, None, None, None)
    best, bs = None, -1e9
    for k in idx:
        i = int(ti_ok[k])
        kind, w, h, dmm, cells = tspec[i]
        d = math.hypot(tp[i][0] - cen.x, tp[i][1] - cen.y)
        s = -d
        if w:
            s += 6.0 * math.exp(-abs(w - w0) / 0.15)
        else:
            s -= 3.0
        if s > bs:
            best, bs = i, s
    return tt[best], tspec[best]


# ---- 폭 4.5m 초과 체인쌍은 박스/암거 표기가 있을 때만 인정 (떨어진 두 측구를 한 관로로 오인 방지)
_k2 = []
for c in kept2:
    if c['w'] <= 4.5:
        _k2.append(c); continue
    _, (k_, sw_, _, _, _) = near_text(c['poly'], c['w'])
    if k_ in ('박스', '암거/교량') and sw_ and sw_ >= 0.7 * c['w']:
        _k2.append(c)
print('[B] 폭>4.5m 체인쌍 %d개 중 박스/암거 표기 확인 %d개만 채택' % (sum(1 for c in kept2 if c['w'] > 4.5),
                                                          sum(1 for c in _k2 if c['w'] > 4.5)))
kept2 = _k2

# ---- B2. 짝이 없는 단선(관로 중심선 1선 표기) -> 규격 텍스트 폭으로 버퍼 폐합
paired = unary_union([p for p in rings] + [c['poly'] for c in kept2]).buffer(0.03)
ptree_tmp = STRtree(rings + [c['poly'] for c in kept2])
all_poly_tmp = rings + [c['poly'] for c in kept2]
DEF_W = 0.30                                   # 규격 텍스트 없는 단선의 기본 폭
singles = []
for lay, g in rchains:
    d = g.difference(paired)
    if d.is_empty:
        continue
    for part in (d.geoms if hasattr(d, 'geoms') else [d]):
        if part.length < 2.0:
            continue
        ds_, ps_ = samples(part)
        # 이미 만든 폴리곤 옆에 붙어 달리는 선(3선 평행의 바깥선·관벽 잔재) 은 제외
        near = 0
        cand_p = [all_poly_tmp[int(j)] for j in ptree_tmp.query(part.buffer(1.2))]
        if cand_p:
            cu = unary_union(cand_p)
            near = sum(1 for p_ in ps_ if cu.distance(p_) < 1.2)
        if near > 0.6 * len(ps_):
            continue
        singles.append((lay, part))
print('[B2] 단선(짝 없음) %d개 / %.0fm -> 규격 폭 버퍼' % (len(singles), sum(g.length for _, g in singles)))


def line_text(g, r=8.0):
    """선에서 r m 이내 규격 텍스트 중 최빈 (종류,폭) 채택, 동률이면 가까운 것"""
    b = g.buffer(r)
    minx, miny, maxx, maxy = b.bounds
    sel = np.where((tp_ok[:, 0] >= minx) & (tp_ok[:, 0] <= maxx) & (tp_ok[:, 1] >= miny) & (tp_ok[:, 1] <= maxy))[0]
    votes = collections.defaultdict(list)
    for k in sel:
        i = int(ti_ok[k])
        pt = Point(tp[i]); d = g.distance(pt)
        if d > r:
            continue
        kind, w, h, dmm, cells = tspec[i]
        if not w:
            continue
        votes[(kind, w, h, dmm, cells)].append((d, tt[i]))
    if not votes:
        return None, (None, None, None, None, None)
    key = max(votes, key=lambda k_: (len(votes[k_]), -min(d for d, _ in votes[k_])))
    tag = min(votes[key])[1]
    return tag, key


rows, cls = [], []
for src_kind, items in (('폐합(도면)', [(p, None) for p in rings]),
                        ('자동폐합(체인쌍)', [(c['poly'], c) for c in kept2])):
    for pg, c in items:
        if c is None:
            w0, l0 = short_long(pg)
            cl_ = centerline(pg)
        else:
            w0, l0 = c['w'], c['li'].length
            cl_ = curve_centerline(c)
        tag, (kind, sw, sh, dmm, cells) = near_text(pg, w0)
        if kind == '원형관':
            if dmm and abs(dmm / 1000.0 - w0) / max(w0, 1e-6) < 0.25:
                dia, csrc = dmm, '도면표기'
            else:
                s = min(STD, key=lambda v: abs(v - w0 * 1000))
                dia, csrc = ((s, '실측폭(규격보정)') if abs(s - w0 * 1000) / (w0 * 1000) < 0.12
                             else (int(round(w0 * 1000)), '실측폭'))
        else:
            dia, csrc = None, ('도면표기' if kind else None)
        if w0 > W_MAX + 0.1 and not (kind == '박스' and sw and w0 <= sw * 1.3):
            kind, csrc = '면형(확인필요)', None
        rows.append({
            '지장물번호': None, '분류': '기타지장물',
            '종류': kind or '미상', '표기': tag,
            '규격_폭m': sw, '규격_깊이m': sh, '연수': cells,
            '관경mm': dia, '관경산출': csrc,
            '실측폭m': round(w0, 3), '실측장변m': round(l0, 2), '면적m2': round(pg.area, 2),
            '구분': '매립' if layer_of(pg) == '배수시설물-매립' else '현행',
            '생성방식': src_kind, '출처': LABEL,
            'geometry': pg,
        })
        cls.append(cl_)
for lay, g in singles:
    tag, (kind, sw, sh, dmm, cells) = line_text(g)
    w0 = sw if sw else DEF_W
    pg = g.buffer(w0 / 2, cap_style=2)
    if pg.geom_type != 'Polygon':
        pg = max(pg.geoms, key=lambda p_: p_.area)
    rows.append({
        '지장물번호': None, '분류': '기타지장물',
        '종류': kind or '미상', '표기': tag,
        '규격_폭m': sw, '규격_깊이m': sh, '연수': cells,
        '관경mm': dmm if kind == '원형관' else None,
        '관경산출': ('도면표기(단선버퍼)' if sw else '추정(단선 기본폭 %.1fm)' % DEF_W),
        '실측폭m': round(w0, 3), '실측장변m': round(g.length, 2), '면적m2': round(pg.area, 2),
        '구분': '매립' if lay == '배수시설물-매립' else '현행',
        '생성방식': '단선버퍼', '출처': LABEL,
        'geometry': pg,
    })
    cls.append(g)
for i, r in enumerate(rows):
    r['지장물번호'] = 'J%04d' % (i + 1)

# ---- 라벨 전파 (인접 승계)
geo = [r['geometry'] for r in rows]
gt = STRtree(geo)
adj = collections.defaultdict(list)
for i, p in enumerate(geo):
    for j in gt.query(p.buffer(1.5)):
        j = int(j)
        if j <= i:
            continue
        wi, wj = rows[i]['실측폭m'], rows[j]['실측폭m']
        if abs(wi - wj) > 0.15 * max(wi, wj):
            continue
        if p.distance(geo[j]) > 1.5:
            continue
        adj[i].append(j); adj[j].append(i)
NO_SEED = ('미상', '깊이표기', '면형(확인필요)')
seed = [i for i, r in enumerate(rows) if r['종류'] not in NO_SEED and r['규격_폭m']]
dist_ = {i: 0 for i in seed}
q = collections.deque(seed)
prop = 0
while q:
    i = q.popleft()
    if dist_[i] >= 6:
        continue
    for j in adj[i]:
        if j in dist_:
            continue
        dist_[j] = dist_[i] + 1
        src = rows[i]
        rows[j].update({'종류': src['종류'], '표기': src['표기'],
                        '규격_폭m': src['규격_폭m'], '규격_깊이m': src['규격_깊이m'],
                        '연수': src['연수'], '관경mm': src['관경mm'],
                        '관경산출': (src['관경산출'] or '').replace('(인접승계)', '') + '(인접승계)'})
        prop += 1
        q.append(j)
print('[라벨전파] 미상 -> 인접 승계 %d개' % prop)

# ---------------------------------------------------------------- 5. 하수(기존) 심볼 -> 원형 폴리곤
sew = []
for e in msp.query('INSERT'):
    lay = e.dxf.layer
    if lay not in ('S.오수맨홀', 'S.정화조'):
        continue
    kind = '오수맨홀' if lay == 'S.오수맨홀' else '정화조'
    r0 = 0.725 if kind == '오수맨홀' else 0.75
    sc = abs(e.dxf.xscale) or 1.0
    r = r0 * sc
    sew.append({'지장물번호': None, '분류': '하수(기존)', '종류': kind, '표기': e.dxf.name,
                '규격_폭m': round(2 * r, 2), '규격_깊이m': None, '연수': None,
                '관경mm': None, '관경산출': None,
                '실측폭m': round(2 * r, 3), '실측장변m': round(2 * r, 2),
                '면적m2': round(math.pi * r * r, 2), '구분': '현행',
                '생성방식': '심볼(블록 %s x%.2f)' % (e.dxf.name, sc), '출처': LABEL,
                'X': round(e.dxf.insert.x, 3), 'Y': round(e.dxf.insert.y, 3),
                'geometry': Point(e.dxf.insert.x, e.dxf.insert.y).buffer(r, 16)})
for i, r in enumerate(sew):
    r['지장물번호'] = 'S%04d' % (i + 1)
print('[하수(기존)] 심볼 %d개 %s' % (len(sew), dict(collections.Counter(r['종류'] for r in sew))))

gdf = gpd.GeoDataFrame(rows, crs=CRS)
print('[속성] 기타지장물 폴리곤 %d개' % len(gdf))
print('  종류 %s' % dict(collections.Counter(gdf['종류'])))
print('  구분 %s / 생성방식 %s' % (dict(collections.Counter(gdf['구분'])), dict(collections.Counter(gdf['생성방식']))))
sdf = gpd.GeoDataFrame(sew, crs=CRS)

# ---------------------------------------------------------------- 5b. 상세 맨홀 (말풍선 + LEADER) -> 맨홀 폴리곤
T0 = []
for e in msp.query('TEXT'):
    if e.dxf.layer != '상세':
        continue
    al, p1, p2 = e.get_placement(); pp = p2 if p2 else p1
    T0.append((pp[0], pp[1], e.dxf.text.strip()))
T0xy = np.array([[a, b] for a, b, _ in T0]) if T0 else np.zeros((0, 2))
ttree = cKDTree(T0xy)
LEAD = []
for e in msp.query('LEADER'):
    v = list(e.vertices)
    if len(v) >= 2:
        LEAD.append(((v[0][0], v[0][1]), (v[-1][0], v[-1][1])))   # (화살표끝, 말풍선쪽)
ltail = np.array([t for _, t in LEAD]) if LEAD else np.zeros((0, 2))
ltree = cKDTree(ltail)
ballo = [e for e in msp.query('CIRCLE') if e.dxf.layer == '상세' and abs(e.dxf.radius - 3.75) < 0.05]


def _num(t):
    try:
        return float(str(t).split(':')[-1])
    except Exception:
        return None


def mh_size(kind, dmm, w):
    if kind == '박스' and w:
        return round(w, 2)
    if not dmm:
        return 0.9
    return 0.9 if dmm <= 600 else 1.2 if dmm <= 900 else 1.5 if dmm <= 1200 else 1.8


mh = []
for b in ballo:
    cx, cy = b.dxf.center.x, b.dxf.center.y
    idx = ttree.query_ball_point([cx, cy], 4.2)
    spec = in_t = out_t = None
    others = []
    for k in idx:
        t = T0[k][2]
        if t.startswith('IN:'):
            in_t = t
        elif t.startswith('OUT:'):
            out_t = t
        elif t.upper().startswith(('%%C', 'BOX', 'D')) or RE_WH.match(t.replace(' ', '')):
            spec = t
        else:
            others.append(t)
    kind, sw, sh, dmm, cells = parse_spec(spec) if spec else (None, None, None, None, None)
    if spec and not kind and '관측불가' in spec:
        kind = '원형관'
    # 실위치: 말풍선 중심 6m 이내 LEADER 꼬리 -> 화살표 끝
    d, li = ltree.query([cx, cy]) if len(LEAD) else (99, -1)
    if d < 6.0:
        px, py = LEAD[li][0]; src = 'LEADER'
    else:
        px, py = cx, cy; src = '말풍선(LEADER없음)'
    ksz = mh_size(kind, dmm, sw)
    geom = (Point(px, py).buffer(ksz / 2, 16) if kind != '박스'
            else Polygon([(px - ksz / 2, py - ksz / 2), (px + ksz / 2, py - ksz / 2),
                          (px + ksz / 2, py + ksz / 2), (px - ksz / 2, py + ksz / 2)]))
    mh.append({'맨홀번호': None, '분류': '기타지장물',
               '관종형': '박스' if kind == '박스' else '원형' if kind == '원형관' else '미상',
               '관경mm': dmm, '관경_원문': spec, '규격_폭m': sw, '규격_깊이m': sh,
               'IN_EL': _num(in_t) if in_t else None, 'OUT_EL': _num(out_t) if out_t else None,
               'IN_원문': in_t, 'OUT_원문': out_t, '기타표기': ' '.join(others) or None,
               '위치출처': src, '맨홀규격m': ksz, 'X': round(px, 3), 'Y': round(py, 3),
               '출처': LABEL, 'geometry': geom})
for i, r in enumerate(mh):
    r['맨홀번호'] = 'MH%03d' % (i + 1)
mdf = gpd.GeoDataFrame(mh, crs=CRS)
print('[맨홀] 말풍선 %d개 -> 맨홀 %d개 %s / 위치 %s' % (len(ballo), len(mdf), dict(collections.Counter(mdf['관종형'])),
                                             dict(collections.Counter(mdf['위치출처']))))

# ---------------------------------------------------------------- 6. 중심선 / 잔여선
cl = gdf.copy(); cl['geometry'] = cls
pgun = unary_union(list(gdf.geometry)).buffer(0.03)
leftover = []
for lay, g in rchains:
    d = g.difference(pgun)
    if d.is_empty:
        continue
    for part in (d.geoms if hasattr(d, 'geoms') else [d]):
        if part.length > 0.5:
            leftover.append({'구분': '매립' if lay == '배수시설물-매립' else '현행',
                             '연장m': round(part.length, 2), 'geometry': part})
lo = gpd.GeoDataFrame(leftover, crs=CRS) if leftover else None
tot = sum(LineString(p).length for _, p in raw)
print('[C] 잔여선 %d개 / %.0fm (원본 %.0fm 의 %.1f%%)' % (len(leftover), sum(r['연장m'] for r in leftover), tot,
                                                   100 * sum(r['연장m'] for r in leftover) / tot))


# ---------------------------------------------------------------- 6b. 기존(--prev) 빌드와 중복 제거
# 측량 납품은 누적본이다(새 회차 도면이 지난 회차 내용을 포함한다). 기존 빌드(--prev)는 수작업
# 보정본이므로 보존하고, 새 빌드(--suf)에서는 기존에 이미 있는 지물을 떨어내 신규분만 남긴다.
V3 = OUT                      # 기존 보정본은 항상 원본 GPKG 에서 읽는다
_v3p = _v3h = _v3m = None
if not NODEDUP:
    try:
        _v3p = gpd.read_file(V3, layer='지장물_폴리곤_%s' % PREV)
        _v3h = gpd.read_file(V3, layer='하수기존_시설_%s' % PREV)
        _v3m = gpd.read_file(V3, layer='지장물_맨홀_%s' % PREV)
    except Exception as _e:
        print('[중복제거] 기존 %s 레이어를 못 읽어 건너뜀 (%s)' % (PREV, _e))
        _v3p = None

def _drop_dup_poly(df, ref, frac=0.3):
    """v3 와 겹침비(교집합/작은쪽 면적) > frac 이면 중복으로 본다.
    v3 는 ㄷ자 보정·골격선 교체 등 수작업 보정이 들어가 IoU 기준으로는 안 잡힌다."""
    if len(df) == 0: return df, []
    tree = STRtree(list(ref.geometry))
    keep = []
    for g in df.geometry:
        dup = False
        for k in tree.query(g):
            h = ref.geometry.iloc[int(k)]
            it = g.intersection(h).area
            if it > 0 and it / min(g.area, h.area) > frac: dup = True; break
        keep.append(not dup)
    return df[keep].reset_index(drop=True), keep


def _drop_dup_pt(df, ref, tol=0.6):
    if len(df) == 0: return df
    R = np.array([[p.centroid.x, p.centroid.y] for p in ref.geometry]) if len(ref) else np.zeros((0, 2))
    if not len(R): return df
    t = cKDTree(R)
    d, _ = t.query(np.array([[p.centroid.x, p.centroid.y] for p in df.geometry]), k=1)
    return df[d > tol].reset_index(drop=True)

if _v3p is not None:
    _n0 = len(gdf)
    gdf, _keep = _drop_dup_poly(gdf, _v3p)
    cl = cl[_keep].reset_index(drop=True)
    print('[중복제거] 기타지장물 폴리곤 %d -> %d (%s 중복 %d 제거)' % (_n0, len(gdf), PREV, _n0 - len(gdf)))
    _n0 = len(sdf); sdf = _drop_dup_pt(sdf, _v3h)
    print('[중복제거] 하수(기존) %d -> %d' % (_n0, len(sdf)))
    _n0 = len(mdf); mdf = _drop_dup_pt(mdf, _v3m)
    print('[중복제거] 맨홀 %d -> %d' % (_n0, len(mdf)))
else:
    print('[중복제거] 건너뜀 (기존 레이어 없음 또는 --no-dedup)')
# 잔여선도 기존 폴리곤/잔여선 영역과 겹치면 제외
if _v3p is not None and lo is not None and len(lo):
    _u = unary_union(list(_v3p.geometry)).buffer(1.5)
    _keep2 = [not (g.intersects(_u) and g.intersection(_u).length > 0.5 * g.length) for g in lo.geometry]
    _n0 = len(lo); lo = lo[_keep2].reset_index(drop=True)
    print('[중복제거] 잔여선 %d -> %d' % (_n0, len(lo)))
    if len(lo) == 0: lo = None
# 번호 재부여
gdf['지장물번호'] = ['J%04d' % (i + 1) for i in range(len(gdf))]
cl['지장물번호'] = list(gdf['지장물번호'])
if len(sdf): sdf['지장물번호'] = ['S%04d' % (i + 1) for i in range(len(sdf))]
if len(mdf): mdf['맨홀번호'] = ['MH%03d' % (i + 1) for i in range(len(mdf))]

# 기존(--prev) 레이어와 동일한 필드 구성 유지: IN_EL/OUT_EL 은 기존 레이어에 수작업으로 추가된
# 컬럼이라 빌더가 안 만든다.
_ORD = (list(_v3p.columns.drop('geometry')) if _v3p is not None
        else list(gdf.columns.drop('geometry')))
for _df in (gdf, cl):
    for _c in ('IN_EL', 'OUT_EL'):
        if _c not in _df.columns: _df[_c] = np.nan
        _df[_c] = _df[_c].astype('float64')
print('[필드정렬] 기존(%s) 기준 %d개 컬럼' % (PREV or '새 빌드', len(_ORD)))
gdf = gdf[_ORD + ['geometry']]
cl = cl[_ORD + ['geometry']]

# ------------------------------------------- 6c. 근거 약한 폴리곤은 '확인필요' 로 분리
# 생성 경로별 근거의 세기가 다르다.
#   폐합(도면)·체인쌍(평행 2선) → 강함. 도면이 관의 양쪽 벽을 그려놨다는 뜻.
#   단선버퍼·추정(기본폭)       → 약함. 근거가 "그 선이 배수시설물 레이어에 있다"뿐이다.
# 약한 것을 같은 레이어에 섞으면 경계선·구조물선이 지장물로 올라간다
# (사례: 교량 옆 강·도로 경계선이 암거 라벨까지 상속받아 지장물로 들어가 있었다).
# 그래서 빌드 단계에서 갈라 쓴다. 라벨이 자기 선에 밀착한 것, 설계사 종단과 규격이
# 일치하는 것은 11_split_unverified.py --promote 로 지장물 쪽으로 되돌린다.
_weak = gdf['관경산출'].fillna('').str.contains('단선버퍼|추정')
gdf_w, cl_w = gdf[_weak].reset_index(drop=True), cl[_weak].reset_index(drop=True)
gdf, cl = gdf[~_weak].reset_index(drop=True), cl[~_weak].reset_index(drop=True)
print('[근거분리] 지장물 %d (폐합·체인쌍) / 확인필요 %d (단선버퍼·추정)' % (len(gdf), len(gdf_w)))
print('           확인필요는 11_split_unverified.py --promote 로 검증분만 되돌릴 것')

# ---------------------------------------------------------------- 7. 저장 (기존 GPKG 에 레이어 추가)
if '--write' in sys.argv:
    import fiona
    for L in tuple('%s_%s' % (b, SUF) for b in
                   ('지장물_폴리곤', '지장물_중심선', '지장물_미폐합_잔여선', '하수기존_시설',
                    '지장물_맨홀', '지장물_확인필요_폴리곤', '지장물_확인필요_중심선')):
        if os.path.exists(OUT) and L in fiona.listlayers(OUT):
            fiona.remove(OUT, layer=L, driver='GPKG')
    gdf.to_file(OUT, layer='지장물_폴리곤_%s' % SUF, driver='GPKG')
    cl.to_file(OUT, layer='지장물_중심선_%s' % SUF, driver='GPKG')
    if len(gdf_w):
        gdf_w.to_file(OUT, layer='지장물_확인필요_폴리곤_%s' % SUF, driver='GPKG')
        cl_w.to_file(OUT, layer='지장물_확인필요_중심선_%s' % SUF, driver='GPKG')
    if lo is not None:
        lo.to_file(OUT, layer='지장물_미폐합_잔여선_%s' % SUF, driver='GPKG')
    sdf.to_file(OUT, layer='하수기존_시설_%s' % SUF, driver='GPKG')
    mdf.to_file(OUT, layer='지장물_맨홀_%s' % SUF, driver='GPKG')
    print('저장 완료 ->', OUT)
