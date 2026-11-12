# -*- coding: utf-8 -*-
"""r4 유량 배분  →  load.json (맨홀별 자체유량 ㎥/일) + 가구배정.csv

    python r4_load.py                          # 가구 → 맨홀 배정(기본)
    python r4_load.py --mode assigned          # house 레이어의 배정맨홀 필드를 그대로 합산
    python r4_load.py --mode uniform --q 113   # 가구 자료가 없을 때: 분구 Q(㎥/일)를 연장 비례로
    python r4_load.py --with-asbuilt           # 준공 맨홀도 배정 후보(기존망 쪽 가구를 신설망에 잘못 싣지 않게)

assign 규칙(SKILL.md 5절)
  - 기존 가구(new_or_old == values.house_existing, 기본 '기존')는 접속맨홀(fields.house.connect_mh)이 망에 있으면 그것.
    기하 최근접만 쓰면 인접 맨홀로 한 칸씩 밀린다.
  - 나머지는 망 맨홀 중 최근접(자연유하 말단 경계 맨홀은 --with-asbuilt 일 때만 후보).
  - 맨홀별 합계 = 가구 합계(불일치 0) 를 출력으로 확인한다.
  - 배정 맨홀에서 --far m 넘게 떨어진 가구는 확인 목록(지선 신설 / 개인처리).
"""
import sys, os, io, csv, math, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 유량 배분(load.json)'))
    ap.add_argument('--mode', choices=('assign', 'assigned', 'uniform'), default='assign')
    ap.add_argument('--q', type=float, default=None, help='uniform 모드 총 유량 ㎥/일')
    ap.add_argument('--where', action='append', default=[], metavar='필드=값', help='가구 거르기(AND)')
    ap.add_argument('--with-asbuilt', action='store_true', help='준공 맨홀도 배정 후보')
    ap.add_argument('--far', type=float, default=100.0, help='확인 목록 이격 기준 m (기본 100)')
    a = ap.parse_args()
    C = R.Ctx(a)
    cfg = C.cfg
    net = C.load('net.json')
    seg, force, node = net['seg'], net['force'], net['node']
    nodes_net = set(s['상류맨홀'] for s in seg.values()) | set(s['하류맨홀'] for s in seg.values()) | set(force)
    load = collections.defaultdict(float)
    rows_out, far = [], []

    if a.mode == 'uniform':
        if not a.q:
            raise SystemExit('--mode uniform 은 --q 총유량(㎥/일)이 필요하다')
        Lt = sum(s['연장'] for s in seg.values())
        for s in seg.values():
            load[s['상류맨홀']] += a.q * s['연장'] / Lt
        print('연장 비례 배분: %.2f ㎥/일 / %.1f m = %.5f ㎥/일·m' % (a.q, Lt, a.q / Lt))
    else:
        hs = R.read_layer(cfg, 'house')
        for k_, v_ in [w.split('=', 1) for w in a.where]:
            hs = hs[hs[k_].astype(str) == v_]
        f = lambda lg: R.fcol(cfg, 'house', lg, hs)
        c_id, c_q, c_no, c_as, c_cn = f('house_id'), f('q_m3d'), f('new_or_old'), f('assigned_mh'), f('connect_mh')
        if not c_q:
            raise SystemExit('fields.house.q_m3d 가 없다')
        EXIST = cfg.value('house_existing', '기존')
        cand = [(k, tuple(node[k])) for k in nodes_net if k in node and k not in net.get('boundary', {})]
        if a.with_asbuilt:
            cand += [(k, (v['x'], v['y'])) for k, v in (net.get('asbuilt') or {}).items()]
            cand += [(k, tuple(node[k])) for k in net.get('boundary', {}) if k in node]
        try:
            from scipy.spatial import cKDTree
            tree = cKDTree([c[1] for c in cand])
        except ImportError:
            tree = None
        via = collections.Counter()
        for _, r in hs.iterrows():
            g = r.geometry
            if g is None or g.is_empty:
                continue
            q = float(R.val(r, c_q, 0) or 0)
            hid = R.val(r, c_id)
            xy = R.point_of(g)
            if a.mode == 'assigned':
                n = R.val(r, c_as)
                basis = '배정맨홀 필드'
                d = math.dist(xy, node[n]) if n in node else None
            else:
                n = None
                cn = R.val(r, c_cn)
                if R.val(r, c_no) == EXIST and cn in node:
                    n, basis = cn, '기존:접속맨홀'
                else:
                    if tree is not None:
                        dd, i = tree.query(xy)
                        n = cand[int(i)][0]
                    else:
                        n = min(cand, key=lambda t: math.dist(xy, t[1]))[0]
                    basis = '%s:최근접' % (R.val(r, c_no) or '가구')
                d = math.dist(xy, node[n]) if n in node else None
            via[basis] += 1
            if n is None:
                continue
            load[n] += q
            rows_out.append(dict(가구=hid, Q_m3d=q, 배정맨홀=n, 이격_m=None if d is None else round(d, 1), 근거=basis,
                                 망=('신설망' if n in nodes_net else '기존망')))
            if d is not None and d > a.far:
                far.append(dict(가구=hid, 배정맨홀=n, 이격_m=round(d, 1), Q_m3d=q))
        tot_h = sum(r['Q_m3d'] for r in rows_out)
        print('가구 %d호 %.2f ㎥/일 → 맨홀 %d개소  근거 %s' % (len(rows_out), tot_h, len(load), dict(via)))
        print('  합계 대조: 가구 %.4f / 맨홀 %.4f → 불일치 %.4f' % (tot_h, sum(load.values()), tot_h - sum(load.values())))
        off = {k: v for k, v in load.items() if k not in nodes_net}
        if off:
            print('  기존망 맨홀 배정 %d개소 %.2f ㎥/일 (이번 망 누가에서 빠진다)' % (len(off), sum(off.values())))
        if far:
            print('  ★ %.0f m 넘게 떨어진 가구 %d호 — 지선 신설/개인처리 확인: %s'
                  % (a.far, len(far), ', '.join('%s(%.0fm)' % (x['가구'], x['이격_m']) for x in far[:10])))
        with io.open(C.path('가구배정.csv'), 'w', encoding='utf-8-sig', newline='') as fo:
            w = csv.DictWriter(fo, fieldnames=['가구', 'Q_m3d', '배정맨홀', '이격_m', '근거', '망'])
            w.writeheader()
            w.writerows(rows_out)

    out = dict(meta=dict(created=R.stamp(), step='r4_load', mode=a.mode, q=a.q, where=a.where),
               load={k: round(v, 6) for k, v in load.items()}, total=round(sum(load.values()), 4),
               far=far, n_house=len(rows_out))
    print('자체유량 합 %.2f ㎥/일 (망 안 %.2f)' % (sum(load.values()), sum(v for k, v in load.items() if k in nodes_net)))
    print('→ %s' % C.save('load.json', out))


if __name__ == '__main__':
    main()
