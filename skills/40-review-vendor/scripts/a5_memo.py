# -*- coding: utf-8 -*-
"""A5 비출력 작업메모 수거 — 메모 레이어(review_vendor.memo.layers, 예: j_none) → 관번호 구간 매핑.

설계사는 출력 안 되는 레이어에 사유 메모를 남긴다(글자높이가 본문의 10배 이상 = memo.min_text_height).
메모를 소스에 박아 두지 않고 전수 자동 추출한다.

메모 x좌표 = 그 행의 누가측점 → 같은 행의 관번호 막대 구간에 매핑한다.
"확인 필요" 같은 미확정 표시가 있으면 그 구간은 미확정으로 간주하고 지적에 넣는다.
"""
import collections, re
import vendorlib as V

ctx = V.argctx('A5 비출력 메모 수거')
cfg = ctx['cfg']
P = V.profile_conf(cfg)
M = V.memo_conf(cfg)
PITCH = P['row_pitch']
if not ctx['profile']:
    raise SystemExit('종단면도.dxf 가 없다.')

import ezdxf
doc = ezdxf.readfile(str(ctx['profile']))

memos, rowlabels, heights = [], [], collections.Counter()


def h(e, t, ox, oy):
    if t not in ('TEXT', 'ATTRIB', 'MTEXT'):
        return
    lay = e.dxf.layer
    s = (getattr(e.dxf, 'text', '') or '').strip()
    if not s:
        return
    ht = float(getattr(e.dxf, 'height', 0) or getattr(e.dxf, 'char_height', 0) or 0)
    heights[round(ht, 1)] += 1
    x = e.dxf.insert[0] + ox
    y = e.dxf.insert[1] + oy
    if lay in M['layers'] and ht >= M['min_text_height']:
        # ★ 행 번호는 floor. 메모는 그 행 도면 **위** +600 띠에 찍히므로
        #   round 를 쓰면 다음 행으로 밀린다('종점 변경' 메모를 다른 계통으로 오판한 원인).
        memos.append(dict(layer=lay, row=V.rowof(y, PITCH), x=round(x, 2),
                          y=round(y, 2), h=round(ht, 1), text=s))


V.walk(doc, doc.modelspace(), h)
print('\n글자높이 분포(상위):', dict(heights.most_common(8)))
print('메모 레이어 %s / 높이 ≥ %s → %d건' % (M['layers'], M['min_text_height'], len(memos)))

# 막대로 구간 매핑
pb = V.profile_bars(ctx['profile'], cfg, verbose=False)
seg = collections.defaultdict(list)
for row, x, nm in sorted(pb['texts'], key=lambda t: (t[0], t[1])):
    seg[row].append([x, nm])
bars = collections.defaultdict(list)
for row, x0, x1 in sorted(pb['bars']):
    bars[row].append((x0, x1))
mapped = {}
for row in sorted(set(seg) | set(bars)):
    tt, bb = seg[row], bars[row]
    if len(tt) == len(bb):
        mapped[row] = [(b[0], b[1], t[1]) for t, b in zip(tt, bb)]

# 행 이름표(x 가 음수인 메모) 분리
labels = [m for m in memos if m['x'] < 0]
notes = [m for m in memos if m['x'] >= 0]
if labels:
    print('\n── 행 이름표 %d' % len(labels))
    for m in sorted(labels, key=lambda r: r['row']):
        print('   row%d  %s' % (m['row'], m['text']))

print('\n── 작업메모 %d' % len(notes))
UNCERT = re.compile(r'확인|검토|미정|변경|필요|\?')
findings = []
for m in sorted(notes, key=lambda r: (r['row'], r['x'])):
    s = mapped.get(m['row'])
    w = len(m['text']) * m['h'] * 0.55
    at = cov = []
    if s:
        at = [v[2] for v in s if v[0] <= m['x'] <= v[1]]
        cov = [v[2] for v in s if not (v[1] < m['x'] or v[0] > m['x'] + w)]
    flag = '  ★ 미확정' if UNCERT.search(m['text']) else ''
    print('\n   row%d x=%9.2f h=%.0f  「%s」%s' % (m['row'], m['x'], m['h'], m['text'], flag))
    if not s:
        print('        → 행 매핑 실패(라벨·막대 수 불일치)')
    else:
        print('        시작 지점 → %s' % (at[0] if at else '구간 사이(공백)'))
        print('        걸친 구간(~%.0fm) → %s' % (w, ', '.join(cov) or '없음'))
    m['시작구간'] = at[0] if at else None
    m['걸친구간'] = cov
    m['미확정'] = bool(UNCERT.search(m['text']))
    if m['미확정']:
        findings.append('%s → %s' % (m['text'], ', '.join(cov) or '구간 미상'))

판정 = 'PASS' if not findings else 'CHECK'
print('\n' + '=' * 78)
print('A5 판정: %s  (메모 %d / 미확정 표시 %d)' % (판정, len(notes), len(findings)))
for x in findings:
    print('   · %s' % x)
print('   → 미확정 구간은 설계 확정 전으로 간주하고 지적에 포함할 것.')

V.save(ctx, 'a5_memo.json',
       dict(판정=판정, 행이름표=labels, 메모=notes, 미확정=findings))
