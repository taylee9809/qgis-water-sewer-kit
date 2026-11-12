# -*- coding: utf-8 -*-
"""[1단계] 긴 스트립 종단면도 DXF → A3 시트 DXF(+ 시트목록 JSON)

설계사 종단면도(모델공간에 계통들이 가로로 길게 이어진 스트립, 계통마다 표제 블록 INSERT)를
A3 여러 장으로 자른다. 각 장을 종이 좌표(mm)로 바꿔 420×297 도곽과 함께 가로로 늘어놓으므로
CAD 에서 도곽을 1:1 로 플롯하면 그대로 A3 이고, 2단계(a3_pdf.py)는 렌더만 한다.

- 전 장 통일 축척: 사다리(profile.a3.scale_denoms)에서 모든 계통이 성립하는 가장 큰 축척
  (`--scale 1200` 으로 고정 가능, 안 되면 자동으로 내려간다).
- 페이지 가로를 꽉 채운다: 계통이 길면 잘라 다음 장, 계통이 끝나면 남은 폭에 다음 계통을
  이어 붙인다. 행(지구)이 바뀌면 새 장. 빈 장엔 통째로 들어가는 짧은 계통은 자르지 않고 넘긴다.
  `--no-pack` 이면 계통마다 새 장.
- 기준고(Δ)는 상단(인출 글자)·하단(밴드) 제약을 동시에 걸어 잘림 없이 잡는다. 표척 눈금값은
  Δ 만큼 다시 쓴다.
- 한 DXF 에 지구를 여러 행으로 쌓은 도면은 표제 블록 y 로 행을 자동으로 찾아 한 줄로 이어
  붙인다(`--rows` 로 일부 행만).
- 제목: 장마다 「리명 + 번호 + 지구 | 관번호 범위」. 리명은 `ri_map.py` 가 만든 관번호→리 JSON.

사용:
  python a3_split.py <종단면도.dxf> [--ri-map ri_map.json] [--scale 1200] [--rows 0,2] [--vk 0.6667]
  python a3_split.py <종단면도.dxf> --probe          # 서식 진단만(행·계통·레이어·관번호 글자)
출력: <out>/<이름>_A3시트.dxf, <이름>_A3시트.json
"""
import argparse
import bisect
import collections
import math
import re
import time

import profcommon as PC

_T0 = time.time()


def T(msg):
    print('  [%6.1fs] %s' % (time.time() - _T0, msg), flush=True)


# ───────────────────────────────────────────── 파라미터
class P:
    """profile 설정을 풀어 둔 상수 묶음 (모듈 전역 대신)."""

    def __init__(self, S, args):
        st, a3 = S['strip'], S['a3']
        L = st['layers']
        self.S = S
        self.PAGE_W, self.PAGE_H = a3['page']
        self.MARGIN_L, self.MARGIN_R, self.MARGIN_B, self.MARGIN_T = a3['margin']
        self.US_W = self.PAGE_W - self.MARGIN_L - self.MARGIN_R
        self.US_H = self.PAGE_H - self.MARGIN_B - self.MARGIN_T
        self.SHEET_GAP = a3['sheet_gap']
        self.FRAME_TOP = st['frame_top']
        self.BAND_TOP = st['band_top']
        self.BAND_LAYERS = set(L['band'])
        self.BAND_LEFT = st['band_left']
        self.PROF_FLOOR = st['floor']
        self.STATION_Y = st['station_y']
        self.POLE_TOP = st['pole_top']
        self.POLE_DX = st['pole_label_dx']
        self.TITLE_W = st['title_w']
        self.TITLE_BLOCK = st['title_block']
        self.UNIT_H, self.UNIT_V = st['unit_h'], st['unit_v']
        self.VK = float(args.vk if args.vk is not None else a3['vk'])
        self.VK_BASE = a3['vk_base']
        self.DENOMS = list(a3['scale_denoms'])
        self.SCALES = [self.UNIT_H / q for q in self.DENOMS]
        self.SEG_GAP = a3['seg_gap']
        self.DELTA_STEP = a3['delta_step']
        self.TOP_PAD, self.BOT_PAD = a3['top_pad'], a3['bot_pad']
        self.PIPE_RE = re.compile(args.pipe_pattern or st['pipe_pattern'])
        self.PROF_LAYERS = set(L['profile'])
        self.ANNOT_LAYERS = set(L['annot'])
        self.MEMO = L['memo']
        self.STATION = L['station']
        self.TEXT_LAYER = L['text']
        self.PUMP_KEY = a3['pump_key']
        self.PUMP_MAG = a3['pump_mag']
        self.PUMP_RENAME = dict(a3['pump_rename'] or {})
        self.JOIN_KEY = a3['join_key']
        self.SUFFIX = a3['district_suffix']
        self.ROW_LABELS = dict(a3['row_labels'] or {})
        self.RI_COLOR = {k: PC.hex_rgb(v) for k, v in (a3['ri_colors'] or {}).items()}
        self.NO_PACK = bool(args.no_pack)
        self.SPOT_BELOW = bool(args.spot_below)


L_FRAME, L_TITLE, L_POLE, L_SPOT, L_PUMP, L_JOIN = (
    'A3_FRAME', 'A3_TITLE', 'A3_POLE', 'A3_SPOT', 'A3_PUMP', 'A3_JOIN')
SPOT_H, SPOT_UP, SPOT_DOWN = 5.6, 14.0, 10.0
PUMP_DX = 7.0


# ───────────────────────────────────────────── 행(지구) 정규화
def detect_rows(p, ents):
    """표제 블록 INSERT 의 y 로 행을 찾는다. 반환: [dict(name, y0, lo, hi)] (y 오름차순)."""
    ys = sorted(e.dxf.insert.y for e in ents
                if e.dxftype() == 'INSERT' and e.dxf.name == p.TITLE_BLOCK)
    if not ys:
        raise SystemExit('표제 블록 INSERT "%s" 가 없다 — 계통 경계를 못 잡는다. '
                         'profile.strip.title_block 을 확인(--probe).' % p.TITLE_BLOCK)
    cl = [[ys[0]]]
    for y in ys[1:]:
        if y - cl[-1][-1] > 100.0:
            cl.append([])
        cl[-1].append(y)
    y0s = [collections.Counter(round(v, 2) for v in c).most_common(1)[0][0] for c in cl]
    k = p.S['strip']['row_split']
    rows = []
    for i, y0 in enumerate(y0s):
        lo = -1e18 if i == 0 else y0s[i - 1] + (y0 - y0s[i - 1]) * k
        hi = 1e18 if i == len(y0s) - 1 else y0 + (y0s[i + 1] - y0) * k
        rows.append(dict(idx=i, y0=y0, lo=lo, hi=hi, name=None))
    # 행 이름표: 큰 TEXT(높이 ≥ row_label_min_h) 가 그 행 y0 ± 60 안에 있으면
    hmin = p.S['strip']['row_label_min_h']
    for e in ents:
        if e.dxftype() != 'TEXT' or (e.dxf.height or 0) < hmin:
            continue
        y = e.dxf.insert.y
        for r in rows:
            if abs(y - r['y0']) <= 60.0 and not r['name']:
                r['name'] = (e.dxf.text or '').strip()
    for r in rows:
        r['name'] = r['name'] or ('%d행' % (r['idx'] + 1) if len(rows) > 1 else '')
    return rows


def normalize_rows(p, ents, rows, keep=None, names=None):
    """행을 한 줄로: y 를 0 으로 내리고 x 를 행마다 밀어 붙인다. keep 밖 행은 버린다."""
    if names:
        for r, nm in zip(rows, names):
            r['name'] = nm
    sel = [r for r in rows if keep is None or r['idx'] in keep or r['name'] in keep]
    if not sel:
        raise SystemExit('--rows 에 맞는 행이 없다. 행 목록: %s'
                         % ', '.join('%d:%s' % (r['idx'], r['name']) for r in rows))
    los = [r['lo'] for r in rows]

    def row_of(y):
        return max(0, bisect.bisect_right(los, y) - 1)

    by = collections.defaultdict(list)
    for e in ents:
        y = PC.anchor_y(e)
        if y is None:
            continue
        r = rows[row_of(y)]
        by[r['idx']].append(e)
    # x 오프셋: 행마다 앞 행 끝 + 2000
    xr = {}
    for r in sel:
        xs = [PC.anchor_x(e) for e in by[r['idx']]]
        xs = [x for x in xs if x is not None]
        xr[r['idx']] = (min(xs), max(xs)) if xs else (0.0, 0.0)
    out, cur = [], 0.0
    for r in sel:
        x0, x1 = xr[r['idx']]
        r['dx'] = math.ceil((cur - x0) / 1000.0) * 1000.0 if out else 0.0
        for e in by[r['idx']]:
            e.translate(r['dx'], -r['y0'], 0)
        out.extend(by[r['idx']])
        r['n'] = len(by[r['idx']])
        cur = x1 + r['dx'] + 2000.0
        r['xstart'] = x0 + r['dx'] - 1000.0
    return out, sel


def vcompress(p, ents):
    """지형부(y > frame_top)만 세로로 VK 배 압축. 밴드(표)는 그대로."""
    VK, B, FT = p.VK, p.VK_BASE, p.FRAME_TOP

    def cy(y):
        return B + (y - B) * VK if y > FT else y
    n = 0
    for e in ents:
        t = e.dxftype()
        if t == 'LINE':
            a, b = e.dxf.start, e.dxf.end
            e.dxf.start = (a.x, cy(a.y), 0)
            e.dxf.end = (b.x, cy(b.y), 0)
        elif t == 'LWPOLYLINE':
            e.set_points([(x, cy(y)) for x, y in e.get_points('xy')], format='xy')
        elif t in ('TEXT', 'INSERT'):
            q = e.dxf.insert
            e.dxf.insert = (q.x, cy(q.y), 0)
            # ★ 정렬된 TEXT 는 align_point 가 실제 자리 — 같이 옮기지 않으면 글자 상자가
            #   원래 높이까지 늘어난 것으로 잡혀 분할이 되레 나빠진다
            if t == 'TEXT' and e.dxf.hasattr('align_point'):
                q = e.dxf.align_point
                e.dxf.align_point = (q.x, cy(q.y), 0)
            if t == 'TEXT':     # 45° 인출선이 눕는 만큼 글자도 같이 눕힌다
                rr = math.radians(float(e.dxf.rotation or 0.0))
                e.dxf.rotation = math.degrees(math.atan2(math.sin(rr) * VK, math.cos(rr)))
        elif t in ('CIRCLE', 'ELLIPSE'):
            c = e.dxf.center
            e.dxf.center = (c.x, cy(c.y), 0)
        else:
            continue
        n += 1
    return n


def xform(e, sc, bx, by):
    """균등 확대 + 평행이동만 하는 빠른 변환 (ezdxf transform 은 행렬 분해 때문에 느리다)."""
    t = e.dxftype()
    d = e.dxf
    if t == 'LINE':
        a, b = d.start, d.end
        d.start = (a.x * sc + bx, a.y * sc + by, 0)
        d.end = (b.x * sc + bx, b.y * sc + by, 0)
    elif t == 'LWPOLYLINE':
        # 정점 폭(치수선 화살촉)은 버린다 — ezdxf 출력기는 폭 있는 폴리라인의 폭 0 구간을
        # 그리지 않아 관번호 막대 선이 사라진다. 폭을 버리면 가는 선으로 남는다(원 도구와 같은 결과)
        cw = d.const_width
        e.set_points([(q[0] * sc + bx, q[1] * sc + by) for q in e.get_points('xy')], format='xy')
        if cw:
            d.const_width = cw * sc
    elif t == 'TEXT':
        q = d.insert
        d.insert = (q.x * sc + bx, q.y * sc + by, 0)
        if d.hasattr('align_point'):
            q = d.align_point
            d.align_point = (q.x * sc + bx, q.y * sc + by, 0)
        d.height = (d.height or 2.5) * sc
    elif t == 'CIRCLE':
        c = d.center
        d.center = (c.x * sc + bx, c.y * sc + by, 0)
        d.radius = d.radius * sc
    elif t == 'ELLIPSE':
        c, m = d.center, d.major_axis
        d.center = (c.x * sc + bx, c.y * sc + by, 0)
        d.major_axis = (m.x * sc, m.y * sc, 0)
    elif t == 'INSERT':
        q = d.insert
        d.insert = (q.x * sc + bx, q.y * sc + by, 0)
        d.xscale = (d.xscale or 1.0) * sc
        d.yscale = (d.yscale or 1.0) * sc
        d.zscale = (d.zscale or 1.0) * sc
    elif t == 'SOLID':
        for i in range(4):
            k = 'vtx%d' % i
            if d.hasattr(k):
                v = getattr(d, k)
                setattr(d, k, (v.x * sc + bx, v.y * sc + by, 0))


def enlarge_pump(p, ents):
    """맨홀펌프장 라벨을 키운다(분할·축척 계산 전에 해야 자리가 확보된다)."""
    n = 0
    for e in ents:
        if e.dxftype() != 'TEXT' or p.PUMP_KEY not in (e.dxf.text or ''):
            continue
        m = re.search(r'\(([^)]+)\)', e.dxf.text)
        if m and m.group(1) in p.PUMP_RENAME:
            e.dxf.text = e.dxf.text.replace(m.group(0), '(%s)' % p.PUMP_RENAME[m.group(1)])
        e.dxf.height = (e.dxf.height or 2.5) * p.PUMP_MAG
        n += 1
    return n


def pump_hl(e, layer, grow=0.0):
    """글자 뒤에 깔 형광 바탕(글자와 같은 각도의 SOLID)."""
    from ezdxf.entities import factory
    h = e.dxf.height or 2.5
    t = e.dxf.text or ''
    w = sum((1.25 if ord(c) > 0x2000 else 0.74) for c in t) * h * 1.08
    pad = h * (0.16 + grow)
    r = math.radians(e.dxf.rotation or 0.0)
    cs, sn = math.cos(r), math.sin(r)
    q0 = e.dxf.insert
    q = []
    for lx, ly in ((-pad, -0.30 * h), (w + pad, -0.30 * h), (-pad, h + pad), (w + pad, h + pad)):
        q.append((q0.x + lx * cs - ly * sn, q0.y + lx * sn + ly * cs, 0))
    sld = factory.new('SOLID', dxfattribs={'layer': layer})
    for i, v in enumerate(q):
        setattr(sld.dxf, 'vtx%d' % i, v)
    return sld


# ───────────────────────────────────────────── 분할
def collect(p, ents, rows):
    segs_x = sorted(e.dxf.insert.x for e in ents
                    if e.dxftype() == 'INSERT' and e.dxf.name == p.TITLE_BLOCK)
    starts = [r['xstart'] for r in rows]

    def dist_of(x):
        return rows[max(0, bisect.bisect_right(starts, x) - 1)]['name']

    texts = [e for e in ents if e.dxftype() == 'TEXT']
    seg_info = []
    for x0 in segs_x:
        lab = {}
        for e in texts:
            dx = e.dxf.insert.x - x0
            if -45 <= dx <= -6 and abs(dx - p.POLE_DX) < 1.5 and e.dxf.insert.y > 100:
                try:
                    lab[round(e.dxf.insert.y, 2)] = float(e.dxf.text)
                except ValueError:
                    pass
        ys = sorted(lab)
        seg_info.append(dict(x0=x0, dist=dist_of(x0), ylab=ys, vlab=[lab[y] for y in ys]))

    pipes = sorted((e.dxf.insert.x, (e.dxf.text or '').strip()) for e in texts
                   if p.PIPE_RE.match((e.dxf.text or '').strip()))

    frame, float_, station, pumps = [], [], [], []
    for e in ents:
        if e.dxftype() == 'INSERT':
            continue
        if e.dxf.layer == p.MEMO:
            # 메모 레이어: 행 이름표·수정 구름·지시선이 도면 범위를 수백 unit 밖으로 늘려
            # 분할을 깨뜨리므로 통째로 뺀다
            continue
        if e.dxftype() == 'LWPOLYLINE':
            q = e.get_points('xy')
            if q and max(v[1] for v in q) - min(v[1] for v in q) > 300.0:
                if len(q) == 2:     # 높이 300 넘는 세로선 = 맨홀펌프장 강조선(페이지 높이로 다시 그림)
                    pumps.append((q[0][0], e.dxf.const_width or 1.0, e))
                continue
        bb = PC.ent_box(e)
        if bb is None:
            continue
        x1, x2, y1, y2 = bb
        si = max(0, bisect.bisect_right(segs_x, x1 + 40) - 1)
        # ★ 밴드 표 가로선·좌우 테두리는 계통 원점보다 band_left(10) 왼쪽에서 시작한다.
        #   여유를 6 으로 두면 이것들이 통째로 버려져 표 선이 사라진다
        if x1 < segs_x[si] - (p.BAND_LEFT + 2.0):
            continue
        if (e.dxftype() == 'LINE' and e.dxf.layer == p.STATION
                and abs(y1 - p.STATION_Y) < 0.2 and y2 > p.FRAME_TOP):
            station.append((e, si, x1, x2, y2))
        elif y2 <= p.FRAME_TOP:
            frame.append((e, si, x1, x2))
        else:
            float_.append((e, si, x1, x2, y1, y2))
    return segs_x, seg_info, pipes, frame, float_, station, pumps


def plan(p, segs_x, seg_info, frame, float_, station, fixed_scale=None):
    """측점 셀 단위로 미리 집계해 두고(엔티티 재순회 없음 — O(n³) 방지) 분할·축척을 정한다."""
    st_x = {}
    for e, si, x1, x2, y2 in station:
        st_x.setdefault(si, set()).add(round(x1, 3))
    for si in st_x:
        st_x[si] = sorted(st_x[si])

    INF = 1e18
    CELL = {}                       # si -> (xs, G, A, X, R, SY)
    for si, xs in st_x.items():
        n = len(xs) - 1
        if n < 1:
            continue
        CELL[si] = (xs, [INF] * n, [INF] * n, [-INF] * n, [-INF] * n, [-INF] * (n + 1))

    def cell_of(xs, x1):
        k = bisect.bisect_right(xs, x1 + 0.5) - 1
        return min(max(k, 0), len(xs) - 2)

    for e, si, x1, x2, y1, y2 in float_:
        c = CELL.get(si)
        if not c:
            continue
        xs, G, A, X, R, SY = c
        k = cell_of(xs, x1)
        A[k] = min(A[k], y1)
        X[k] = max(X[k], y2)
        if e.dxf.layer in p.PROF_LAYERS and y1 < G[k]:
            G[k] = y1
        if x2 - x1 <= 100.0 and x2 > R[k]:     # 계통을 가로지르는 긴 선은 폭에서 제외(폭 폭주 방지)
            R[k] = x2
    for e, si, x1, x2 in frame:
        c = CELL.get(si)
        if not c:
            continue
        xs, G, A, X, R, SY = c
        k = cell_of(xs, x1)
        if x2 - x1 <= 100.0 and x2 > R[k]:
            R[k] = x2
    for e, si, x1, x2, y2 in station:
        c = CELL.get(si)
        if not c:
            continue
        xs, G, A, X, R, SY = c
        k = min(max(bisect.bisect_left(xs, x1 - 0.05), 0), len(xs) - 1)
        SY[k] = max(SY[k], y2)

    def window(si, i, j):
        xs, G, A, X, R, SY = CELL[si]
        g = a = INF
        x = r = -INF
        for k in range(i, j):
            g, a, x, r = min(g, G[k]), min(a, A[k]), max(x, X[k]), max(r, R[k])
        for k in range(i, j + 1):
            x = max(x, SY[k])
        if g > 1e17:
            g = p.STATION_Y
        if a > 1e17:
            a = g
        if x < -1e17:
            x = p.FRAME_TOP
        return g, a, x, max(r, xs[j])

    def pick_delta(si, i, j, s):
        g, a, x, r = window(si, i, j)
        H = p.US_H / s
        dmax = min(g - p.PROF_FLOOR, a + p.BOT_PAD)
        dmin = x - H + p.TOP_PAD
        slack = max(dmax - dmin, 0.0)
        d = dmax - min(slack * 0.45, 45.0)
        d = math.floor(d / p.DELTA_STEP) * p.DELTA_STEP
        if d < dmin:
            d = math.ceil(dmin / p.DELTA_STEP) * p.DELTA_STEP
        return d, r

    def pack_at(s):
        H, W = p.US_H / s, p.US_W / s
        pages, cur, used = [], [], 0.0

        def cut(si, i, avail):
            """[i..] 에서 폭 avail·높이 H 안에 들어가는 최대 j (없으면 None)"""
            xs, G, A, X, R, SY = CELL[si]
            g = a = INF
            x = SY[i]
            r = -INF
            last = None
            left = xs[i] - p.TITLE_W
            for j in range(i + 1, len(xs)):
                k = j - 1
                g, a, x, r = min(g, G[k]), min(a, A[k]), max(x, X[k], SY[j]), max(r, R[k])
                gg = p.STATION_Y if g > 1e17 else g
                aa = gg if a > 1e17 else a
                xx = p.FRAME_TOP if x < -1e17 else x
                rr = max(r, xs[j])
                if min(gg - p.PROF_FLOOR, aa + p.BOT_PAD) < xx - H + p.TOP_PAD:
                    break
                if (rr + 3.0) - left > avail:
                    break
                last = j
            return last

        prev_dist = None
        for si in sorted(CELL):
            xs = CELL[si][0]
            nn = len(xs)
            dist = seg_info[si]['dist']
            if cur and (dist != prev_dist or p.NO_PACK):    # 행(지구)이 바뀌면 새 장
                pages.append(cur)
                cur, used = [], 0.0
            prev_dist = dist
            i, guard = 0, 0
            while i < nn - 1:
                guard += 1
                if guard > 4 * nn + 50:
                    raise RuntimeError('패킹 루프 이상 (계통 %d)' % si)
                gap = p.SEG_GAP if cur else 0.0
                avail = W - used - gap
                j = cut(si, i, avail) if avail > p.TITLE_W + 20.0 else None
                if j is None:
                    if not cur:
                        return None            # 빈 페이지에도 안 들어감
                    pages.append(cur)
                    cur, used = [], 0.0
                    continue
                if cur and j < nn - 1:
                    # 여기선 잘리지만 빈 페이지엔 통째로 들어가면 다음 장에서 시작
                    jf = cut(si, i, W)
                    if jf is not None and jf >= nn - 1:
                        pages.append(cur)
                        cur, used = [], 0.0
                        continue
                d, r = pick_delta(si, i, j, s)
                w = (r + 3.0) - (xs[i] - p.TITLE_W)
                used += gap
                cur.append(dict(si=si, i=i, j=j, a=xs[i], b=xs[j], delta=d, rmax=r, width=w, off=used))
                used += w
                i = j
                if i < nn - 1:                 # 계통이 남았다 = 폭을 다 씀
                    pages.append(cur)
                    cur, used = [], 0.0
        if cur:
            pages.append(cur)
        return [dict(parts=pg) for pg in pages]

    if fixed_scale:
        r = pack_at(fixed_scale)
        if r is not None:
            return r, fixed_scale
        print('  지정 축척 H=1:%.0f 로는 분할 불가 -> 사다리 자동' % (p.UNIT_H / fixed_scale))
    for s in p.SCALES:
        r = pack_at(s)
        if r is not None:
            return r, s
    raise RuntimeError('어떤 축척으로도 분할 불가 — profile.a3.scale_denoms 를 늘리거나 --vk 로 세로를 줄일 것')


# ───────────────────────────────────────────── 제목
def rng_text(names):
    grp = collections.OrderedDict()
    for t in names:
        pre, no = t.rsplit('-', 1)
        grp.setdefault(pre, []).append(no)
    out = []
    for pre, nos in grp.items():
        nos = sorted(nos)
        out.append("%s-%s" % (pre, nos[0]) if len(nos) == 1 else "%s-%s~%s" % (pre, nos[0], nos[-1]))
    return "  /  ".join(out)


def dist_label(p, d):
    if d in p.ROW_LABELS:
        return p.ROW_LABELS[d]
    if not d or d.endswith('관로') or not p.SUFFIX:
        return d
    return d + p.SUFFIX


def make_titles(p, pages, seg_info, pipes, ri_map):
    """페이지 = 조각(계통 일부) 여러 개. 관번호는 조각 전부에서 모은다."""
    px = [q[0] for q in pipes]
    done = []
    for pg in pages:
        names = []
        for part in pg['parts']:
            # ★ 계통 첫 구간 관번호 라벨은 짧은 구간에서 중앙정렬로 원점보다 최대 7 unit 왼쪽에
            #   찍힌다 → 계통 시작일 때만 여유를 15 로
            i = bisect.bisect_left(px, part['a'] - (15.0 if part['i'] == 0 else 3.0))
            j = bisect.bisect_right(px, part['rmax'] + 3.0)
            names.extend(pipes[k][1] for k in range(i, j))
        pg['cont'] = not names
        if not names and done:
            names = done[-1]['pipes'][-1:]
        pg['pipes'] = names
        dists = []
        for part in pg['parts']:
            nm = seg_info[part['si']]['dist']
            if nm not in dists:
                dists.append(nm)
        cnt = collections.Counter(ri_map[t][0] for t in names if t in ri_map) if ri_map else None
        if ri_map:
            pg['ri'] = cnt.most_common(1)[0][0] if cnt else (done[-1]['ri'] if done else '')
        else:                                   # 리 매핑이 없으면 지구 이름을 큰 제목으로
            pg['ri'] = dist_label(p, dists[0]) if dists and dists[0] else '종단면도'
        pg['dist'] = ' · '.join(dists)
        labs = [dist_label(p, d) for d in dists if d]
        rng = (rng_text(names) + (' (계속)' if pg['cont'] else '')) if names else '-'
        pg['sub'] = "%s  |  %s" % (' · '.join(labs), rng) if labs else rng
        done.append(pg)
    seen = collections.Counter()
    for pg in pages:
        seen[pg['ri']] += 1
        pg['no'] = seen[pg['ri']]


# ───────────────────────────────────────────── 쓰기
def clip_line(e, xlo, xhi):
    """페이지 밖으로 뻗는 LINE 은 경계에서 자른다(파라메트릭).
    ★ 오른쪽 경계에서도 `x1 < lim → x1 = lim` 식으로 당기면 긴 밴드 가로선이 길이 0 인 점으로 뭉개진다."""
    a, b = e.dxf.start, e.dxf.end
    x1, y1, x2, y2 = a.x, a.y, b.x, b.y
    if max(x1, x2) < xlo or min(x1, x2) > xhi:
        return False
    dx = x2 - x1
    if abs(dx) > 1e-9:
        ta, tb = (xlo - x1) / dx, (xhi - x1) / dx
        t0, t1 = (ta, tb) if dx > 0 else (tb, ta)
        t0, t1 = max(0.0, t0), min(1.0, t1)
        if t1 <= t0:
            return False
        dy = y2 - y1
        e.dxf.start = (x1 + dx * t0, y1 + dy * t0, 0)
        e.dxf.end = (x1 + dx * t1, y1 + dy * t1, 0)
    return True


def clip_hpoly(e, xlo, xhi):
    """가로 LWPOLYLINE(관로 연장 치수선, 화살촉 폭 포함)을 경계에서 자른다.
    계통 전체 길이로 한 줄이라 자르지 않으면 옆 조각·표제란을 가로지른다."""
    q = e.get_points('xyseb')
    if len(q) < 2 or max(v[1] for v in q) - min(v[1] for v in q) > 0.01:
        return True
    xs = [v[0] for v in q]
    if max(xs) < xlo or min(xs) > xhi:
        return False
    if min(xs) >= xlo and max(xs) <= xhi:
        return True
    y = q[0][1]
    out = []
    for k, v in enumerate(q):
        if xlo <= v[0] <= xhi:
            out.append(v)
        if k + 1 < len(q):
            for bnd in (xlo, xhi):
                if (v[0] - bnd) * (q[k + 1][0] - bnd) < 0:
                    out.append((bnd, y, 0.0, 0.0, 0.0))
    out.sort(key=lambda v: v[0], reverse=xs[0] > xs[-1])
    if len(out) < 2:
        return False
    e.set_points(out, format='xyseb')
    return True


def build(p, dxf_path, out_dxf, out_json, ri_map, fixed_scale=None, spots=(), keep_rows=None,
          row_names=None):
    import ezdxf
    from ezdxf.entities import factory
    nspot = [0]
    spot_done = set()
    doc = PC.load_dxf(dxf_path)
    msp = doc.modelspace()
    T('DXF 로드 (%d 엔티티)' % len(msp))
    ents = list(msp)
    rows = detect_rows(p, ents)
    ents, rows = normalize_rows(p, ents, rows, keep_rows, row_names)
    print('행: %s' % ', '.join('%d:%s(y0=%.0f, %d개)' % (r['idx'], r['name'] or '-', r['y0'], r['n'])
                              for r in rows), flush=True)
    for sp in spots:            # 원도 좌표(x, y) 로 준 표시 지점 → 정규화 x
        if 'y' in sp:
            r = next((r for r in rows if r['lo'] <= sp['y'] < r['hi']), None)
            sp['x'] = sp['x'] + (r['dx'] if r else 0.0)
    if abs(p.VK - 1.0) > 1e-9:
        print('지형부 세로 압축 VK=%.3f -> %d개' % (p.VK, vcompress(p, ents)), flush=True)
    print('%s 라벨 %d개 확대(x%.2f)' % (p.PUMP_KEY, enlarge_pump(p, ents), p.PUMP_MAG))
    segs_x, seg_info, pipes, frame, float_, station, pumps = collect(p, ents, rows)
    T('엔티티 분류 (고정틀 %d / 지형부 %d / 측점선 %d, 계통 %d, 관번호 %d)'
      % (len(frame), len(float_), len(station), len(segs_x), len(pipes)))
    pages, s = plan(p, segs_x, seg_info, frame, float_, station, fixed_scale)
    T('분할 계산')
    make_titles(p, pages, seg_info, pipes, ri_map)
    print("통일 축척 H=1:%.0f  V=1:%.0f  ->  %d 장"
          % (p.UNIT_H / s, p.UNIT_V / s / p.VK, len(pages)))

    for lay, col in ((L_FRAME, 8), (L_TITLE, 7), (L_POLE, 7), (L_PUMP, 2), (L_SPOT, 7), (L_JOIN, 6)):
        if lay not in doc.layers:
            doc.layers.add(lay, color=col)

    fl, st, fr = {}, {}, {}
    for r in float_:
        fl.setdefault(r[1], []).append(r)
    for r in station:
        st.setdefault(r[1], []).append(r)
    for r in frame:
        fr.setdefault(r[1], []).append(r)

    def belongs(layer, x1, b):
        """마지막 측점 b 이후로 뻗는 것은 인출·글자류만 남긴다."""
        if x1 > b + 3.0:
            return False
        if x1 >= b - 0.5 and layer not in p.ANNOT_LAYERS:
            return False
        return True

    new, meta = [], []
    crushed = [0]
    H = p.US_H / s
    ylo = -p.BOT_PAD
    BY = p.MARGIN_B - ylo * s
    for n, pg in enumerate(pages):
        PX = n * (p.PAGE_W + p.SHEET_GAP)
        for part in pg['parts']:
            si = part['si']
            info = seg_info[si]
            a, b, d = part['a'], part['b'], part['delta']
            xlo = a - p.TITLE_W - 1.0
            xhi = part['rmax'] + 3.0
            BX = PX + p.MARGIN_L + (part['off'] - xlo) * s
            band_r = [-1e18]        # 밴드 가로선이 실제로 끝나는 x

            def take(e, dy=0.0, _xlo=xlo, _xhi=xhi, _bx=BX, _a=a, _band=band_r):
                c = e.copy()
                if dy:
                    c.translate(0, dy, 0)
                if c.dxftype() == 'TEXT' and (c.dxf.text or '').strip().lower() == 'pump':
                    # 옛 서식의 세로 'pump' 글자: 표척을 비키고, 위로 (글자수 × 높이) 만큼 뻗으니 내린다
                    c.dxf.layer = L_PUMP
                    c.translate(PUMP_DX, 0, 0)
                    ph = c.dxf.height or 2.5
                    over = (c.dxf.insert.y + len((c.dxf.text or '').strip()) * ph * 1.05) - (ylo + H - 4.0)
                    if over > 0:
                        c.translate(0, -over, 0)
                lo = _xlo
                band = (c.dxftype() == 'LINE' and c.dxf.layer in p.BAND_LAYERS
                        and abs(c.dxf.start.y - c.dxf.end.y) < 0.01
                        and c.dxf.start.y <= p.BAND_TOP + 0.5)
                if band:
                    lo = _a - p.BAND_LEFT      # 표제란을 가로지르지 않게
                if c.dxftype() == 'LINE':
                    l0 = abs(c.dxf.end.x - c.dxf.start.x) + abs(c.dxf.end.y - c.dxf.start.y)
                    if not clip_line(c, lo, _xhi):
                        return
                    if l0 > 1e-9 and abs(c.dxf.end.x - c.dxf.start.x) + abs(c.dxf.end.y - c.dxf.start.y) < 1e-9:
                        crushed[0] += 1          # 자르다가 점으로 뭉갠 선(0 이어야 한다)
                if c.dxftype() == 'LWPOLYLINE' and not clip_hpoly(c, _a - 1.0, _xhi):
                    return
                if band:
                    _band[0] = max(_band[0], c.dxf.start.x, c.dxf.end.x)
                if c.dxftype() == 'TEXT' and p.PUMP_KEY and p.PUMP_KEY in (c.dxf.text or ''):
                    hl = pump_hl(c, L_PUMP)          # 노란 형광은 글자 밑에 깔린다
                    xform(hl, s, _bx, BY)
                    new.append(hl)
                elif c.dxftype() == 'TEXT' and p.JOIN_KEY and p.JOIN_KEY in (c.dxf.text or ''):
                    hl = pump_hl(c, L_JOIN, 0.25)    # 다른 노선 접속 = 분홍 바탕
                    xform(hl, s, _bx, BY)
                    new.append(hl)
                xform(c, s, _bx, BY)
                new.append(c)

            for e, _si, x1, x2 in fr.get(si, []):
                if x2 >= a - 1 and belongs(e.dxf.layer, x1, b):
                    take(e)
            for e, _si, x1, x2, y1, y2 in fl.get(si, []):
                if x2 >= a - 1 and belongs(e.dxf.layer, x1, b):
                    take(e, -d)
            for e, _si, x1, x2, y2 in st.get(si, []):
                if a - 1 <= x1 <= b + 0.5:      # 측점 세로선: 아래끝 고정, 위끝만 Δ 이동
                    c = e.copy()
                    q0, q1 = c.dxf.start, c.dxf.end
                    lo_, hi_ = (q0, q1) if q0.y < q1.y else (q1, q0)
                    c.dxf.start = (lo_.x, p.STATION_Y, 0)
                    c.dxf.end = (hi_.x, hi_.y - d, 0)
                    xform(c, s, BX, BY)
                    new.append(c)
            for px_, pw, pe in pumps:
                if a - 2 <= px_ <= part['rmax'] + 2:
                    c = pe.copy()
                    c.dxf.layer = L_PUMP
                    c.set_points([(px_, ylo), (px_, ylo + H)], format='xy')
                    xform(c, s, BX, BY)
                    new.append(c)

            blk = factory.new('INSERT', dxfattribs={'name': p.TITLE_BLOCK, 'insert': (a, 0, 0)})
            xform(blk, s, BX, BY)
            new.append(blk)

            # 밴드 표 우측 테두리 — 원본 세로선은 계통 끝에만 있어 조각에서는 표가 열린 채 끝난다.
            # ★ 위치는 '실제로 그려진 가로선의 끝'. rmax 는 인출 글자까지 포함해 표에서 멀다
            if band_r[0] > -1e17:
                vb = factory.new('LINE', dxfattribs={'layer': sorted(p.BAND_LAYERS)[0],
                                                     'start': (band_r[0], 0.0, 0),
                                                     'end': (band_r[0], p.BAND_TOP, 0)})
                xform(vb, s, BX, BY)
                new.append(vb)

            # 표척 연장 + 눈금값 재작성 + 축척 글자
            top = ylo + H - 3.0
            y_base = info['ylab'][0] if info['ylab'] else p.STATION_Y + 1.0
            upm = p.UNIT_H / p.UNIT_V                 # 1 m 당 세로 unit (H=1:1000·V=1:200 이면 5)
            v_base = (info['vlab'][0] if info['vlab'] else 0.0) + d / (upm * p.VK)
            seg = []
            if top > p.POLE_TOP:
                for xo in (-10.0, -12.0):
                    seg.append((('L', a + xo, p.POLE_TOP, a + xo, top), 0.0))
                yy, flip = p.POLE_TOP + 10.0, True
                while yy - 10.0 < top:
                    xo = -11.5 if flip else -10.5
                    seg.append((('P', a + xo, yy - 10.0, a + xo, min(yy, top)), 2.6))
                    flip = not flip
                    yy += 10.0
                yg = p.POLE_TOP + 50.0
                while yg <= top:
                    seg.append((('L', a - 28.0, yg, a - 10.0, yg), 0.0))
                    yg += 50.0
            for (kind, sx, sy, ex, ey), w in seg:
                if kind == 'L':
                    e = factory.new('LINE', dxfattribs={'layer': L_POLE, 'start': (sx, sy, 0), 'end': (ex, ey, 0)})
                else:
                    e = factory.new('LWPOLYLINE', dxfattribs={'layer': L_POLE, 'const_width': w})
                    e.set_points([(sx, sy), (ex, ey)], format='xy')
                xform(e, s, BX, BY)
                new.append(e)
            k = 0
            while y_base + 50.0 * k <= top:
                e = factory.new('TEXT', dxfattribs={
                    'layer': L_POLE, 'height': 3.0,
                    'text': "%.2f" % (v_base + 50.0 / upm / p.VK * k),
                    'insert': (a + p.POLE_DX, y_base + 50.0 * k, 0)})
                xform(e, s, BX, BY)
                new.append(e)
                k += 1
            for txt, xx, yy, rot in (("H=1:%.0f" % (p.UNIT_H / s), a - 28.0, -4.5, 0),
                                     ("V=1:%.0f" % (p.UNIT_V / s / p.VK), a - 31.5, 0.0, 90)):
                e = factory.new('TEXT', dxfattribs={'layer': L_POLE, 'height': 2.5, 'rotation': rot,
                                                    'text': txt, 'insert': (xx, yy, 0)})
                xform(e, s, BX, BY)
                new.append(e)

            # 표시 지점 라벨 — 그 자리 지형선 위(또는 관 밑)에 색 테두리 박스로
            placed = []
            for sp in spots:
                lx = sp['x']
                if not (a - 1.0 <= lx <= part['rmax'] + 1.0) or id(sp) in spot_done:
                    continue
                spot_done.add(id(sp))
                gy, gl = [], []
                for e, _si, x1, x2, y1, y2 in fl.get(si, []):
                    if e.dxf.layer in p.PROF_LAYERS and x1 <= lx + 18.0 and x2 >= lx - 18.0:
                        gy.append(y2)
                        gl.append(y1)
                base = (max(gy) if gy else 200.0) - d
                txt = sp['label']
                h = SPOT_H
                w = sum((1.20 if ord(ch) > 0x2000 else 0.66) for ch in txt) * h * 1.16
                pad = h * 0.45
                ly = base + SPOT_UP
                tp = ylo + H - 4.0
                if ly + h + 2 * pad > tp:
                    ly = tp - h - 2 * pad
                col = ezdxf.rgb2int(tuple(sp.get('rgb', (0x44, 0x44, 0x44))))
                half = w / 2 + pad
                cx = min(max(lx, a - 8.0 + half), part['rmax'] + 2.0 - half)
                x0, x1b = cx - half, cx + half
                bh = h + 2 * pad
                step = bh + 1.6

                def clash(yy, _x0=x0, _x1=x1b, _bh=bh, _pl=placed):
                    return any(_x1 > a0 and _x0 < a1 and yy + _bh > b0 and yy < b1 for a0, a1, b0, b1 in _pl)

                for _ in range(8):
                    if not clash(ly):
                        break
                    ly += step
                if ly + bh > tp:
                    ly = min(base + SPOT_UP, tp - bh)
                    for _ in range(8):
                        if not clash(ly):
                            break
                        ly -= step
                if p.SPOT_BELOW:
                    low = (min(gl) if gl else 200.0) - d
                    floor_ = p.FRAME_TOP + 3.0
                    ly0 = max(low - SPOT_DOWN - bh, floor_)
                    cands = [ly0 - k_ * step for k_ in range(8) if ly0 - k_ * step >= floor_] \
                        + [ly0 + k_ * step for k_ in range(1, 8) if ly0 + k_ * step + bh <= low - 1.0]
                    ly = next((c_ for c_ in cands if not clash(c_)), ly0)
                y0, y1b = ly, ly + bh
                placed.append((x0, x1b, y0, y1b))
                bg = factory.new('SOLID', dxfattribs={
                    'layer': L_SPOT, 'true_color': ezdxf.rgb2int(tuple(sp.get('bg', (255, 255, 254))))})
                for k_, v_ in enumerate(((x0, y0), (x1b, y0), (x0, y1b), (x1b, y1b))):
                    setattr(bg.dxf, 'vtx%d' % k_, (v_[0], v_[1], 0))
                xform(bg, s, BX, BY)
                new.append(bg)
                box = factory.new('LWPOLYLINE', dxfattribs={'layer': L_SPOT, 'true_color': col,
                                                            'const_width': 0.75, 'flags': 1})
                box.set_points([(x0, y0), (x1b, y0), (x1b, y1b), (x0, y1b)], format='xy')
                xform(box, s, BX, BY)
                new.append(box)
                if sp.get('leader'):
                    anchor = ((min(gl) if gl else 200.0) - d) if p.SPOT_BELOW else ((max(gy) if gy else 200.0) - d)
                    ld = factory.new('LINE', dxfattribs={'layer': L_SPOT, 'true_color': col,
                                                         'start': (lx, anchor, 0),
                                                         'end': (cx, y1b if p.SPOT_BELOW else y0, 0)})
                    xform(ld, s, BX, BY)
                    new.append(ld)
                t = factory.new('TEXT', dxfattribs={'layer': L_SPOT, 'height': h, 'true_color': col,
                                                    'text': txt, 'insert': (cx - w / 2, ly + pad, 0)})
                xform(t, s, BX, BY)
                new.append(t)
                nspot[0] += 1

        fill = sum(q['width'] for q in pg['parts']) + p.SEG_GAP * (len(pg['parts']) - 1)
        pg['fill'] = fill / (p.US_W / s)
        meta.append(dict(sheet=n + 1, x=PX, ri=pg['ri'], no=pg['no'], sub=pg['sub'], dist=pg['dist'],
                         pipes=pg['pipes'], scale=s, fill=round(pg['fill'], 3),
                         parts=[dict(seg=q['si'] + 1,
                                     a=round(q['a'] - seg_info[q['si']]['x0'], 2),
                                     b=round(q['b'] - seg_info[q['si']]['x0'], 2)) for q in pg['parts']]))

    if spots:
        print('표시 지점 라벨 %d/%d개 배치' % (nspot[0], len(spots)))
    T('페이지 엔티티 생성 (%d개)' % len(new))
    # 자체 검증: ① 원도 관번호가 장 제목에서 빠지지 않았나 ② 클리핑이 선을 점으로 뭉개지 않았나
    allp = {t for _, t in pipes}
    got = set()
    for m in meta:
        got.update(m['pipes'])
    miss = sorted(allp - got)
    print('검증: 관번호 %d 중 제목 누락 %d%s · 자르다 점으로 뭉갠 선 %d'
          % (len(allp), len(miss), (' ' + ', '.join(miss[:8])) if miss else '', crushed[0]))
    check = dict(pipes=len(allp), missing=miss, crushed_lines=crushed[0])
    msp.delete_all_entities()
    for e in new:
        msp.add_entity(e)

    colors = assign_colors(p, pages)
    for n, pg in enumerate(pages):
        PX = n * (p.PAGE_W + p.SHEET_GAP)
        msp.add_lwpolyline([(PX, 0), (PX + p.PAGE_W, 0), (PX + p.PAGE_W, p.PAGE_H), (PX, p.PAGE_H)],
                           format='xy', close=True, dxfattribs={'layer': L_FRAME})
        draw_title(p, msp, PX, pg, s, n + 1, len(pages), colors.get(pg['ri'], (0x44,) * 3))

    doc.header['$INSUNITS'] = 4          # mm
    doc.saveas(out_dxf)
    T('DXF 저장')
    PC.write_json(out_json, dict(scale=s, unit_h=p.UNIT_H, unit_v=p.UNIT_V, vk=p.VK,
                                 page=[p.PAGE_W, p.PAGE_H], gap=p.SHEET_GAP,
                                 margin=[p.MARGIN_L, p.MARGIN_R, p.MARGIN_B, p.MARGIN_T],
                                 rows=[dict(idx=r['idx'], name=r['name'], y0=r['y0'], dx=r['dx']) for r in rows],
                                 check=check, sheets=meta))
    for m in meta:
        print("  %2d/%d  %-14s %-6s %-2d  %3.0f%%  %s"
              % (m['sheet'], len(meta), m['dist'], m['ri'], m['no'], m['fill'] * 100, rng_text(m['pipes'])[:46]))
    return len(pages)


def assign_colors(p, pages):
    cols = dict(p.RI_COLOR)
    k = 0
    for pg in pages:
        r = pg['ri']
        if r not in cols:
            cols[r] = PC.hex_rgb(PC.PALETTE[k % len(PC.PALETTE)])
            k += 1
    return cols


def draw_title(p, msp, PX, sh, s, n, ntot, col):
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    CX, CY, CS = 7.5, p.PAGE_H - 20.5, 14.5       # 번호 칩 좌하단·크기
    msp.add_solid([(PX + CX, CY), (PX + CX + CS, CY), (PX + CX, CY + CS), (PX + CX + CS, CY + CS)],
                  dxfattribs={'layer': L_TITLE, 'true_color': ezdxf.rgb2int(col)})
    t = msp.add_text(str(sh['no']), height=9.5,
                     dxfattribs={'layer': L_TITLE, 'true_color': ezdxf.rgb2int((255, 255, 255))})
    t.set_placement((PX + CX + CS / 2, CY + CS / 2 - 0.6), align=TextEntityAlignment.MIDDLE_CENTER)
    tx = CX + CS + 4.5
    t = msp.add_text(sh['ri'], height=11.6, dxfattribs={'layer': L_TITLE, 'true_color': ezdxf.rgb2int(col)})
    t.set_placement((PX + tx, CY + 2.2))
    t = msp.add_text(sh['sub'], height=5.3, dxfattribs={'layer': L_TITLE, 'true_color': ezdxf.rgb2int((0x33,) * 3)})
    t.set_placement((PX + tx, CY - 8.0))
    msp.add_line((PX + CX, CY - 11.5), (PX + p.PAGE_W - p.MARGIN_R, CY - 11.5),
                 dxfattribs={'layer': L_TITLE, 'true_color': ezdxf.rgb2int(col)})
    t = msp.add_text("A3   |   H=1:%.0f   V=1:%.0f   |   SHEET %d / %d"
                     % (p.UNIT_H / s, p.UNIT_V / s / p.VK, n, ntot), height=3.7,
                     dxfattribs={'layer': L_TITLE, 'true_color': ezdxf.rgb2int((0x33,) * 3)})
    t.set_placement((PX + p.PAGE_W - p.MARGIN_R, CY + CS - 4.0), align=TextEntityAlignment.RIGHT)


# ───────────────────────────────────────────── 진단
def probe(p, dxf_path):
    doc = PC.load_dxf(dxf_path)
    ents = list(doc.modelspace())
    print('엔티티 %d' % len(ents))
    print('종류:', collections.Counter(e.dxftype() for e in ents).most_common())
    print('레이어:', collections.Counter(e.dxf.layer for e in ents).most_common(25))
    ins = collections.Counter(e.dxf.name for e in ents if e.dxftype() == 'INSERT')
    print('블록 INSERT:', ins.most_common(10))
    if p.TITLE_BLOCK not in ins:
        print('★ 표제 블록 "%s" 가 없다 → profile.strip.title_block 을 위 목록에서 고를 것' % p.TITLE_BLOCK)
        return
    rows = detect_rows(p, ents)
    for r in rows:
        nseg = sum(1 for e in ents if e.dxftype() == 'INSERT' and e.dxf.name == p.TITLE_BLOCK
                   and r['lo'] <= e.dxf.insert.y < r['hi'])
        print('행 %d  이름=%-10s y0=%8.1f  계통 %d' % (r['idx'], r['name'] or '-', r['y0'], nseg))
    txt = [e for e in ents if e.dxftype() == 'TEXT']
    pip = [e.dxf.text.strip() for e in txt if p.PIPE_RE.match((e.dxf.text or '').strip())]
    print('관번호 글자 %d개 (고유 %d) 예: %s' % (len(pip), len(set(pip)), sorted(set(pip))[:5]))
    rot = collections.Counter(round(e.dxf.rotation or 0) for e in txt)
    print('TEXT 회전각:', rot.most_common(6), '— 0 이 아닌 것은 2단계에서 matplotlib 글자로 그린다')
    st = sum(1 for e in ents if e.dxftype() == 'LINE' and e.dxf.layer == p.STATION
             and abs(min(e.dxf.start.y, e.dxf.end.y) - p.STATION_Y) < 0.2)
    print('측점 세로선(%s, y=%.0f 시작) %d개' % (p.STATION, p.STATION_Y, st))
    tits = [e for e in ents if e.dxftype() == 'INSERT' and e.dxf.name == p.TITLE_BLOCK][:1]
    if tits:
        x0, y0 = tits[0].dxf.insert.x, tits[0].dxf.insert.y
        lab = [(round(e.dxf.insert.x - x0, 1), round(e.dxf.insert.y - y0, 1), e.dxf.text) for e in txt
               if -45 <= e.dxf.insert.x - x0 <= -6 and 0 < e.dxf.insert.y - y0 < 400]
        print('첫 계통 원점 왼쪽 글자(dx, dy, text) — 표척 눈금값 dx 가 pole_label_dx:', lab[:12])


def main():
    ap = argparse.ArgumentParser(description='[1단계] 긴 스트립 종단면도 DXF → A3 시트 DXF + JSON')
    PC.add_common_args(ap)
    ap.add_argument('dxf', help='원도(스트립) 종단면도 DXF — 읽기만 한다')
    ap.add_argument('--name', default=None, help='산출 파일 이름 앞부분(기본: 입력 파일명)')
    ap.add_argument('--ri-map', default=None, help='ri_map.py 가 만든 관번호→[리, 지구] JSON')
    ap.add_argument('--scale', type=float, default=None, help='H 축척 분모 고정(예: 1200). 안 되면 사다리 자동')
    ap.add_argument('--vk', type=float, default=None, help='지형부 세로 압축비(기본 profile.a3.vk=1.0)')
    ap.add_argument('--rows', default=None, help='남길 행 — 번호 또는 이름, 쉼표(예: 0,2 / OO)')
    ap.add_argument('--row-names', default=None, help='행 이름을 직접(아래 행부터, 쉼표)')
    ap.add_argument('--pipe-pattern', default=None, help='관번호 정규식(기본 profile.strip.pipe_pattern)')
    ap.add_argument('--spots', default=None,
                    help='표시 지점 JSON [{"x":원도x,"y":원도y,"label":"…","rgb":[r,g,b],"leader":false}]')
    ap.add_argument('--spot-below', action='store_true', help='표시 라벨을 관로 밑 빈 공간에')
    ap.add_argument('--no-pack', action='store_true', help='계통마다 새 장(한 장에 계통을 이어 붙이지 않음)')
    ap.add_argument('--probe', action='store_true', help='서식 진단만 하고 끝')
    a = ap.parse_args()
    cfg = PC.load_cfg(a.config)
    S = PC.settings(cfg)
    p = P(S, a)
    if a.probe:
        probe(p, a.dxf)
        return
    import os
    name = a.name or os.path.splitext(os.path.basename(a.dxf))[0]
    od = PC.out_dir(cfg, a.out)
    keep = None
    if a.rows:
        keep = set()
        for v in a.rows.split(','):
            v = v.strip()
            keep.add(int(v) if v.isdigit() else v)
    names = [v.strip() for v in a.row_names.split(',')] if a.row_names else None
    ri = PC.read_json(a.ri_map) if a.ri_map else {}
    spots = PC.read_json(a.spots) if a.spots else []
    out_dxf = od / (name + '_A3시트.dxf')
    out_json = od / (name + '_A3시트.json')
    build(p, a.dxf, str(out_dxf), str(out_json), ri,
          fixed_scale=(p.UNIT_H / a.scale if a.scale else None), spots=spots, keep_rows=keep, row_names=names)
    print('->', out_dxf)
    print('->', out_json)


if __name__ == '__main__':
    main()
