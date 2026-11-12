# -*- coding: utf-8 -*-
"""[2단계] A3 시트 DXF(a3_split.py 산출) → A3 PDF

1단계가 이미 종이 좌표(mm)로 잘라 놨으므로 여기서는 도곽마다 렌더만 한다.
- 페이지 = 레이어 A3_FRAME 의 420×297 LWPOLYLINE
- 색군: 기본 검정 / 관로선(profile.strip.layers.red) 빨강 / A3_PUMP 노랑·A3_JOIN 분홍(맨 밑에 깔림)
  / A3_TITLE·A3_SPOT 은 DXF 색 그대로 / 도곽 회색
- 선 굵기는 mm 절대값
- ★ 회전 TEXT(세로 측점값·45° 인출 라벨)는 matplotlib 글자로 그린다 — ezdxf 출력기는 기울어진
  글자의 구멍(ㅎ·d·0)을 칠해 버린다.

사용: python a3_pdf.py <이름_A3시트.dxf> [-o 출력.pdf] [--pages 1,3] [--png 110]
"""
import argparse
import collections
import os

import profcommon as PC

L_FRAME, L_TITLE, L_PUMP, L_JOIN, L_SPOT = 'A3_FRAME', 'A3_TITLE', 'A3_PUMP', 'A3_JOIN', 'A3_SPOT'
ORDER = ('y', 'p', 'k', 'r', 'g', 'c')          # 형광 띠가 맨 밑


def ent_x(e):
    t = e.dxftype()
    if t == 'LINE':
        return (e.dxf.start.x + e.dxf.end.x) / 2.0
    if t == 'LWPOLYLINE':
        q = e.get_points('xy')
        return sum(v[0] for v in q) / len(q) if q else None
    if t in ('CIRCLE', 'ELLIPSE'):
        return e.dxf.center.x
    if t == 'SOLID':
        return (e.dxf.vtx0.x + e.dxf.vtx2.x) / 2.0
    if t in ('TEXT', 'INSERT', 'MTEXT'):
        return e.dxf.insert.x
    return None


def build(dxf_path, out_pdf, S, meta=None, only=None, png_dpi=0):
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['pdf.fonttype'] = 42          # 글자를 TrueType 으로 넣는다(검색·선택 가능)
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from ezdxf.addons.drawing import RenderContext
    from ezdxf.addons.drawing.config import Configuration, ColorPolicy, LineweightPolicy, LinePolicy

    PC.setup_ezdxf_fonts(S)
    PAGE_W, PAGE_H = S['a3']['page']
    red_layers = set(S['strip']['layers']['red'])
    C = S['a3']['colors']
    doc = PC.load_dxf(dxf_path)
    msp = doc.modelspace()

    frames = sorted(min(v[0] for v in e.get_points('xy'))
                    for e in msp.query('LWPOLYLINE[layer=="%s"]' % L_FRAME))
    if not frames:
        raise SystemExit('A3_FRAME 을 찾지 못했다 — a3_split.py 산출 DXF 인지 확인')
    pitch = (frames[1] - frames[0]) if len(frames) > 1 else PAGE_W

    bucket = collections.defaultdict(list)
    for e in msp:
        x = ent_x(e)
        if x is None:
            continue
        # ★ round 를 쓰면 페이지 오른쪽 절반이 다음 장으로 넘어가 사라진다 → // 로
        i = min(max(int((x - frames[0]) // pitch), 0), len(frames) - 1)
        bucket[i].append(e)

    base = dict(lineweight_policy=LineweightPolicy.ABSOLUTE, lineweight_scaling=1.0,
                line_policy=LinePolicy.ACCURATE)

    def custom(hex6):
        return Configuration(color_policy=ColorPolicy.CUSTOM, custom_fg_color=hex6 + 'FF', **base)
    CFG = {'k': Configuration(color_policy=ColorPolicy.BLACK, **base),
           'r': custom(C['red']), 'y': custom(C['pump']), 'p': custom(C['join']),
           'g': custom(C['frame']), 'c': Configuration(color_policy=ColorPolicy.COLOR, **base)}
    TXT = {'k': '#000000', 'r': C['red'], 'y': C['pump'], 'p': C['join'], 'g': C['frame'], 'c': None}
    ctx = RenderContext(doc)
    ctx.current_layout_properties.set_colors('#FFFFFF')

    def group(e):
        lay = e.dxf.layer
        if lay in red_layers:
            return 'r'
        if lay == L_PUMP:
            return 'y'
        if lay == L_JOIN:
            return 'p'
        if lay == L_FRAME:
            return 'g'
        if lay in (L_TITLE, L_SPOT):
            return 'c'
        return 'k'

    print("%d 장" % len(frames))
    png_dir = None
    if png_dpi:
        png_dir = os.path.splitext(out_pdf)[0] + '_png'
        os.makedirs(png_dir, exist_ok=True)
    nrot = 0
    with PdfPages(out_pdf) as pdf:
        for i, PX in enumerate(frames):
            if only and (i + 1) not in only:
                continue
            fig = plt.figure(figsize=(PAGE_W / 25.4, PAGE_H / 25.4))
            ax = fig.add_axes([0, 0, 1, 1])
            bundle = collections.defaultdict(list)
            for e in bucket[i]:
                bundle[group(e)].append(e)
            for gi, key in enumerate(ORDER):
                if not bundle[key]:
                    continue
                arts, nr = PC.render_group(ax, ctx, CFG[key], bundle[key], 1.0, TXT[key], S['font'])
                nrot += nr
                # 백엔드마다 zorder 를 0 부터 다시 매기므로 그룹 순서대로 띄운다
                # (안 그러면 나중 그룹의 라벨 흰 바탕이 앞 그룹 선 밑에 깔린다)
                for art in arts:
                    art.set_zorder(art.get_zorder() + gi * 1e6)
            ax.set_xlim(PX, PX + PAGE_W)
            ax.set_ylim(0, PAGE_H)
            ax.set_autoscale_on(False)
            ax.set_axis_off()
            pdf.savefig(fig)
            if png_dir:
                fig.savefig(os.path.join(png_dir, 'sheet_%02d.png' % (i + 1)), dpi=png_dpi)
            plt.close(fig)
            tag = ''
            if meta and i < len(meta):
                m = meta[i]
                tag = "%s %s %s" % (m.get('dist', ''), m.get('ri', ''), m.get('no', ''))
            print("  sheet %2d/%d  %d ents  %s" % (i + 1, len(frames), len(bucket[i]), tag), flush=True)
    print('회전 글자 %d개를 matplotlib 글자로 그림' % nrot)
    return len(frames)


def main():
    ap = argparse.ArgumentParser(description='[2단계] A3 시트 DXF → A3 PDF')
    PC.kitconfig.add_config_arg(ap)
    ap.add_argument('dxf', help='a3_split.py 가 만든 <이름>_A3시트.dxf')
    ap.add_argument('-o', '--out', default=None, help='출력 PDF (기본: 시트 DXF 옆 <이름>_A3.pdf)')
    ap.add_argument('--pages', default=None, help='일부 장만(예: 1,6,10)')
    ap.add_argument('--png', type=int, default=0, help='장마다 PNG 도 저장(dpi, 눈 검수용)')
    a = ap.parse_args()
    cfg = PC.load_cfg(a.config)
    S = PC.settings(cfg)
    stem = os.path.splitext(a.dxf)[0]
    out = a.out or (stem[:-len('_A3시트')] if stem.endswith('_A3시트') else stem) + '_A3.pdf'
    only = set(int(v) for v in a.pages.split(',')) if a.pages else None
    js = stem + '.json'
    meta = PC.read_json(js)['sheets'] if os.path.exists(js) else None
    build(a.dxf, out, S, meta, only, a.png)
    print('->', out)


if __name__ == '__main__':
    main()
