# -*- coding: utf-8 -*-
"""13 단계 3 — build.json → 계획 레이어(plan_pipe · plan_manhole · plan_pump) 반영.

기본은 **보고 모드**(아무것도 안 쓴다): 지울 행·넣을 행·필드 매핑·변경메모를 보여 주고 CSV 로 남긴다.
--write 일 때만 GPKG 를 백업한 뒤 고친다.

반영 방식 = 회차 **교체**: --replace-asof 로 준 자료기준일(이전 회차) 행과 이번 build 의 자료기준일 행을 지우고
build 레코드를 넣는다(같은 회차를 다시 돌려도 중복이 안 생긴다). 이전 회차 대비 변경은 레이어에 열을 늘리지 않고
변경메모 CSV(번호변경 추정·값변경·신규·삭제)로 남긴다.

    python p3_apply.py -d YYMMDD --replace-asof "<이전 회차 자료기준일>"            # 보고
    python p3_apply.py -d YYMMDD --replace-asof "<이전 회차 자료기준일>" --write    # 백업 후 반영
    python p3_apply.py --build <build.json> --gpkg <사본.gpkg> --write              # 사본에 시험 반영
    python p3_apply.py ... --carry-fields "금회_*"      # 관번호 같은 구간은 사람이 계산한 열을 이어 붙인다
    python p3_apply.py ... --carry-mh XX-001            # 그 맨홀의 접속맨홀·유출관저고·심도·비고 승계

쓰기는 GPKG 만 한다. PostGIS 레이어는 보고만 하고, GPKG 로 받아 확인한 뒤 옮긴다.
"""
import argparse, collections, datetime, fnmatch, math, shutil, sqlite3, sys
from pathlib import Path

import ingestlib as L

CMP = ('length_m', 'dia_mm', 'us_invert', 'ds_invert', 'us_ground', 'kind')
RECKEY = {'plan_pipe': 'pipes', 'plan_manhole': 'manholes', 'plan_pump': 'pumps'}
IDKEY = {'plan_pipe': 'pipe_id', 'plan_manhole': 'mh_id', 'plan_pump': 'pump_id'}


def conv(v, ftype):
    if v is None:
        return None
    t = (ftype or '').split(':')[0]
    try:
        if t == 'str':
            return v if isinstance(v, str) else ('%s' % v)
        if t in ('int', 'int32', 'int64'):
            return int(round(float(v)))
        if t == 'float':
            return float(v)
    except (TypeError, ValueError):
        return None
    return v


def geometry(key, gtype, r, C):
    g = (gtype or '').replace('3D ', '')
    if key == 'plan_pipe':
        coords = [tuple(p) for p in r['_geom']]
        return {'type': 'MultiLineString', 'coordinates': [coords]} if g == 'MultiLineString' else \
            {'type': 'LineString', 'coordinates': coords}
    x, y = r['_xy']
    if g in ('Polygon', 'MultiPolygon'):
        pump_outer = next((v.get('outer_mm') for v in C['mh_types'].values() if v.get('role') == 'pump'), None)
        d = r.get('outer_mm') or (pump_outer if key == 'plan_pump' else None) or 1200
        ring = L.circle(x, y, d)
        return {'type': 'MultiPolygon', 'coordinates': [ring]} if g == 'MultiPolygon' else \
            {'type': 'Polygon', 'coordinates': ring}
    if g == 'MultiPoint':
        return {'type': 'MultiPoint', 'coordinates': [(x, y)]}
    return {'type': 'Point', 'coordinates': (x, y)}


def change_memo(cfg, B, old_rows, tol):
    """새 노선 ↔ 지워질 이전 행 — 값변경 / 번호변경 추정(상류절점 동일) / 신규 / 삭제."""
    fm = cfg.fields('plan_pipe')
    idf = fm.get('pipe_id')
    cmpf = [(k, fm[k]) for k in CMP if k in fm]
    old = {}
    for r in old_rows:
        old[r.get(idf)] = r
    newset = {p['pipe_id'] for p in B['pipes']}
    rows = []
    for p in B['pipes']:
        n = p['pipe_id']
        o = old.get(n)
        if o:
            ch = []
            for k, f in cmpf:
                a, v = p.get(k), o.get(f)
                if a is None and v is None:
                    continue
                if isinstance(a, (int, float)) and isinstance(v, (int, float)):
                    if abs(a - v) > 0.005:
                        ch.append('%s %s→%s' % (f, v, a))
                elif a != v:
                    ch.append('%s %s→%s' % (f, v, a))
            rows.append((n, '값변경' if ch else '동일', ('이전 대비 ' + ' · '.join(ch)) if ch else ''))
            continue
        s = tuple(p['_geom'][0]) if p['_geom'] else None
        cand = sorted((math.dist(s, o['_xy'][0]), k) for k, o in old.items()
                      if k not in newset and o.get('_xy') and s) if s else []
        if cand and cand[0][0] <= tol:
            k = cand[0][1]
            o = old[k]
            rows.append((n, '번호변경추정', '이전 %s 와 상류절점 동일(%.2f m) [이전 연장 %s / 관저 %s→%s]'
                         % (k, cand[0][0], o.get(fm.get('length_m')), o.get(fm.get('us_invert')),
                            o.get(fm.get('ds_invert')))))
        else:
            rows.append((n, '신규', '이전 회차에 대응 구간 없음'))
        if p.get('flo_name'):
            rows[-1] = rows[-1][:2] + ((rows[-1][2] + ' | ' if rows[-1][2] else '') +
                                       'flo 번호 %s → 도면 번호 채택' % p['flo_name'],)
    for k in sorted(set(old) - newset, key=str):
        rows.append((k, '삭제', '이번 회차에 없음'))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='13 계획 레이어 반영 (기본 보고 모드, --write 일 때만 씀)')
    L.common_args(ap)
    ap.add_argument('--build', default=None, help='build.json (생략: -d/--outdir 의 산출 폴더)')
    ap.add_argument('--gpkg', default=None, help='plan_* 레이어를 이 GPKG 에서 같은 이름으로 — 사본 시험용')
    ap.add_argument('--replace-asof', action='append', default=[], help='지울 이전 회차 자료기준일 값(여러 개 가능)')
    ap.add_argument('--carry-fields', action='append', default=[],
                    help='관번호 같은 이전 행에서 이어 붙일 노선 필드(실제 이름, * 와일드카드)')
    ap.add_argument('--carry-mh', action='append', default=[],
                    help='이전 행의 접속맨홀·유출관저고·심도·비고를 승계할 맨홀번호(설계가 안 바뀐 것만)')
    ap.add_argument('--backup-dir', default=None, help='백업 폴더(기본: 산출 폴더/backup)')
    ap.add_argument('--write', action='store_true', help='실제로 쓴다(백업 후). 없으면 보고만')
    a = ap.parse_args(argv)

    cfg, C = L.load(a.config)
    bp = L.find_build(cfg, a)
    B = L.load_json(bp)
    od = bp.parent
    asof = B['meta']['asof']
    dels = list(dict.fromkeys(a.replace_asof + [asof]))
    print('=' * 78)
    print('[13 반영] %s  — %s' % (bp, '쓰기' if a.write else '보고 모드(쓰지 않음)'))
    print('  build: 경로 %s · 노선 %d · 맨홀 %d · 펌프장 %d · 자료기준일 %r'
          % (B['meta']['mode'], len(B['pipes']), len(B['manholes']), len(B['pumps']), asof))
    print('  지울 자료기준일: %s' % dels)
    print('=' * 78)

    plan, stop = {}, False
    for key in L.PLAN_KEYS:
        recs = B[RECKEY[key]]
        sp = L.layer_spec(cfg, key, a.gpkg)
        if sp is None:
            print('\n[%s] layers.%s 가 설정에 없다 — %d건 건너뜀' % (key, key, len(recs)))
            continue
        fm = cfg.fields(key)
        print('\n[%s] %s' % (key, L.label(sp)))
        fields, gtype, rows = L.read_rows(cfg, sp)
        fnames = {f for f, _ in fields}
        ftype = dict(fields)
        af, idf = fm.get('asof'), fm.get(IDKEY[key])
        if not af or af not in fnames or not idf or idf not in fnames:
            print('  ! fields.%s.asof / %s 매핑이 없거나 레이어에 없다 → 교체 불가(이 레이어 건너뜀)' % (key, IDKEY[key]))
            stop = True
            continue
        used = {k for r in recs for k in r if not k.startswith('_')}
        mapped = {k: f for k, f in fm.items() if k in used and f in fnames}
        miss = sorted(f for k, f in fm.items() if k in used and f not in fnames)
        unmapped = sorted(k for k in used if k not in fm)
        print('  기하 %s · 필드 %d · 기존 행 %d' % (gtype, len(fields), len(rows)))
        print('  쓰는 필드 %d: %s' % (len(mapped), ', '.join('%s→%s' % kv for kv in sorted(mapped.items()))))
        if miss:
            print('  ! 매핑했지만 레이어에 없는 필드(무시): %s' % miss)
        if unmapped:
            print('  (매핑 없어 안 쓰는 값: %s)' % ', '.join(unmapped))
        cnt = collections.Counter(r.get(af) for r in rows if r.get(af) in dels)
        drop = [r for r in rows if r.get(af) in dels]
        # 비교·승계 기준 = 이전 회차 행. 없으면(같은 회차 재반영) 이번 자료기준일로 이미 들어가 있는 행
        old = [r for r in drop if r.get(af) != asof] or [r for r in drop if r.get(af) == asof]
        keep = [r for r in rows if r.get(af) not in dels]
        for v in dels:
            print('  지울 행 %-5d  %s = %r' % (cnt.get(v, 0), af, v))
        dist = fm.get('district')
        kid = collections.Counter((r.get(dist) if dist else None, r.get(idf)) for r in keep)
        clash = sorted({r[IDKEY[key]] for r in recs
                        if kid.get((r.get('district') if dist else None, r[IDKEY[key]]))})
        if clash:
            print('  ! 남는 행과 번호가 겹친다 %d건(지구+번호): %s — 이전 회차 자료기준일을 --replace-asof 로 줬는지 확인'
                  % (len(clash), clash[:10]))
        print('  넣을 행 %d' % len(recs))
        plan[key] = dict(sp=sp, fm=mapped, ftype=ftype, gtype=gtype, recs=recs, drop=drop, old=old, keep=keep, af=af, idf=idf,
                         fields=[f for f, _ in fields])

    # ── 변경메모
    memo = []
    if 'plan_pipe' in plan:
        P = plan['plan_pipe']
        memo = change_memo(cfg, B, P['old'], C['tolerance']['renumber_m'])
        stamp = datetime.datetime.now().strftime('%y%m%d_%H%M%S')
        mp = od / ('변경메모_%s.csv' % stamp)
        L.save_csv(mp, ['관번호', '분류', '내용'], memo)
        base = sorted({r.get(P['af']) for r in P['old']}, key=str)
        print('\n변경메모 %s — 비교 기준 %d행 %s%s' % (
            mp.name, len(P['old']), base, '  (같은 회차 재반영)' if base == [asof] else ''))
        print('  %s' % dict(collections.Counter(m[1] for m in memo)))
        for m in [m for m in memo if m[1] in ('번호변경추정', '신규', '삭제')][:15]:
            print('   %-14s %-8s %s' % m)

    # ── 승계값 (p4_verify 가 대조에서 빼도록 apply_log 에 남긴다)
    carried = collections.defaultdict(dict)
    if a.carry_fields and 'plan_pipe' in plan:
        P = plan['plan_pipe']
        pats = [f for f in P['fields'] if any(fnmatch.fnmatch(f, p) for p in a.carry_fields)]
        oldv = {r.get(P['idf']): r for r in P['old']}
        n = 0
        for r in P['recs']:
            o = oldv.get(r['pipe_id'])
            if o:
                r['_carry'] = {f: o.get(f) for f in pats}
                carried['plan_pipe'][r['pipe_id']] = pats
                n += 1
        print('\n승계 필드 %s → 관번호 같은 %d구간' % (pats, n))
    if a.carry_mh and 'plan_manhole' in plan:
        M = plan['plan_manhole']
        fm = cfg.fields('plan_manhole')
        oldv = {r.get(M['idf']): r for r in M['old']}
        for r in M['recs']:
            o = oldv.get(r['mh_id'])
            if r['mh_id'] in a.carry_mh and o:
                for k in ('connect_to', 'out_invert', 'depth', 'note'):
                    if k in fm and fm[k] in o:
                        r[k] = o[fm[k]]
                carried['plan_manhole'][r['mh_id']] = [fm[k] for k in ('connect_to', 'out_invert', 'depth', 'note')
                                                       if k in fm]
                r['note'] = ((r.get('note') or '') + ' | 이전 회차 접속정보 승계').lstrip(' |')
                print('  맨홀 %s 접속정보 승계: %s' % (r['mh_id'], {k: r.get(k) for k in ('connect_to', 'out_invert')}))
        lost = [m for m in a.carry_mh if m not in oldv]
        if lost:
            print('  ! --carry-mh 이전 행 없음: %s' % lost)

    if not a.write:
        print('\n보고 모드 — 아무것도 쓰지 않았다. 확인 후 --write 로 다시 돌린다.')
        return 1 if stop else 0
    if stop:
        raise SystemExit('매핑 문제로 건너뛴 레이어가 있다 — 고친 뒤 다시.')

    # ── 쓰기 (GPKG 만)
    gp = {k: v for k, v in plan.items() if v['sp']['kind'] == 'gpkg'}
    pg = [k for k, v in plan.items() if v['sp']['kind'] != 'gpkg']
    if pg:
        raise SystemExit('PostGIS 레이어(%s)는 쓰지 않는다 — --gpkg 로 사본 GPKG 에 반영해 확인한 뒤 옮길 것.' % pg)
    bdir = Path(a.backup_dir) if a.backup_dir else (od / 'backup')
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%y%m%d_%H%M%S')
    done = {}
    for path in sorted({str(v['sp']['path']) for v in gp.values()}):
        try:
            con = sqlite3.connect(path, timeout=2)
            con.execute('BEGIN IMMEDIATE')
            con.rollback()
            con.close()
        except sqlite3.OperationalError as e:
            raise SystemExit('GPKG 가 잠겨 있다(%s): %s — QGIS 편집 모드를 끝내거나 닫고 다시.' % (e, path))
        bk = bdir / ('%s_bak_%s.gpkg' % (Path(path).stem, stamp))
        shutil.copy2(path, bk)
        done[path] = str(bk)
        print('\n백업 %s' % bk)

    L.kitconfig.fix_proj()                      # rasterio/pyproj 와 QGIS PROJ_DATA 충돌 우회 — fiona import 전에
    import fiona
    log = {}
    for key, P in gp.items():
        path, lay = str(P['sp']['path']), P['sp']['layer']
        con = sqlite3.connect(path)
        q = 'delete from "%s" where "%s" in (%s)' % (lay, P['af'], ','.join('?' * len(dels)))
        try:
            nd = con.execute(q, dels).rowcount
            con.commit()
        except sqlite3.OperationalError as e:
            con.close()
            raise SystemExit('삭제 실패 %s: %s — 백업 %s 로 되돌릴 수 있다' % (lay, e, done[path]))
        con.close()
        ni = 0
        with fiona.open(path, 'a', layer=lay) as dst:
            keys = list(dst.schema['properties'])
            for r in P['recs']:
                props = {f: None for f in keys}
                for k, f in P['fm'].items():
                    props[f] = conv(r.get(k), P['ftype'].get(f))
                for f, v in (r.get('_carry') or {}).items():
                    if f in props:
                        props[f] = v
                dst.write({'geometry': geometry(key, P['gtype'], r, C), 'properties': props})
                ni += 1
        con = sqlite3.connect(path)
        tot = con.execute('select count(*) from "%s"' % lay).fetchone()[0]
        oc = con.execute('select feature_count from gpkg_ogr_contents where table_name=?', (lay,)).fetchone()
        try:
            gcol = con.execute('select column_name from gpkg_geometry_columns where table_name=?', (lay,)).fetchone()[0]
            rt = con.execute('select count(*) from "rtree_%s_%s"' % (lay, gcol)).fetchone()[0]
        except Exception:
            rt = None
        con.close()
        print('[%s] 삭제 %d · 추가 %d → 총 %d (ogr_contents %s / rtree %s)'
              % (key, nd, ni, tot, oc[0] if oc else '-', rt))
        log[key] = dict(layer=lay, path=path, deleted=nd, inserted=ni, total=tot)
    L.save_json(od / ('apply_log_%s.json' % stamp),
                dict(build=str(bp), asof=asof, deleted_asof=dels, backup=done, layers=log, carried=carried,
                     at=L.now()))
    print('\n완료 — 백업 %s\nQGIS 에 열려 있으면 레이어를 새로고침할 것. 검증: p4_verify.py' % list(done.values()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
