# -*- coding: utf-8 -*-
"""조판·삽도용 SVG 양식 생성기 — 표준 라이브러리만 쓴다.

assets/svg/ 의 양식(제목·범례·딱지·로드뷰 배지·지명 라벨·키표·지도 아이콘)을 글자·색·번호만 바꿔 여러 장 찍는다.
템플릿 파일은 이 스크립트가 자리표시 글자(○○처리구역, XX-A-001~004 …)로 만든 결과물이다(--templates).

사용 예
  python make_svg.py --list                          # 종류(type)와 받는 값·기본값
  python make_svg.py --templates                     # assets/svg/*/ 템플릿 다시 만들기
  python make_svg.py examples/spec.json -o out       # JSON 목록 → SVG 여러 장
  python make_svg.py examples/spec.csv  -o out --item-height 7.2
                                                     # CSV(한 줄 = 한 장) + 조판 아이템 폭(mm) 계산
  python make_svg.py --sheet out/sheet.html out      # 폴더 안 SVG 를 한 장 HTML 로(브라우저로 눈 검사)

spec.json — 객체 하나가 SVG 한 장. type·out 말고는 전부 그 type 의 인자(빠지면 기본값)
  [{"type": "tag_pill",   "out": "구간1.svg", "no": 1, "text": "XX-A-001~004"},
   {"type": "tag_pill",   "out": "구간2.svg", "no": [5, 6], "text": "XX-B-001~011, XX-C-001~003"},
   {"type": "tag_pill",   "out": "압송1.svg", "no": 3, "text": "#1 압송관", "kind": "press"},
   {"type": "capsule",    "out": "검토_하천.svg", "text": "하천하월", "fill": "#7B2FBE"},
   {"type": "title_band", "out": "제목.svg", "text": "○○처리구역"},
   {"type": "keytable",   "out": "키표.svg", "rows": [["맨홀펌프장", "①", "맨홀펌프장 #1", "0.00㎥/min × 0m", "-"]]},
   {"type": "template",   "out": "방위_남색.svg", "src": "icon/compass.svg", "replace": {"#ba585e": "#1F4E79"}}]

spec.csv — 첫 줄 = 열 이름(type, out, 그 밖 인자). 빈 칸은 기본값. 목록 값은 | 로 나눈다(lines = 첫줄|둘째줄).
  type,out,no,text,kind,fill
  tag_pill,구간1.svg,1,XX-A-001~004,grav,
  capsule,검토_하천.svg,,하천하월,,#7B2FBE

딱지 폭 규칙 (tag_pill — 검토구간 알약 딱지)
  viewBox 폭 = 145.5 + (글자수 - 12) × 9  (+ 20 × (번호 글자수 - 1))
  글자수는 영숫자·기호 1, 한글·전각 15/9 로 센다(글꼴 15 에서 영숫자 0.6em = 9, 한글 1em = 15).
  번호가 두 자(⑤⑥)면 원형 배지가 알약 배지로 바뀌고 20 넓어진다.
  조판 그림 아이템은 Zoom(비율 유지)이라 viewBox 폭이 바뀌면 아이템 폭도 같이 늘려야 한다:
  아이템 폭 = 아이템 높이 × viewBox 폭 / viewBox 높이(30).  --item-height 를 주면 장마다 계산해 찍는다.

QGIS(QtSvg) 주의 — 이 생성기는 아래를 지켜 만든다
  · 글자 테두리(text stroke)는 QtSvg 가 그리지 않는다 → 후광은 글자를 여러 방향으로 겹쳐 그린다(tag_halo, badge_pin).
  · dominant-baseline 은 QtSvg 가 무시한다 → 기준선(y) 을 직접 준다(label_note).
  · 글꼴은 이름 하나만 준다 — QtSvg 는 'A, B, sans-serif' 목록을 못 읽고 기본 글꼴로 그린다.
    기본 Malgun Gothic · Gulim · GulimChe. 없는 PC 는 --sans NanumGothic 처럼 바꿔 다시 찍는다.
  · 글꼴 폭은 어림값이라 긴 글자는 눈으로 확인한다.
"""
import argparse
import csv
import html
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 글꼴은 이름 하나만 쓴다 — QGIS(QtSvg)는 'A, B, sans-serif' 같은 목록을 못 읽고 기본 글꼴로 바꿔 그린다.
# 맑은 고딕·굴림이 없는 PC 는 --sans/--gulim/--gulimche 로 바꿔 다시 찍는다(예: --sans NanumGothic).
SANS = 'Malgun Gothic'
GULIM = 'Gulim'
GULIMCHE = 'GulimChe'


# ───────── 공통 ─────────
def n(x):
    """좌표 숫자 → 짧은 문자열 (소수 둘째 자리까지)"""
    s = ('%.2f' % x).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def esc(s):
    return html.escape(str(s), quote=False)


def wide(c):
    """한글·한자·전각과 한글 글꼴에서 전각으로 그려지는 기호(①○→ 등)"""
    o = ord(c)
    return o >= 0x2E80 or 0x2190 <= o <= 0x21FF or 0x2460 <= o <= 0x24FF or 0x25A0 <= o <= 0x27BF


def tw(s, fs, narrow=0.6, full=1.0):
    """글자 폭 어림값(SVG 단위): 전각 full·em, 그 밖 narrow·em"""
    return sum(fs * (full if wide(c) else narrow) for c in str(s))


def units(s):
    """딱지 폭 규칙의 '글자수' — 영숫자 1, 한글·전각 15/9"""
    return sum((15.0 / 9.0) if wide(c) else 1.0 for c in str(s))


def _circ(k):
    if 1 <= k <= 20:
        return chr(0x2460 + k - 1)
    if 21 <= k <= 35:
        return chr(0x3251 + k - 21)
    if 36 <= k <= 50:
        return chr(0x32B1 + k - 36)
    return str(k)


def circled(no):
    """1 → ①, [5, 6]·'5,6'·'5+6' → ⑤⑥, 이미 ①·'1-1' 같은 글자면 그대로"""
    if no is None or no == '':
        return ''
    if isinstance(no, (list, tuple)):
        return ''.join(circled(x) for x in no)
    if isinstance(no, (int, float)):
        return _circ(int(no))
    s = str(no).strip()
    if re.fullmatch(r'\d+(\s*[,+]\s*\d+)*', s):
        return ''.join(_circ(int(x)) for x in re.split(r'\s*[,+]\s*', s))
    return s


def as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [x for x in str(v).split('|')]


def svg_open(W, H, mm=False):
    size = ' width="%smm" height="%smm"' % (n(W), n(H)) if mm else ''
    return '<svg xmlns="http://www.w3.org/2000/svg"%s viewBox="0 0 %s %s">' % (size, n(W), n(H))


def halo_text(x, y, text, fs, fill, halo, r, attrs):
    """QtSvg 는 text stroke 를 안 그린다 → 12 방향으로 겹쳐 그린 후광 + 본 글자"""
    out = []
    for k in range(12):
        a = math.pi * 2 * k / 12
        out.append('<text x="%s" y="%s" %s font-size="%s" fill="%s">%s</text>'
                   % (n(x + r * math.cos(a)), n(y + r * math.sin(a)), attrs, n(fs), halo, esc(text)))
    out.append('<text x="%s" y="%s" %s font-size="%s" fill="%s">%s</text>' % (n(x), n(y), attrs, n(fs), fill, esc(text)))
    return ''.join(out)


# ───────── 제목 ─────────
def title_band(text='○○처리구역', band='#2F5D50', band2='#1B3A31', gold='#D8B24A', ink='#F5E6B8'):
    """제목 상자 — 진녹 그라데이션 + 금색 테두리 + 왼쪽 금색 띠. 폭은 글자에 맞춰(6자 = 121)"""
    bw = 27 + tw(text, 15)                 # 금색 띠 7 + 좌우 여백 20 + 글자
    W, H = bw + 4, 52
    tx = 9 + (bw - 7) / 2
    s = (svg_open(W, H) + '\n'
         '<defs><linearGradient id="g5" x1="0" y1="10" x2="0" y2="42" gradientUnits="userSpaceOnUse">'
         '<stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>\n'
         '<rect x="2" y="10" width="%s" height="32" fill="url(#g5)" stroke="%s" stroke-width="1"/>\n'
         '<rect x="2" y="10" width="7" height="32" fill="%s"/>\n'
         '<text x="%s" y="31.5" text-anchor="middle" font-family="%s" font-size="15" font-weight="bold" fill="%s">%s</text>\n'
         '</svg>\n') % (band, band2, n(bw), gold, gold, n(tx), SANS, ink, esc(text))
    return s, W, H


def title_ribbon(text='○○지구 농어촌마을하수도 정비사업', color='#BA585E', ink='#FFFFFF'):
    """사선 리본 제목 — 굴림체 12 (가로 0.92) 흰 글자 + 위쪽 광택"""
    t = sum(12 if wide(c) else 6 for c in text) * 0.92
    W, H = 22.02 + t + 32, 52
    e = W - 13.55
    stops = [(0, 0), (.12, .02), (.25, .07), (.37, .15), (.5, .26), (.63, .41), (.76, .59), (.89, .8), (1, 1)]
    st = ''.join('<stop offset="%s" stop-color="#fff" stop-opacity="%s"/>' % (n(o), n(a)) for o, a in stops)
    s = (svg_open(W, H) + '\n'
         '<defs><linearGradient id="sheen" x1="0" y1="34.57" x2="0" y2="17.56" gradientUnits="userSpaceOnUse">%s</linearGradient></defs>\n'
         '<path d="M14.86,17.56v15a2,2,0,0,0,2,2H%s a3.74,3.74,0,0,0,2.95,-1.73l8.55,-15.28Z" fill="%s"/>\n'
         '<path d="M66,17.56l-45.21,15c-3.28,1.1,-5.07,2,-4,2H%s a4.09,4.09,0,0,0,3.08,-1.73l8.42,-15.28Z" fill="url(#sheen)" opacity="0.25"/>\n'
         '<text transform="translate(22.02 30.31) scale(0.92 1)" font-family="%s" font-size="12" fill="%s" stroke="%s" stroke-width="0.3">%s</text>\n'
         '</svg>\n') % (st, n(e), color, n(e), GULIMCHE, ink, ink, esc(text))
    return s, W, H


def title_pill(text='○○처리구역', fill='#C5DBEA', shadow='#6894B3', edge='#84BBE0', ink='#003F73'):
    """알약 제목 — 하늘색 알약 + 오른쪽 아래 그림자. 폭은 글자 + 여백(6자 = 260)"""
    W, H = tw(text, 15) + 170, 52
    s = (svg_open(W, H) + '\n'
         '<path d="M%s,36.4 a8,8,0,0,1,-8,8 H24 a8,8,0,0,1,-8,-8 V19.6 a8,8,0,0,1,8,-8 H%s a8,8,0,0,1,8,8 Z" fill="%s"/>\n'
         '<path d="M%s,34.4 a8,8,0,0,1,-8,8 H20 a8,8,0,0,1,-8,-8 V17.6 a8,8,0,0,1,8,-8 H%s a8,8,0,0,1,8,8 Z" fill="%s" stroke="%s" stroke-width="0.7"/>\n'
         '<text x="%s" y="31.5" text-anchor="middle" font-family="%s" font-size="15" font-weight="bold" fill="%s">%s</text>\n'
         '</svg>\n') % (n(W - 8), n(W - 16), shadow, n(W - 12), n(W - 20), fill, edge, n(W / 2), SANS, ink, esc(text))
    return s, W, H


# ───────── 범례 ─────────
HOUSE = 'M50,12L88,46H78V88H60V58H40V88H22V46H12Z'
HOUSE_COLORS = {'house_exist': ('#F4F2FF', '#312E81'), 'house_plan': ('#FEF2F2', '#DC2626'),
                'house_low': ('#FFE200', '#166534')}
LEGEND_ITEMS = [['기존', '맨홀펌프장', 'pump'], ['기존', '자연유하관', 'grav_exist'], ['기존', '압송관', 'press_exist'],
                ['기존', '검토미대상', 'other'], ['계획', '맨홀펌프장', 'pump_plan'], ['계획', '자연유하관', 'grav_plan'],
                ['계획', '압송관', 'press_plan']]


def legend_list(items=None, ink='#231815'):
    """목록형 범례 — 민트 바탕 + 흰 본문(왼쪽 위 '범례' 탭) + 회색 구분열(기존/계획).
    items = [[구분, 이름, 기호], ...]  기호: pump · grav_exist · press_exist · other · pump_plan · grav_plan · press_plan
            · house_exist · house_plan · house_low"""
    items = items or LEGEND_ITEMS
    PITCH, TOP, GX = 7.61, 12.38, 33.14
    X0, X1 = 38.71, 51.95
    BX, BW, BH = 38.32, 14.03, 3.0
    TX = 56.82
    W = max(92.0, TX + max(tw(it[1], 5.62, 0.5) * 0.95 for it in items) + 6)

    def band(cy, color):
        return '<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>' % (BX, n(cy - BH / 2), BW, BH, color)

    def solid(cy, color, w=1.5):
        return ('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s" stroke-linecap="round"/>'
                % (X0, n(cy), X1, n(cy), color, w))

    def dashed(cy, color):
        return ('<line x1="38.32" y1="{0}" x2="39.29" y2="{0}" stroke="{1}" stroke-width="1.07"/>'
                '<line x1="40.21" y1="{0}" x2="50.92" y2="{0}" stroke="{1}" stroke-width="1.07" stroke-dasharray="1.86 0.93"/>'
                '<line x1="51.38" y1="{0}" x2="52.34" y2="{0}" stroke="{1}" stroke-width="1.07"/>').format(n(cy), color)

    def pump(cy, color=None):
        cx = 45.33
        c = color or ink
        body = ('fill="#fff" stroke="%s" stroke-width="0.45"' % c) if color else ('fill="url(#pg)" stroke="%s" stroke-width="0.32"' % c)
        return ('<circle cx="%s" cy="%s" r="2.85" fill="#fff" stroke="#fff" stroke-width="0.97"/>'
                '<circle cx="%s" cy="%s" r="2.85" %s/>'
                '<polygon points="%s,%s %s,%s %s,%s" fill="%s"/>'
                % (cx, n(cy), cx, n(cy), body, n(cx - 1.15), n(cy - 2.43), n(cx - 1.15), n(cy + 2.43), n(cx + 2.64), n(cy), c))

    def house(cy, kind):
        f, e = HOUSE_COLORS[kind]
        return ('<path d="%s" transform="translate(%s,%s) scale(0.06)" fill="%s" stroke="%s" stroke-width="12" '
                'stroke-linejoin="round"/>' % (HOUSE, n(45.33 - 3), n(cy - 3), f, e))

    def symbol(kind, cy):
        if kind.startswith('house'):
            return house(cy, kind)
        return {'pump': lambda: pump(cy),
                'grav_exist': lambda: band(cy, '#F5C82B') + solid(cy, '#fff', 2.3) + solid(cy, '#111111'),
                'press_exist': lambda: band(cy, '#B39DDB') + solid(cy, '#fff', 2.3) + dashed(cy, '#111111'),
                'other': lambda: solid(cy, '#404040', 1.03),
                'pump_plan': lambda: pump(cy, '#E01B1B'),
                'grav_plan': lambda: solid(cy, '#0B5FE0'),
                'press_plan': lambda: dashed(cy, '#E01B1B')}[kind]()

    groups = []
    for g, lab, kind in items:
        if not groups or groups[-1][0] != g:
            groups.append((g, []))
        groups[-1][1].append((lab, kind))
    inner_bottom = TOP + sum(len(r) * PITCH + 1.0 for _, r in groups)
    H = inner_bottom + 2.25
    ob = inner_bottom + 0.8
    s = [svg_open(W, H),
         '<defs><radialGradient id="pg" cx="0.5" cy="0.5" r="0.65"><stop offset="0" stop-color="#fff"/>'
         '<stop offset="0.67" stop-color="#fff"/><stop offset="1" stop-color="#999"/></radialGradient></defs>',
         '<rect x="0.75" y="0.75" width="%s" height="%s" rx="1.98" fill="#D2E4E0" stroke="#fff" stroke-width="1.5"/>' % (n(W - 1.5), n(ob)),
         '<rect x="0.75" y="0.75" width="%s" height="%s" rx="1.98" fill="#D2E4E0" stroke="#6A8942" stroke-width="0.6"/>' % (n(W - 1.5), n(ob)),
         '<path d="M1.48,13.16 V%s a1.42,1.42 0 0 0 1.42,1.41 H%s a1.43,1.43 0 0 0 1.4,-1.41 V2.84 a1.44,1.44 0 0 0 -1.4,-1.42 '
         'H39.4 a2.38,2.38 0 0 0 -2,1.3 L34.06,10.45 a2.38,2.38 0 0 1 -2,1.3 H2.9 a1.42,1.42 0 0 0 -1.42,1.41 Z" fill="#fff"/>'
         % (n(ob - 0.65), n(W - 2.85)),
         '<text x="9.4" y="9.45" font-family="%s" font-size="8.6" fill="%s">범</text>' % (GULIMCHE, ink),
         '<text x="20.5" y="9.45" font-family="%s" font-size="8.6" fill="%s">례</text>' % (GULIMCHE, ink),
         '<path d="M%s,%s H3.53 a1.43,1.43 0 0 1 -1.37,-1.41 V13.8 a1.42,1.42 0 0 1 1.41,-1.42 H%s" fill="#E6E6E6"/>'
         % (n(GX), n(ob - 0.8), n(GX)),
         '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#333" stroke-width="0.3"/>' % (GX, TOP, GX, n(ob - 0.8))]
    y = TOP
    for gi, (g, rows) in enumerate(groups):
        y0 = y
        for i, (lab, kind) in enumerate(rows):
            cy = y + 4.3 + i * PITCH
            s.append(symbol(kind, cy))
            s.append('<text transform="translate(%s %s) scale(0.95 1)" font-family="%s" font-size="5.62" fill="%s">%s</text>'
                     % (TX, n(cy + 2.13), GULIMCHE, ink, esc(lab)))
        y += len(rows) * PITCH + 1.0
        s.append('<text x="17.65" y="%s" text-anchor="middle" font-family="%s" font-size="7.3" fill="%s">%s</text>'
                 % (n((y0 + y) / 2 + 2.6), GULIMCHE, ink, esc(g)))
        if gi < len(groups) - 1:
            s.append('<line x1="2.12" y1="%s" x2="%s" y2="%s" stroke="#333" stroke-width="0.3"/>' % (n(y), n(W - 2.05), n(y)))
    s.append('</svg>\n')
    return '\n'.join(s), W, H


LEGEND_COMPARE_VALUES = [['0,000m', '0,000m'], ['00,000m', '00,000m'], ['00,000m', '00,000m'],
                         ['00개소', '00개소'], ['000가구', '000가구']]


def legend_compare(title='계획시설', col_a='기본계획', col_b='금회', values=None):
    """비교표형 범례 — 관로(압송·자연유하·계)·맨홀펌프장·배수설비를 두 열(예: 기본계획/금회)로.
    values = [[압송관 a, b], [자연유하관 a, b], [계 a, b], [맨홀펌프장 a, b], [배수설비 a, b]]"""
    v = values or LEGEND_COMPARE_VALUES
    W, H = 190.0, 125.5
    frame = 'M5,0.8h180.0c2.3,0,4.2,1.9,4.2,4.2v115.6c0,2.3-1.9,4.2-4.2,4.2H5c-2.3,0-4.2-1.9-4.2-4.2V4.8C1,2.4,2.7,0.8,5,0.8z'
    INK = '#231815'
    s = [svg_open(W, H),       # 원본은 CSS class 였으나 QtSvg 에서 class 글자크기 + font-family 속성이 섞이면 크기가 틀어져 전부 속성으로
         '<path d="%s" fill="none" stroke="#FFFFFF" stroke-width="1.5"/>' % frame,
         '<path d="%s" fill="none" stroke="#6A8942" stroke-width="0.6"/>' % frame,
         '<path d="%s" fill="#D2E4E0"/>' % frame,
         '<path d="M187.8,120.2c0,1.7-1.3,2.9-2.9,2.9H5.4c-1.7,0-2.9-1.3-2.9-2.9V26.9c0-1.7,1.3-2.9,2.9-2.9h61.4'
         'c1.9,0,3.4-1.1,4.2-2.7L78.2,5c0.8-1.7,2.3-2.7,4.2-2.7h103.4c1.7,0,2.9,1.3,2.9,2.9C187.8,5.2,187.8,120.2,187.8,120.2z" fill="#FFFFFF"/>',
         '<path d="M56.2,121.4H6.7c-1.7,0-2.9-1.5-2.9-2.9V28.1c0-1.7,1.3-2.9,2.9-2.9h49.5" fill="#E6E6E6"/>',
         '<rect x="56.0" y="63.9" width="22.0" height="19.3" fill="#E6E6E6"/>',
         '<rect x="78.0" y="63.9" width="108.5" height="19.3" fill="#F2F2F2"/>']
    for x1, y1, x2, y2 in [(17, 44.5, 186.5, 44.5), (17, 25.2, 17, 83.2), (17, 63.9, 186.5, 63.9), (3.7, 83.2, 186.5, 83.2),
                           (3.7, 102.5, 186.5, 102.5), (56, 25.2, 56, 63.9), (56, 83.2, 56, 121.4), (78, 25.2, 78, 121.4),
                           (132.3, 8, 132.3, 121.4), (78, 8, 78, 25.2)]:
        s.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#333333" stroke-width="0.3"/>' % (x1, y1, x2, y2))

    def T(x, y, t, fs, sx=None, mid=True, extra=''):
        pos = ('transform="matrix(%s 0 0 1 %s %s)"' % (n(sx), n(x), n(y))) if sx else ('x="%s" y="%s"' % (n(x), n(y)))
        return ('<text %s%s font-family="%s" font-size="%s" %s>%s</text>'
                % (pos, ' text-anchor="middle"' if mid else '', GULIMCHE, n(fs), extra or 'fill="%s"' % INK, esc(t)))

    tsx = min(0.85, 62 / max(1.0, tw(title, 16.832, 0.5)))
    s += [T(7.7876, 18.375, title, 16.832, tsx, False),
          T(7.7876, 18.375, title, 16.832, tsx, False, 'fill="none" stroke="#000000" stroke-width="0.2"'),
          T(105.1, 19.2, col_a, 11), T(159.4, 19.2, col_b, 11),
          T(10.35, 52.3, '관', 11.5), T(10.35, 65.3, '로', 11.5),
          T(36.5, 39.1, '압송관', 11.5, 0.85), T(36.5, 58.4, '자연유하관', 11.5, 0.66), T(47.5, 77.7, '계', 11.5),
          T(6.1, 97.0, '맨홀펌프장', 11.5, 0.85, False), T(10.9, 116.3, '배수설비', 11.5, 0.85, False),
          '<line x1="58.35" y1="34.85" x2="75.65" y2="34.85" stroke="#FFFFFF" stroke-width="4.6" stroke-linecap="round"/>',
          '<line x1="58.35" y1="34.85" x2="75.65" y2="34.85" stroke="#E31A1C" stroke-width="2.8" stroke-dasharray="5,2.6"/>',
          '<line x1="58.35" y1="54.2" x2="75.65" y2="54.2" stroke="#FFFFFF" stroke-width="4.6" stroke-linecap="round"/>',
          '<line x1="58.35" y1="54.2" x2="75.65" y2="54.2" stroke="#1F78FF" stroke-width="2.8" stroke-linecap="round"/>',
          '<g transform="translate(67.00,92.55) scale(1.45)"><circle cx="0" cy="0" r="2.9" fill="none" stroke="#FFFFFF" stroke-width="1.4"/>'
          '<circle cx="0" cy="0" r="2.9" fill="#FFFFFF" stroke="#DB3F33" stroke-width="0.5"/>'
          '<polygon points="-1.1,-2.5 -1.1,2.4 2.7,0" fill="#DB3F33"/></g>']
    for x, (f, e) in ((59.05, HOUSE_COLORS['house_plan']), (66.45, HOUSE_COLORS['house_low'])):
        s.append('<path transform="translate(%s,107.7) scale(0.085)" d="%s" fill="%s" stroke="%s" stroke-width="12" '
                 'stroke-linejoin="round" stroke-linecap="round"/>' % (n(x), HOUSE, f, e))
    for (a, b), y in zip(v, (39.1, 58.4, 77.7, 97.0, 116.3)):
        s.append(T(105.1, y, a, 10))
        s.append(T(159.4, y, b, 10))
    s.append('</svg>\n')
    return '\n'.join(s), W, H


# ───────── 딱지 ─────────
PILL = {'grav': ('#FFB627', '#8A5A00', '#111111'), 'press': ('#B39DDB', '#2A1650', '#2A1650')}


def tag_pill(no=1, text='XX-A-001~004', kind='grav', fill=None, edge=None, ink=None):
    """검토구간 알약 딱지 — 색 알약 + 왼쪽 번호 배지 + 굵은 글자. kind: grav(주황, 자연유하) / press(보라, 압송).
    폭 = 145.5 + (글자수-12)×9 (+20×(번호 글자수-1))"""
    no = circled(no)
    f, e, k = PILL.get(kind, PILL['grav'])
    f, e, k = fill or f, edge or e, ink or k
    d = 20.0 * max(0, len(no) - 1)
    W, H = 145.5 + (units(text) - 12) * 9 + d, 30
    if len(no) <= 1:
        badge = '<circle cx="15" cy="15" r="11" fill="%s"/>' % e
    else:
        badge = '<rect x="4" y="4" width="%s" height="22" rx="11" ry="11" fill="%s"/>' % (n(22 + d), e)
    s = (svg_open(W, H) + '\n'
         '<rect x="2" y="2" width="%s" height="26" rx="13" ry="13" fill="%s" stroke="%s" stroke-width="1"/>\n%s\n'
         '<text x="%s" y="22.3" text-anchor="middle" font-family="%s" font-size="20" font-weight="bold" fill="#FFFFFF">%s</text>\n'
         '<text x="%s" y="20.8" font-family="%s" font-size="15" font-weight="bold" fill="%s">%s</text>\n</svg>\n'
         ) % (n(W - 4), f, e, badge, n(15 + d / 2), SANS, esc(no), n(27.5 + d), SANS, k, esc(text))
    return s, W, H


def tag_number(no=1, fill='#3D5A80', ink='#FFFFFF'):
    """번호 원 — 검토구간 색띠 옆에 놓는 ①"""
    s = (svg_open(30, 30) + '\n'
         '<circle cx="15" cy="15" r="13" fill="%s" stroke="#FFFFFF" stroke-width="2"/>\n'
         '<text x="15" y="21.5" text-anchor="middle" font-family="%s" font-size="19" font-weight="bold" fill="%s">%s</text>\n'
         '</svg>\n') % (fill, SANS, ink, esc(circled(no)))
    return s, 30, 30


BOX_FRAME = 'M6.23,18.53V6.12a.85.85,0,0,1,.85-.85H86.71a.85.85,0,0,1,.85.85V18.53Z'


def tag_box_pump(no=1, title='맨홀펌프장 #1', spec='0.00 ㎥/min × 0 m', color='#2A1650', body='#F3EEF8'):
    """시설 제원 상자 — 진보라 머리띠 + 흰 원 번호 + 아래 제원 줄 (맨홀펌프장)"""
    fs = 7.17 if tw(title, 7.17) <= 62 else 62 / tw(title, 1)
    W, H = 94.5, 35.03
    s = (svg_open(W, H) + '\n'
         '<rect x="6.23" y="5.27" width="81.33" height="24.22" rx="0.85" fill="%s"/>\n'
         '<path d="%s" fill="%s"/>\n'
         '<circle cx="14.2" cy="11.9" r="5.1" fill="#FFFFFF"/>\n'
         '<text x="14.2" y="15.1" text-anchor="middle" font-family="%s" font-size="8.6" fill="%s">%s</text>\n'
         '<text x="53.6" y="15.3" text-anchor="middle" font-family="%s" font-size="%s" font-weight="bold" fill="#FFFFFF">%s</text>\n'
         '<text x="46.9" y="26.9" text-anchor="middle" font-family="%s" font-size="8.79" fill="%s">%s</text>\n'
         '<rect x="6.23" y="5.27" width="81.33" height="24.22" rx="0.85" fill="none" stroke="%s" stroke-width="1.3"/>\n'
         '</svg>\n') % (body, BOX_FRAME, color, GULIM, color, esc(circled(no)), SANS, n(fs), esc(title),
                        GULIM, color, esc(spec), color)
    return s, W, H


def tag_box_section(title='① XX-A-001~005', spec='D200  L=000.0 m', color='#000000'):
    """구간 제원 상자 — 검정 머리띠(흰 글자: 번호·관번호) + 흰 본문(관경·연장)"""
    sx = min(0.9, 76 / max(1.0, tw(title, 10.28, 0.55)))
    sx2 = min(0.92, 76 / max(1.0, tw(spec, 9.07, 0.55)))
    W, H = 94.5, 35.03
    s = (svg_open(W, H) + '\n'
         '<rect x="6.23" y="5.27" width="81.33" height="24.22" rx="0.85" fill="#FFFFFF"/>\n'
         '<path d="%s" fill="%s"/>\n'
         '<text text-anchor="middle" transform="translate(46.9 15.56) scale(%s 1)" font-family="%s" font-size="10.28" '
         'letter-spacing="-0.2" fill="#FFFFFF">%s</text>\n'
         '<text text-anchor="middle" transform="translate(46.9 27.18) scale(%s 1)" font-family="%s" font-size="9.07" '
         'letter-spacing="-0.18" fill="%s">%s</text>\n'
         '<rect x="6.23" y="5.27" width="81.33" height="24.22" rx="0.85" fill="none" stroke="%s" stroke-width="0.7"/>\n'
         '</svg>\n') % (BOX_FRAME, color, n(sx), GULIM, esc(title), n(sx2), GULIM, color, esc(spec), color)
    return s, W, H


CAPSULE = {  # 높이, 테두리색, 테두리폭, 좌우 여백, 글자 y, 기본 채움
    'review': (30, None, 0, 33.0, 20.5, '#7B2FBE'),
    'pump': (32, '#FFFFFF', 2.6, 20.8, 21.4, '#C8102E'),
    'inflow': (30, '#FFFFFF', 1.6, 24.3, 21.0, '#8B3A2A'),
}


def capsule(text='검토항목', preset='review', fill=None, ink='#FFFFFF'):
    """단색 캡슐 딱지 — preset: review(검토사항, 테두리 없음) · pump(신설 펌프장 '○○#1', 흰 테두리) ·
    inflow(유입 표시, 흰 테두리)"""
    h, edge, ew, pad, ty, f0 = CAPSULE.get(preset, CAPSULE['review'])
    W = tw(text, 15) + pad
    stroke = ' stroke="%s" stroke-width="%s"' % (edge, n(ew)) if edge else ''
    s = (svg_open(W, h) + '\n'
         '<rect x="2" y="2" width="%s" height="%s" rx="%s" ry="%s" fill="%s"%s/>\n'
         '<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="15" font-weight="bold" fill="%s">%s</text>\n'
         '</svg>\n') % (n(W - 4), h - 4, n((h - 4) / 2), n((h - 4) / 2), fill or f0, stroke, n(W / 2), n(ty), SANS, ink, esc(text))
    return s, W, h


def tag_halo(text='고점 EL.00.00', fill='#FFFFFF', halo='#000000', size=16):
    """후광 글자 — 흰 글자 + 검정 후광(지도 위 고점·표고 표시). QtSvg 용으로 후광을 겹쳐 그린다"""
    W, H = tw(text, size) + 19, 30
    s = (svg_open(W, H) + '\n'
         + halo_text(W / 2, 21, text, size, fill, halo, 2.3,
                     'text-anchor="middle" font-family="%s" font-weight="bold"' % SANS)
         + '\n</svg>\n')
    return s, W, H


# ───────── 로드뷰(현장사진 위치) ─────────
def _mm(s, pt):
    em = pt * 0.3528
    return sum(em * (0.95 if wide(c) else 0.58) for c in str(s))


def badge_mark(num='1-1', color='#E4007F', d='down'):
    """지도 위치표시 — 번호 알약 + 꼭지(d=down 꼭지 아래 / up 꼭지 위). 단위 mm"""
    pt = 7.0
    bw, bh, tip = _mm(num, pt) + 3.0, 4.4, 1.7
    w, h = bw + 0.6, bh + tip + 0.6
    cx = w / 2
    if d == 'down':
        y0 = 0.3
        tri = '%s,%s %s,%s %s,%s' % (n(cx - 1.3), n(y0 + bh - 0.2), n(cx + 1.3), n(y0 + bh - 0.2), n(cx), n(y0 + bh + tip))
    else:
        y0 = 0.3 + tip
        tri = '%s,%s %s,%s %s,0.3' % (n(cx - 1.3), n(y0 + 0.2), n(cx + 1.3), n(y0 + 0.2), n(cx))
    s = (svg_open(w, h, mm=True)
         + '<polygon points="%s" fill="%s" stroke="#fff" stroke-width="0.35" stroke-linejoin="round"/>' % (tri, color)
         + '<rect x="0.3" y="%s" width="%s" height="%s" rx="%s" fill="%s" stroke="#fff" stroke-width="0.35"/>'
         % (n(y0), n(bw), n(bh), n(bh / 2), color)
         + '<polygon points="%s" fill="%s"/>' % (tri, color)
         + '<text x="%s" y="%s" font-family="%s" font-weight="bold" font-size="%s" fill="#fff" text-anchor="middle">%s</text>'
         % (n(cx), n(y0 + bh / 2 + 0.9), SANS, n(pt * 0.3528), esc(num))
         + '</svg>\n')
    return s, w, h


def badge_pin(name='○○마을회관', num='', color='#1E8C7E', pin='#F56652', pos='below'):
    """유입지점 핀 — 빨간 핀 + 이름(흰 글자·검정 후광). num 을 주면 이름 앞에 색 번호칩.
    pos = below | belowright | above | left. 단위 mm"""
    pt = 7.5
    fs = pt * 0.3528
    nw = _mm(name, pt)
    cw = _mm(num, 6.5) + 2.0 if num else -0.8
    lw, lh = cw + 0.8 + nw + 0.6, fs + 1.4
    pw, ph = 5.8, 6.4

    def pin_at(cx, top):
        return ('<path d="M%s,%s C%s,%s %s,%s %s,%s A2.6,2.6 0 1 1 %s,%s C%s,%s %s,%s %s,%s Z" fill="%s" stroke="#fff" '
                'stroke-width="0.3"/><circle cx="%s" cy="%s" r="1.0" fill="#fff"/>'
                % (n(cx), n(top + ph), n(cx - 0.9), n(top + ph - 1.6), n(cx - 2.6), n(top + ph - 3.0), n(cx - 2.6), n(top + 2.9),
                   n(cx + 2.6), n(top + 2.9), n(cx + 2.6), n(top + ph - 3.0), n(cx + 0.9), n(top + ph - 1.6), n(cx), n(top + ph),
                   pin, n(cx), n(top + 2.9)))

    def label(lx, ly):
        ty = ly + fs + 0.35
        out = ''
        if num:
            out = ('<rect x="%s" y="%s" width="%s" height="%s" rx="0.8" fill="%s" stroke="#fff" stroke-width="0.25"/>'
                   '<text x="%s" y="%s" font-family="%s" font-weight="bold" font-size="%s" fill="#fff" text-anchor="middle">%s</text>'
                   % (n(lx), n(ly + 0.2), n(cw), n(lh - 0.4), color, n(lx + cw / 2), n(ty - 0.15), SANS, n(6.5 * 0.3528), esc(num)))
        return out + halo_text(lx + cw + 0.8, ty, name, fs, '#fff', '#111', 0.25,
                               'font-family="%s" font-weight="bold"' % SANS)

    if pos == 'below':
        w = max(pw, lw) + 0.6
        cx = w / 2
        h = ph + 0.8 + lh + 0.3
        body = pin_at(cx, 0.1) + label(cx - lw / 2, ph + 0.8)
    elif pos == 'belowright':
        cx = max(pw, cw, 0) / 2 + 0.3
        lx = cx - max(cw, 2.6) / 2
        w = lx + lw + 0.3
        h = ph + 0.8 + lh + 0.3
        body = pin_at(cx, 0.1) + label(lx, ph + 0.8)
    elif pos == 'above':
        w = max(pw, lw) + 0.6
        cx = w / 2
        h = lh + 0.6 + ph + 0.2
        body = label(cx - lw / 2, 0.1) + pin_at(cx, lh + 0.6)
    else:  # left
        w = lw + 0.6 + pw + 0.3
        cx = lw + 0.6 + pw / 2
        h = ph + 0.3
        body = label(0.1, 2.9 - lh / 2 + 0.1) + pin_at(cx, 0.1)
    return svg_open(w, h, mm=True) + body + '</svg>\n', w, h


def badge_ribbon(num='1-1', title='검토항목(1)', color='#E6007E', height=4.6, ink='#1C2620'):
    """사진 표제 띠 — [색 번호칩] + 반투명 먹색 띠 위 흰 제목. 높이 height mm"""
    H = float(height)
    fs = H * 0.47
    em = lambda t: sum(fs * (0.95 if wide(c) else 0.58) for c in str(t))
    cw = em(num) + H * 0.44
    w = cw + em(title) + H * 0.6
    r = H * 0.22
    s = (svg_open(w, H, mm=True)
         + '<rect x="0" y="0" width="%s" height="%s" rx="%s" fill="%s" fill-opacity="0.88"/>' % (n(w), n(H), n(r), ink)
         + '<rect x="0" y="0" width="%s" height="%s" rx="%s" fill="%s"/>' % (n(cw), n(H), n(r), color)
         + '<rect x="%s" y="0" width="%s" height="%s" fill="%s"/>' % (n(cw - r), n(r), n(H), color)
         + '<text x="%s" y="%s" font-family="%s" font-weight="bold" font-size="%s" fill="#fff" text-anchor="middle">%s</text>'
         % (n(cw / 2), n(H * 0.676), SANS, n(fs), esc(num))
         + '<text x="%s" y="%s" font-family="%s" font-weight="bold" font-size="%s" fill="#fff">%s</text>'
         % (n(cw + H * 0.26), n(H * 0.676), SANS, n(fs), esc(title))
         + '</svg>\n')
    return s, w, H


def tag_site(num='1-1', text='검토항목', color='#E6007E'):
    """현장 딱지 — 색 알약 + 흰 번호캡슐(색 번호) + 흰 글자. 지도의 사진 지점 표시"""
    tw2 = lambda t, fs: sum(fs * (1.0 if wide(c) else 0.62) for c in str(t))
    cw = tw2(num, 13) + 9.0
    tx0 = 5.5 + cw + 4.0
    W, H = tx0 + tw2(text, 15) + 9.0, 30
    s = (svg_open(W, H) + '\n'
         '<rect x="2" y="2" width="%s" height="26" rx="13" ry="13" fill="%s"/>\n'
         '<text x="%s" y="20.5" text-anchor="middle" font-family="%s" font-size="15" font-weight="bold" fill="#FFFFFF">%s</text>\n'
         '<rect x="5.5" y="5.5" width="%s" height="19" rx="9.5" ry="9.5" fill="#FFFFFF"/>\n'
         '<text x="%s" y="19.7" text-anchor="middle" font-family="%s" font-size="13" font-weight="bold" fill="%s">%s</text>\n'
         '</svg>\n') % (n(W - 4), color, n((tx0 + W - 6) / 2), SANS, esc(text), n(cw), n(5.5 + cw / 2), SANS, color, esc(num))
    return s, W, H


# ───────── 지명·시설 라벨 ─────────
def label_village(text='○○리', ink='#111111', edge='#222222'):
    """마을(리) 이름 — 흰 반투명 알약 + 굵은 검정 테두리 + 큰 굵은 글자"""
    W, H = tw(text, 46) + 41, 90
    s = (svg_open(W, H) + '\n'
         '<rect x="4" y="4" width="%s" height="82" rx="41" ry="41" fill="#FFFFFF" fill-opacity="0.92" stroke="%s" stroke-width="4"/>\n'
         '<text x="%s" y="62" text-anchor="middle" font-family="%s" font-size="46" font-weight="bold" fill="%s">%s</text>\n'
         '</svg>\n') % (n(W - 8), edge, n(W / 2), SANS, ink, esc(text))
    return s, W, H


def label_point(text='○○교', side='left', ink='#111111', edge='#222222'):
    """지점 이름 — 검정 점(흰 테) + 흰 반투명 상자. side = left(점이 왼쪽) / right"""
    rw = tw(text, 34) + 15
    W, H = rw + 32, 70
    rx, cx = (28, 12) if side == 'left' else (4, W - 11.8)
    s = (svg_open(W, H) + '\n'
         '<rect x="%s" y="12" width="%s" height="46" rx="6" ry="6" fill="#FFFFFF" fill-opacity="0.92" stroke="%s" stroke-width="3"/>\n'
         '<text x="%s" y="47" text-anchor="middle" font-family="%s" font-size="34" font-weight="bold" fill="%s">%s</text>\n'
         '<circle cx="%s" cy="35" r="10" fill="#111111" stroke="#FFFFFF" stroke-width="4"/>\n'
         '</svg>\n') % (rx, n(rw), edge, n(rx + rw / 2), SANS, ink, esc(text), n(cx))
    return s, W, H


def label_yellow(text='○○마을회관', fill='#FFFAAA', bold=False):
    """노란 캡슐 이름표(처리구역명·주요 지점) — 굴림 8.5, 흰 테두리. 좌우 빈 여백은 원본 그대로(마커 중심 맞춤)"""
    x0 = 49.15
    x1 = x0 + tw(text, 8.5, 0.55) * 0.9 + 8.1
    W, H = x1 + x0, 47.28
    d = ('M%s,23.64c0,2.86,-2.81,5.19,-6.24,5.19H55.38c-3.43,0,-6.23,-2.33,-6.23,-5.19s2.8,-5.19,6.23,-5.19H%s'
         'C%s,18.45,%s,20.78,%s,23.64Z') % (n(x1), n(x1 - 6.24), n(x1 - 2.81), n(x1), n(x1))
    s = (svg_open(W, H) + '\n'
         '<path d="%s" fill="%s"/>\n<path d="%s" fill="none" stroke="#fff" stroke-width="0.5"/>\n'
         '<text transform="translate(%s 26.65) scale(0.9 1)" text-anchor="middle" font-family="%s" font-size="8.5"%s '
         'fill="#000" stroke="#fff" stroke-width="0.2">%s</text>\n</svg>\n'
         ) % (d, fill, d, n((x0 + x1) / 2), GULIM, ' font-weight="bold"' if bold else '', esc(text))
    return s, W, H


def label_tiles(text='○○면', fill='#47A69A'):
    """읍면 이름 — 글자마다 청록 네모 칸(흰 글자)"""
    chars = list(text)
    W = 10.94 * len(chars) - 0.9
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="45 16 %s 10">' % n(W)]
    for i, c in enumerate(chars):
        x = 46.02 + i * 10.94
        t = ('<text transform="translate(%s 23.64) scale(0.95 1)" font-family="%s" font-size="6.5" letter-spacing="-0.13" '
             'fill="#fff">%s</text>') % (n(x + 0.8 if wide(c) else x + 2.2), GULIM, esc(c))
        out.append('<rect x="%s" y="17.29" width="7.65" height="7.65" rx="0.71" fill="none" stroke="#fff" stroke-width="0.3"/>%s'
                   '<rect x="%s" y="17.29" width="7.65" height="7.65" rx="0.71" fill="%s"/>%s' % (n(x), t, n(x), fill, t))
    out.append('</svg>\n')
    return ''.join(out), W, 10


def label_char(char='천', edge='#1F5C8B', ink='#14405F'):
    """한 글자 원 — 하천명 등을 선 따라 한 자씩 놓을 때"""
    s = (svg_open(64, 64) + '\n'
         '<circle cx="32" cy="32" r="25.5" fill="#ffffff" fill-opacity="0.94" stroke="%s" stroke-width="2.2"/>\n'
         '<text x="32" y="46" text-anchor="middle" font-family="%s" font-size="39" font-weight="bold" fill="%s">%s</text>\n'
         '</svg>\n') % (edge, SANS, ink, esc(char))
    return s, 64, 64


FACILITY = {'exist': ('#404040', '#8A9AA8', '기 존'), 'new': ('#DB3F33', '#DF6E1F', '신 설')}


def label_facility(name='○○하수처리시설', q='Q=000㎥/일', kind='exist', status=None):
    """처리시설 이름표 — 머리띠(이름) + 왼쪽 아래 상태칸(기존/신설) + 용량. kind = exist(회색) / new(빨강)"""
    head, badge, st0 = FACILITY.get(kind, FACILITY['exist'])
    sx = min(0.9, 76 / max(1.0, tw(name, 10.28, 0.55)))
    W, H = 94.5, 35.03
    T = 'text-anchor="middle" transform="translate(46.89 15.56) scale(%s 1)" font-family="%s" font-size="10.28" letter-spacing="-0.2"' % (n(sx), GULIM)
    s = (svg_open(W, H) + '\n'
         '<rect x="6.23" y="5.27" width="81.33" height="24.22" rx="0.85" fill="#fff"/>\n'
         '<polyline points="6.23 18.53 6.23 5.27 87.56 5.27 87.56 18.53" fill="%s"/>\n'
         '<text %s fill="#231815">{t}</text><text %s fill="none" stroke="#fff" stroke-linejoin="round" stroke-width="0.3">{t}</text>'
         '<text %s fill="#fff">{t}</text>\n'
         '<path d="M34.91,29.49H7.4a.88.88,0,0,1-.87-.83V18.55H34.91Z" fill="%s"/>\n'
         '<text transform="translate(20.7 27.18) scale(0.92 1)" text-anchor="middle" font-family="%s" font-size="9.07" '
         'letter-spacing="-0.18" fill="#fff" stroke="#fff" stroke-width="0.2">%s</text>\n'
         '<text transform="translate(61.2 27.18) scale(0.92 1)" text-anchor="middle" font-family="%s" font-size="9.07" '
         'letter-spacing="-0.18" fill="#231815">%s</text>\n'
         '<rect x="6.23" y="5.27" width="81.33" height="24.22" rx="0.85" fill="none" stroke="%s" stroke-width="0.7"/>\n'
         '</svg>\n') % (head, T, T, T, badge, GULIM, esc(status or st0), GULIM, esc(q), head)
    return s.replace('{t}', esc(name)), W, H


def label_sheet(num='○○1', sub='○○관로', fill='#4A7470'):
    """도면(용지) 번호표 — 진청록 상자 + 큰 번호 + 작은 부제"""
    bw = max(tw(num, 4.2), tw(sub, 1.6)) + 1.1
    W, H = bw + 1, 10
    ny = 5.6 if sub else 6.9
    s = (svg_open(W, H)
         + '<rect x="0.5" y="0.5" width="%s" height="9" rx="0.6" fill="%s"/>' % (n(bw), fill)
         + '<rect x="0.5" y="0.5" width="%s" height="9" rx="0.6" fill="none" stroke="#fff" stroke-width="0.3"/>' % n(bw)
         + '<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="4.2" font-weight="bold" fill="#fff">%s</text>'
         % (n(W / 2), ny, GULIM, esc(num))
         + ('<text x="%s" y="8.3" text-anchor="middle" font-family="%s" font-size="1.6" font-weight="bold" fill="#fff">%s</text>'
            % (n(W / 2), GULIM, esc(sub)) if sub else '')
         + '</svg>\n')
    return s, W, H


NOTE = {'cream': ('#FEF3C7', '#78350F'), 'mint': ('#CCFBF1', '#042F2E'), 'wine': ('#7F1D1D', '#FEF2F2'),
        'indigo': ('#4F46E5', '#FFFFFF'), 'slate': ('#E2E8F0', '#1E293B'), 'amber': ('#D97706', '#FFFFFF')}


def label_note(lines=None, preset='cream', fill=None, ink=None):
    """조판 메모 상자 — 둥근 색 상자 + 가운데 맞춤 굵은 글자 여러 줄.
    preset = cream · mint · wine · indigo · slate · amber"""
    lines = as_list(lines) or ['[○○리](○○)', '하수도정비 : 00가구', '금회조사 : 00가구']
    f, k = NOTE.get(preset, NOTE['cream'])
    f, k = fill or f, ink or k
    W = round(max(tw(t, 14) for t in lines) + 12)
    H = 10 + 20 * len(lines)
    s = [svg_open(W, H), '<rect x="0" y="0" width="%s" height="%s" rx="%s" fill="%s"/>' % (W, H, n(min(20, H / 2)), f)]
    for i, t in enumerate(lines):
        s.append('<text x="%s" y="%s" text-anchor="middle" fill="%s" font-size="14" font-weight="bold" font-family="%s">%s</text>'
                 % (n(W / 2), n(20.25 + 20 * i), k, SANS, esc(t)))
    s.append('</svg>\n')
    return '\n'.join(s), W, H


# ───────── 키표 ─────────
KEY_HEADER = ['구분', '번호', '시설명', '제원 · 연장', '교체여부']
KEY_ROWS = [['맨홀펌프장', '①', '맨홀펌프장 #1', '0.00㎥/min × 0m', '-'],
            ['', '②', '맨홀펌프장 #2', '0.00㎥/min × 0m', '-'],
            ['압송관', '③', '#1 압송관', 'D80  L=000.0m', '-'],
            ['자연유하관', '④', 'XX-A-001~004', 'D200  L=000.0m', '교체'],
            ['', '⑤', 'XX-B-001~011', 'D200  L=000.0m', '-']]
KEY_BADGE = {'맨홀펌프장': ('#FFFFFF', '#2A1650', '#2A1650'), '압송관': ('#2A1650', '#2A1650', '#FFFFFF'),
             '자연유하관': ('#8A5A00', '#8A5A00', '#FFFFFF')}


def keytable(header=None, rows=None, cols=None, badge=None):
    """키표(제원표) — 진녹 머리줄(연금색 글자) + 구분 열 연녹 띠 + 짝수행 음영 + 번호 원.
    rows = [[구분(같은 구분이면 빈칸), 번호, 시설명, 제원·연장, 비고], ...]  비고가 '-' 가 아니면 빨강 굵게.
    번호 원 색은 구분 이름(맨홀펌프장·압송관·자연유하관) 또는 badge={구분: [채움, 테두리, 글자]}"""
    hdr = header or KEY_HEADER
    rows = [[('' if v is None else str(v)) for v in r] for r in (rows or KEY_ROWS)]
    W = 190.0
    cols = cols or [0, 30, 42, 106, 166, W]
    W = float(cols[-1])
    bmap = dict(KEY_BADGE)
    bmap.update(badge or {})
    RH, HH, FS = 8.4, 9.0, 5.42
    nr = len(rows)
    grp = []
    for i, r in enumerate(rows):
        if r[0] or not grp:
            grp.append([r[0], i, i])
        else:
            grp[-1][2] = i
    H = HH + nr * RH + 1.0
    s = [svg_open(W, H),
         '<rect x="0.3" y="0.3" width="%s" height="%s" rx="1.2" fill="#FFFFFF" stroke="#2A5346" stroke-width="0.6"/>' % (n(W - 0.6), n(H - 0.6))]
    y = 0.3
    s.append('<path d="M0.3,%s a1.2,1.2 0 0 1 1.2,-1.2 H%s a1.2,1.2 0 0 1 1.2,1.2 V%s H0.3 Z" fill="#2A5346"/>' % (n(y + 1.2), n(W - 1.5), n(y + HH)))
    for i, h in enumerate(hdr[:len(cols) - 1]):
        s.append('<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="%s" font-weight="bold" fill="#F5E6B8">%s</text>'
                 % (n((cols[i] + cols[i + 1]) / 2), n(y + 6.3), SANS, FS, esc(h)))
    y += HH
    s.append('<rect x="0.3" y="%s" width="%s" height="%s" fill="#E4EEE2"/>' % (n(y), n(cols[1] - 0.3), n(nr * RH)))
    for i in range(nr):
        if i % 2 == 1:
            s.append('<rect x="%s" y="%s" width="%s" height="%s" fill="#F4F7F3"/>' % (n(cols[1]), n(y + i * RH), n(W - cols[1] - 0.3), RH))
    for gname, a, b in grp:
        cy = y + (a + b + 1) / 2 * RH
        s.append('<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="%s" font-weight="bold" fill="#2A5346">%s</text>'
                 % (n(cols[1] / 2), n(cy + 1.9), SANS, FS, esc(gname)))
        if b < nr - 1:
            s.append('<line x1="0.3" y1="%s" x2="%s" y2="%s" stroke="#2A5346" stroke-width="0.5"/>' % (n(y + (b + 1) * RH), n(W - 0.3), n(y + (b + 1) * RH)))
    for k in range(1, len(cols) - 1):
        s.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#C5D2C3" stroke-width="0.3"/>' % (n(cols[k]), n(y), n(cols[k]), n(y + nr * RH)))
    cur = None
    for i, r in enumerate(rows):
        cy = y + i * RH + RH / 2
        cur = r[0] or cur
        bf, bs, bt = bmap.get(cur, ('#FFFFFF', '#333333', '#333333'))
        cx = (cols[1] + cols[2]) / 2
        if len(r) > 1 and r[1]:
            s.append('<circle cx="%s" cy="%s" r="3.9" fill="%s" stroke="%s" stroke-width="0.45"/>' % (n(cx), n(cy), bf, bs))
            s.append('<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="7.2" font-weight="bold" fill="%s">%s</text>'
                     % (n(cx), n(cy + 2.6), SANS, bt, esc(circled(r[1]))))
        for j in range(2, len(cols) - 1):
            if len(r) <= j:
                continue
            cell = r[j]
            mark = j == len(cols) - 2 and cell not in ('', '-')
            s.append('<text x="%s" y="%s" text-anchor="middle" font-family="%s" font-size="%s"%s fill="%s">%s</text>'
                     % (n((cols[j] + cols[j + 1]) / 2), n(cy + 1.9), SANS, FS, ' font-weight="bold"' if mark else '',
                        '#C94040' if mark else '#222222', esc(cell)))
    s.append('</svg>\n')
    return '\n'.join(s), W, H


# ───────── 지도 아이콘(색 바꿀 수 있는 것) ─────────
def icon_house(fill='#F4F2FF', stroke='#312E81'):
    """배수설비 가구(집) — 기본 = 기존(연보라/남색). 계획 = #FEF2F2/#DC2626, 계획(저지대) = #FFE200/#166534"""
    s = (svg_open(100, 100) + '\n<path d="%s" fill="%s" stroke="%s" stroke-width="12" stroke-linejoin="round" '
         'stroke-linecap="round"/>\n</svg>\n') % (HOUSE, fill, stroke)
    return s, 100, 100


def icon_pump(color='#DB3F33'):
    """맨홀펌프장 — 흰 원(흰 후광) + 색 테두리 + 토출 방향 삼각형. 기본 = 계획(빨강), 기존 = #231815"""
    s = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-3.7 -3.7 7.4 7.4">\n'
         '<circle cx="0" cy="0" r="2.9" fill="none" stroke="#FFFFFF" stroke-width="1.4"/>\n'
         '<circle cx="0" cy="0" r="2.9" fill="#FFFFFF" stroke="%s" stroke-width="0.5"/>\n'
         '<polygon points="-1.1,-2.5 -1.1,2.4 2.7,0" fill="%s"/>\n</svg>\n') % (color, color)
    return s, 7.4, 7.4


def template(src, replace=None):
    """아무 템플릿(src, assets/svg 기준 상대경로)에 글자 바꾸기 replace={옛: 새} 를 적용"""
    p = src if os.path.isabs(src) else os.path.join(HERE, src)
    s = open(p, encoding='utf-8').read()
    for a, b in (replace or {}).items():
        s = s.replace(a, b)
    m = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)', s)
    W, H = (float(m.group(3)), float(m.group(4))) if m else (0, 0)
    return s, W, H


TYPES = {f.__name__: f for f in (title_band, title_ribbon, title_pill, legend_list, legend_compare, tag_pill, tag_number,
                                  tag_box_pump, tag_box_section, capsule, tag_halo, badge_mark, badge_pin, badge_ribbon,
                                  tag_site, label_village, label_point, label_yellow, label_tiles, label_char,
                                  label_facility, label_sheet, label_note, keytable, icon_house, icon_pump, template)}

# 템플릿(자리표시 글자) — (assets/svg 기준 경로, type, 인자)
TEMPLATES = [
    ('title/title_band_green.svg', 'title_band', {}),
    ('title/title_ribbon.svg', 'title_ribbon', {}),
    ('title/title_pill_blue.svg', 'title_pill', {}),
    ('legend/legend_list.svg', 'legend_list', {}),
    ('legend/legend_compare.svg', 'legend_compare', {}),
    ('tag/tag_pill_gravity.svg', 'tag_pill', {}),
    ('tag/tag_pill_force.svg', 'tag_pill', {'no': 1, 'text': '#1 압송관', 'kind': 'press'}),
    ('tag/tag_number.svg', 'tag_number', {}),
    ('tag/tag_box_pump.svg', 'tag_box_pump', {}),
    ('tag/tag_box_section.svg', 'tag_box_section', {}),
    ('tag/tag_review.svg', 'capsule', {}),
    ('tag/tag_pump_capsule.svg', 'capsule', {'text': '○○#1', 'preset': 'pump'}),
    ('tag/tag_inflow.svg', 'capsule', {'text': '계획하수 유입', 'preset': 'inflow'}),
    ('tag/tag_halo.svg', 'tag_halo', {}),
    ('roadview/badge_mark.svg', 'badge_mark', {}),
    ('roadview/badge_pin.svg', 'badge_pin', {}),
    ('roadview/badge_ribbon.svg', 'badge_ribbon', {}),
    ('roadview/tag_site.svg', 'tag_site', {}),
    ('label/label_village.svg', 'label_village', {}),
    ('label/label_point.svg', 'label_point', {}),
    ('label/label_yellow.svg', 'label_yellow', {}),
    ('label/label_tiles.svg', 'label_tiles', {}),
    ('label/label_char.svg', 'label_char', {}),
    ('label/label_facility_existing.svg', 'label_facility', {}),
    ('label/label_facility_new.svg', 'label_facility', {'name': '○○하수처리시설(안)', 'kind': 'new'}),
    ('label/label_sheet.svg', 'label_sheet', {}),
    ('label/label_note.svg', 'label_note', {}),
    ('keytable/keytable.svg', 'keytable', {}),
    ('icon/house.svg', 'icon_house', {}),
    ('icon/pump.svg', 'icon_pump', {}),
]


# ───────── 실행 ─────────
def make(spec, outdir, item_h=None):
    spec = dict(spec)
    t = spec.pop('type')
    out = spec.pop('out', None) or ('%s.svg' % t)
    if t not in TYPES:
        raise SystemExit('모르는 type: %s (python make_svg.py --list)' % t)
    s, W, H = TYPES[t](**spec)
    p = out if os.path.isabs(out) else os.path.join(outdir, out)
    os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
    with open(p, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(s)
    msg = '%-34s viewBox %s × %s' % (os.path.relpath(p, outdir) if not os.path.isabs(out) else p, n(W), n(H))
    if item_h and H:
        msg += '   조판 아이템 %s × %s mm' % (n(item_h * W / H), n(item_h))
    print(msg)
    return p


def read_spec(path):
    if path.lower().endswith('.json'):
        data = json.load(open(path, encoding='utf-8-sig'))
        return data if isinstance(data, list) else [data]
    rows = []
    with open(path, encoding='utf-8-sig', newline='') as fh:
        for r in csv.DictReader(fh):
            d = {}
            for k, v in r.items():
                if k is None or v is None or v.strip() == '':
                    continue
                k, v = k.strip(), v.strip()
                if k in ('lines', 'header'):
                    v = as_list(v)
                elif k in ('rows', 'items', 'values', 'replace', 'cols', 'badge'):
                    v = json.loads(v)
                elif k in ('height', 'size'):
                    v = float(v)
                elif k == 'bold':
                    v = v.lower() in ('1', 'true', 'y', 'yes', 'o')
                d[k] = v
            if d.get('type'):
                rows.append(d)
    return rows


def write_sheet(out_html, dirs):
    """SVG 모음 HTML — 폴더별로 실제 비율 그대로 늘어놓는다(브라우저·headless Edge 로 찍어 검사)"""
    px = {'title': 64, 'legend': 230, 'keytable': 210, 'tag': 40, 'roadview': 44, 'label': 52, 'icon': 64}
    cards = []
    base = os.path.dirname(os.path.abspath(out_html))
    for d in dirs:
        for root, _, files in os.walk(d):
            for f in sorted(files):
                if not f.lower().endswith('.svg'):
                    continue
                p = os.path.join(root, f)
                s = open(p, encoding='utf-8').read()
                m = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)', s)
                vw, vh = (float(m.group(3)), float(m.group(4))) if m else (1, 1)
                kind = os.path.basename(root)
                h = px.get(kind, 56)
                w = h * vw / vh
                if w > 720:
                    w, h = 720, 720 * vh / vw
                try:
                    rel = os.path.relpath(p, base).replace(os.sep, '/')
                except ValueError:                      # 다른 드라이브 → 절대 file URI
                    rel = 'file:///' + os.path.abspath(p).replace(os.sep, '/')
                cards.append((kind, f, rel, w, h))
    groups = {}
    for c in cards:
        groups.setdefault(c[0], []).append(c)
    order = ['title', 'legend', 'keytable', 'tag', 'roadview', 'label', 'icon']
    body = []
    for k in sorted(groups, key=lambda x: (order.index(x) if x in order else 99, x)):
        body.append('<h2>%s</h2><div class="g">' % esc(k))
        for _, f, rel, w, h in groups[k]:
            body.append('<figure><div class="im"><img src="%s" style="width:%spx;height:%spx"></div>'
                        '<figcaption>%s</figcaption></figure>' % (html.escape(rel), n(w), n(h), esc(f)))
        body.append('</div>')
    page = ('<!doctype html><html><head><meta charset="utf-8"><style>'
            'body{margin:0;padding:14px 18px;background:#8fa39b;font:13px "Malgun Gothic",sans-serif;color:#1d2a25}'
            'h2{margin:10px 0 6px;font-size:15px;color:#fff;letter-spacing:.5px}'
            '.g{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end}'
            'figure{margin:0;display:flex;flex-direction:column;align-items:center}'
            '.im{display:flex;align-items:center;justify-content:center;padding:6px;'
            'background:repeating-conic-gradient(#c9d3cf 0 25%,#b7c4bf 0 50%) 0 0/14px 14px;border-radius:4px}'
            'figcaption{font-size:11px;margin-top:3px;color:#123}'
            '</style></head><body>@BODY@</body></html>\n').replace('@BODY@', ''.join(body))
    with open(out_html, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(page)
    print('%s  (%d 장)' % (out_html, len(cards)))


def main(argv=None):
    ap = argparse.ArgumentParser(description='조판·삽도 SVG 양식 생성기')
    ap.add_argument('spec', nargs='?', help='JSON 또는 CSV 목록')
    ap.add_argument('-o', '--out', default='.', help='출력 폴더 (기본: 현재 폴더)')
    ap.add_argument('--item-height', type=float, help='조판 아이템 높이(mm) — 주면 아이템 폭을 함께 출력')
    ap.add_argument('--templates', action='store_true', help='assets/svg/*/ 템플릿 다시 만들기')
    ap.add_argument('--list', action='store_true', help='type 목록과 인자')
    ap.add_argument('--sheet', metavar='HTML', help='SVG 모음 HTML 만들기 (뒤에 폴더들)')
    ap.add_argument('--sans', help='맑은 고딕 대신 쓸 글꼴 이름 (하나만)')
    ap.add_argument('--gulim', help='굴림 대신 쓸 글꼴 이름')
    ap.add_argument('--gulimche', help='굴림체 대신 쓸 글꼴 이름')
    ap.add_argument('dirs', nargs='*')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    global SANS, GULIM, GULIMCHE
    SANS, GULIM, GULIMCHE = a.sans or SANS, a.gulim or GULIM, a.gulimche or GULIMCHE
    if a.list:
        import inspect
        for k, f in TYPES.items():
            sig = str(inspect.signature(f))
            print('%s%s\n    %s\n' % (k, sig, (f.__doc__ or '').strip().replace('\n', '\n    ')))
        return
    if a.templates:
        for rel, t, kw in TEMPLATES:
            make(dict(kw, type=t, out=rel), HERE, a.item_height)
        return
    if a.sheet:
        dirs = ([a.spec] if a.spec else []) + a.dirs
        write_sheet(a.sheet, dirs or [HERE])
        return
    if not a.spec:
        ap.print_help()
        return
    for sp in read_spec(a.spec):
        make(sp, a.out, a.item_height)


if __name__ == '__main__':
    main()
