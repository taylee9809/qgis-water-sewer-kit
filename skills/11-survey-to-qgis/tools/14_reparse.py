# -*- coding: utf-8 -*-
"""14 한 건씩 원도에서 형상 재파싱 — 평행 2선(관벽)을 짝지어 **사이를 채운 띠**로 다시 만든다.

왜 필요한가
-----------
U형측구는 말 그대로 **U자 수로**다. 원도의 평행 2선은 그 **양쪽 벽**이고, 구조물은 두 선
사이를 꽉 채운 띠다. 그런데 02 의 전역 체인쌍 매칭이 그 짝을 못 찾으면 폴리곤이
`단선버퍼` 로 떨어지고, 두 벽선을 각각 감싼 꼴이 되어 **가운데가 길고 가느다란 구멍으로
남는다**(사용자 지적 사례 3건). 구멍만 메우면 폭이 그대로
틀린 채(0.63/0.60/1.36 vs 표기 0.5/0.3/0.8) 남으므로, **원도에서 다시 파싱**해야 한다.

02 와 뭐가 다른가
-----------------
전역 폭창 `W_MIN..W_MAX = 0.10..6.5` 대신 **그 지장물의 표기폭을 사전값으로** 쓴다
(`표기폭 × [0.55, 1.70]`). 짝후보가 확 줄어 U0.3 측구 옆 1.2 m 구조물에 붙는 오매칭이 없다.
나머지(샘플 간격·폭 일정성·투영 구간·면적 검증)는 02 와 같은 방식이다.

사용 (공통: [--config sewer.config.json] [--gpkg ...] [--poly ...] [--cl ...] [--src <경량.dxf>])
    python -X utf8 14_reparse.py --ids <번호1>,<번호2>,<번호3> [--write] [--export]
    python -X utf8 14_reparse.py --adopt "<13확인.gpkg>::확인_복원안" --write   승인된 도형 그대로 채택
    python -X utf8 14_reparse.py --from-13 사람확인 [--chk <13 결과.csv>]   13 결과에서 그 판정만 골라서
출력
    <산출>/14_reparse.csv · (--export) <산출>/지장물_14재파싱_<날짜>.gpkg (원본/재파싱안 2레이어)
    --write 를 줘야 정본 GPKG 에 반영한다(백업 자동). 보정 기록 필드 = note_field()
"""
import os
import sys
import csv
import math
import datetime
import collections

TOOL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL)
from _common import *                                      # noqa: E402,F403

import numpy as np                                         # noqa: E402
from shapely.geometry import Polygon, LineString, Point    # noqa: E402
from shapely.ops import substring, linemerge, unary_union  # noqa: E402
from shapely.strtree import STRtree                        # noqa: E402

GP, DEF_POLY, DEF_CL = obstacle_src()
DXF = light_dxf()
CHK = argval('--chk') or out_path('13_gugeo_check.csv')
OUT = out_path('14_reparse.csv')

STEP = 0.25          # 02 와 동일: 기준 체인 샘플 간격
OVR = 0.25           # 겹침 구간 최소 비율
SNAP = 0.05
# 표기폭 대비 허용 폭창 ← 02 의 전역 0.10~6.5 와 다른 핵심.
# ★ 2단계로 간다: 먼저 좁은 창(TIGHT)으로 보고, 원 폴리곤 길이의 COVER 이상 덮으면 그걸 쓴다.
#   한 자리에 폭이 다른 구조물이 겹쳐 있을 때 긴 쪽에 끌려가는 걸 막는다
#   (사례: 표기 U0.3 인데 옆 매립 0.50 짝이 run 이 길어 선택됐다).
TIGHT = (0.80, 1.25)
WIDE = (0.55, 1.70)
COVER = 0.40


def snap_chains(lines, wmax=None):
    """끝점 스냅 후 체인화 (02 의 1단계 축약판)."""
    pts = []
    for g in lines:
        c = list(g.coords)
        pts.append(c)
    ends = [(c[0], c[-1]) for c in pts]
    flat = [p for e in ends for p in e]
    for i, c in enumerate(pts):
        for k, idx in ((0, 2 * i), (-1, 2 * i + 1)):
            p = flat[idx]
            for q in flat:
                if q is p:
                    continue
                if math.dist(p, q) <= SNAP:
                    c[k] = q
                    break
    gs = [LineString(c) for c in pts if LineString(c).length > 1e-6]
    m = linemerge(unary_union(gs))
    out = list(m.geoms) if hasattr(m, 'geoms') else [m]
    res = []
    for g in out:
        if g.geom_type != 'LineString' or g.length <= 0.3:
            continue
        for h in _split_rev(g):   # 양쪽 벽이 끝점을 공유하면 한 줄로 붙는다(13번과 같은 함정)
            res += _split_caps(h, wmax)
    return [g for g in res if g.length > 0.3]


def _split_caps(ls, wmax):
    """마구리(짧은 연결변)에서 끊어 **양쪽 벽을 분리**한다.

    ★ 꺾임각만으로는 안 된다 — 네모난 마구리를 돌면 **90도 두 번**이라 한 번도 임계값을
    못 넘는다(사례: 두 벽 18 m 씩이 0.40 m 마구리로 이어져 50 m 한 줄이 됐다).
    `_common.digeut` 과 같은 '역평행도' 로 본다: 짧은 변 앞뒤 방향이 거의 반대면 마구리다."""
    if wmax is None:
        return [ls]
    cs = [tuple(c[:2]) for c in ls.coords]
    if len(cs) < 4:
        return [ls]
    ang = lambda a, b: math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    for i in range(1, len(cs) - 2):
        w = math.dist(cs[i], cs[i + 1])
        if w > wmax * 1.4:
            continue
        dev = abs((ang(cs[i - 1], cs[i]) - ang(cs[i + 1], cs[i + 2]) + 180) % 360 - 180)
        if dev < 145.0:
            continue
        a, b = LineString(cs[:i + 1]), LineString(cs[i + 1:])
        out = []
        for h in (a, b):
            out += _split_caps(h, wmax) if h.length > 0.3 else []
        return out or [ls]
    return [ls]


def _split_rev(ls, thr=110.0):
    cs = [tuple(c[:2]) for c in ls.coords]
    if len(cs) < 3:
        return [ls]
    ang = lambda a, b: math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    cut = [0]
    for i in range(1, len(cs) - 1):
        if abs((ang(cs[i - 1], cs[i]) - ang(cs[i], cs[i + 1]) + 180) % 360 - 180) >= thr:
            cut.append(i)
    cut.append(len(cs) - 1)
    return [LineString(cs[s:e + 1]) for s, e in zip(cut, cut[1:]) if e - s >= 1]


def pair_band(chains_, w0, want_len):
    """02 의 체인쌍 매칭을 표기폭 사전값으로 좁혀 수행.

    ★ 벽선은 교차부·지반선 때문에 **여러 토막으로 끊겨 있다**. 짝을 하나만 쓰면 구조물의
    일부(12 m / 31 m)만 덮는다 → 겹치지 않는 짝을 **전부** 모아 합친다(02 와 같은 방식)."""
    for lo, hi in (TIGHT, WIDE):
        r = _pairs(chains_, w0 * lo, w0 * hi)
        if r and r['run'] >= COVER * max(want_len, 1e-6):
            r['창'] = '좁음' if (lo, hi) == TIGHT else '넓음'
            return r
    r = _pairs(chains_, w0 * WIDE[0], w0 * WIDE[1])
    if r:
        r['창'] = '넓음(미달)'
    return r


def poly_centerline(poly, n=60):
    """띠 폴리곤의 중심선 — 양 끝(짧은 변)에서 경계를 둘로 갈라 호장 비례로 평균낸다.

    ★ `_common.band_axis` 를 쓰면 안 된다: '가장 긴 변 두 개의 중점을 잇는' 방식이라
    정점이 잘게 쪼개진 **굽은 띠**에서 엉뚱한 현(chord)이 된다
    (사례: 실제 23.1 m 인데 band_axis 는 6.2 m).
    폴리곤과 중심선은 같이 가야 한다 — 중심선만 옛 형상으로 두면 지장물이 없는 자리에
    선만 남는다."""
    try:
        ring = LineString(poly.exterior.coords)
        mrr = poly.minimum_rotated_rectangle
        cs = list(mrr.exterior.coords)[:-1]
        if len(cs) < 4:
            return band_axis(poly)
        edges = sorted(((math.dist(cs[i], cs[(i + 1) % 4]),
                         ((cs[i][0] + cs[(i + 1) % 4][0]) / 2,
                          (cs[i][1] + cs[(i + 1) % 4][1]) / 2))
                        for i in range(4)), key=lambda e: e[0])
        t1 = ring.project(Point(edges[0][1]))
        t2 = ring.project(Point(edges[1][1]))
        a, b = sorted((t1, t2))
        if b - a < 1e-6 or ring.length - (b - a) < 1e-6:
            return band_axis(poly)
        c1 = substring(ring, a, b)
        c2 = substring(ring, b, ring.length)
        tail = substring(ring, 0, a)
        if tail.length > 1e-9:
            c2 = LineString(list(c2.coords) + list(tail.coords))
        pts = []
        for k in range(n + 1):
            f = k / n
            q1 = c1.interpolate(f, normalized=True)
            q2 = c2.interpolate(1 - f, normalized=True)
            pts.append(((q1.x + q2.x) / 2, (q1.y + q2.y) / 2))
        out = [pts[0]]
        for q in pts[1:]:
            if math.dist(out[-1], q) > 1e-9:
                out.append(q)
        return LineString(out) if len(out) > 1 else band_axis(poly)
    except Exception:
        return band_axis(poly)


def keep_here(gp, g, w):
    """**원래 폴리곤 자리에 있는 조각만** 남긴다.

    ★ 재파싱은 '이 지장물'을 다시 읽는 것이지 옆 구조물까지 가져오는 게 아니다. 벽선은
    이웃 측구까지 이어져 있어서 짝매칭이 그대로 넘어간다(한 건은 1.9 m 떨어진 다른
    구조물 4.8 ㎡, 다른 한 건은 끝을 지나 6.4 ㎡ 를 물었다).
    정본 레이어가 Polygon 전용이라 멀티폴리곤은 쓸 수도 없다 → 여기서 한 조각으로 만든다.
    버린 조각은 --export 의 `버린조각` 레이어로 남겨 사람이 보게 한다."""
    parts = list(gp.geoms) if hasattr(gp, 'geoms') else [gp]
    if len(parts) == 1:
        return gp, []
    env = g.buffer(max(0.3, 1.2 * w))
    kept = [q for q in parts if q.intersection(env).area >= 0.5 * q.area]
    drop = [q for q in parts if q.intersection(env).area < 0.5 * q.area]
    if not kept:
        return gp, []
    m = unary_union(kept).buffer(0)
    if m.geom_type != 'Polygon':
        m2 = m.buffer(0.05).buffer(-0.05)       # 맞닿은 조각끼리는 붙인다
        if m2.geom_type == 'Polygon':
            m = m2
        else:                                    # 그래도 갈라지면 가장 큰 조각만
            bits = sorted(m.geoms, key=lambda q: -q.area)
            drop += bits[1:]
            m = bits[0]
    return m, drop


def _pairs(chains_, wmin, wmax):
    tree = STRtree(chains_)
    cands = []
    for i in sorted(range(len(chains_)), key=lambda k: -chains_[k].length):
        gi = chains_[i]
        n_ = max(2, int(gi.length / STEP) + 1)
        ds = np.linspace(0, gi.length, n_)
        ps = [gi.interpolate(d) for d in ds]
        for j in tree.query(gi.buffer(wmax + 0.2)):
            j = int(j)
            if j == i:
                continue
            gj = chains_[j]
            if gj.length > gi.length + 1e-9 or (abs(gj.length - gi.length) < 1e-9 and j < i):
                continue
            dist = np.array([gj.distance(p) for p in ps])
            ok = (dist >= wmin) & (dist <= wmax)
            k = 0
            while k < len(ds):
                if not ok[k]:
                    k += 1
                    continue
                s = k
                while k + 1 < len(ds) and ok[k + 1] and abs(dist[k + 1] - dist[k]) < 0.12:
                    k += 1
                e = k
                k += 1
                run = ds[e] - ds[s]
                if run < max(0.3, OVR * min(gi.length, gj.length)):
                    continue
                w = float(np.median(dist[s:e + 1]))
                if np.std(dist[s:e + 1]) > max(0.06, 0.12 * w):
                    continue
                sub_i = substring(gi, ds[s], ds[e])
                tj = sorted([gj.project(Point(sub_i.coords[0])),
                             gj.project(Point(sub_i.coords[-1]))])
                if tj[1] - tj[0] < 0.2:
                    continue
                sub_j = substring(gj, tj[0], tj[1])
                ci, cj = list(sub_i.coords), list(sub_j.coords)
                if Point(ci[-1]).distance(Point(cj[-1])) < Point(ci[-1]).distance(Point(cj[0])):
                    cj = cj[::-1]
                poly = Polygon(ci + cj)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty or poly.geom_type != 'Polygon' or poly.area < 0.05:
                    continue
                if abs(poly.area / max(sub_i.length, 1e-6) - w) > 0.35 * w + 0.05:
                    continue
                cands.append(dict(poly=poly, w=w, run=run, li=sub_i, lj=sub_j))

    if not cands:
        return None
    cands.sort(key=lambda d: -d['run'])
    kept = []
    for c in cands:                      # 서로 55% 넘게 겹치면 중복
        if any(k['poly'].intersects(c['poly']) and
               k['poly'].intersection(c['poly']).area >
               0.55 * min(k['poly'].area, c['poly'].area) for k in kept):
            continue
        kept.append(c)
    merged = unary_union([k['poly'] for k in kept]).buffer(0)
    tot = sum(k['run'] for k in kept)
    ws = [k['w'] for k in kept]
    return dict(poly=merged, w=float(np.median(ws)), run=tot, n=len(kept),
                wmin=min(ws), wmax=max(ws))


def main():
    ids = [x.strip() for x in (argval('--ids', '') or '').split(',') if x.strip()]
    if has('--from-13'):
        want = argval('--from-13', '사람확인')
        with open(CHK, encoding='utf-8-sig') as f:
            ids += [r['지장물번호'] for r in csv.DictReader(f) if r['판정'] == want]
    ids = list(dict.fromkeys(ids))
    named = set(ids)            # --ids 로 직접 부른 건 재파싱이 우선(채택보다 앞선다)
    adopt = {}
    if has('--adopt'):
        # 13 이 낸 복원안처럼 **이미 사람이 승인한 도형**을 그대로 채택한다.
        # 형식: <gpkg>::<layer>  (키는 지장물번호)
        spec = argval('--adopt', '')
        gp_, _, ly_ = spec.partition('::')
        for pr_, g_ in read_layer(gp_, ly_ or '확인_복원안'):
            k_ = pr_.get('지장물번호')
            if k_:
                adopt[k_] = g_
        print('채택원 %s::%s 에서 %d건' % (os.path.basename(gp_), ly_, len(adopt)))
    if not ids and not adopt:
        print(__doc__)
        return
    ids += [k for k in adopt if k not in ids]

    rows_all = read_layer(GP, DEF_POLY)
    bykey = {pr.get('지장물번호'): (pr, g) for pr, g in rows_all}
    print('대상 %d건' % len(ids))

    print('원도 읽는 중 …')
    doc = load_dxf(DXF)
    lines = dxf_lines(doc)
    ltree = STRtree([g for _, g in lines])

    out, items, dropped = [], [], []
    for key in ids:
        if key not in bykey:
            print('  %-10s 없음' % key)
            continue
        pr, g = bykey[key]
        w0 = pr.get('규격_폭m')
        try:
            w0 = float(w0)
        except (TypeError, ValueError):
            w0 = None
        if not w0:
            print('  %-10s 표기폭 없음 — 건너뜀' % key)
            continue
        if key in adopt and key not in named:  # 승인된 도형은 재파싱하지 않고 그대로
            ag = adopt[key]
            aw = band_width(ag)
            print('  %-10s 표기 %-5s → 채택(승인된 복원안) 폭 %s  면적 %6.2f→%6.2f'
                  % (key, w0, ('%.2f' % aw) if aw else '?', g.area, ag.area))
            out.append(dict(지장물번호=key, 표기=pr.get('표기') or '', 표기폭=w0,
                            현재면적=round(g.area, 2),
                            현재띠폭=round(band_width(g), 2) if band_width(g) else '',
                            현재구멍=len(g.interiors), 결과='채택',
                            재파싱면적=round(ag.area, 2),
                            재파싱폭=round(aw, 2) if aw else '', 덮은길이='',
                            폭오차pct='', 비고='승인된 복원안 그대로'))
            items.append((key, pr, g, ag, aw or w0))
            continue
        env = g.buffer(max(3.0, 2.0 * w0))
        near = [lines[int(i)][1] for i in ltree.query(env)]
        near = [ln for ln in near if ln.intersection(env).length > 0.3]
        ch = snap_chains(near, wmax=w0 * WIDE[1])
        L0, _ = band_dims(g)
        res = pair_band(ch, w0, L0 or g.length / 2)

        if res is not None:
            res['poly'], res['drop'] = keep_here(res['poly'], g, res['w'])
        cur_w = band_width(g)
        nholes = len(g.interiors)
        if res is None:
            print('  %-10s 표기 %-6s 체인쌍 못 찾음 (주변 체인 %d)' % (key, w0, len(ch)))
            out.append(dict(지장물번호=key, 표기=pr.get('표기') or '', 표기폭=w0,
                            현재면적=round(g.area, 2), 현재띠폭=round(cur_w, 2) if cur_w else '',
                            현재구멍=nholes, 결과='실패', 재파싱면적='', 재파싱폭='',
                            덮은길이='', 폭오차pct='', 비고='주변 체인 %d개에서 짝 없음' % len(ch)))
            continue
        np_, nw = res['poly'], res['w']
        err = abs(nw - w0) / w0 * 100
        print('  %-10s 표기 %-5s → 폭 %.2f (오차 %4.1f%%)  면적 %6.2f→%6.2f  덮은길이 %.1f m  짝 %d  창 %s'
              % (key, w0, nw, err, g.area, np_.area, res["run"], res["n"], res["창"]))
        out.append(dict(지장물번호=key, 표기=pr.get('표기') or '', 표기폭=w0,
                        현재면적=round(g.area, 2), 현재띠폭=round(cur_w, 2) if cur_w else '',
                        현재구멍=nholes, 결과='성공', 재파싱면적=round(np_.area, 2),
                        재파싱폭=round(nw, 2), 덮은길이=round(res['run'], 1),
                        폭오차pct=round(err, 1),
                        비고=' '.join(filter(None, [
                            '구멍 %d개 제거' % nholes if nholes else '',
                            '자리밖 조각 %d개 제외(%s㎡)' % (
                                len(res.get('drop', [])),
                                '+'.join('%.1f' % q.area for q in res['drop']))
                            if res.get('drop') else '']))))
        items.append((key, pr, g, np_, nw))
        for q in res.get('drop', []):
            dropped.append((key, q))

    if not out:                                    # 빈 결과에서 IndexError 방지
        print('대상 중 레이어에 있는 것이 없다 — 번호 확인')
        return
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print('→ %s' % OUT)

    if has('--export'):
        export(items, dropped)
    if has('--write'):
        apply_write(items)
    else:
        print('※ 정본 미반영 — 반영하려면 --write')


def export(items, dropped=()):
    dst = out_path('지장물_14재파싱_%s.gpkg' % datetime.datetime.now().strftime('%y%m%d'))
    if os.path.exists(dst):
        os.remove(dst)
    props = collections.OrderedDict([('지장물번호', 'str:32'), ('표기', 'str:64'),
                                     ('표기폭', 'float'), ('폭', 'float'), ('면적', 'float')])
    if dropped:
        dp = collections.OrderedDict([('지장물번호', 'str:32'), ('면적', 'float')])
        with fiona.open(dst, 'w', driver='GPKG', layer='버린조각',
                        schema={'geometry': 'Polygon', 'properties': dp},
                        crs=CRS.from_epsg(epsg())) as d:
            d.writerecords([{'geometry': mapping(q), 'properties': {
                '지장물번호': k, '면적': round(q.area, 2)}} for k, q in dropped])
    for layer, pick in (('원본', 2), ('재파싱안', 3)):
        feats = []
        for key, pr, g, np_, nw in items:
            gg = (g, np_)[0 if pick == 2 else 1]
            feats.append({'geometry': mapping(gg), 'properties': {
                '지장물번호': key, '표기': pr.get('표기') or '',
                '표기폭': float(pr.get('규격_폭m')), '폭': round(nw, 3),
                '면적': round(gg.area, 2)}})
        if feats:
            with fiona.open(dst, 'w', driver='GPKG', layer=layer,
                            schema={'geometry': 'Polygon', 'properties': props},
                            crs=CRS.from_epsg(epsg())) as d:
                d.writerecords(feats)
    print('→ %s' % dst)


def apply_write(items):
    if not items:
        print('반영할 것 없음')
        return
    backup(GP)
    tag = datetime.datetime.now().strftime('%y%m%d')
    col = note_field()
    if col not in columns(GP, DEF_POLY):
        add_column(GP, DEF_POLY, col)
    repl = []
    for key, pr, g, np_, nw in items:
        p2 = dict(pr)
        w_old = pr.get('규격_폭m')
        p2['규격_폭m'] = round(nw, 2)
        p2[col] = ('재파싱: 평행2선 사이 채움, 폭 %s→%.2f, 면적 %.1f→%.1f㎡ [14_reparse %s]'
                   % (w_old, nw, g.area, np_.area, tag))
        repl.append((key, np_, p2))         # replace_features = [(키, geom, props)]
    if not has('--cl-only'):
        n = replace_features(GP, DEF_POLY, repl)
        print('폴리곤 반영 %d건' % n)
    # 중심선도 같이 — 안 하면 지장물 없는 자리에 옛 선만 남는다
    cls = [(k, poly_centerline(g2), pr2) for k, g2, pr2 in repl]
    try:
        m = replace_features(GP, DEF_CL, cls)
        print('중심선 반영 %d건' % m)
    except Exception as e:
        print('중심선 갱신 실패:', e)
    for k, g2, _ in repl:
        c = poly_centerline(g2)
        print('    %-10s 폴리곤 %6.2f㎡  중심선 %6.2f m  폴리곤밖 %.2f'
              % (k, g2.area, c.length, c.difference(g2.buffer(0.02)).length))


if __name__ == '__main__':
    main()
