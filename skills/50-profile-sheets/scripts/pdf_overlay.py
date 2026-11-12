# -*- coding: utf-8 -*-
"""완성된 A3 종단면도 PDF 위에 SVG 아이콘·딱지를 얹는다(다시 출력하지 않고 후처리).

- 아이콘: A3 시트 DXF(a3_split.py 산출, 이미 종이 mm 좌표)에서 `--match` 글자(기본: 맨홀펌프장)가 든
  TEXT 를 찾아, 그 글자 시작점 왼쪽 위에 SVG 아이콘을 벡터로 얹는다.
  ★ 자리는 시트 DXF 에서 가져온다(PDF 안 글자 검색에 기대지 않는다 — ezdxf 가 그린 글자는 경로라 검색이 안 된다).
- 딱지: `--tags` JSON 의 [{"page": 10, "text": "하천횡단", "x_mm": 120, "y_mm": 100}, …]
  (x_mm·y_mm = 페이지 왼쪽 위 기준 mm, 선택 w_mm·h_mm·color·font_pt).
- ★ MuPDF(PyMuPDF)의 SVG 변환은 ① CSS <style> 클래스를 무시해 그 요소를 검게 칠하고 ② radialGradient 를
  못 그리고 ③ base64 그림자 <image> 를 검은 얼룩으로 그린다. 그래서 SVG 를 넣기 전에 클래스를 인라인
  style 로 옮기고, <image> 를 지우고, `url(#…)` 그라디언트 채움을 흰색으로 바꾼 사본을 쓴다.

사용: python pdf_overlay.py <A3.pdf> --sheets <이름_A3시트.dxf> [--icon pump.svg] [--tags tags.json] [-o 출력.pdf]
"""
import argparse
import os
import re

import profcommon as PC

MM = 72 / 25.4
RE_STYLE = re.compile(r'<style[^>]*>(.*?)</style>', re.S)
RE_RULE = re.compile(r'([^{}]+)[{]([^}]*)[}]')
RE_TAG = re.compile(r'<([A-Za-z][\w:-]*)(\s[^<>]*?)?(/?)>')
RE_CLASS = re.compile(r'(?<![\w-])class="([^"]*)"')
RE_STYLE_ATTR = re.compile(r'(?<![\w-])style="([^"]*)"')
RE_IMG_SELF = re.compile(r'<image(?![\w-])[^>]*?/>', re.S)
RE_IMG_PAIR = re.compile(r'<image(?![\w-]).*?</image>', re.S)
RE_URL_CSS = re.compile(r'fill\s*:\s*url[(]#[^)]*[)]')
RE_URL_ATTR = re.compile(r'fill\s*=\s*"url[(]#[^)]*[)]"')


def _inline_css(s):
    """<style> 의 .class{…} 규칙을 각 요소 style="" 로 옮긴다."""
    rules = {}
    for m in RE_STYLE.finditer(s):
        for sel, body in RE_RULE.findall(m.group(1)):
            for one in sel.split(','):
                one = one.strip()
                if one.startswith('.'):
                    rules[one[1:]] = rules.get(one[1:], '') + body.strip().rstrip(';') + ';'
    s = RE_STYLE.sub('', s)

    def tag(m):
        name, attrs, close = m.group(1), m.group(2) or '', m.group(3)
        cm = RE_CLASS.search(attrs)
        if not cm:
            return m.group(0)
        decl = ''.join(rules.get(c, '') for c in cm.group(1).split())
        attrs = attrs.replace(cm.group(0), '')
        sm = RE_STYLE_ATTR.search(attrs)
        if sm:
            attrs = attrs.replace(sm.group(0), 'style="%s%s"' % (decl, sm.group(1)))
        elif decl:
            attrs = attrs + ' style="%s"' % decl
        return '<%s%s%s>' % (name, attrs, close)
    return RE_TAG.sub(tag, s), len(rules)


def sanitize_svg(src, dst):
    """MuPDF 가 못 그리는 것 정리 → 사본(dst). 반환: (인라인한 클래스 수, 지운 image 수, 바꾼 그라디언트 수)."""
    s = open(src, encoding='utf-8', errors='replace').read()
    n_img = len(RE_IMG_SELF.findall(s)) + len(RE_IMG_PAIR.findall(s))
    s = RE_IMG_SELF.sub('', s)
    s = RE_IMG_PAIR.sub('', s)
    s, n_css = _inline_css(s)
    n_url = len(RE_URL_CSS.findall(s)) + len(RE_URL_ATTR.findall(s))
    s = RE_URL_CSS.sub('fill:#FFFFFF', s)
    s = RE_URL_ATTR.sub('fill="#FFFFFF"', s)
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(s)
    return n_css, n_img, n_url


def sheet_frames(msp):
    xs = sorted(min(v[0] for v in e.get_points('xy')) for e in msp.query('LWPOLYLINE[layer=="A3_FRAME"]'))
    if not xs:
        raise SystemExit('A3_FRAME 이 없다 — a3_split.py 가 만든 시트 DXF 를 줄 것')
    return xs, ((xs[1] - xs[0]) if len(xs) > 1 else 460.0)


def find_labels(sheets_dxf, match):
    doc = PC.load_dxf(sheets_dxf)
    msp = doc.modelspace()
    xs, pitch = sheet_frames(msp)
    out = []
    for e in msp.query('TEXT'):
        if match not in (e.dxf.text or ''):
            continue
        q = e.dxf.insert
        if (e.dxf.get('halign', 0) or e.dxf.get('valign', 0)) and e.dxf.hasattr('align_point'):
            q = e.dxf.align_point            # 정렬된 TEXT 는 두 번째 점이 실제 자리
        x, y = q.x, q.y
        i = min(max(int((x - xs[0]) // pitch), 0), len(xs) - 1)
        out.append(dict(page=i + 1, x_mm=x - xs[i], y_mm=y, text=e.dxf.text, rot=e.dxf.rotation or 0.0))
    return out, len(xs)


def hex01(h):
    return tuple(v / 255.0 for v in PC.hex_rgb(h))


def main():
    ap = argparse.ArgumentParser(description='A3 종단면도 PDF 위에 SVG 아이콘·딱지 얹기')
    PC.kitconfig.add_config_arg(ap)
    ap.add_argument('pdf', help='a3_pdf.py 가 만든 A3 PDF — 읽기만, 결과는 새 파일')
    ap.add_argument('--sheets', default=None, help='같은 회차 <이름>_A3시트.dxf (아이콘 자리)')
    ap.add_argument('--icon', default=None, help='SVG 아이콘(CSS 클래스·그라디언트·그림자는 자동 정리)')
    ap.add_argument('--match', default=None, help='아이콘을 붙일 글자(기본 profile.a3.pump_key)')
    ap.add_argument('--icon-mm', type=float, default=9.0, help='아이콘 지름 mm')
    ap.add_argument('--tags', default=None, help='딱지 JSON [{page,text,x_mm,y_mm,(w_mm,h_mm,color,font_pt)}]')
    ap.add_argument('-o', '--out', default=None, help='출력 PDF (기본: 입력 옆 <이름>_아이콘.pdf)')
    a = ap.parse_args()
    import pymupdf
    cfg = PC.load_cfg(a.config)
    S = PC.settings(cfg)
    out = a.out or os.path.splitext(a.pdf)[0] + '_아이콘.pdf'
    if os.path.abspath(out) == os.path.abspath(a.pdf):
        raise SystemExit('입력 PDF 를 덮어쓰지 않는다 — -o 로 다른 이름을 줄 것')
    doc = pymupdf.open(a.pdf)
    PAGE_H = doc[0].rect.height / MM
    placed = []
    if a.icon:
        if not a.sheets:
            raise SystemExit('--icon 에는 --sheets(시트 DXF)가 필요하다')
        clean = os.path.splitext(out)[0] + '_icon.svg'
        nc, ni, nu = sanitize_svg(a.icon, clean)
        print('SVG 정리: CSS 클래스 %d개 인라인, <image> %d개 삭제, 그라디언트 채움 %d개 → 흰색' % (nc, ni, nu))
        svg_pdf = pymupdf.open('pdf', pymupdf.open(clean).convert_to_pdf())
        labels, nframes = find_labels(a.sheets, a.match or S['a3']['pump_key'])
        if nframes != doc.page_count:
            raise SystemExit('시트 DXF 도곽 %d장 ≠ PDF %d쪽 — 같은 회차·전체 장 PDF 인지 확인'
                             % (nframes, doc.page_count))
        w = a.icon_mm * MM
        for lb in labels:
            px, py = lb['x_mm'] * MM, (PAGE_H - lb['y_mm']) * MM
            r = pymupdf.Rect(px - 1.25 * w, py - 1.2 * w, px - 0.25 * w, py - 0.2 * w)   # 글자 시작점 왼쪽 위
            doc[lb['page'] - 1].show_pdf_page(r, svg_pdf, 0)
            placed.append('p%d(%.0f,%.0f)' % (lb['page'], lb['x_mm'], lb['y_mm']))
        print('아이콘 %d곳: %s' % (len(placed), ', '.join(placed)))
    if a.tags:
        fontfile = PC.font_path(S['font_bold']) or PC.font_path(S['font'])
        tags = PC.read_json(a.tags)
        for t in tags:
            page = doc[int(t['page']) - 1]
            ww, hh = t.get('w_mm', 26.0) * MM, t.get('h_mm', 7.0) * MM
            x0, y0 = t['x_mm'] * MM, t['y_mm'] * MM
            col = hex01(t.get('color', '#DB4033'))
            page.draw_rect(pymupdf.Rect(x0, y0, x0 + ww, y0 + hh), color=col, fill=(1, 1, 1), width=1.2)
            kw = dict(fontsize=t.get('font_pt', 10.0), color=col, align=pymupdf.TEXT_ALIGN_CENTER)
            if fontfile:
                kw.update(fontname='kitfont', fontfile=fontfile)
            rc = page.insert_textbox(pymupdf.Rect(x0, y0 + 4, x0 + ww, y0 + hh), t['text'], **kw)
            if rc < 0:          # 상자가 좁다 — 26×7 mm / 10 pt 면 한글 4자
                raise SystemExit('딱지 글자가 상자를 넘친다: p%s %s (w_mm 을 늘릴 것)' % (t['page'], t['text']))
        print('딱지 %d개' % len(tags))
    doc.save(out, garbage=3, deflate=True)
    print('->', out)


if __name__ == '__main__':
    main()
