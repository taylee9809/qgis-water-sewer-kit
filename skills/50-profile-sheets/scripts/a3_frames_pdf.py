# -*- coding: utf-8 -*-
"""[변형] 사람이 DXF 에 직접 그린 A3 프레임대로 → A3 PDF (자동 분할 대신)

종단면도 DXF 모델공간에 페이지 구분 프레임 블록(기본 이름 'EEE', A3 비율 사각형)을 INSERT 해 두면
그 구간을 그대로 한 장으로 쓴다.
- 장마다 실제 도면 내용 bbox 를 A3 유효영역에 비율 유지 최대 확대(장마다 축척이 다름, `-u` 로 통일).
- 계통마다 기준고(Δ)를 다시 잡아 세로 여백을 흡수하고 표척 눈금값을 Δ 만큼 다시 쓴다.
- 페이지 좌단은 프레임 좌단이 아니라 첫 계통 표제란 좌단(프레임 좌단을 쓰면 최대 80 unit 이 여백).
- 표척 연장은 페이지 꼭대기가 아니라 그 계통 내용 상단까지.
- 표제: `--ri-map` 을 주면 「리명 − 번호(리별 페이지 순) | 노선-시작관~끝관」을 자동으로 만든다
  (번호는 페이지당 하나 — 한 장에 계통이 셋이어도 번호 하나). `--titles` 로 직접 줄 수도 있다.
- 계통 원점: 표제 블록 INSERT 가 있으면 그 x, 블록이 분해(explode)된 도면이면 표척(j_pole) 묶음
  오른쪽 끝 + 10.
- ★ 회전 TEXT 는 matplotlib 글자로 그린다(ezdxf 가 기울어진 글자의 구멍을 칠하는 문제).

사용: python a3_frames_pdf.py <종단면도.dxf> [-o 출력.pdf] [-t 제목] [--ri-map ri_map.json] [-u] [-p 1,6]
"""
import argparse
import collections
import math
import os
import re

import profcommon as PC

PT = 72 / 25.4
TITLE_FS, SUB_FS, CHIP_FS = 33.0, 15.0, 27.0       # 리명·부제·번호칩 숫자(pt)
CHIP_X, CHIP_Y, CHIP_S = 7.5, 6.0, 14.5             # 번호 칩 위치·크기(mm)
POLE_RIGHT = -10.0                                  # 표척 오른쪽 선 = 원점 − 10
NAME_DX = -41.0                                     # 'N장' 계통 이름표 = 원점 − 41 (h ≥ 5)


class Rec:
    __slots__ = ('e', 'x1', 'x2', 'y1', 'y2', 'seg', 'kind', 'col')


def hatch_boxes(h):
    """SOLID HATCH 의 path 하나하나를 사각형 bbox 로(도면 전체를 덮는 HATCH 를 기둥별로 나눠 그린다)."""
    out = []
    for pa in h.paths:
        xs, ys = [], []
        if hasattr(pa, 'vertices'):
            for v in pa.vertices:
                xs.append(v[0])
                ys.append(v[1])
        else:
            for ed in pa.edges:
                for q in (getattr(ed, 'start', None), getattr(ed, 'end', None)):
                    if q is not None:
                        xs.append(q[0])
                        ys.append(q[1])
        if xs:
            out.append((min(xs), max(xs), min(ys), max(ys)))
    return out


def frames_of(doc, msp, name):
    blk = doc.blocks.get(name)
    if blk is None:
        raise SystemExit('프레임 블록 "%s" 가 없다 — --frame-block 으로 이름을 줄 것' % name)
    pts = None
    for x in blk:
        if x.dxftype() == 'LWPOLYLINE':
            pts = x.get_points('xy')
    if not pts:
        raise SystemExit('프레임 블록 "%s" 안에 LWPOLYLINE 사각형이 없다' % name)
    bp = blk.block.dxf.base_point
    out = []
    for e in msp.query('INSERT[name=="%s"]' % name):
        ix, iy = e.dxf.insert.x, e.dxf.insert.y
        sx, sy = e.dxf.xscale, e.dxf.yscale
        wx = [ix + (q[0] - bp.x) * sx for q in pts]
        wy = [iy + (q[1] - bp.y) * sy for q in pts]
        out.append((min(wx), max(wx), min(wy), max(wy)))
    out.sort()
    return out


def segments(msp, S):
    """계통 원점 x 목록 + 이름. 표제 블록이 있으면 그 INSERT, 없으면 표척 묶음."""
    st = S['strip']
    L = st['layers']
    tb = st['title_block']
    x0s = sorted(e.dxf.insert.x for e in msp.query('INSERT[name=="%s"]' % tb))
    if not x0s:
        xs = []
        for e in msp:
            if e.dxf.layer != L['pole']:
                continue
            t = e.dxftype()
            if t == 'LINE':
                xs.append(max(e.dxf.start.x, e.dxf.end.x))
            elif t == 'LWPOLYLINE':
                xs.append(max(q[0] for q in e.get_points('xy')))
            elif e.dxf.hasattr('insert'):
                xs.append(e.dxf.insert.x)
        if not xs:
            raise SystemExit('계통 원점을 못 찾았다 — 표제 블록 "%s" 도 표척 레이어 "%s" 도 없다'
                             % (tb, L['pole']))
        xs.sort()
        cl = [[xs[0]]]
        for v in xs[1:]:
            if v - cl[-1][-1] > 60:
                cl.append([])
            cl[-1].append(v)
        x0s = [c[-1] - POLE_RIGHT for c in cl]
    names = {}
    for e in msp.query('TEXT[layer=="%s"]' % L['memo']):
        if (e.dxf.height or 0) >= 5:
            names[e.dxf.insert.x] = (e.dxf.text or '').strip()
    segs = []
    for i, x0 in enumerate(x0s):
        nm = None
        for nx, nt in names.items():
            if abs((x0 + NAME_DX) - nx) < 3.0:
                nm = nt
        segs.append(dict(x0=x0, name=nm))      # ★ 이름 없는 계통에 순번을 채우면 번호가 밀린다
    return segs


def make_titles(msp, pages, segs, seg_of, ri, pipe_re, colors):
    """리 매핑으로 페이지 제목: 페이지 대표 리 = 계통들의 다수결, 번호 = 리별 페이지 순."""
    names_by = collections.defaultdict(set)
    for e in msp.query('TEXT'):
        t = (e.dxf.text or '').strip()
        if pipe_re.match(t):
            names_by[seg_of(e.dxf.insert.x)].add(t)
    seg_ri, seg_span = {}, {}
    for i in range(len(segs)):
        nm = sorted(names_by.get(i, []))
        if not nm:
            seg_ri[i] = seg_span[i] = None
            continue
        c = collections.Counter(ri[n][0] for n in nm if n in ri)
        seg_ri[i] = c.most_common(1)[0][0] if c else None
        route = nm[0].rsplit('-', 1)[0]
        a, b = nm[0].rsplit('-', 1)[1], nm[-1].rsplit('-', 1)[1]
        seg_span[i] = "%s-%s~%s" % (route, a, b) if a != b else "%s-%s" % (route, a)
    out, cnt = [], collections.Counter()
    pal = iter(PC.PALETTE * 4)
    for pg in pages:
        c = collections.Counter(seg_ri[i] for i in pg['segs'] if seg_ri.get(i))
        r = c.most_common(1)[0][0] if c else None
        no = 0
        if r:
            cnt[r] += 1
            no = cnt[r]
            if r not in colors:
                colors[r] = next(pal)
        out.append({'ri': r or '', 'no': no,
                    'sub': '   /   '.join(seg_span[i] for i in pg['segs'] if seg_span.get(i)),
                    'color': colors.get(r, '#444444')})
    return out


def build(dxf_path, out_pdf, doc_title, S, args, titles=None, ri=None):
    st = S['strip']
    L = st['layers']
    FRAME_TOP, PROF_FLOOR, POLE_TOP = st['frame_top'], st['floor'], st['pole_top']
    STATION_Y = st['station_y']
    UPM = st['unit_h'] / st['unit_v']                # 1 m 당 세로 unit
    DELTA_STEP = S['a3']['delta_step']
    TITLE_W = args.title_w
    SEG_GRAB = args.seg_grab
    skip = set(args.skip_layers)
    pink = set(args.pink_layers)
    red = set(L['red'])
    doc = PC.load_dxf(dxf_path)
    msp = doc.modelspace()
    frames = frames_of(doc, msp, args.frame_block)
    segs = segments(msp, S)
    nseg = len(segs)
    seg_x0 = [s['x0'] for s in segs]

    def seg_of(x):
        i = 0
        for k, v in enumerate(seg_x0):
            if x >= v - SEG_GRAB:
                i = k
        return i

    def is_pole_label(e, x0):
        if e.dxftype() != 'TEXT' or e.dxf.layer != L['text'] or (e.dxf.height or 0) < 2.9:
            return False
        if abs(e.dxf.insert.x - (x0 + st['pole_label_dx'])) > 2.5:
            return False
        y = e.dxf.insert.y
        return y >= STATION_Y and abs((y - (STATION_Y + 1.0)) % 50.0) < 1.0

    def is_scale_text(e):
        t = (e.dxf.text or '').replace(' ', '') if e.dxftype() == 'TEXT' else ''
        return t.startswith('H=1:') or t.startswith('V=1:')

    recs, cols, pole_base = [], [], {}
    for e in msp:
        if e.dxftype() == 'INSERT' and e.dxf.name == args.frame_block:
            continue
        if e.dxf.layer in skip:
            continue
        if e.dxftype() == 'HATCH':
            if args.hatch_layer and e.dxf.layer == args.hatch_layer:
                cols.extend(hatch_boxes(e))
            continue
        if e.dxftype() == 'INSERT':
            if e.dxf.name != st['title_block']:
                continue
            bb = (e.dxf.insert.x - st['title_w'], e.dxf.insert.x, -5.0, POLE_TOP)
        else:
            bb = PC.ent_box(e)
        if bb is None:
            continue
        x1, x2, y1, y2 = bb
        si = seg_of((x1 + x2) / 2.0)
        x0 = seg_x0[si]
        if is_pole_label(e, x0):
            if abs(e.dxf.insert.y - (STATION_Y + 1.0)) < 1.0:
                try:
                    pole_base[si] = float(e.dxf.text)
                except ValueError:
                    pass
            continue                      # 재작성하므로 원본은 버림
        if is_scale_text(e):
            continue                      # 출력축척으로 재작성
        if e.dxftype() == 'TEXT' and e.dxf.layer == L['memo'] and (e.dxf.height or 0) >= 5:
            continue                      # 'N장' 이름표는 표제란에서 뺀다(폭이 줄어 축척이 3~4% 오른다)
        r = Rec()
        r.e, r.x1, r.x2, r.y1, r.y2, r.seg = e, x1, x2, y1, y2, si
        lay = e.dxf.layer
        r.col = 'r' if lay in red else ('p' if lay in pink else 'k')
        if lay == L['pole'] or e.dxftype() == 'INSERT':
            r.kind = 'pole'
        elif (e.dxftype() == 'LINE' and lay == L['station']
              and abs(min(y1, y2) - STATION_Y) < 0.3 and y2 > FRAME_TOP):
            r.kind = 'station'
        elif y2 <= FRAME_TOP:
            r.kind = 'fix'
        else:
            r.kind = 'float'
        recs.append(r)

    by_seg = collections.defaultdict(list)
    for r in recs:
        by_seg[r.seg].append(r)

    pages = []
    for fx0, fx1, fy0, fy1 in frames:
        sl = [i for i, x in enumerate(seg_x0) if fx0 - 2 <= x <= fx1 + 2]
        if sl:
            pages.append(dict(fx0=fx0, fx1=fx1, segs=sl))

    prof = {}
    for si in range(nseg):
        lo, hi = 1e18, -1e18
        for r in by_seg[si]:
            if r.kind == 'float':
                lo, hi = min(lo, r.y1), max(hi, r.y2)
            elif r.kind == 'station':
                hi = max(hi, r.y2)
        prof[si] = (lo, hi) if lo < 1e17 else (STATION_Y, FRAME_TOP)

    PAGE_W, PAGE_H = S['a3']['page']
    ML, MR, MB, MT = S['a3']['margin']
    US_W, US_H = PAGE_W - ML - MR, PAGE_H - MB - MT
    for pg in pages:
        sl = pg['segs']
        d0 = {si: max(0.0, math.floor((prof[si][0] - PROF_FLOOR) / DELTA_STEP) * DELTA_STEP) for si in sl}
        X0 = min(seg_x0[si] - TITLE_W for si in sl)
        X1 = max(r.x2 for si in sl for r in by_seg[si]) if any(by_seg[si] for si in sl) else X0 + 100
        Y0 = -6.0

        def top_of(d, _sl=sl):
            return max([POLE_TOP + 2.0] + [prof[si][1] - d[si] + 4.0 for si in _sl])

        W = (X1 - X0) + 3.0
        H = top_of(d0) - Y0
        s = min(US_W / W, US_H / H)
        slack = US_H / s - H               # 세로가 남으면 계통을 원 위치 쪽으로 되돌려 여백을 나눈다
        if slack > 2.0:
            back = math.floor(slack * 0.45 / DELTA_STEP) * DELTA_STEP
            d0 = {si: max(0.0, d0[si] - back) for si in sl}
            H = top_of(d0) - Y0
            s = min(US_W / W, US_H / H)
        pg.update(delta=d0, X0=X0, X1=X1, Y0=Y0, scale=s)
    if args.uniform and pages:
        s = min(q['scale'] for q in pages)
        for q in pages:
            q['scale'] = s

    colors = dict(S['a3']['ri_colors'] or {})
    if titles is None and ri:
        titles = make_titles(msp, pages, segs, seg_of, ri,
                             re.compile(args.pipe_pattern or st['pipe_pattern']), colors)
    print("%s : %d pages" % (doc_title, len(pages)))
    render(doc, pages, segs, by_seg, pole_base, prof, cols, out_pdf, doc_title, S, args, titles,
           dict(US_W=US_W, US_H=US_H, ML=ML, MR=MR, MB=MB, PAGE_W=PAGE_W, PAGE_H=PAGE_H, UPM=UPM))
    return pages, titles


def draw_titleblock(fig, t, s, n, ntot, S, K):
    import matplotlib.pyplot as plt
    PAGE_W, PAGE_H, MR = K['PAGE_W'], K['PAGE_H'], K['MR']
    st = S['strip']
    fp = PC.font_props(S['font'])
    fb = PC.font_props(S['font_bold'], S['font'])

    def X(mm):
        return mm / PAGE_W

    def Y(mm):                      # 위에서부터 mm
        return 1 - mm / PAGE_H
    col = t.get('color') or '#444444'
    ri, no, sub = t.get('ri', ''), t.get('no', 0), t.get('sub', '')
    tx = CHIP_X
    if no:
        fig.add_artist(plt.Rectangle((X(CHIP_X), Y(CHIP_Y + CHIP_S)), X(CHIP_S), CHIP_S / PAGE_H,
                                     fc=col, ec='none', zorder=6, transform=fig.transFigure))
        fig.text(X(CHIP_X + CHIP_S / 2), Y(CHIP_Y + CHIP_S / 2), str(no), ha='center', va='center',
                 fontproperties=fb, fontsize=CHIP_FS, color='white', zorder=7)
        tx = CHIP_X + CHIP_S + 4.5
    fig.text(X(tx), Y(CHIP_Y + CHIP_S * 0.60), ri, ha='left', va='baseline', fontproperties=fb,
             fontsize=TITLE_FS, color=col, zorder=7)
    if sub:
        fig.text(X(tx), Y(CHIP_Y + CHIP_S + 0.4), sub, ha='left', va='top', fontproperties=fb,
                 fontsize=SUB_FS, color='#333333', zorder=7)
    y = CHIP_Y + CHIP_S + SUB_FS / PT + 2.6
    fig.add_artist(plt.Line2D([X(CHIP_X), 1 - X(MR)], [Y(y), Y(y)], color=col, lw=1.6, zorder=6,
                              transform=fig.transFigure))
    fig.text(1 - X(MR), Y(CHIP_Y + 3.0), "A3   |   H=1:%.0f   V=1:%.0f   |   SHEET %d / %d"
             % (st['unit_h'] / s, st['unit_v'] / s, n, ntot), ha='right', va='top', fontproperties=fp,
             fontsize=10.5, color='#333333', zorder=7)


def render(doc, pages, segs, by_seg, pole_base, prof, cols, out_pdf, doc_title, S, args, titles, K):
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['pdf.fonttype'] = 42
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.patches import Rectangle
    from ezdxf.addons.drawing import RenderContext
    from ezdxf.addons.drawing.config import Configuration, ColorPolicy, LineweightPolicy, LinePolicy

    PC.setup_ezdxf_fonts(S)
    st = S['strip']
    STATION_Y, POLE_TOP = st['station_y'], st['pole_top']
    PAGE_W, PAGE_H = K['PAGE_W'], K['PAGE_H']
    US_W, US_H, ML, MB = K['US_W'], K['US_H'], K['ML'], K['MB']
    fp = PC.font_props(S['font'])
    base = dict(lineweight_policy=LineweightPolicy.ABSOLUTE, lineweight_scaling=1.0,
                line_policy=LinePolicy.ACCURATE)
    red = S['a3']['colors']['red']
    CFG = {'k': Configuration(color_policy=ColorPolicy.BLACK, **base),
           'r': Configuration(color_policy=ColorPolicy.CUSTOM, custom_fg_color=red + 'FF', **base),
           'p': Configuration(color_policy=ColorPolicy.CUSTOM, custom_fg_color=args.pink_color + 'FF', **base)}
    TXT = {'k': '#000000', 'r': red, 'p': args.pink_color}
    ctx = RenderContext(doc)
    ctx.current_layout_properties.set_colors('#FFFFFF')
    with PdfPages(out_pdf) as pdf:
        for n, pg in enumerate(pages, 1):
            if args.only and n not in args.only:
                continue
            s, d, sl = pg['scale'], pg['delta'], pg['segs']
            W, H = US_W / s, US_H / s
            cx = (pg['X0'] + pg['X1'] + 3.0) / 2.0
            x0 = min(cx - W / 2, pg['X0'] - 1.5)
            xlim = (x0, x0 + W)
            ylim = (pg['Y0'] - 1.0, pg['Y0'] - 1.0 + H)
            fig = plt.figure(figsize=(PAGE_W / 25.4, PAGE_H / 25.4))
            ax = fig.add_axes([ML / PAGE_W, MB / PAGE_H, US_W / PAGE_W, US_H / PAGE_H])
            for hx1, hx2, hy1, hy2 in cols:      # 형광 기둥(도면 밑)
                if hx2 < pg['X0'] - 1 or hx1 > pg['X1'] + 1:
                    continue
                ax.add_patch(Rectangle((hx1, hy1), hx2 - hx1, hy2 - hy1, fc=args.hatch_color, ec='none',
                                       alpha=0.55, zorder=0))
            ents, moved, stmod = {'k': [], 'r': [], 'p': []}, [], []
            for si in sl:
                dd = d[si]
                for r in by_seg[si]:
                    e = r.e
                    if r.kind == 'float' and dd:
                        e.translate(0, -dd, 0)
                        moved.append((e, dd))
                    elif r.kind == 'station' and dd:
                        p0, p1 = e.dxf.start, e.dxf.end
                        stmod.append((e, p0, p1))
                        lo, hi = (p0, p1) if p0.y < p1.y else (p1, p0)
                        e.dxf.start = (lo.x, STATION_Y, 0)
                        e.dxf.end = (hi.x, hi.y - dd, 0)
                    ents[r.col].append(e)
            for key in ('k', 'r', 'p'):
                if ents[key]:
                    arts, _ = PC.render_group(ax, ctx, CFG[key], ents[key], s, TXT[key], S['font'])
                    for a_ in arts:            # 마스킹(1000) 밑에 남도록
                        a_.set_zorder(min(a_.get_zorder(), 999))
            for e, dd in moved:               # 원위치(다음 장에서 다시 쓴다)
                e.translate(0, dd, 0)
            for e, p0, p1 in stmod:
                e.dxf.start = p0
                e.dxf.end = p1
            # 인접 장 마스킹
            ax.add_patch(Rectangle((xlim[0] - 20, ylim[0] - 20), (pg['X0'] - 1.0) - (xlim[0] - 20), H + 40,
                                   fc='white', ec='none', zorder=1000))
            ax.add_patch(Rectangle((pg['X1'] + 1.5, ylim[0] - 20), xlim[1] + 20 - (pg['X1'] + 1.5), H + 40,
                                   fc='white', ec='none', zorder=1000))
            tp = dict(zorder=1001, color='black', fontproperties=fp)
            page_top = ylim[1] - 2.0
            lw = 0.35 * s
            upm = K['UPM']
            for si in sl:
                a = segs[si]['x0']
                dd = d[si]
                top = min(page_top, max(POLE_TOP, prof[si][1] - dd + 8.0))   # 그 계통 내용 상단까지만
                if top > POLE_TOP:
                    for xo in (POLE_RIGHT, POLE_RIGHT - 2.0):
                        ax.plot([a + xo, a + xo], [POLE_TOP, top], lw=lw, color='k', zorder=1001)
                    yy, flip = POLE_TOP + 10.0, True
                    while yy - 10.0 < top:
                        xo = POLE_RIGHT - (1.6 if flip else 0.6)
                        ax.plot([a + xo, a + xo], [yy - 10.0, min(yy, top)], lw=2.6 * s, color='k',
                                zorder=1001, solid_capstyle='butt')
                        flip = not flip
                        yy += 10.0
                    yg = POLE_TOP + 50.0
                    while yg <= top:
                        ax.plot([a + POLE_RIGHT - 18.0, a + POLE_RIGHT], [yg, yg], lw=lw, color='k', zorder=1001)
                        yg += 50.0
                b0 = pole_base.get(si)
                if b0 is not None:
                    k = 0
                    while STATION_Y + 1.0 + 50.0 * k <= top:
                        t = ax.text(a + st['pole_label_dx'], STATION_Y + 1.0 + 50.0 * k,
                                    "%.2f" % (b0 + 50.0 / upm * k + dd / upm),
                                    ha='left', va='baseline', **tp)
                        t.set_fontsize(3.0 * PT * s)
                        k += 1
                for txt, xx, yy, rot in (("H=1:%.0f" % (st['unit_h'] / s), a - 28.0, -4.5, 0),
                                         ("V=1:%.0f" % (st['unit_v'] / s), a - 31.0, 0.0, 90)):
                    t = ax.text(xx, yy, txt, ha='left', va='baseline', rotation=rot, **tp)
                    t.set_fontsize(2.5 * PT * s)
            ax.set_xlim(*xlim)
            ax.set_ylim(*ylim)
            ax.set_autoscale_on(False)
            ax.set_axis_off()
            if not args.no_border:
                fig.add_artist(plt.Rectangle((ML / PAGE_W * 0.45, MB / PAGE_H * 0.45),
                                             1 - ML / PAGE_W * 0.9, 1 - MB / PAGE_H * 0.9, fill=False,
                                             ec='black', lw=0.9, zorder=5, transform=fig.transFigure))
            nm = ', '.join(segs[si]['name'] for si in sl if segs[si]['name'])
            t = titles[n - 1] if titles and n <= len(titles) else None
            if isinstance(t, str):
                t = {'ri': t, 'no': 0, 'sub': '', 'color': '#444444'}
            elif t is None:
                t = {'ri': doc_title, 'no': 0, 'sub': nm, 'color': '#444444'}
            draw_titleblock(fig, t, s, n, len(pages), S, K)
            pdf.savefig(fig)
            if args.png:
                fig.savefig(os.path.splitext(out_pdf)[0] + '_p%02d.png' % n, dpi=args.png)
            plt.close(fig)
            print("  sheet %2d/%d  %-14s x %8.1f~%8.1f  L=%6.1fm  H=1:%-5.0f  d=%s  %s"
                  % (n, len(pages), nm or '-', pg['X0'], pg['X1'], pg['X1'] - pg['X0'],
                     st['unit_h'] / s, [int(d[si]) for si in sl],
                     ('%s %s' % (t.get('ri', ''), t.get('no', '') or '')).strip()))


def main():
    ap = argparse.ArgumentParser(description='사람이 그린 A3 프레임(EEE) 단위로 종단면도 → A3 PDF')
    PC.kitconfig.add_config_arg(ap)
    ap.add_argument('dxf', help='프레임 블록을 INSERT 해 둔 종단면도 DXF — 읽기만 한다')
    ap.add_argument('-o', '--out', default=None, help='출력 PDF (기본: <outputs.root>/50-profile/<이름>_A3.pdf)')
    ap.add_argument('-t', '--title', default=None, help='제목(리 매핑·--titles 가 없을 때 큰 제목)')
    ap.add_argument('-u', '--uniform', action='store_true', help='모든 장 축척 통일(가장 작은 축척으로)')
    ap.add_argument('-p', '--pages', default=None, help='일부 장만(예: 1,6,10)')
    ap.add_argument('--no-border', action='store_true')
    ap.add_argument('--titles', default=None, help='페이지 제목 JSON — 문자열 또는 {ri,no,sub,color} 배열')
    ap.add_argument('--ri-map', default=None, help='ri_map.py 가 만든 관번호→[리, 지구] JSON — 제목 자동')
    ap.add_argument('--frame-block', default='EEE', help='페이지 프레임 블록 이름(기본 EEE)')
    ap.add_argument('--title-w', type=float, default=33.0,
                    help='원점 왼쪽 표제란 폭(기본 33 — 계통 이름표 N장 을 뺀 폭)')
    ap.add_argument('--seg-grab', type=float, default=58.0, help='엔티티를 계통에 붙이는 원점 왼쪽 여유')
    ap.add_argument('--skip-layers', default='표고수치,Defpoints',
                    help='버릴 레이어(쉼표) — 도면 밖 잔여물이 bbox 를 폭주시키는 레이어')
    ap.add_argument('--hatch-layer', default=None,
                    help='SOLID HATCH 를 기둥별 노란 형광으로 그릴 레이어(예: 소블록 경계)')
    ap.add_argument('--hatch-color', default='#FFFF00')
    ap.add_argument('--pink-layers', default='', help='분홍으로 그릴 레이어(쉼표, 예: 주요위치)')
    ap.add_argument('--pink-color', default='#FF1493')
    ap.add_argument('--pipe-pattern', default=None)
    ap.add_argument('--png', type=int, default=0, help='장마다 PNG 도(dpi, 눈 검수용)')
    a = ap.parse_args()
    a.skip_layers = [v for v in a.skip_layers.split(',') if v]
    a.pink_layers = [v for v in a.pink_layers.split(',') if v]
    a.only = set(int(v) for v in a.pages.split(',')) if a.pages else None
    cfg = PC.load_cfg(a.config)
    S = PC.settings(cfg)
    title = a.title or os.path.splitext(os.path.basename(a.dxf))[0]
    out = a.out or str(PC.out_dir(cfg, None) / (os.path.splitext(os.path.basename(a.dxf))[0] + '_A3.pdf'))
    tt = PC.read_json(a.titles) if a.titles else None
    ri = PC.read_json(a.ri_map) if a.ri_map else None
    pages, titles = build(a.dxf, out, title, S, a, titles=tt, ri=ri)
    if titles and not tt:
        tj = os.path.splitext(out)[0] + '_titles.json'
        PC.write_json(tj, titles)
        print('제목 ->', tj)
    print('->', out)


if __name__ == '__main__':
    main()
