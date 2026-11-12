# -*- coding: utf-8 -*-
"""P2 신설 맨홀펌프장 용량 산정 — 펌프용량·토출구경·전양정·축동력·표준전동기·흡수정 운전.

  HWL = 유입관저(최저) − hwl_below_inlet_m,  LWL = HWL − effective_depth_m,  저면 = LWL − dead_depth_m
  q   = MAX(ROUNDUP(시간최대 ㎥/min ÷ n_duty, q_roundup_digits), q_min_m3min)
  토출구경 = 146·√(q/v) 가 bore_velocity_mps 범위에 드는 최소 표준구경 (없으면 bore_min_mm 이상 최소)
  hf  = 손실항목표(fittings) — 펌프측은 토출구경, 토출구·편락관은 후단 매설배관(= 압송관경)
  hl  = Hazen-Williams(C=hw_c), 후단 매설배관 관경, 압송연장
  H   = ROUND(ha + hf + hl + head_margin_m, 0),  P = 0.163·q·H/η × power_margin → motor_table
  흡수정 V = π/4·well_dia²·유효수심,  최소운전 V/q,  최대기동 60/(4V/q),  시간최대 시 운전 V/(q−Q)·정지 V/Q
  직렬 순간유입 = 상류 펌프장 q 합 + (자기 시간최대 − 상류 펌프장 시간최대 합)
  압송관경 대안 = alt_force_main_dia_mm 각각의 hl·H·kW·유속

기존 펌프장은 산정하지 않는다(준공 스펙 고정 → p3_existing.py). 직렬 유입 계산에만 설치 q 를 쓴다.
산출: <out>/pump_new.json · pump_new.csv · pump_new_hf.csv (손실항목)
--write: layers.plan_pump 의 같은 펌프장 행에 q_m3min·head_m·power_kw·force_main_m·flow_m3d·in_invert·discharge_to
         (+ 매핑돼 있으면 hwl·lwl·static_head_m·force_main_dia_mm) 를 쓴다. 쓰기 전 백업.
"""
import pumplib as PL


def calc_new(s, P):
    r = dict(s)
    if r['유입관저고'] is None:
        r['판정'] = '유입관저고 없음 — --inlet 필요'
        return r
    r['HWL'] = round(r['유입관저고'] - P['hwl_below_inlet_m'], 2)
    r['LWL'] = round(r['HWL'] - P['effective_depth_m'], 2)
    r['저면고'] = round(r['LWL'] - P['dead_depth_m'], 2)
    q = PL.pump_q(r['Q_m3min'], P)
    r['펌프용량'] = r['운전q'] = q
    r['토출구경'] = PL.bore(q, P)
    d_back = r['압송관경']
    r['ha'] = r['압송최상단BOP'] - r['LWL']
    h = PL.head_calc(q, r['ha'], r['압송연장'], r['토출구경'], d_back, P)
    r.update(hf_items=h['hf_items'], hf=h['hf'], hl=h['hl'], H=h['H'], hv=h['hv'], 축동력=h['축동력'],
             동력=h['동력'], v_압송=h['v'])
    V = PL.well_volume(P)
    r['유효용량_m3'] = V
    r.update(PL.cycles(V, q, r['Q_m3min']))
    for dd in P['alt_force_main_dia_mm']:
        hl = PL.hl_hw(q, dd, r['압송연장'], P)
        H = PL.total_head(r['ha'] + r['hf'] + hl, P)
        p = PL.shaft_kw(q, H, P)
        r['alt_D%d' % dd] = dict(hl=hl, H=H, P=p, kW=PL.motor_kw(p, P), v=PL.vel(q, dd, P))
    j = []
    if not P['_vmin'] <= r['v_압송'] <= P['_vmax']:
        j.append('압송유속 %.2f m/s (%.1f~%.1f 밖)' % (r['v_압송'], P['_vmin'], P['_vmax']))
    if r['N_max'] > P['max_starts_per_h']:
        j.append('최대기동 %.1f 회/h > %g' % (r['N_max'], P['max_starts_per_h']))
    r['판정'] = ' / '.join(j) or 'OK'
    return r


def main():
    ap = PL.base_parser(__doc__)
    ap.add_argument('--write', action='store_true', help='plan_pump 레이어에 결과를 쓴다(백업 후)')
    args = ap.parse_args()
    cfg = PL.load_cfg(args, optional=bool((args.flo or args.net) and args.outdir))
    P = PL.params(cfg, args.set)
    st, recs, meta = PL.network_from_args(args, cfg, P)
    specs = PL.load_specs(cfg, args.specs)
    out = PL.out_dir(cfg, args)

    res = []
    for s in st:
        if s['구분'] == '신설':
            res.append(calc_new(s, P))
        else:                                  # 기존: 직렬 유입용 설치 q 만
            sp = specs.get(s['펌프장'], {})
            q = sp.get('q_m3min')
            res.append(dict(s, 운전q=float(q) if q else PL.pump_q(s['Q_m3min'], P),
                            운전q_근거='설치 제원' if q else '제원 없음 → 산정식 q'))
    by = {r['펌프장']: r for r in res}
    for r in res:
        if 'Q_m3min' not in r or r.get('운전q') is None:
            continue
        upq = sum(by[u].get('운전q') or 0 for u in r['상류펌프장'])
        upQ = sum(by[u]['Q_m3min'] for u in r['상류펌프장'])
        r['직렬유입_max'] = upq + (r['Q_m3min'] - upQ)
        r['직렬_여유'] = r['운전q'] / r['직렬유입_max'] if r['직렬유입_max'] > 0 else None
    new = [r for r in res if r['구분'] == '신설']

    hf_rows = [dict(펌프장=r['펌프장'], 항목=a, 규격_mm=b, K=c, 손실_m=d)
               for r in new for (a, b, c, d) in r.get('hf_items', [])]
    PL.dump_json(out / 'pump_new.json', dict(meta=meta, params={k: v for k, v in P.items()}, stations=new))
    cols = ['펌프장', 'Q_m3d', 'Q_m3min', '유입관저고', '유입관저고_가정', 'HWL', 'LWL', '저면고', '펌프용량', '토출구경',
            '압송관경', '압송연장', '압송최상단BOP', 'BOP위치', 'ha', 'hf', 'hl', 'hv', 'H', '축동력', '동력', 'v_압송',
            't_min', 'N_max', '운전_min', '정지_min', 'N_시간최대', '상류펌프장', '직렬유입_max', '직렬_여유', '토출대상', '판정']
    PL.dump_csv(out / 'pump_new.csv', new, cols)
    PL.dump_csv(out / 'pump_new_hf.csv', hf_rows)

    print('신설 %d개소 (기존 %d개소는 p3_existing.py) — η=%.2f 여유율 %.2f 여유양정 %.1f m C=%g'
          % (len(new), len(res) - len(new), P['efficiency'], P['power_margin'], P['head_margin_m'], P['hw_c']))
    for r in new:
        if 'H' not in r:
            print('  %-16s %s' % (r['펌프장'], r['판정']))
            continue
        alt = '  '.join('D%d:%dm/%gkW' % (d, r['alt_D%d' % d]['H'], r['alt_D%d' % d]['kW'])
                        for d in P['alt_force_main_dia_mm'])
        print('  %-16s Q=%.4f q=%.2f %dA  L=%6.1f D%d  ha=%.2f hf=%.3f hl=%.3f → H=%d m  P=%.3f → %g kW  '
              'v=%.2f  N=%.1f/h  직렬=%.3f  [%s]  %s'
              % (r['펌프장'], r['Q_m3min'], r['펌프용량'], r['토출구경'], r['압송연장'], r['압송관경'], r['ha'], r['hf'],
                 r['hl'], r['H'], r['축동력'], r['동력'], r['v_압송'], r['N_max'], r['직렬유입_max'], alt, r['판정']))
    if any(r.get('유입관저고_가정') for r in new):
        print('\n※ 유입관저고 가정: ' + '; '.join('%s %s' % (r['펌프장'], r['유입관저고_가정']) for r in new
                                            if r.get('유입관저고_가정')))
    if any((r.get('펌프용량') or 0) >= 0.3 for r in new):
        print('※ 전효율 %.0f %% 는 0.1~0.2 ㎥/min 급 관행값 — q ≥ 0.3 펌프는 제작사 곡선(p4_oppoint)으로 동력 재확인'
              % (P['efficiency'] * 100))
    print('저장: %s' % (out / 'pump_new.json'))

    if args.write:
        if cfg is None or not cfg.has_layer('plan_pump'):
            raise SystemExit('--write: layers.plan_pump 가 없다.')
        F = cfg.fields('plan_pump')
        up = {}
        for r in new:
            if 'H' not in r:
                continue
            up[r['펌프장']] = dict(q_m3min=r['펌프용량'], head_m=r['H'], power_kw=r['동력'], force_main_m=r['압송연장'],
                                  flow_m3d=round(r['Q_m3d'], 3), in_invert=r['유입관저고'], discharge_to=r['토출대상'],
                                  hwl=r['HWL'], lwl=r['LWL'], static_head_m=round(r['ha'], 2),
                                  force_main_dia_mm=r['압송관경'])
        n, miss, skip = PL.write_layer(cfg, 'plan_pump', 'pump_id', up, F)
        print('plan_pump 갱신 %d행 · 레이어에 없는 펌프장 %d %s · 안 쓴 논리필드(매핑·열 없음) %s'
              % (n, len(miss), miss[:10], skip))


if __name__ == '__main__':
    main()
