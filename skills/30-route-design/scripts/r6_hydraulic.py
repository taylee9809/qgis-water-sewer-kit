# -*- coding: utf-8 -*-
"""r6 수리계산(자연유하 Manning 부분만관 · 판정)  →  design.json

    python r6_hydraulic.py                 # load.json(맨홀 자체유량) → 위상순 누가
    python r6_hydraulic.py --flow field    # 관로 레이어의 누가유량(fields.plan_pipe.flow_m3d)을 그대로
    python r6_hydraulic.py --flow none     # 유량 0 (종단 형상만 볼 때)

누가유량: 자연유하 구간 + 압송 체인(펌프장 → 토출 맨홀)을 한 망으로 위상순 누가.
          맨홀 자체유량은 그 맨홀에서 나가는 관에 얹는다. 말단(유출관 없음)에 배정된 유량은 보고만.
수리: n = design.manning_n, 원형관 부분만관(이분법). 판정 = 실유속 < min_velocity_mps → LESS,
      > max_velocity_mps → OVER, 그 밖 GOOD. 유량이 아주 작으면 0.6 m/s 는 경사·관경으로 못 만든다
      (SKILL.md 6절 — KDS 18.6 대책 또는 '최소경사 + 유속 미달 사유 명기').
산출 design.json = {res: 관번호별 종단·수리, IL, ARR, GL, OBS, force, MH, node, load} — r7·r8 이 읽는다.
"""
import sys, os, math, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 수리계산(design.json)'))
    ap.add_argument('--flow', choices=('load', 'field', 'none'), default=None,
                    help='유량 출처(기본: load.json 있으면 load, 없으면 field)')
    a = ap.parse_args()
    C = R.Ctx(a)
    P = C.P
    net, gnd, pro = C.load('net.json'), C.load('ground.json'), C.load('profile.json')
    seg, force, GL = net['seg'], net['force'], gnd['GL']
    IL, ARR, OBS, cover = pro['IL'], pro['ARR'], pro['OBS'], pro['cover']
    N_MAN = P['manning_n']
    flow = a.flow
    ld = C.load('load.json', required=False)
    if flow is None:
        flow = 'load' if ld else 'field'

    # ── 누가유량 (L/s)
    Q = {}
    cumF = {}
    if flow == 'field':
        miss = [p for p, s in seg.items() if '입력누가_m3d' not in s]
        Q = {p: s['입력누가_m3d'] / 86.4 for p, s in seg.items() if '입력누가_m3d' in s}
        print('유량: 관로 레이어 누가유량 %d/%d구간%s' % (len(Q), len(seg), (' (없음 %d → 0)' % len(miss)) if miss else ''))
    elif flow == 'load':
        if not ld:
            raise SystemExit('load.json 이 없다 — r4_load.py 를 먼저 돌리거나 --flow field')
        load = ld['load']
        recs = [(pid, s['상류맨홀'], s['하류맨홀']) for pid, s in seg.items()] + \
               [('F:' + nm, nm, c['하류맨홀']) for nm, c in force.items()]
        nodes = {x for _, u, d in recs for x in (u, d)}
        order = R.topo_order(nodes, [(u, d) for _, u, d in recs])
        down = collections.defaultdict(list)
        for i, (_, u, d) in enumerate(recs):
            down[u].append(i)
        inflow = collections.defaultdict(float)
        cum = [0.0] * len(recs)
        for n in order:
            t = inflow[n]
            for i in down[n]:
                cum[i] = t + load.get(n, 0.0) / 86400.0
                inflow[recs[i][2]] += cum[i]
        for (pid, u, d), c in zip(recs, cum):
            if pid.startswith('F:'):
                cumF[pid[2:]] = c * 1000
            else:
                Q[pid] = c * 1000
        ups = {u for _, u, _ in recs}
        lost = {n: v for n, v in load.items() if n in nodes and n not in ups and v}
        outside = {n: v for n, v in load.items() if n not in nodes and v}
        print('유량: 자체유량 %.2f ㎥/일 → 누가 최대 %.4f L/s' % (sum(load.values()), max(Q.values() or [0])))
        for n in sorted(nodes - ups):
            print('  말단 %-16s 도달 %.2f ㎥/일' % (n, inflow[n] * 86400))
        if lost:
            print('  ! 유출관 없는 맨홀에 배정된 유량 %.2f ㎥/일: %s' % (sum(lost.values()), list(lost)[:6]))
        if outside:
            print('  ! 망 밖 맨홀 유량 %.2f ㎥/일 (기존망) — 이번 누가에서 빠짐' % sum(outside.values()))

    # ── 구간 수리
    res = {}
    for pid, s in seg.items():
        u, dn = s['상류맨홀'], s['하류맨홀']
        D = s['관경'] / 1000.0
        ilu, ild, L = IL[u], ARR[pid], s['연장']
        S = (ilu - ild) / L * 1000.0
        glu, gld, qq = GL.get(u), GL.get(dn), Q.get(pid, 0.0)
        Vf, Qf, v, h, hr, pct = R.manning_partial(s['관경'], S, qq, N_MAN)
        m = pro['mid'].get(pid, {})
        res[pid] = dict(관번호=pid, 노선=s['노선'], 상류맨홀=u, 하류맨홀=dn, 관경=s['관경'], 연장=L,
                        상류관저고=ilu, 하류관저고=ild, 하류단차=round(ild - IL[dn], 3), 경사=round(S, 3),
                        상류지반고=glu, 하류지반고=gld,
                        상류토피=round(glu - ilu - D, 3) if glu else None,
                        하류토피=round(gld - ild - D, 3) if gld else None,
                        토피기준=cover[pid], 지장물=len(OBS.get(pid, [])),
                        누가유량_Lps=round(qq, 4), 만관유속=round(Vf, 4), 만관유량_Lps=round(Qf, 4),
                        실유속_mps=round(v, 4), 수심_m=round(h, 4), 수심비=round(hr, 4), 유량비율_pct=round(pct, 4),
                        수리판정=R.judge_velocity(v, P),
                        중간최소토피=m.get('중간최소토피'), 보호공절점=len(m.get('보호공') or []))

    # ── 압송 체인 유속(유입 누가 기준 — 펌프 토출량은 r7 에서)
    fres = {}
    for nm, c in force.items():
        q = cumF.get(nm, sum(r['누가유량_Lps'] for r in res.values() if r['하류맨홀'] == nm))
        A = math.pi * (c['관경'] / 1000.0) ** 2 / 4
        fres[nm] = dict(펌프장=nm, 토출맨홀=c['하류맨홀'], 관경=c['관경'], 연장=c['L'], 유입누가_Lps=round(q, 4),
                        유입관저고=IL.get(nm), 유입기준유속_mps=round(q / 1000.0 / A, 4) if A else None)

    jc = collections.Counter(r['수리판정'] for r in res.values())
    print('=== 수리 === 판정: %s' % dict(jc))
    if res:
        print(' 총연장 %.1fm · 누가유량 최대 %.3f L/s · 유속 %s 초과 %d'
              % (sum(r['연장'] for r in res.values()), max(r['누가유량_Lps'] for r in res.values()),
                 P['max_velocity_mps'], sum(1 for r in res.values() if r['실유속_mps'] > P['max_velocity_mps'])))
    for nm, f in fres.items():
        print(' 펌프장 %-14s 유입관저 %s · 유입 %.3f L/s · 압송 D%d %.1f m → %s'
              % (nm, f['유입관저고'], f['유입누가_Lps'], f['관경'], f['연장'], f['토출맨홀']))

    out = dict(meta=dict(created=R.stamp(), step='r6_hydraulic', flow=flow, manning_n=N_MAN,
                         params=R.params_table(P, C.src, ['manning_n', 'min_velocity_mps', 'max_velocity_mps'])),
               res=res, IL=IL, ARR=ARR, GL=GL, OBS=OBS, force=force, force_res=fres, MH=net['MH'],
               node=net['node'], load=(ld or {}).get('load', {}))
    print('→ %s' % C.save('design.json', out))


if __name__ == '__main__':
    main()
