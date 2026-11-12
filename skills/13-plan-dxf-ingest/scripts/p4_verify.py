# -*- coding: utf-8 -*-
"""13 단계 4 — 반영 검증 (읽기 전용).

build.json ↔ 계획 레이어(자료기준일 = build 의 값) 를 거꾸로 대조한다.
  · 건수·관번호 집합 · 매핑된 필드 전부(실수 ±0.001) · 도형 정점수·도형길이↔연장
  · 계획맨홀 중심 ↔ 노선 시점 · GPKG 피처수 = rtree = gpkg_ogr_contents
  · 설계기준 훑기: 판정 분포 · 단차 · 심도 · 토피(design.min_cover_m) · 경사(design.min_slope_permil) · 역경사
불일치가 있으면 종료코드 1.

    python p4_verify.py -d YYMMDD
    python p4_verify.py --build <build.json> --gpkg <백업 또는 사본.gpkg>         # 그 GPKG 와 대조
    python p4_verify.py --build <build.json> --gpkg <원본 반영본.gpkg> --asof "<그때 자료기준일>"   # 회귀 대조
"""
import argparse, collections, math, sys

import ingestlib as L

RECKEY = {'plan_pipe': 'pipes', 'plan_manhole': 'manholes', 'plan_pump': 'pumps'}
IDKEY = {'plan_pipe': 'pipe_id', 'plan_manhole': 'mh_id', 'plan_pump': 'pump_id'}


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 0.001
    return str(a) == str(b)


def center(xy):
    if not xy:
        return None
    if len(xy) == 1:
        return xy[0]
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def main(argv=None):
    ap = argparse.ArgumentParser(description='13 반영 검증(읽기 전용)')
    L.common_args(ap)
    ap.add_argument('--build', default=None, help='build.json (생략: -d/--outdir 의 산출 폴더)')
    ap.add_argument('--gpkg', default=None, help='plan_* 레이어를 이 GPKG 에서 같은 이름으로 읽는다')
    ap.add_argument('--asof', default=None, help='대조할 자료기준일(기본: build 의 값)')
    ap.add_argument('--drop-list', type=float, default=0.3, help='단차 목록 기준 m (기본 0.3)')
    ap.add_argument('--depth-list', type=float, default=4.0, help='심도 목록 기준 m (기본 4.0)')
    a = ap.parse_args(argv)
    cfg, C = L.load(a.config)
    bp = L.find_build(cfg, a)
    B = L.load_json(bp)
    asof = a.asof or B['meta']['asof']
    print('=' * 78)
    print('[13 검증] %s\n  자료기준일 %r' % (bp, asof))
    carried = {}
    logs = sorted(bp.parent.glob('apply_log_*.json'))
    if logs and not a.gpkg:
        lg = L.load_json(logs[-1])
        if lg.get('asof') == asof:
            carried = lg.get('carried') or {}
            n = sum(len(v) for v in carried.values())
            if n:
                print('  승계값 %d건은 대조에서 뺀다 (%s)' % (n, logs[-1].name))
    print('=' * 78)
    bad_total = 0
    got = {}
    for key in L.PLAN_KEYS:
        sp = L.layer_spec(cfg, key, a.gpkg)
        recs = B[RECKEY[key]]
        if sp is None:
            print('\n[%s] 설정에 없음 — 건너뜀' % key)
            continue
        fm = cfg.fields(key)
        fields, gtype, rows = L.read_rows(cfg, sp)
        af, idf = fm.get('asof'), fm.get(IDKEY[key])
        mine = [r for r in rows if r.get(af) == asof]
        got[key] = mine
        db = collections.defaultdict(list)
        for r in mine:
            db[r.get(idf)].append(r)
        bb = {r[IDKEY[key]]: r for r in recs}
        dup = [k for k, v in db.items() if len(v) > 1]
        print('\n[%s] %s\n  build %d / DB %d (번호 중복 %d)' % (key, L.label(sp), len(recs), len(mine), len(dup)))
        only_b, only_d = sorted(set(bb) - set(db)), sorted(set(db) - set(bb), key=str)
        if only_b or only_d:
            print('  ! build 에만 %d %s / DB 에만 %d %s' % (len(only_b), only_b[:8], len(only_d), only_d[:8]))
        fnames = {f for f, _ in fields}
        cmpf = [(k, f) for k, f in fm.items() if f in fnames and any(k in r for r in recs[:1])]
        diff, ex = collections.Counter(), collections.defaultdict(list)
        cset = carried.get(key, {})
        for k in set(bb) & set(db):
            r, d = bb[k], db[k][0]
            for lk, f in cmpf:
                if f in cset.get(k, ()):
                    continue                           # p3 --carry-* 로 이전 행에서 이어 붙인 값
                if not same(r.get(lk), d.get(f)):
                    diff[f] += 1
                    if len(ex[f]) < 3:
                        ex[f].append((k, r.get(lk), d.get(f)))
        print('  속성 역대조 불일치: %s' % (dict(diff) or '0건'))
        for f, v in ex.items():
            print('     %s 예: %s' % (f, v))
        bad_total += len(only_b) + len(only_d) + sum(diff.values()) + len(dup)
        if key == 'plan_pipe':
            vtx = collections.Counter(len(d['_xy'] or []) for d in mine)
            gl = sorted((abs(L.polylen(d['_xy']) - (d.get(fm.get('length_m')) or 0)), d.get(idf))
                        for d in mine if d['_xy'] and fm.get('length_m'))
            print('  도형 정점수 분포: %s' % sorted(vtx.items()))
            if gl:
                print('  도형길이 vs 연장: 중앙 %.3f / 최대 %.3f m (%s)' % (gl[len(gl) // 2][0], gl[-1][0], gl[-1][1]))
        if sp['kind'] == 'gpkg':
            con = L.ro_connect(sp['path'])
            lay = sp['layer']
            n = con.execute('select count(*) from "%s"' % lay).fetchone()[0]
            oc = con.execute('select feature_count from gpkg_ogr_contents where table_name=?', (lay,)).fetchone()
            try:
                gcol = con.execute('select column_name from gpkg_geometry_columns where table_name=?',
                                   (lay,)).fetchone()[0]
                rt = con.execute('select count(*) from "rtree_%s_%s"' % (lay, gcol)).fetchone()[0]
            except Exception:
                rt = None
            gnull = con.execute('select count(*) from "%s" where "%s" is null' % (lay, gcol)).fetchone()[0] if rt is not None else None
            con.close()
            ok = (oc is None or oc[0] in (None, n)) and (rt is None or rt == n)
            print('  GPKG 피처 %d / ogr_contents %s / rtree %s / 도형 NULL %s %s'
                  % (n, oc[0] if oc else '-', rt, gnull, '' if ok else '  ! 불일치'))
            bad_total += 0 if ok else 1

    # ── 맨홀 중심 ↔ 노선 시점
    if 'plan_pipe' in got and 'plan_manhole' in got:
        fmp, fmm = cfg.fields('plan_pipe'), cfg.fields('plan_manhole')
        mc = {d.get(fmm.get('mh_id')): center(d['_xy']) for d in got['plan_manhole']}
        dd = sorted((math.dist(mc[d.get(fmp.get('pipe_id'))], d['_xy'][0]), d.get(fmp.get('pipe_id')))
                    for d in got['plan_pipe'] if d['_xy'] and mc.get(d.get(fmp.get('pipe_id'))))
        nomh = [d.get(fmp.get('pipe_id')) for d in got['plan_pipe'] if d.get(fmp.get('pipe_id')) not in mc]
        bk = {p['pipe_id']: p['kind'] for p in B['pipes']}
        KP = cfg.value('kind_pressure', '압송')
        mid = [n for n in nomh if bk.get(n) == KP]
        rest = [n for n in nomh if bk.get(n) != KP]
        if dd:
            print('\n맨홀 중심 ↔ 노선 시점: 중앙 %.4f / 최대 %.4f m (%s) / %d개'
                  % (dd[len(dd) // 2][0], dd[-1][0], dd[-1][1], len(dd)))
        print('노선 시점에 계획맨홀 없음 %d %s%s' % (len(rest), rest[:10],
              ('  (압송 중간절점 %d 은 맨홀이 아니라 정상)' % len(mid)) if mid else ''))

    # ── 설계기준 훑기 (build 값 기준)
    P = B['pipes']
    KG = cfg.value('kind_gravity', '자연유하')
    print('\n판정 분포: %s' % collections.Counter(p['judge'] for p in P).most_common())
    print('자체유량 합 %.1f ㎥/일 / 누가유량 최대 %s' % (sum(p['self_flow_m3d'] or 0 for p in P),
                                                 max((p['flow_m3d'] or 0) for p in P) if P else None))
    for m in B['pumps']:
        print('펌프장 %-14s 압송 %s m / 유입IL %s / 깊이 %s / 최상단BOP %s / 토출 %s / 누가 %s'
              % (m['pump_id'], m['force_main_m'], m['in_invert'], m['depth_m'], m['top_bop'], m['discharge_to'],
                 m['flow_m3d']))
    drops = sorted(((m['drop'], m['mh_id']) for m in B['manholes'] if (m['drop'] or 0) > a.drop_list), reverse=True)
    print('단차 %.1f m 초과 %d: %s' % (a.drop_list, len(drops), drops[:15]))
    deep = sorted(((m['depth'], m['mh_id']) for m in B['manholes'] if (m['depth'] or 0) > a.depth_list), reverse=True)
    print('심도 %.1f m 초과 %d: %s' % (a.depth_list, len(deep), deep[:10]))
    mc_, ms_ = cfg.get('design.min_cover_m', 1.0), cfg.get('design.min_slope_permil', 5.0)
    print('토피 < %.2f m: %s' % (mc_, sorted((p['us_cover'], p['pipe_id']) for p in P
                                            if p['us_cover'] is not None and p['us_cover'] < mc_)[:10]))
    print('경사 < %.1f‰ (%s): %s' % (ms_, KG, sorted((p['slope_permil'], p['pipe_id']) for p in P if p['kind'] == KG
                                                  and p['slope_permil'] is not None and p['slope_permil'] < ms_)[:10]))
    print('역경사(%s): %s' % (KG, [(p['pipe_id'], p['slope_permil']) for p in P if p['kind'] == KG
                                   and p['slope_permil'] is not None and p['slope_permil'] < 0]))
    print('\n판정: %s' % ('통과' if bad_total == 0 else '*** 불일치 %d건 ***' % bad_total))
    return 0 if bad_total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
