# -*- coding: utf-8 -*-
"""SWMM 교차검증 — r8 --inp 가 쓴 route_design.inp 를 EPA SWMM(pyswmm)으로 돌려 자체 Manning(design.json)과 대조.

    python swmm_check.py                 # <run>/route_design.inp
    python swmm_check.py --inp <다른.inp>

비교: 관별 최대 수심비 d/D · 맨홀 월류. SWMM 은 동적파, 자체 계산은 등류 Manning 이라 완전일치는 기대하지 않는다.
두 엔진이 크게 다르면 어느 가정이 문제인지 드러난다. makesw .OUT 의 압송 구간 수치는 등류로 역구배를 푼 값이라
믿을 수 없다 — 압송은 SWMM 이나 r7 로 따로 본다.
필요: pip install pyswmm. SWMM 엔진은 한글 경로를 못 연다(ERROR 303) → ASCII 임시 폴더로 복사해 돌린다.
"""
import sys, os, shutil, tempfile, statistics, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R


def main():
    ap = R.common_args(argparse.ArgumentParser(description='SWMM 교차검증'))
    ap.add_argument('--inp', default=None)
    ap.add_argument('--every', type=int, default=20, help='표본 간격(스텝, 기본 20)')
    a = ap.parse_args()
    C = R.Ctx(a)
    des = C.load('design.json')
    inp = a.inp or str(C.path('route_design.inp'))
    if not os.path.exists(inp):
        raise SystemExit('.inp 없음 — r8_export.py --inp 먼저: %s' % inp)
    try:
        from pyswmm import Simulation, Links, Nodes
    except ImportError:
        raise SystemExit('pyswmm 이 없다 — pip install pyswmm')
    work = os.path.join(tempfile.gettempdir(), 'swmm_route_check')
    os.makedirs(work, exist_ok=True)
    run = os.path.join(work, 'model.inp')
    shutil.copyfile(inp, run)
    dmax, flood = collections.defaultdict(float), collections.defaultdict(float)
    with Simulation(run) as sim:
        links, nodes = Links(sim), Nodes(sim)
        n = 0
        for _ in sim:
            n += 1
            if n % a.every:
                continue
            for lk in links:
                dmax[lk.linkid] = max(dmax[lk.linkid], lk.depth)
            for nd in nodes:
                flood[nd.nodeid] = max(flood[nd.nodeid], nd.flooding)
        err = sim.flow_routing_error
    rows = []
    for k, r in des['res'].items():
        if k in dmax:
            rows.append((k, float(r['수심비']), dmax[k] / (float(r['관경']) / 1000.0)))
    print('SWMM 유출 연속오차 %.3f%% · 스텝 %d · 대조 관로 %d' % (err, n, len(rows)))
    if rows:
        my, sw = [r[1] for r in rows], [r[2] for r in rows]
        print('  수심비 d/D  자체 Manning 중앙 %.4f 최대 %.4f' % (statistics.median(my), max(my)))
        print('              SWMM 동적파   중앙 %.4f 최대 %.4f' % (statistics.median(sw), max(sw)))
        over = [r[0] for r in rows if r[2] > 1.0]
        print('  SWMM 만관초과 %d건 %s' % (len(over), over[:6]))
    fl = {k: v for k, v in flood.items() if v > 1e-9}
    print('  월류 맨홀 %d개 %s' % (len(fl), list(fl)[:6]))
    C.save('swmm_check.json', dict(error_pct=err, rows=rows, flood=fl))


if __name__ == '__main__':
    main()
