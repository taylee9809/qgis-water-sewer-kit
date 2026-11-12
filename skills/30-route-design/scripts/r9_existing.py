# -*- coding: utf-8 -*-
"""r9 기존관 통수능 — 신설 유량이 실제로 지나가는 기존관  →  existing.json + 기존관_통수능.csv

    python r9_existing.py --where 지구=OO --where 지구=OO(추가)     # 준공 관로 거르기(같은 필드는 OR)
    python r9_existing.py --all                                     # 접속 하류만이 아니라 거른 기존관 전부

입력  layers.asbuilt_pipe — fields pipe_id · us_mh · ds_mh · dia_mm · us_invert · ds_invert · length_m · flow_kind · status
      (status == values.status_abandon 인 기존폐쇄 관은 뺀다)
망    신설 자연유하 + 압송 체인(net.json) + 기존관을 한 망으로 잇는다. 접속점 = 신설 말단·토출 맨홀 중 기존관의 맨홀.
대상  접속점에서 하류로 따라간 기존관(신설 유량이 지나가는 관만 — 원 '기존관 검토 대상' 방식). --all 이면 전부.
유량  load.json 맨홀 자체유량(기존망 맨홀 배정분 포함)을 위상순 누가.
판정  자연유하: Manning 부분만관(n = design.manning_n) → 유량비율(만관 대비 %) · 수심비 · 유속.
      압송: 유속 = Q/A (기존 압송 유속 0.6 미달은 용량이 아니라 퇴적·황화수소 대책 문제 — SKILL.md 7절).
      역경사(관저 상류 ≤ 하류)는 판정 '역경사'.
"""
import sys, os, io, csv, math, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 기존관 통수능(existing.json)'))
    ap.add_argument('--where', action='append', default=[], metavar='필드=값', help='준공 관로 거르기(같은 필드는 OR)')
    ap.add_argument('--all', action='store_true', help='접속 하류만이 아니라 거른 기존관 전부')
    a = ap.parse_args()
    C = R.Ctx(a)
    cfg, P = C.cfg, C.P
    net = C.load('net.json')
    ld = C.load('load.json', required=False)
    if not ld:
        print('load.json 없음 — 기존관 통수능은 유량 배분(r4) 뒤에 돈다. 건너뜀')
        return
    load = ld['load']
    ap_ = R.read_layer(cfg, 'asbuilt_pipe')
    flt = collections.defaultdict(list)
    for w in a.where:
        k, _, v = w.partition('=')
        flt[k].append(v)
    for k, vs in flt.items():
        ap_ = ap_[ap_[k].astype(str).isin(vs)]
    f = lambda lg: R.fcol(cfg, 'asbuilt_pipe', lg, ap_)
    c_id, c_us, c_ds, c_d, c_iu, c_id2, c_L, c_k, c_st = (f('pipe_id'), f('us_mh'), f('ds_mh'), f('dia_mm'), f('us_invert'),
                                                          f('ds_invert'), f('length_m'), f('flow_kind'), f('status'))
    if not (c_us and c_ds):
        raise SystemExit('fields.asbuilt_pipe.us_mh / ds_mh 가 필요하다')
    ABN = cfg.value('status_abandon', '기존폐쇄')
    K_P = cfg.value('kind_pressure', '압송')
    old = []
    for _, r in ap_.iterrows():
        u, d = R.val(r, c_us), R.val(r, c_ds)
        if not u or not d or (c_st and R.val(r, c_st) == ABN):
            continue
        L = R.val(r, c_L)
        L = float(L) if L is not None else (r.geometry.length if r.geometry is not None else None)
        dia = R.val(r, c_d)
        kind = R.val(r, c_k)
        if kind is None and dia is not None:
            kind = K_P if float(dia) <= P['pressure_max_d_mm'] else cfg.value('kind_gravity', '자연유하')
        old.append(dict(pid=str(R.val(r, c_id) or u), up=str(u), dn=str(d), L=L, D=float(dia) if dia else None,
                        ILu=R.val(r, c_iu), ILd=R.val(r, c_id2), kind=kind))
    print('기존관 %d구간 %.1f m (압송 %d)' % (len(old), sum(o['L'] or 0 for o in old), sum(o['kind'] == K_P for o in old)))

    recs = [dict(src='신설', up=s['상류맨홀'], dn=s['하류맨홀']) for s in net['seg'].values()] + \
           [dict(src='신설', up=nm, dn=c['하류맨홀']) for nm, c in net['force'].items()] + \
           [dict(src='기존', **o) for o in old]
    nodes = {x for r in recs for x in (r['up'], r['dn'])}
    order = R.topo_order(nodes, [(r['up'], r['dn']) for r in recs])
    down = collections.defaultdict(list)
    for i, r in enumerate(recs):
        down[r['up']].append(i)
    inflow = collections.defaultdict(float)
    cum = [0.0] * len(recs)
    for n in order:
        t = inflow[n]
        for i in down[n]:
            cum[i] = t + load.get(n, 0.0) / 86400.0
            inflow[recs[i]['dn']] += cum[i]
    old_up = {r['up'] for r in recs if r['src'] == '기존'}
    joins = sorted({(r['up'], r['dn']) for r in recs if r['src'] == '신설' and r['dn'] in old_up})
    print('접속점 %d: %s' % (len(joins), ', '.join('%s→%s' % j for j in joins)))
    outside = {k: v for k, v in load.items() if k not in nodes and v}
    if outside:
        print('  ! 망 밖 맨홀 유량 %.2f ㎥/일 (%d개소) — 누가에서 빠짐: %s'
              % (sum(outside.values()), len(outside), list(outside)[:6]))

    target = collections.OrderedDict()
    oldmap = {r['up']: i for i, r in enumerate(recs) if r['src'] == '기존'}
    if a.all:
        for i, r in enumerate(recs):
            if r['src'] == '기존':
                target[i] = True
    else:
        for _, d in joins:
            cur, seen = d, set()
            while cur in oldmap and cur not in seen:
                seen.add(cur)
                target[oldmap[cur]] = True
                cur = recs[oldmap[cur]]['dn']
    rows = []
    N_ = P['manning_n']
    for i in target:
        r = recs[i]
        q = cum[i]
        D = (r['D'] or 200) / 1000.0
        if r['kind'] == K_P:
            V = q / (math.pi * D * D / 4)
            row = dict(경사=None, 만관유량_Lps=None, 실유속_mps=round(V, 3), 수심_m=None, 유량비율_pct=None, 수리판정='압송')
        elif r['ILu'] is None or r['ILd'] is None or not r['L']:
            row = dict(경사=None, 만관유량_Lps=None, 실유속_mps=None, 수심_m=None, 유량비율_pct=None, 수리판정='관저없음')
        else:
            S = (float(r['ILu']) - float(r['ILd'])) / r['L'] * 1000
            if S <= 0:
                row = dict(경사=round(S, 2), 만관유량_Lps=0, 실유속_mps=0, 수심_m=0, 유량비율_pct=0, 수리판정='역경사')
            else:
                Vf, Qf, v, h, hr, pct = R.manning_partial(r['D'] or 200, S, q * 1000, N_)
                pct = 100 * q * 1000 / Qf if Qf else 0
                row = dict(경사=round(S, 2), 만관유량_Lps=round(Qf, 2), 실유속_mps=round(v, 3), 수심_m=round(h, 4),
                           유량비율_pct=round(pct, 2), 수리판정=R.judge_velocity(v, P) if pct <= 100 else '만관초과')
        rows.append(dict(관번호=r['pid'], 상류맨홀=r['up'], 하류맨홀=r['dn'], 구분=r['kind'], 관경=r['D'], 연장=r['L'],
                         상류관저고=r['ILu'], 하류관저고=r['ILd'], 자체유량_m3d=round(load.get(r['up'], 0.0), 3),
                         누가유량_m3d=round(q * 86400, 3), 누가유량_Lps=round(q * 1000, 4), **row))
    jc = collections.Counter(r['수리판정'] for r in rows)
    pc = [r['유량비율_pct'] for r in rows if r['유량비율_pct'] is not None]
    print('검토 대상 기존관 %d구간 (%s) · 판정 %s · 유량비율 최대 %.2f %% · 만관초과 %d'
          % (len(rows), '전부' if a.all else '접속 하류', dict(jc), max(pc or [0]), jc.get('만관초과', 0)))
    for t in sorted(nodes - {r['up'] for r in recs}):
        print('  말단 %-16s 도달 %.2f ㎥/일' % (t, inflow[t] * 86400))
    with io.open(C.path('기존관_통수능.csv'), 'w', encoding='utf-8-sig', newline='') as fo:
        if rows:
            w = csv.DictWriter(fo, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    out = dict(meta=dict(created=R.stamp(), step='r9_existing', where=a.where, all=a.all),
               joins=joins, rows=rows, terminal={t: round(inflow[t] * 86400, 3) for t in nodes - {r['up'] for r in recs}})
    print('→ %s' % C.save('existing.json', out))


if __name__ == '__main__':
    main()
