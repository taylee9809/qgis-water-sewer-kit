# -*- coding: utf-8 -*-
"""A2 연장 전수 1:1 대조 — 종단면도 막대 ↔ flo ↔ 설계 DB(계획관로) 반영분.

합산 규칙 (중요)
  종단면도는 압송관을 **모관번호 1막대**로 그린다 → 압송 세분구간(-00801)은 모관번호로 합산해 비교.
  그러나 **자연유하인데 세분번호인 것은 합산하지 않는다.** 그건 설계사가 이전 회차의 압송 서브번호를
  고치지 않고 자연유하로 바꿔버린 경우이고, 연장 오차가 아니라 **번호 불일치 지적**이다.
  (사례: 평면 XX-A-02501~02503 = 종단 도면 XX-A-026~028)
  압송 판정: flo 관경 D ≤ design.pressure_max_d_mm, flo 에 없으면 계획관로 kind == values.kind_pressure.

범위
  판정은 **금회 납품 범위(flo 에 들어있는 구간)** 로만 한다. 설계 DB 에 섞여 있는 이전 회차·기존
  계통의 종단 막대는 `참고`로만 찍는다(압송 막대가 여러 시트에 걸려 순서짝짓기가 밀리는 등
  금회와 무관한 사유가 많다).

합격: 금회 범위 불일치 0건.
"""
import collections, re
import vendorlib as V

ctx = V.argctx('A2 연장 전수 대조')
cfg = ctx['cfg']
TOL = V.tol(cfg, 'length_m')
DMAX = V.pressure_dmax(cfg)
PRESS = V.kind_pressure(cfg)
if not ctx['profile']:
    raise SystemExit('종단면도.dxf 가 없다.')

# ── 종단 막대
pb = V.profile_bars(ctx['profile'], cfg)
prof = pb['length']

# ── 0. 막대 실측폭 ↔ 도면에 글자로 적힌 L= (작도 오류 검사)
draw = [(k, prof[k], pb['label_len'][k]) for k in pb['label_len']
        if abs(prof[k] - pb['label_len'][k]) > TOL]
print('\n── 막대 실측폭 ↔ 도면 기재 L= : 대조 %d / **불일치 %d**'
      % (len(pb['label_len']), len(draw)))
for k, a, b in sorted(draw, key=lambda t: -abs(t[1] - t[2]))[:20]:
    print('     %-14s 막대 %8.2f  도면기재 %8.2f  (%+7.2f)  ← 작도 오류'
          % (k, a, b, a - b))
# 이후 대조는 도면에 적힌 값을 기준으로 쓴다(사람이 읽는 값이므로)
prof = {k: pb['label_len'].get(k, v) for k, v in prof.items()}

# ── 설계 DB 계획관로 (구분 판정의 기준으로도 쓴다)
qg = V.plan_routes(ctx)

# ── flo
flo = V.read_all_flo(ctx)
flo_raw, flo_press = {}, {}
for f, rows in flo.items():
    for r in rows:
        flo_raw[r['up']] = r['L']
        try:
            flo_press[r['up']] = float(r['D'] or 0) <= DMAX
        except ValueError:
            flo_press[r['up']] = False


def is_press_flo(no):
    if no in flo_press:
        return flo_press[no]
    g = qg.get(no)
    return bool(g and g['구분'] == PRESS)


def is_press_qg(no):
    g = qg.get(no)
    if g and g['구분']:
        return g['구분'] == PRESS
    return is_press_flo(no)


def agg(src, press):
    """압송인 세분번호만 모관번호로 합산."""
    out = collections.defaultdict(float)
    for no, L in src.items():
        key = V.mother(no) if (V.mother(no) != no and press(no)) else no
        out[key] += (L or 0)
    return {k: round(v, 2) for k, v in out.items()}


flo_agg = agg(flo_raw, is_press_flo)
qg_agg = agg({k: v['L'] for k, v in qg.items() if v['L'] is not None}, is_press_qg)

# ── 금회 범위 = flo 에 들어있는 구간(합산 키 기준)
SCOPE = set(flo_agg) | set(flo_raw)
print('\n금회 납품 범위: flo %d구간 → 합산키 %d개' % (len(flo_raw), len(flo_agg)))


def cmp(name, A, B, la, lb, scope=True):
    both = sorted(set(A) & set(B) & SCOPE) if scope else sorted(set(A) & set(B) - SCOPE)
    diff = [(k, A[k], B[k]) for k in both if abs(A[k] - B[k]) > TOL]
    print('\n── %s%s : 대조 %d / 일치 %d / **불일치 %d**'
          % (name, '' if scope else '  [참고 · 금회 범위 밖]',
             len(both), len(both) - len(diff), len(diff)))
    for k, a, b in sorted(diff, key=lambda t: -abs(t[1] - t[2])):
        g = qg.get(k) or {}
        print('     %-14s %s %8.2f  %s %8.2f  (%+7.2f)  %s %s'
              % (k, la, a, lb, b, a - b, g.get('구분') or '', g.get('지구') or ''))
    return [dict(관번호=k, **{la: a, lb: b, '차': round(a - b, 2),
                            '구분': (qg.get(k) or {}).get('구분'),
                            '지구': (qg.get(k) or {}).get('지구')}) for k, a, b in diff]


# ── 설계 DB 가 이 회차를 담고 있는지. 옛 회차를 최신 DB 와 비교하면 전부 불일치로 보인다.
#    (fields.plan_pipe.asof 가 매핑돼 있을 때만 본다)
stale = False
if V.fname(cfg, 'plan_pipe', 'asof'):
    asofs = collections.Counter((qg[k]['자료기준일'] or '') for k in flo_raw if k in qg)
    m = re.match(r'^(\d{6})', ctx['delivery'].name)
    yy = ('20%s-%s-%s' % (m.group(1)[:2], m.group(1)[2:4], m.group(1)[4:6])) if m else ''
    stale = bool(asofs and yy and not any(yy in a for a in asofs))
    if stale:
        print('\n★ 경고: 설계 DB 의 금회 구간 자료기준일이 이 납품물(%s)과 다르다.' % yy)
        for a, c0 in asofs.most_common(3):
            print('     %-58s %4d' % (a or '(없음)', c0))
        print('   → 이 설계 DB 에는 다른 회차가 반영돼 있다. flo↔QGIS·종단↔QGIS 불일치는')
        print('     납품물 오류가 아니라 회차 차이일 수 있다. 종단↔flo 결과만 보거나,')
        print('     --gpkg 로 그 회차 백업 GPKG 를 지정할 것.')

print('\n' + '=' * 78)
print('허용차 ±%.2f m / 압송 판정 D ≤ %d mm' % (TOL, DMAX))
d1 = cmp('종단 ↔ flo', prof, flo_agg, '종단', 'flo')
d2 = cmp('종단 ↔ QGIS', prof, qg_agg, '종단', 'QGIS')
d3 = cmp('flo ↔ QGIS (구간별, 합산 안 함)', flo_raw, {k: v['L'] for k, v in qg.items()
                                               if v['L'] is not None}, 'flo', 'QGIS')
ref = cmp('종단 ↔ QGIS', prof, qg_agg, '종단', 'QGIS', scope=False)

# ── 한쪽에만 있는 구간
only_flo = sorted(set(flo_agg) - set(prof))
sub_ok = [k for k in only_flo if V.mother(k) != k and is_press_flo(k)]
real = [k for k in only_flo if k not in sub_ok]
only_prof_scope = sorted((set(prof) - set(flo_agg)) & {V.mother(k) for k in SCOPE})
print('\n── 종단에 이름이 없는 flo 합산키 %d' % len(only_flo))
print('   압송 세분(정상) %d: %s' % (len(sub_ok), sub_ok[:10]))
print('   ★ 번호 불일치·누락 후보 %d: %s' % (len(real), real))
if only_prof_scope:
    print('   ★ 종단에만 있고 flo 에 없는 구간 %d: %s' % (len(only_prof_scope), only_prof_scope))

판정 = 'PASS' if not (draw or d1 or d2 or d3 or real or only_prof_scope
                      or pb['unpaired']) else 'FAIL'
print('\n' + '=' * 78)
print('A2 판정: %s' % 판정)
print('   작도(막대↔도면기재 L) 불일치 %d' % len(draw))
print('   금회 불일치  종단↔flo %d · 종단↔QGIS %d · flo↔QGIS %d' % (len(d1), len(d2), len(d3)))
print('   번호 불일치·누락 %d / 종단에만 %d / 행 짝짓기 실패 %s'
      % (len(real), len(only_prof_scope), pb['unpaired'] or '없음'))
print('   참고(금회 범위 밖) 불일치 %d건 — 판정에 넣지 않음' % len(ref))

V.save(ctx, 'a2_len.json',
       dict(판정=판정, 허용차=TOL, 압송판정D=DMAX,
            행진단=pb['diag'], 행짝짓기실패=pb['unpaired'],
            불일치_막대_도면기재=[dict(관번호=k, 막대=a, 도면기재=b, 차=round(a - b, 2))
                           for k, a, b in draw],
            종단연장=prof, 금회범위=len(SCOPE),
            불일치_종단_flo=d1, 불일치_종단_QGIS=d2, 불일치_flo_QGIS=d3,
            번호불일치_누락후보=real, 종단에만=only_prof_scope,
            압송세분_정상=sub_ok, 참고_범위밖=ref))
