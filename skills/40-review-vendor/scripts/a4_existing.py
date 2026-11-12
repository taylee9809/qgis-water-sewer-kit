# -*- coding: utf-8 -*-
"""A4 기존 접속부 관저고 — flo 방류노드(말단) ↔ 준공 기준 DB 유출관저고.

방법
  1. flo 말단 노드(상류에 안 나오는 하류노드) = 기존망 접속점.
     그 행의 꼬리 숫자에 설계사가 적은 **기존 지반고·기존 관저고**가 들어 있다.
  2. 접속점 좌표 = 그 구간의 계획관로(layers.plan_pipe) 도형 끝점.
  3. 좌표에서 준공 맨홀을 근접 검색(node_snap_m) → **유출관저고** 비교.
     기준 DB: review_vendor.asbuilt_asof 가 있으면 계획맨홀 레이어 중 그 자료기준일(준공 편입분),
              비어 있으면 layers.asbuilt_manhole.
  4. ★ flo 기존관저가 '신설 하류관저 + 0.01~0.08' 로 기계적 복사돼 있는지 검사.
     그렇다면 실측 조회를 안 한 것이므로 전 접속점 재확인을 지적한다.

합격: 전 접속점 ±invert_m 이내.
"""
import re
import vendorlib as V

ctx = V.argctx('A4 기존 접속부 관저고')
cfg = ctx['cfg']
TOL = V.tol(cfg, 'invert_m')
SNAP = V.tol(cfg, 'node_snap_m')
NOTE = V.rv(cfg, 'asbuilt_note') or V.DEFAULT_ASBUILT_NOTE

flo = V.read_all_flo(ctx)
qg = V.plan_routes(ctx)
asb, asb_src = V.asbuilt_manholes(ctx)
print('\n준공 DB 맨홀 %d개 (기준: %s)' % (len(asb), asb_src))
if not asb:
    print('★ 준공 맨홀이 0개다 — %s 의 review_vendor.asbuilt_asof / layers.asbuilt_manhole 을 확인할 것.'
          % cfg.path.name)

rows = []
for f, rs in flo.items():
    ups = {r['up'] for r in rs}
    말단 = sorted({r['dn'] for r in rs} - ups)
    for term in 말단:
        for r in rs:
            if r['dn'] != term:
                continue
            # 기존 GL·IL = 토큰 8·9 (지반고 다음 두 칸). 소수점이 있어야 값이다.
            # 일반 행:  … ILu ILd GL   0  200
            # 접속 행:  … ILu ILd GL  기존GL 기존IL  0  200
            # (정규식으로 꼬리 실수를 긁으면 n=0.01 을 집는다 — 반드시 토큰 위치로)
            exist_gl = exist_il = None
            tail = r['tail']
            if len(tail) >= 2 and '.' in tail[0] and '.' in tail[1]:
                try:
                    exist_gl, exist_il = float(tail[0]), float(tail[1])
                except ValueError:
                    exist_gl = exist_il = None
            g = qg.get(r['up']) or {}
            pt = g.get('B')
            cand = V.nearest(pt, asb, 3) if pt else []
            near = cand[0] if (cand and cand[0][0] <= SNAP) else None
            복사 = (exist_il is not None and abs(exist_il - r['ILd']) <= 0.08
                   and exist_il >= r['ILd'])
            diff = (exist_il - near[4]) if (near and exist_il is not None
                                            and near[4] is not None) else None
            rows.append(dict(flo=f, 접속점=term, 상류구간=r['up'],
                             신설하류관저=r['ILd'], 설계사_기존관저=exist_il,
                             설계사_기존지반=exist_gl,
                             준공맨홀=near[1] if near else None,
                             이격_m=round(near[0], 2) if near else None,
                             준공_유출관저=near[4] if near else None,
                             준공_지반고=near[3] if near else None,
                             차_m=round(diff, 2) if diff is not None else None,
                             자기값복사=복사,
                             후보=[(round(c[0], 2), c[1], c[4]) for c in cand]))

print('\n' + '=' * 78)
print('%-12s %-12s %8s %8s  %-12s %7s %8s %8s  %s'
      % ('접속점', '상류구간', '신설IL', '설계IL', '준공맨홀', '이격', '준공IL', '차', '비고'))
ng, copy = [], []
for d in rows:
    비고 = []
    if d['자기값복사']:
        비고.append('자기값복사')
        copy.append(d['접속점'])
    if d['차_m'] is None:
        비고.append('준공맨홀 없음' if d['준공맨홀'] is None else '값없음')
        ng.append(d['접속점'])
    elif abs(d['차_m']) > TOL:
        비고.append('★ 불일치')
        ng.append(d['접속점'])
    print('%-12s %-12s %8s %8s  %-12s %7s %8s %8s  %s'
          % (d['접속점'], d['상류구간'],
             '%.2f' % d['신설하류관저'] if d['신설하류관저'] is not None else '-',
             '%.2f' % d['설계사_기존관저'] if d['설계사_기존관저'] is not None else '-',
             d['준공맨홀'] or '-',
             '%.2f' % d['이격_m'] if d['이격_m'] is not None else '-',
             '%.2f' % d['준공_유출관저'] if d['준공_유출관저'] is not None else '-',
             '%+.2f' % d['차_m'] if d['차_m'] is not None else '-',
             ' '.join(비고)))
    if d['준공맨홀'] is None and d['후보']:
        if d['후보'][0][0] > 100:
            print('%26s 최근접 준공맨홀이 %.0f m — 기준 DB 에 해당 지구 준공도가 안 들어있다.'
                  % ('', d['후보'][0][0]))
            print('%26s %s' % ('', NOTE))
        else:
            print('%26s 후보: %s' % ('', ', '.join('%s %.1fm IL=%s' % (c[1], c[0], c[2])
                                                 for c in d['후보'])))

print('\n' + '=' * 78)
if copy:
    print('★ flo 기존관저가 신설 하류관저 +0~0.08 로 복사된 접속점 %d/%d : %s'
          % (len(copy), len(rows), ', '.join(copy)))
    print('   → 실측 조회를 안 한 것이다. **전 접속점 관저고 재확인**을 지적할 것.')
판정 = 'PASS' if not ng else 'FAIL'
print('A4 판정: %s  (허용 ±%.2f m / 불일치·미확인 %d / 자기값복사 %d)'
      % (판정, TOL, len(ng), len(copy)))

V.save(ctx, 'a4_existing.json',
       dict(판정=판정, 허용차=TOL, 근접반경=SNAP, 접속점수=len(rows),
            불일치=ng, 자기값복사=copy, 상세=rows))
