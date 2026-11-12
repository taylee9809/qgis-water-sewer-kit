# -*- coding: utf-8 -*-
"""13 구거 기준 단선버퍼 검증 — 구거·하천 필지를 '기준자'로 써서 되접힌 폴리곤을 잡아낸다.

왜 필요한가
-----------
단선버퍼로 만든 폴리곤은 **박스 양쪽 벽선을 한 줄로 이어붙인 선**을 버퍼한 것이라
화면에서 머리핀처럼 되접힌다(사례 13_사례_되접힘.md). 되접힘 자체는 도면만 봐서는 잡기
어렵지만, 이런 구조물은 대개 **구거를 복개한 암거**라 지적도 지목 '구/천/유' 필지가
선형·연장의 독립 근거가 된다. 눈대중 대신 이걸 쓴다.

무엇을 하는가 (GPKG 는 읽기만 — 아무것도 안 고친다)
  1. 되접힘 판정   — 중심선 꺾임각 + 비인접 구간 자기겹침
  2. 벽선 복원     — 원도 배수시설물 선을 체인화해 2개면 체인쌍 폴리곤 재구성
  3. 폭 근거 대조  — 표기폭 / 복원안 띠폭 / 마구리 실측 / .flo X·x 토큰 크기
  4. 구거 정합     — 현재·복원안의 구거 필지 포함률
  5. 판정          — 정상 · 자동복원가능 · 사람확인

사용
    python -X utf8 13_gugeo_check.py [--config sewer.config.json] [--all] [--fig] [--export]
        [--export-to <gpkg>] [--jijeok <지적.shp>] [--gpkg ...] [--poly ...] [--cl ...]
        [--src <경량.dxf>] [--qc <05 결과.csv>] [--prof <06 결과.csv>]
      --all        단선버퍼 플래그 없는 것까지 전수(느림). 기본은 단선버퍼 ∩ 구거.
      --fig        판정이 '형상정상'이 아닌 건의 그림을 <산출>/13_fig/ 에 저장.
      --export     확인 대상만 QGIS 확인 레이어(GPKG)로. 스타일은 QGIS 에서 따로 입힌다.
      지적         --jijeok > layers.parcel (GPKG·SHP·PostGIS). 지번 필드 = fields.parcel.jibun
                   (기본 JIBUN, 연속지적도 형식 '123-4 구' — 끝 글자가 지목). SHP 인코딩은
                   survey.parcel_encoding (기본 cp949).
      --qc/--prof  기본 <산출>/05_qc_flags.csv · 06_profile_check.csv (05·06 을 먼저 돌릴 것)
출력
    <산출>/13_gugeo_check.csv · 13_fig/<지장물번호>.png · 지장물_13확인_<날짜>.gpkg
"""
import os
import sys
import csv
import math
import collections

TOOL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL)
from _common import *                                      # noqa: E402,F403
from _common import _ang                                   # noqa: E402  (언더스코어는 * 로 안 옴)

from shapely.geometry import Polygon, LineString, Point    # noqa: E402
from shapely.ops import linemerge, unary_union             # noqa: E402
from shapely.strtree import STRtree                        # noqa: E402

GP, DEF_POLY, DEF_CL = obstacle_src()
DXF = light_dxf()
QC = argval('--qc') or out_path('05_qc_flags.csv')
PROF = argval('--prof') or out_path('06_profile_check.csv')
JIBUN = fld('parcel', 'jibun', 'JIBUN')
OUT = out_path('13_gugeo_check.csv')
FIGDIR = out_dir('13_fig') if has('--fig') else None


def parcel_spec():
    """지적 레이어 spec — --jijeok(SHP/GPKG 경로) > layers.parcel."""
    j = argval('--jijeok')
    if j:
        return {'kind': 'gpkg', 'path': j, 'layer': os.path.splitext(os.path.basename(j))[0]}
    c = cfg()
    if not c.has_layer('parcel'):
        raise kitconfig.ConfigError('지적 레이어가 없다 — --jijeok 로 주거나 layers.parcel 을 설정할 것.')
    return c.layer('parcel')

GU_SUFFIX = ('구', '천', '유')      # 지목: 구거 · 하천 · 유지
GU_MIN = 0.50                       # 구거 필지에 이만큼 들어 있으면 '구거 위'
TURN_MAX = 100.0                    # 중심선 꺾임각이 이보다 크면 되접힘 의심
WIDTH_TOL = 0.15                    # 폭 근거끼리 ±15% 안이면 일치


# ─────────────────────────────────────────────────────── 판정 도구
def max_turn(coords):
    """중심선에서 가장 심한 꺾임각(도). 180 에 가까울수록 제자리로 되돌아온 것."""
    cs = [tuple(c[:2]) for c in coords]
    worst = 0.0
    for i in range(1, len(cs) - 1):
        a = _ang(cs[i - 1], cs[i])
        b = _ang(cs[i], cs[i + 1])
        worst = max(worst, abs((a - b + 180) % 360 - 180))
    return worst


def self_overlap(cl, w):
    """중심선이 제 몸에 되돌아와 겹치는가(되접힘의 본질).

    ★ 함정: '인덱스가 안 붙어 있다'만으로 보면 정점이 조밀한 **직선**도 걸린다
    (i 와 i+2 가 당연히 가깝다 → 실제로 오판한 적 있음). 그래서 두 구간이
    **선을 따라 충분히 떨어져 있는데도** 직선거리로 가까운 경우만 인정한다."""
    cs = list(cl.coords)
    segs = [LineString([cs[i], cs[i + 1]]) for i in range(len(cs) - 1)]
    if len(segs) < 3:
        return False
    acc, s = [0.0], 0.0
    for g in segs:
        s += g.length
        acc.append(s)
    mid = [(acc[i] + acc[i + 1]) / 2 for i in range(len(segs))]
    far = max(3.0 * w, 2.0)
    for i in range(len(segs)):
        for j in range(i + 2, len(segs)):
            if abs(mid[j] - mid[i]) > far and segs[i].distance(segs[j]) < w * 0.9:
                return True
    return False


def split_reversals(ls, thr=110.0, min_len=1.0):
    """되돌림 지점에서 체인을 끊는다.

    ★ 함정: 박스 양쪽 벽선은 토출부에서 **끝점을 공유**하는 일이 잦다. 그러면 그 절점이
    차수 2 라 linemerge 가 두 벽선을 한 줄로 붙여버리고(머리핀), 벽선쌍 탐지가 통째로
    실패한다(13_사례_되접힘.md). 꺾임각이 thr 넘는 정점에서 잘라 벽선을 되살린다."""
    cs = [tuple(c[:2]) for c in ls.coords]
    cut = [0]
    for i in range(1, len(cs) - 1):
        if abs((_ang(cs[i - 1], cs[i]) - _ang(cs[i], cs[i + 1]) + 180) % 360 - 180) >= thr:
            cut.append(i)
    cut.append(len(cs) - 1)
    out = []
    for s, e in zip(cut, cut[1:]):
        if e - s >= 1:
            seg = LineString(cs[s:e + 1])
            if seg.length >= min_len:
                out.append(seg)
    return out or [ls]


def chain_pair(lines, poly, tol=1.2):
    """폴리곤 주변 원도 선을 체인화해 '벽선 2줄'이면 (체인A, 체인B) 반환."""
    env = poly.buffer(tol)
    near = [g for _, g in lines if g.intersection(env).length > g.length * 0.6]
    if len(near) < 2:
        return None
    cs = []
    for c in chains(near, min_len=1.0):
        if c.geom_type == 'LineString':
            cs += split_reversals(c)
    cs.sort(key=lambda g: -g.length)
    if len(cs) < 2:
        return None
    a, b = cs[0], cs[1]
    if b.length < a.length * 0.35:          # 짝이 될 만큼 길지 않으면 벽선쌍 아님
        return None
    return a, b


def build_band(a, b):
    """두 벽선으로 띠 폴리곤. b 의 방향을 뒤집어 보고 더 멀쩡한 쪽을 쓴다."""
    best = None
    for rev in (True, False):
        bc = list(b.coords)[::-1] if rev else list(b.coords)
        ring = list(a.coords) + bc
        if len(ring) < 3:
            continue
        try:
            p = Polygon(ring + [ring[0]])
        except Exception:
            continue
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty or p.geom_type != 'Polygon' or p.area <= 0:
            continue
        # 띠다운 쪽: 길이/폭 비가 크고 둘레가 짧은 쪽(되접히면 둘레가 길어진다)
        L, w = band_dims(p)
        if not L or not w or w <= 0:
            continue
        score = (p.area / p.exterior.length, L / w)
        if best is None or score > best[0]:
            best = (score, p)
    return best[1] if best else None


def near_or_none(a, b):
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return abs(a - b) / max(a, b)


def main():
    do_all = has('--all')
    do_fig = has('--fig')

    # ── 05 플래그
    flags = collections.defaultdict(set)
    with open(QC, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            flags[r['지장물번호']].add(r['플래그'])

    # ── 06 종단 토큰 크기 (지장물번호 → 크기)
    flo_sz = {}
    if os.path.exists(PROF):
        with open(PROF, encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                k = (r.get('폴리곤') or '').strip()
                try:
                    v = float(r.get('크기') or '')
                except ValueError:
                    continue
                if k:
                    flo_sz.setdefault(k, v)

    # ── 지장물
    polys = read_layer(GP, DEF_POLY)
    cls = {pr.get('지장물번호'): g for pr, g in read_layer(GP, DEF_CL)}
    print('지장물 폴리곤 %d건 · 중심선 %d건' % (len(polys), len(cls)))

    xs = [g.bounds for _, g in polys if g is not None and not g.is_empty]
    bbox = (min(b[0] for b in xs) - 5, min(b[1] for b in xs) - 5,
            max(b[2] for b in xs) + 5, max(b[3] for b in xs) + 5)

    # ── 구거·하천·유지 필지
    gu, gu_name = [], []
    enc = (cfg(required=False).get('survey.parcel_encoding') if cfg(required=False) else None) \
        or 'cp949'
    for props, geom in read_spec(parcel_spec(), bbox=bbox, encoding=enc):
        jb = (props.get(JIBUN) or '').strip()
        if jb.endswith(GU_SUFFIX):
            gu.append(geom)
            gu_name.append(jb)
    print('구거·하천·유지 필지 %d개' % len(gu))
    tree = STRtree(gu)

    # ── 원도 선
    print('원도 읽는 중 …')
    doc = load_dxf(DXF)
    lines = dxf_lines(doc)
    ltree = STRtree([g for _, g in lines])
    print('원도 배수시설물 선 %d개' % len(lines))

    rows, tally, geoms = [], collections.Counter(), {}
    for pr, g in polys:
        key = pr.get('지장물번호')
        if g is None or g.is_empty or g.area <= 0:
            continue
        fl = flags.get(key, set())
        if not do_all and '단선버퍼' not in fl:
            continue

        idx = tree.query(g)
        ov = unary_union([gu[i] for i in idx]).intersection(g).area if len(idx) else 0.0
        gu_rate = ov / g.area
        names = ','.join(sorted({gu_name[i] for i in idx
                                 if gu[i].intersection(g).area > 0.05 * g.area}))
        if not do_all and gu_rate < GU_MIN:
            continue

        cl = cls.get(key)
        turn = max_turn(cl.coords) if cl is not None and cl.geom_type == 'LineString' else None
        w_now = band_width(g)
        folded = bool(turn and turn >= TURN_MAX) or bool(
            cl is not None and cl.geom_type == 'LineString' and w_now and self_overlap(cl, w_now))

        # 원도 벽선쌍 복원
        li = [(lines[i][0], lines[i][1]) for i in ltree.query(g.buffer(1.5))]
        cp = chain_pair(li, g)
        new = build_band(*cp) if cp else None
        w_new = band_width(new) if new is not None else None
        cap = None
        if cp:
            a, b = cp
            # 마구리(끝 막음변) = 두 벽선 끝점 사이 거리. 단, 벽선이 토출부에서 끝점을
            # 공유하면 '반대쪽 끝까지의 거리'가 잡혀 폭으로 오인된다(사례: 12.3 m) →
            # 폭이 될 수 있는 범위(≤8 m)만 인정하고 아니면 비워 둔다.
            ends = [math.dist(a.coords[0], b.coords[0]), math.dist(a.coords[0], b.coords[-1]),
                    math.dist(a.coords[-1], b.coords[0]), math.dist(a.coords[-1], b.coords[-1])]
            ends = [e for e in ends if 0.05 < e <= 8.0]
            cap = min(ends) if ends else None
        gu_new = None
        if new is not None and new.area > 0:
            i2 = tree.query(new)
            gu_new = (unary_union([gu[i] for i in i2]).intersection(new).area / new.area
                      if len(i2) else 0.0)

        w_tag = pr.get('규격_폭m')
        w_tag = float(w_tag) if w_tag not in (None, '') else None
        w_flo = flo_sz.get(key)

        agree = [v for v in (near_or_none(w_new, cap), near_or_none(w_new, w_flo),
                             near_or_none(cap, w_flo)) if v is not None]
        n_ok = sum(1 for v in agree if v <= WIDTH_TOL)

        if not folded:
            verdict = '형상정상'      # 형상만 정상이라는 뜻 — 폭은 아래 '폭의심'을 볼 것
        elif new is not None and n_ok >= 1 and (gu_new or 0) >= (gu_rate - 0.05):
            verdict = '자동복원가능'
        else:
            verdict = '사람확인'
        tally[verdict] += 1

        # 폭은 형상과 별개다. 표기폭(내폭)과 종단 토큰 크기(외폭)가 벌어지면 의심.
        dw = near_or_none(w_tag, w_flo)
        w_doubt = 'Y' if (dw is not None and dw > WIDTH_TOL) else ''
        if w_doubt:
            tally['└ 폭의심'] += 1

        rows.append(dict(
            지장물번호=key, 종류=pr.get('종류') or '', 표기=pr.get('표기') or '',
            판정=verdict, 되접힘='Y' if folded else '',
            최대꺾임각=round(turn, 1) if turn else '',
            현재면적=round(g.area, 1), 현재띠폭=round(w_now, 2) if w_now else '',
            복원면적=round(new.area, 1) if new is not None else '',
            복원띠폭=round(w_new, 2) if w_new else '',
            마구리실측=round(cap, 2) if cap else '',
            표기폭=w_tag if w_tag else '', 종단크기=w_flo if w_flo else '',
            폭근거일치수=n_ok, 폭의심=w_doubt,
            구거포함_현재=round(gu_rate * 100), 구거포함_복원=round(gu_new * 100) if gu_new is not None else '',
            구거필지=names, 플래그='|'.join(sorted(fl)),
        ))

        if do_fig and verdict != '형상정상':
            draw(key, g, new, cp, [gu[i] for i in idx], FIGDIR)
        if verdict != '형상정상' or w_doubt:
            geoms[key] = (g, new)

    rows.sort(key=lambda r: (r['판정'] != '사람확인', -r['현재면적']))
    if not rows:                                   # 빈 결과에서 IndexError 방지
        print('\n대상 0건 — 단선버퍼 ∩ 구거 필지가 없다(05 결과·지적 레이어 확인)')
        return
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print()
    print('대상 %d건' % len(rows))
    for k, v in tally.most_common():
        print('   %-10s %d' % (k, v))
    print()
    print('→ %s' % OUT)
    if do_fig:
        print('→ %s' % FIGDIR)
    if has('--export'):
        export_gpkg(rows, geoms)


def export_gpkg(rows, geoms):
    """확인 대상만 QGIS 확인 레이어로. 현재 도형과 복원안을 각각 따로 둔다
       (겹쳐 놓으면 어느 쪽이 제안인지 화면에서 구분이 안 된다)."""
    out = argval('--export-to', out_path(   # --export 는 값 없는 플래그
        '지장물_13확인_%s.gpkg' % datetime.datetime.now().strftime('%y%m%d')))
    if os.path.exists(out):
        os.remove(out)
    props = collections.OrderedDict([
        ('지장물번호', 'str:32'), ('종류', 'str:32'), ('표기', 'str:64'),
        ('판정', 'str:16'), ('되접힘', 'str:4'), ('최대꺾임각', 'float'),
        ('현재띠폭', 'float'), ('복원띠폭', 'float'), ('마구리실측', 'float'),
        ('표기폭', 'float'), ('종단크기', 'float'), ('폭의심', 'str:4'),
        ('구거포함_현재', 'int'), ('구거포함_복원', 'int'),
        ('구거필지', 'str:64'), ('해야할일', 'str:128'),
    ])

    def todo(r):
        if r['판정'] == '자동복원가능':
            return '복원안으로 교체 — 근거 충분'
        if r['판정'] == '사람확인':
            return ('복원안 있음, 폭 결정 필요' if r['복원면적'] != ''
                    else '원도 확인 필요 — 벽선쌍 복원 실패')
        return '형상은 정상, 폭 필드만 재검토'

    def pack(r, g):
        pr = {}
        for k, t in props.items():
            if k == '해야할일':
                pr[k] = todo(r)
                continue
            v = r.get(k, '')
            if v == '':
                pr[k] = None
            elif t.startswith('float'):
                pr[k] = float(v)
            elif t.startswith('int'):
                pr[k] = int(v)
            else:
                pr[k] = str(v)
        return {'geometry': mapping(g), 'properties': pr}

    n1 = n2 = 0
    for layer, pick in (('확인_현재', 0), ('확인_복원안', 1)):
        feats = []
        for r in rows:
            gg = geoms.get(r['지장물번호'])
            if not gg or gg[pick] is None:
                continue
            feats.append(pack(r, gg[pick]))
        if not feats:
            continue
        with fiona.open(out, 'w', driver='GPKG', layer=layer,
                        schema={'geometry': 'Polygon', 'properties': props},
                        crs=CRS.from_epsg(epsg())) as dst:
            dst.writerecords(feats)
        if pick == 0:
            n1 = len(feats)
        else:
            n2 = len(feats)
    print('→ %s  (확인_현재 %d · 확인_복원안 %d)' % (out, n1, n2))


def draw(key, cur, new, cp, gus, outdir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = 'Malgun Gothic'
    plt.rcParams['axes.unicode_minus'] = False
    os.makedirs(outdir, exist_ok=True)
    cx, cy = cur.centroid.x, cur.centroid.y
    R = max(25.0, max(cur.bounds[2] - cur.bounds[0], cur.bounds[3] - cur.bounds[1]) * 0.8)
    fig, ax = plt.subplots(figsize=(9, 9))
    for g in gus:
        x, y = g.exterior.xy
        ax.fill(x, y, fc='#8ecae6', alpha=.45, ec='#1f77b4', lw=1.6, zorder=1)
    if cp:
        for c, col in zip(cp, ('k', '#555')):
            x, y = c.xy
            ax.plot(x, y, '-', color=col, lw=2.4, zorder=6)
    x, y = cur.exterior.xy
    ax.fill(x, y, fc='red', alpha=.38, ec='darkred', lw=1.4, zorder=4, label='현재(단선버퍼)')
    if new is not None:
        x, y = new.exterior.xy
        ax.plot(x, y, '-', color='green', lw=2.2, zorder=7, label='체인쌍 복원안')
    ax.set_xlim(cx - R, cx + R)
    ax.set_ylim(cy - R, cy + R)
    ax.set_aspect('equal')
    ax.grid(alpha=.25)
    ax.legend(loc='upper right', fontsize=9)
    ax.set_title('%s  (파란 면 = 구거·하천 필지)' % key, fontsize=12)
    fig.savefig(os.path.join(outdir, '%s.png' % key), dpi=100, bbox_inches='tight')
    plt.close(fig)


if __name__ == '__main__':
    main()
