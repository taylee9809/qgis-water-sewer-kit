# -*- coding: utf-8 -*-
"""진단용 — 종단면도 밴드 숫자·레이어 덤프. **서식이 바뀌어 A2/A5 가 안 먹을 때** 쓴다.

여기서 읽은 국소 y 값을 sewer.config.json 의 review_vendor.profile.label_y_lo/hi · bar_layer 에 반영한다.

    python band_dump.py                          # 최신 납품물, row0
    python band_dump.py --row 5 --xmin 2700 --xmax 3500
    python band_dump.py --row 4 --ylo 50 --yhi 140
    python band_dump.py --layers                 # 레이어별 엔티티 census
"""
import collections
import vendorlib as V


def extra(ap):
    ap.add_argument('--row', type=int, default=0, help='행 번호(종단 시트 행)')
    ap.add_argument('--xmin', type=float, default=None)
    ap.add_argument('--xmax', type=float, default=None)
    ap.add_argument('--ylo', type=float, default=0.0, help='국소 y 하한')
    ap.add_argument('--yhi', type=float, default=140.0, help='국소 y 상한')
    ap.add_argument('--layers', action='store_true', help='레이어 census 만')


ctx = V.argctx('종단면도 밴드 덤프(진단)', extra)
a = ctx['args']
cfg = ctx['cfg']
P = V.profile_conf(cfg)
PITCH = P['row_pitch']
if not ctx['profile']:
    raise SystemExit('종단면도.dxf 가 없다.')

import ezdxf
doc = ezdxf.readfile(str(ctx['profile']))
rows, census = [], collections.Counter()


def h(e, t, ox, oy):
    census[(t, e.dxf.layer)] += 1
    if a.layers or t not in ('TEXT', 'ATTRIB'):
        return
    x = e.dxf.insert[0] + ox
    y = e.dxf.insert[1] + oy
    if V.rowof(y, PITCH) != a.row:
        return
    ly = y - a.row * PITCH
    if not (a.ylo <= ly <= a.yhi):
        return
    if a.xmin is not None and x < a.xmin:
        return
    if a.xmax is not None and x > a.xmax:
        return
    rows.append((round(ly, 1), round(x, 2), (e.dxf.text or '').strip(),
                 e.dxf.layer, round(getattr(e.dxf, 'rotation', 0) or 0, 0),
                 round(float(getattr(e.dxf, 'height', 0) or 0), 1)))


V.walk(doc, doc.modelspace(), h)

if a.layers:
    print('\n── 레이어 census (상위 40)')
    for (t, lay), c in census.most_common(40):
        print('   %-12s %-24s %7d' % (t, lay, c))
    raise SystemExit

print('\nrow%d / 국소 y %s~%s / x %s~%s → TEXT %d개'
      % (a.row, a.ylo, a.yhi, a.xmin, a.xmax, len(rows)))
print('국소 y 별 개수:', dict(collections.Counter(r[0] for r in rows).most_common(20)))
for y in sorted(set(r[0] for r in rows)):
    g = sorted([r for r in rows if r[0] == y], key=lambda r: r[1])
    print('\n--- 국소y=%6.1f  n=%4d  layer=%s  rot=%s  h=%s'
          % (y, len(g), g[0][3], g[0][4], g[0][5]))
    print('    ' + '  '.join('%.1f:%s' % (r[1], r[2]) for r in g[:30]))
print('\n→ 관번호 라벨이 찍힌 국소 y 를 %s 의 review_vendor.profile.label_y_lo/hi 에 반영할 것.'
      % cfg.path.name)
print('  막대 레이어는 --layers 로 LWPOLYLINE 레이어를 확인(현재 설정 %r).'
      % P['bar_layer'])
