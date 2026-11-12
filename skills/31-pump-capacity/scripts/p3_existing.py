# -*- coding: utf-8 -*-
"""P3 기존(준공) 맨홀펌프장·관로 용량검토 — 준공 스펙 고정, 유량만 금회 망으로 바꿔 본다.

제원(앞의 것을 뒤가 덮어씀): layers.asbuilt_pump (fields.asbuilt_pump.*) → --specs 파일(.json/.csv)
  q_m3min·head_m·power_kw(설치 명판)  ·  lwl·hwl·bop·force_main_m·back_dia_mm·bore·well_volume_m3(또는 well_dia_m)
  bop·force_main_m 이 없으면 망 추적값, lwl 이 없으면 유입관저 규칙, back_dia_mm 이 없으면
  design.pump.existing_back_dia_mm → 망 압송관경 순.

판정 (시나리오마다)
  용량   소요 q = MAX(ROUNDUP(Q/가동대수,2), q_min) ≤ 설치 q          · 사용률 = Q / 설치 q
  양정   ha + hf + hl (q = MAX(소요, 설치)) ≤ 설치양정  (existing_head_check = margin 이면 ROUND(+여유) 로)
  동력   0.163·q·H/η×여유율 → 표준전동기 ≤ 설치동력
  압송   설치 q 의 압송유속이 design.min/max_velocity_mps 안 — 0.6 미달은 용량이 아니라 퇴적·황화수소 문제
  기동   최대기동 60/(4V/q) ≤ max_starts_per_h,  시간최대 유입 때 운전/정지/기동횟수
  저류(참고) 1시간 유입 vs V + q·60 — 1차 판정은 언제나 「설치 q ≥ 시간최대」(KDS 61 45 00 표 1.1.1)

시나리오: --scenario 라벨=배율 (망 유량 전체에 곱함) 또는 라벨=network.json (p1 산출, 예: 당초 망).
         없으면 「금회」 하나.
관로: --pipes 정규식 → 그 이름(상류 노드)의 자연유하관 통수능(Manning 부분만관, 사용률·실유속·수심비).
산출: <out>/pump_existing.json · pump_existing.csv · (pipes_existing.csv)
"""
import json
import re
from pathlib import Path

import pumplib as PL


def scen_recs(base_recs, base_st, args):
    """[(라벨, stations, recs)] — 배율이면 펌프장 Q 와 관 유량을 그대로 곱하고, 파일이면 그 망을 쓴다."""
    if not args.scenario:
        return [('금회', base_st, base_recs)]
    out = []
    for s in args.scenario:
        lab, _, v = s.partition('=')
        try:
            k = float(v)
        except ValueError:
            J = json.loads(Path(v).read_text(encoding='utf-8'))
            out.append((lab.strip(), J['stations'], J.get('recs', [])))
            continue
        recs = [dict(r, Q=(r['Q'] or 0) * k, cum=None if r.get('cum') is None else r['cum'] * k) for r in base_recs]
        st = [dict(x, Q_m3s=x['Q_m3s'] * k, Q_m3d=x['Q_m3d'] * k, Q_m3min=x['Q_m3min'] * k) for x in base_st]
        out.append((lab.strip(), st, recs))
    return out


def calc_existing(s, sp, P, upq=None):
    n_duty = int(sp.get('n_duty') or P['n_duty'] or 1)
    q_inst = sp.get('q_m3min')
    r = dict(펌프장=s['펌프장'], 지구=sp.get('district'), Q_m3d=s['Q_m3d'], Q_m3min=s['Q_m3min'],
             설치용량=q_inst, 설치양정=sp.get('head_m'), 설치동력=sp.get('power_kw'), 가동대수=n_duty,
             제원출처=sp.get('_src', '없음'), 상류펌프장=s.get('상류펌프장', []), 토출대상=s.get('토출대상'))
    if not q_inst:
        r['판정'] = '설치 제원(q_m3min) 없음 — asbuilt_pump 또는 --specs 보완'
        return r
    q_inst = float(q_inst)
    Qm = s['Q_m3min']
    q_req = PL.pump_q(Qm, P, n_duty)
    q_use = max(q_req, q_inst)
    lwl = sp.get('lwl')
    lwl_src = '준공'
    if lwl is None and s.get('유입관저고') is not None:
        lwl = round(s['유입관저고'] - P['hwl_below_inlet_m'] - P['effective_depth_m'], 2)
        lwl_src = '유입관저 규칙'
    bop = sp.get('bop', s.get('압송최상단BOP'))
    L = sp.get('force_main_m', s.get('압송연장'))
    back = sp.get('back_dia_mm') or P.get('existing_back_dia_mm') or s.get('압송관경')
    d_pump = sp.get('bore') or P['existing_bore_mm']
    r.update(소요용량=q_req, 여유배=q_inst * n_duty / Qm if Qm else None,
             사용률_pct=Qm / (q_inst * n_duty) * 100, LWL=lwl, LWL근거=lwl_src, HWL=sp.get('hwl'),
             BOP=bop, BOP근거='준공' if 'bop' in sp else '망 추적', 압송연장=L,
             압송연장근거='준공' if 'force_main_m' in sp else '망 추적', 후단관경=back, 토출구경=d_pump)
    if lwl is None or bop is None or L is None or not back:
        r['판정'] = '양정 검토 불가 — LWL·BOP·압송연장·관경 중 결측'
        return r
    ha = float(bop) - float(lwl)
    h = PL.head_calc(q_use, ha, float(L), float(d_pump), float(back), P)
    r.update(실양정=ha, hf=h['hf'], hl=h['hl'], 소요양정=h['base'], 소요전양정=h['H'], hv=h['hv'],
             축동력=h['축동력'], 소요동력=h['동력'], hf_items=h['hf_items'])
    v = PL.vel(q_inst, float(back), P)
    r['압송유속'] = v
    V = PL.well_volume(P, sp)
    r['유효용량_m3'] = V
    r.update(PL.cycles(V, q_inst, Qm / n_duty))
    r['시간유입_m3h'] = Qm * 60
    r['허용유입_m3h'] = V + q_inst * n_duty * 60
    r['저류여유_m3h'] = r['허용유입_m3h'] - r['시간유입_m3h']
    Hchk = r['소요전양정'] if P.get('existing_head_check') == 'margin' else r['소요양정']
    H_inst = float(sp['head_m']) if sp.get('head_m') is not None else None
    kw_inst = float(sp['power_kw']) if sp.get('power_kw') is not None else None
    j = ['용량부족' if q_req > q_inst else '용량 OK',
         ('양정부족' if Hchk > H_inst else '양정 OK') if H_inst is not None else '양정 ?',
         ('동력부족' if r['소요동력'] > kw_inst else '동력 OK') if kw_inst is not None else '동력 ?',
         '기동과다' if r['N_max'] > P['max_starts_per_h'] else '기동 OK']
    if v > P['_vmax']:
        j.append('압송유속>%.1f' % P['_vmax'])
    elif v < P['_vmin']:
        j.append('압송유속<%.1f(퇴적·황화수소 대책)' % P['_vmin'])
    if Qm >= q_inst * n_duty:
        j.append('연속운전')
    r['판정'] = ' / '.join(j)
    return r


def pipes_check(recs, rx, P, lab):
    acc = PL.accumulate(recs)
    out = []
    for r in recs:
        if r['D'] <= P['_dmax'] or not re.search(rx, r['up']):
            continue
        if r['ilu'] is None or r['ild'] is None or not r['L']:
            continue
        S = (r['ilu'] - r['ild']) / r['L']
        q = (r['cum'] if r.get('cum') is not None else acc.get(r['up'], 0.0)) * 1000
        Vf, Qf, v, h, hr, pct = PL.manning_partial(r['D'], S, q, P['_manning_n'])
        jd = ('만관초과' if pct >= 100 else 'OVER%.1f' % P['_vmax'] if v > P['_vmax']
              else 'GOOD' if v >= P['_vmin'] else 'LESS%.1f' % P['_vmin'])
        out.append(dict(시나리오=lab, 상류=r['up'], 하류=r['dn'], 관경=r['D'], 연장=r['L'], 경사_permil=S * 1000,
                        누가유량_m3d=q / 1000 * 86400, 누가유량_Lps=q, 만관유속=Vf, 만관유량_Lps=Qf, 실유속=v,
                        수심_m=h, 수심비=hr, 사용률_pct=pct, 판정=jd))
    return out


def main():
    ap = PL.base_parser(__doc__)
    ap.add_argument('--scenario', action='append', default=[], metavar='라벨=배율|network.json')
    ap.add_argument('--pipes', metavar='정규식', help='통수능을 볼 자연유하관(상류 노드명) 정규식, 예 "^XX-"')
    ap.add_argument('--new-json', help='p2 산출 pump_new.json — 상류 신설 펌프의 q 를 직렬 유입에 쓴다')
    args = ap.parse_args()
    cfg = PL.load_cfg(args, optional=bool((args.flo or args.net) and args.outdir))
    P = PL.params(cfg, args.set)
    st, recs, meta = PL.network_from_args(args, cfg, P)
    specs = PL.load_specs(cfg, args.specs)
    out = PL.out_dir(cfg, args)
    newq = {}
    if args.new_json:
        for r in json.loads(Path(args.new_json).read_text(encoding='utf-8'))['stations']:
            if r.get('펌프용량'):
                newq[r['펌프장']] = r['펌프용량']

    rows, prow = [], []
    for lab, sst, srecs in scen_recs(recs, st, args):
        by = {s['펌프장']: s for s in sst}
        res = []
        for s in sst:
            if s['구분'] != '기존':
                continue
            r = calc_existing(s, specs.get(s['펌프장'], {}), P)
            r['시나리오'] = lab
            res.append(r)
        for r in res:
            ups = r['상류펌프장']
            qq = []
            for u in ups:
                sp = specs.get(u, {})
                qq.append(float(sp['q_m3min']) if sp.get('q_m3min') else newq.get(u, PL.pump_q(by[u]['Q_m3min'], P)))
            if r.get('설치용량'):
                r['직렬유입_max'] = sum(qq) + (r['Q_m3min'] - sum(by[u]['Q_m3min'] for u in ups))
                r['직렬_여유'] = r['설치용량'] / r['직렬유입_max'] if r['직렬유입_max'] > 0 else None
        rows += res
        if args.pipes:
            prow += pipes_check(srecs, args.pipes, P, lab)

    PL.dump_json(out / 'pump_existing.json', dict(meta=meta, params=P, stations=rows))
    cols = ['시나리오', '펌프장', '지구', 'Q_m3d', 'Q_m3min', '설치용량', '설치양정', '설치동력', '가동대수', '소요용량',
            '여유배', '사용률_pct', 'LWL', 'LWL근거', 'BOP', 'BOP근거', '압송연장', '압송연장근거', '후단관경', '토출구경',
            '실양정', 'hf', 'hl', '소요양정', '소요전양정', '축동력', '소요동력', '압송유속', '유효용량_m3', 't_min',
            'N_max', '운전_min', '정지_min', 'N_시간최대', '시간유입_m3h', '허용유입_m3h', '저류여유_m3h',
            '상류펌프장', '직렬유입_max', '직렬_여유', '제원출처', '판정']
    PL.dump_csv(out / 'pump_existing.csv', rows, cols)
    if prow:
        PL.dump_csv(out / 'pipes_existing.csv', prow)

    print('기존 펌프장 검토 %d행 (시나리오 %s) — 양정판정 %s, 기동 허용 %g 회/h'
          % (len(rows), sorted({r['시나리오'] for r in rows}), P['existing_head_check'], P['max_starts_per_h']))
    for r in rows:
        if '실양정' not in r:
            print('  [%s] %-16s %s' % (r['시나리오'], r['펌프장'], r.get('판정')))
            continue
        print('  [%s] %-16s Q=%.4f 설치 %.2f×%gmH×%gkW  여유 %.2f배(사용률 %.1f%%)  ha=%.2f hf=%.3f hl=%.3f '
              '소요 %.2f m(여유포함 %d)  v=%.2f  N=%.1f/h  저류여유 %.2f ㎥/h  → %s'
              % (r['시나리오'], r['펌프장'], r['Q_m3min'], r['설치용량'], r['설치양정'] or 0, r['설치동력'] or 0,
                 r['여유배'] or 0, r['사용률_pct'], r['실양정'], r['hf'], r['hl'], r['소요양정'], r['소요전양정'],
                 r['압송유속'], r['N_max'], r['저류여유_m3h'], r['판정']))
    if prow:
        top = sorted(prow, key=lambda x: -x['사용률_pct'])[:8]
        print('\n자연유하관 %d구간 — 사용률 상위:' % len(prow))
        for p in top:
            print('  [%s] %s→%s D%d %.2f‰ q=%.3f L/s  만관 %.1f L/s  사용률 %.2f%%  v=%.2f  %s'
                  % (p['시나리오'], p['상류'], p['하류'], p['관경'], p['경사_permil'], p['누가유량_Lps'],
                     p['만관유량_Lps'], p['사용률_pct'], p['실유속'], p['판정']))
    print('저장: %s' % (out / 'pump_existing.json'))


if __name__ == '__main__':
    main()
