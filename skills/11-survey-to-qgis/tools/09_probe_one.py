# -*- coding: utf-8 -*-
"""09 — 한 폴리곤 주변 전수 덤프: 폴리곤이 어떤 선에서 만들어졌는지, 그 선이 지장물인지.

사용
  python -X utf8 09_probe_one.py <반경m> <지장물번호> [--config sewer.config.json]
        [--src <경량.dxf>] [--gpkg ...] [--poly ...] [--note-field <필드>]
  예: 09_probe_one.py 15 <지장물번호>
"""
import os
import sys
import math
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import *                                      # noqa: E402,F403

_pos = positionals()
if len(_pos) < 2:
    print(__doc__)
    sys.exit(0)
RAD = float(_pos[0])
KEY = _pos[1]
GP, PL, _ = obstacle_src(need_cl=False)
DXF = light_dxf()

rows = read_layer(GP, PL)
NOTES = note_fields(columns(GP, PL))
tgt = next(((pr, g) for pr, g in rows if pr.get('지장물번호') == KEY), None)
if tgt is None:
    print('%s 가 %s | %s 에 없다' % (KEY, os.path.basename(GP), PL))
    sys.exit(1)
pr0, g0 = tgt
C = g0.representative_point()
print('%s %s %s 면적 %.2f 폭필드 %s' % (KEY, pr0.get('종류'), pr0.get('표기'), g0.area,
                                   pr0.get('규격_폭m')))
print('  좌표: ' + ' '.join('(%.2f,%.2f)' % (x, y) for x, y in list(g0.exterior.coords)))
print('  중심 (%.2f, %.2f)' % (C.x, C.y))

# 원도 전 레이어(경량본에 남은 것) 선·텍스트
doc = load_dxf(DXF)
allseg = []
for e in doc.modelspace():
    t = e.dxftype()
    lay = e.dxf.layer
    if t == 'LINE':
        allseg.append((lay, LineString([(e.dxf.start.x, e.dxf.start.y),
                                        (e.dxf.end.x, e.dxf.end.y)])))
    elif t == 'LWPOLYLINE':
        p = [(a[0], a[1]) for a in e.get_points()]
        if getattr(e, 'closed', False) and len(p) > 2:
            p = p + [p[0]]
        if len(p) >= 2:
            allseg.append((lay, LineString(p)))
near = [(g.distance(C), lay, g) for lay, g in allseg if g.distance(C) <= RAD]
near.sort(key=lambda r: r[0])
print('\n-- 원도 선 (반경 %.0f m, 경량본 전 레이어) %d개 --' % (RAD, len(near)))
print('   레이어 분포:', dict(collections.Counter(l for _, l, _ in near)))
for d, lay, g in near[:28]:
    a, b = g.coords[0], g.coords[-1]
    print('   %5.2f  %-16s L %6.2f 각 %5.1f (%.2f,%.2f)-(%.2f,%.2f)' % (
        d, lay, g.length, math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180,
        a[0], a[1], b[0], b[1]))

print('\n-- 폴리곤 안/경계에 들어있는 원도 선 --')
inside = [(lay, g) for lay, g in allseg if g.within(g0.buffer(0.05))]
touch = [(lay, g) for lay, g in allseg if g.distance(g0) < 0.03 and not g.within(g0.buffer(0.05))]
print('   내부 %d개:' % len(inside))
for lay, g in inside[:10]:
    print('      %-16s L %6.2f' % (lay, g.length))
print('   경계 접촉 %d개:' % len(touch))
for lay, g in touch[:12]:
    a, b = g.coords[0], g.coords[-1]
    print('      %-16s L %6.2f 각 %5.1f (%.2f,%.2f)-(%.2f,%.2f)' % (
        lay, g.length, math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180,
        a[0], a[1], b[0], b[1]))

txts = dxf_texts(doc, layers=None)
tn = sorted(((math.dist((x, y), (C.x, C.y)), lay, t) for lay, t, x, y in txts
             if math.dist((x, y), (C.x, C.y)) <= RAD), key=lambda r: r[0])
print('\n-- 주변 텍스트 %d개 --' % len(tn))
for d, lay, t in tn[:20]:
    print('   %5.2f  %-12s %r' % (d, lay, t))

print('\n-- 주변 QGIS 폴리곤 --')
for pr, g in sorted(((pr, g) for pr, g in rows if g.distance(C) <= RAD),
                    key=lambda r: r[1].distance(C)):
    L, w = band_dims(g)
    print('   %5.2fm %-10s %-8s %-14s 폭 %-5s 면적 %7.2f 실측 %-6s %s' % (
        g.distance(C), pr.get('지장물번호'), pr.get('종류'), pr.get('표기'),
        pr.get('규격_폭m'), g.area, round(w, 3) if w else '-',
        note_text(pr, NOTES)[:28]))
