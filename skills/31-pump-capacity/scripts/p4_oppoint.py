# -*- coding: utf-8 -*-
"""P4 운전점 — 제조사 성능곡선(Q-H) ∩ 시스템곡선(ha + hf + hl) → 실제 토출량·양정·축동력.

곡선 값은 코드에 넣지 않는다. 입력 파일(JSON) 하나로 준다 — 예시: ../examples/curves.example.json
  {"curves":   {"모델A": {"Q": [㎥/min…], "H": [m…], "kW": [..], "eff": [%..], "motor_kw": 1.5, "rated": [0.10, 13]}},
   "stations": {"XX-001": {"model": "모델A", "bop": 28.6, "lwl": 19.6, "hwl": 20.1, "force_main_m": 174.0,
                            "back_dia_mm": 75, "bore": 50}}}
  stations 의 수리값(bop·lwl·hwl·force_main_m·back_dia_mm·bore·well_volume_m3)을 비우면
  asbuilt_pump 레이어 / --specs 파일에서 채운다(파일 값이 우선).

시스템곡선 h(q) = (BOP − 수위) + hf(q) + hl(q) — p2·p3 와 같은 손실식(design.pump.*).
수위 = LWL(실양정 최대 → 토출 최소, 판정 기준) 과 HWL 두 경우.
교점: 0 ~ 곡선 끝 Q 를 3000 등분해 (펌프H − 시스템H) 부호가 바뀌는 곳을 선형보간.
  체절양정 < 실양정 이면 「토출 불가」, 곡선 끝까지 교점이 없으면 「런아웃」(곡선 끝 Q 로 찍음).

--inflow pump_existing.json (p3 산출) 을 주면 시나리오별 유입 vs 운전점 표:
  가동률 = 유입/운전점Q, 운전·정지·기동횟수(흡수정 V), 판정 = 유입 < 운전점Q.
  ※ 판정 기준을 정격점(명판)으로 할지 운전점으로 할지는 사람이 정한다 — 이 표는 참고용으로 많이 쓴다.
산출: <out>/oppoint.csv · oppoint_curves.csv · (oppoint_scen.csv)
"""
import json
from pathlib import Path

import pumplib as PL


def interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]


def hsys(q, ha, L, d_pump, d_back, P):
    return ha + PL.hf(q, d_pump, d_back, P) + PL.hl_hw(q, d_back, L, P)


def oppoint(c, ha, L, d_pump, d_back, P, n=3000):
    qmax = c['Q'][-1]
    qs = [qmax * i / n for i in range(n + 1)]
    d = [interp(q, c['Q'], c['H']) - hsys(q, ha, L, d_pump, d_back, P) for q in qs]
    if d[0] < 0:
        return None, '토출 불가(체절양정 < 실양정)'
    for i in range(1, len(qs)):
        if d[i] <= 0:
            f = d[i - 1] / (d[i - 1] - d[i])
            return qs[i - 1] + (qs[i] - qs[i - 1]) * f, ''
    return qs[-1], '런아웃'


def main():
    ap = PL.base_parser(__doc__, net=False)
    ap.add_argument('curves', help='곡선·펌프장 입력 JSON')
    ap.add_argument('--specs', help='기존 펌프 제원 파일(.json/.csv) — stations 수리값 보충')
    ap.add_argument('--inflow', help='p3 산출 pump_existing.json — 시나리오별 유입 대조')
    ap.add_argument('--step', type=float, default=0.01, help='곡선표 Q 간격 ㎥/min (기본 0.01)')
    args = ap.parse_args()
    cfg = PL.load_cfg(args, optional=bool(args.outdir))
    P = PL.params(cfg, args.set)
    J = json.loads(Path(args.curves).read_text(encoding='utf-8'))
    curves = {k: v for k, v in J['curves'].items() if not k.startswith('_')}
    specs = PL.load_specs(cfg, args.specs)
    out = PL.out_dir(cfg, args)

    rows, crow, stv = [], [], {}
    for st, s in J['stations'].items():
        if st.startswith('_'):
            continue
        sp = dict(specs.get(st, {}))
        sp.update({k: v for k, v in s.items() if v not in (None, '')})
        c = curves[sp['model']]
        miss = [k for k in ('bop', 'lwl', 'force_main_m') if sp.get(k) is None]
        if miss:
            print('  ! %s: %s 없음 — 건너뜀' % (st, '/'.join(miss)))
            continue
        bop, lwl, L = float(sp['bop']), float(sp['lwl']), float(sp['force_main_m'])
        hwl = float(sp['hwl']) if sp.get('hwl') is not None else lwl + P['effective_depth_m']
        d_back = float(sp.get('back_dia_mm') or P.get('existing_back_dia_mm') or 75)
        d_pump = float(sp.get('bore') or P['existing_bore_mm'])
        stv[st] = PL.well_volume(P, dict(sp, hwl=hwl, lwl=lwl))
        for lvl, wl in (('LWL', lwl), ('HWL', hwl)):
            ha = bop - wl
            q, note = oppoint(c, ha, L, d_pump, d_back, P)
            if q is None:
                H = kw = eff = None
            else:
                H, kw = interp(q, c['Q'], c['H']), interp(q, c['Q'], c['kW']) if c.get('kW') else None
                eff = interp(q, c['Q'], c['eff']) if c.get('eff') else None
            rated = c.get('rated') or [None, None]
            rows.append(dict(펌프장=st, 모델=sp['model'], 수위=lvl, 실양정=ha, 운전점Q=q, 운전점H=H, 축동력kW=kw,
                             효율=eff, 전동기kW=c.get('motor_kw'), 정격Q=rated[0], 정격H=rated[1], 체절양정=c['H'][0],
                             후단관경=d_back, 압송연장=L, 비고=note))
        q = 0.0
        while q <= c['Q'][-1] + 1e-9:
            crow.append(dict(펌프장=st, Q=round(q, 4), 펌프H=interp(q, c['Q'], c['H']),
                             시스템H_LWL=hsys(q, bop - lwl, L, d_pump, d_back, P),
                             시스템H_HWL=hsys(q, bop - hwl, L, d_pump, d_back, P)))
            q += args.step
    PL.dump_csv(out / 'oppoint.csv', rows)
    PL.dump_csv(out / 'oppoint_curves.csv', crow)
    print('운전점 — 후단배관·손실식은 design.pump.* (C=%g, π=%s)' % (P['hw_c'], P['pi']))
    for r in rows:
        print('  %-14s %-12s %s 실양정 %5.2f → Q=%s H=%s kW=%s  (정격 %s×%s, 체절 %.1f) %s'
              % (r['펌프장'], r['모델'], r['수위'], r['실양정'],
                 '%.4f' % r['운전점Q'] if r['운전점Q'] is not None else '-',
                 '%.2f' % r['운전점H'] if r['운전점H'] is not None else '-',
                 '%.2f' % r['축동력kW'] if r['축동력kW'] is not None else '-',
                 r['정격Q'], r['정격H'], r['체절양정'], r['비고']))

    if args.inflow:
        qop = {r['펌프장']: r['운전점Q'] for r in rows if r['수위'] == 'LWL'}
        qoh = {r['펌프장']: r['운전점Q'] for r in rows if r['수위'] == 'HWL'}
        sc = []
        for p in json.loads(Path(args.inflow).read_text(encoding='utf-8'))['stations']:
            st = p['펌프장']
            if st not in qop or qop[st] is None:
                continue
            qin, qo, V = p['Q_m3min'], qop[st], stv[st]
            run = V / (qo - qin) if qin < qo else None
            stop = V / qin if qin > 0 else None
            sc.append(dict(시나리오=p.get('시나리오'), 펌프장=st, 총유입_m3d=p['Q_m3d'], 총유입_m3min=qin,
                           운전점Q_LWL=qo, 운전점Q_HWL=qoh.get(st), 여유배=qo / qin if qin else None,
                           가동률_pct=qin / qo * 100, 운전_min=run, 정지_min=stop,
                           기동횟수_h=60 / (run + stop) if (run and stop) else None, 최소운전_min=V / qo,
                           최대기동_h=15 * qo / V, 판정='OK' if qin < qo else '용량부족(연속운전)'))
        PL.dump_csv(out / 'oppoint_scen.csv', sc)
        print('\n시나리오별 유입 vs 운전점(LWL):')
        for r in sc:
            print('  [%s] %-14s 유입 %.4f / 운전점 %.4f ㎥/min  가동률 %.1f%%  %s'
                  % (r['시나리오'], r['펌프장'], r['총유입_m3min'], r['운전점Q_LWL'], r['가동률_pct'], r['판정']))
    print('저장: %s' % (out / 'oppoint.csv'))


if __name__ == '__main__':
    main()
