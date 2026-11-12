# -*- coding: utf-8 -*-
"""r8 산출 — GPKG · CSV · makesw .flo · (선택) SWMM .inp · (선택) 노선 DXF

    python r8_export.py                                  # GPKG + CSV + .flo → <run>/
    python r8_export.py --flo-template <설계사.flo>        # 1·2행(머리·강우 파라미터)을 그 파일에서
    python r8_export.py --outfall XX-01-034=방류-100       # 말단 이름 바꾸기(여러 번) / --outfall-auto
    python r8_export.py --inp                            # EPA SWMM 입력(.inp) — swmm_check.py 로 교차검증
    python r8_export.py --dxf [--dxf-template <서식.dxf>]  # 노선 DXF(관로·맨홀·관로명)
    python r8_export.py --to-config                       # 설정 레이어 GPKG 에 '<레이어>_설계' 새 레이어로(덮어쓰지 않음)

GPKG 레이어 이름은 설정 layers.plan_pipe/plan_manhole/plan_pump 의 이름(+ --suffix)을 쓰고, 필드 이름은
fields.<레이어>.<논리필드> 를 따른다 — 결과를 그대로 그 팀 레이어 자리에 넣어 볼 수 있다. 설정에 없는 논리 필드는
킷 표준 이름(templates/db_schema.json)으로 쓴다. 원본 레이어는 --overwrite(백업 <레이어>_bak_YYMMDD 후)일 때만 바꾼다.

.flo 는 킷 lib/flospec.py 로 쓴다(쓰기 전 자체 검증 — ERROR 면 쓰지 않는다). 압송은 구배구간마다
<펌프장>01, 02 … 부번호로 나누고 마지막 줄만 지반고 3개. makesw 에는 펌프 용량 항목이 없다(SKILL.md 6절).
"""
import sys, os, io, csv, math, json, datetime, collections, argparse
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R
import flospec  # 킷 lib (routelib 이 lib 경로를 넣는다)

STD = {   # 논리 필드 → 킷 표준 이름(설정에 없을 때)
    'plan_pipe': dict(pipe_id='관번호', district='지구', kind='구분', length_m='연장_m', dia_mm='관경',
                      us_invert='상류관저고', ds_invert='하류관저고', us_ground='상류지반고', slope_permil='경사퍼밀',
                      us_cover='상류토피', flow_m3d='누가유량_m3d', velocity_mps='실유속_mps', judge='수리판정',
                      obstacles='종단지장물', asof='자료기준일', source='출처'),
    'plan_manhole': dict(mh_id='맨홀번호', district='지구', kind='구분', mh_type='맨홀호수', ground='지반고',
                         out_invert='유출관저고', depth='심도', drop='단차', asof='자료기준일', connect_to='접속맨홀'),
    'plan_pump': dict(pump_id='펌프장', district='지구', kind='구분', discharge_to='토출대상', flow_m3d='누가유량_m3d',
                      q_m3min='적용용량_m3min', head_m='전양정_m', power_kw='적용동력_kW', force_main_m='압송연장_m',
                      in_invert='유입관저고_m', asof='자료기준일'),
}
STD_LAYER = {'plan_pipe': '노선_실시설계', 'plan_manhole': '계획맨홀_실시설계', 'plan_pump': '맨홀펌프장_실시설계'}
DEF_HEADER = '00처리분구 makesw inputdata'
DEF_PARAM = '   2      651.1  1.0140 0.500      544.4  1.0000 0.500    5.0 10.0'


def fname(cfg, key, logical):
    return cfg.get('fields.%s.%s' % (key, logical)) or STD[key][logical]


def gl_at(pts, s):
    for a, b in zip(pts, pts[1:]):
        if a['s'] <= s <= b['s']:
            f = (s - a['s']) / (b['s'] - a['s']) if b['s'] > a['s'] else 0
            return a['gl'] + (b['gl'] - a['gl']) * f
    return pts[-1]['gl']


def obs_text(lst):
    out = []
    for o in sorted(lst, key=lambda o: o['x']):
        out.append('%s %s@%.1f(%.2f)' % (o.get('구분') or o.get('kind') or '', o.get('규격') or '', o['x'], o['bot']))
    return ' / '.join(x.strip() for x in out)


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 산출(GPKG·CSV·.flo·.inp·DXF)'))
    ap.add_argument('--gpkg', default=None, help='출력 GPKG (기본 <run>/route_design.gpkg)')
    ap.add_argument('--suffix', default='_설계', help='레이어 이름 접미(기본 _설계)')
    ap.add_argument('--to-config', action='store_true', help='설정 레이어의 GPKG 에 새 레이어로 쓴다')
    ap.add_argument('--overwrite', action='store_true', help='--to-config 에서 접미 없이 원 레이어를 바꾼다(백업 후)')
    ap.add_argument('--no-flo', action='store_true')
    ap.add_argument('--flo-template', default=None, help='1·2행을 가져올 .flo (기본 설정 route_design.flo_template)')
    ap.add_argument('--flo-name', default='route_design.flo')
    ap.add_argument('--flo-n', type=float, default=None, help='.flo 조도(기본 design.manning_n)')
    ap.add_argument('--flo-force-d', type=int, default=None, help='.flo 압송 관경 표기(기본 실제 관경)')
    ap.add_argument('--outfall', action='append', default=[], metavar='노드=이름', help='.flo 말단 이름 바꾸기')
    ap.add_argument('--outfall-auto', action='store_true', help="말단을 '방류-100, 102 …' 로")
    ap.add_argument('--gravity-only', action='store_true', help='.flo 에서 압송 제외')
    ap.add_argument('--inp', action='store_true', help='EPA SWMM .inp 도 쓴다')
    ap.add_argument('--dxf', action='store_true', help='노선 DXF 도 쓴다')
    ap.add_argument('--dxf-template', default=None, help='레이어·블록·스타일을 가져올 DXF')
    a = ap.parse_args()
    C = R.Ctx(a)
    cfg, P = C.cfg, C.P
    net, des = C.load('net.json'), C.load('design.json')
    fz = C.load('force.json', required=False) or {}
    pro = C.load('profile.json', required=False) or {}
    res, IL, GL, seg = des['res'], des['IL'], des['GL'], net['seg']
    chains, pumps = fz.get('chains') or {}, fz.get('pumps') or []
    OBS = des.get('OBS') or {}
    load = des.get('load') or {}
    today = datetime.date.today().isoformat()
    source = '30-route-design %s (%s)' % (C.dir.name, today)
    K_G, K_P = cfg.value('kind_gravity', '자연유하'), cfg.value('kind_pressure', '압송')

    # ── GPKG
    import geopandas as gpd
    from shapely.geometry import LineString, Point
    crs = cfg.get('project.crs', 'EPSG:5186')
    fp = lambda lg: fname(cfg, 'plan_pipe', lg)
    rows = []
    for pid, r in res.items():
        s = seg[pid]
        rows.append({fp('pipe_id'): pid, fp('kind'): K_G, fp('dia_mm'): r['관경'], fp('length_m'): r['연장'],
                     fp('us_invert'): r['상류관저고'], fp('ds_invert'): r['하류관저고'], fp('us_ground'): r['상류지반고'],
                     fp('slope_permil'): r['경사'], fp('us_cover'): r['상류토피'],
                     fp('flow_m3d'): round(r['누가유량_Lps'] * 86.4, 3), fp('velocity_mps'): r['실유속_mps'],
                     fp('judge'): r['수리판정'], fp('obstacles'): obs_text(OBS.get(pid, [])) or None,
                     fp('asof'): today, fp('source'): source,
                     '노선': r['노선'], '상류맨홀': r['상류맨홀'], '하류맨홀': r['하류맨홀'], '하류지반고': r['하류지반고'],
                     '하류토피': r['하류토피'], '하류단차': r['하류단차'], '토피기준': r['토피기준'], '수심비': r['수심비'],
                     '중간최소토피': r.get('중간최소토피'), '보호공절점': r.get('보호공절점'),
                     'geometry': LineString(s['coords'])})
    fres = des.get('force_res') or {}
    for nm, c in net['force'].items():
        ch = chains.get(nm, {})
        sg = ch.get('segs') or []
        rows.append({fp('pipe_id'): nm, fp('kind'): K_P, fp('dia_mm'): c['관경'], fp('length_m'): c['L'],
                     fp('us_invert'): sg[0]['il_a'] if sg else None, fp('ds_invert'): sg[-1]['il_b'] if sg else None,
                     fp('us_ground'): ch['pts'][0]['gl'] if ch.get('pts') else None,
                     fp('flow_m3d'): round(fres.get(nm, {}).get('유입누가_Lps', 0) * 86.4, 3),
                     fp('asof'): today, fp('source'): source,
                     '노선': R.route_of(nm), '상류맨홀': nm, '하류맨홀': c['하류맨홀'],
                     '압송_종단': json.dumps([[q['a'], q['il_a']] for q in sg] + ([[sg[-1]['b'], sg[-1]['il_b']]] if sg else []),
                                          ensure_ascii=False) if sg else None,
                     '배니밸브': len(ch.get('sag') or []), '공기밸브': len(ch.get('high') or []),
                     'geometry': LineString(c['pts'])})
    gp = gpd.GeoDataFrame(rows, geometry='geometry', crs=crs)

    fm = lambda lg: fname(cfg, 'plan_manhole', lg)
    drop_in = collections.defaultdict(float)
    for r in res.values():
        drop_in[r['하류맨홀']] = max(drop_in[r['하류맨홀']], r['하류단차'])
    pump_set = set(net['force'])
    mrows = []
    for n, xy in net['node'].items():
        if n not in IL and n not in pump_set:
            continue
        il = IL.get(n)
        gl = GL.get(n)
        mrows.append({fm('mh_id'): n, fm('kind'): '맨홀펌프장' if n in pump_set else
                      ('기존맨홀' if n in (net.get('boundary') or {}) else '계획맨홀'),
                      fm('mh_type'): (net['MH'].get(n) or {}).get('호수'), fm('ground'): gl, fm('out_invert'): il,
                      fm('depth'): round(gl - il, 3) if (gl is not None and il is not None) else None,
                      fm('drop'): round(drop_in.get(n, 0.0), 3), fm('asof'): today, '출처': source,
                      '고정관저': (pro.get('FIXED_src') or {}).get(n), 'geometry': Point(xy)})
    gm = gpd.GeoDataFrame(mrows, geometry='geometry', crs=crs)
    fu = lambda lg: fname(cfg, 'plan_pump', lg)
    urows = []
    for p in pumps:
        xy = net['node'].get(p['펌프장'])
        d = {fu('pump_id'): p['펌프장'], fu('kind'): p['구분'], fu('discharge_to'): p['토출대상'],
             fu('flow_m3d'): p['누가유량_m3d'], fu('q_m3min'): p['적용용량_m3min'], fu('head_m'): p['전양정H_m'],
             fu('power_kw'): p['적용동력_kW'], fu('force_main_m'): p['압송연장_m'], fu('in_invert'): p['유입관저고'],
             fu('asof'): today, '출처': source}
        d.update({k: v for k, v in p.items() if k not in ('펌프장', '구분', '토출대상', '누가유량_m3d', '적용용량_m3min',
                                                         '전양정H_m', '적용동력_kW', '압송연장_m', '유입관저고')})
        d['geometry'] = Point(xy) if xy else None
        urows.append(d)

    def lname(key):
        base = cfg.layer(key)['layer'] if cfg.has_layer(key) else STD_LAYER[key]
        return base if (a.to_config and a.overwrite) else base + a.suffix

    def target(key):
        if a.to_config:
            L = cfg.layer(key) if cfg.has_layer(key) else None
            if not L or L['kind'] != 'gpkg':
                raise SystemExit('--to-config 는 GPKG 레이어만 된다(layers.%s)' % key)
            return Path(L['path'])
        return Path(a.gpkg) if a.gpkg else C.path('route_design.gpkg')

    import pyogrio
    written = []
    for key, df in (('plan_pipe', gp), ('plan_manhole', gm), ('plan_pump', gpd.GeoDataFrame(urows, geometry='geometry', crs=crs) if urows else None)):
        if df is None or not len(df):
            continue
        tp, ln = target(key), lname(key)
        if a.to_config and a.overwrite and tp.exists():
            have = [n for n, _ in pyogrio.list_layers(tp)]
            if ln in have:
                bak = '%s_bak_%s' % (ln, datetime.date.today().strftime('%y%m%d'))
                old = gpd.read_file(tp, layer=ln)
                old.to_file(tp, layer=bak, driver='GPKG')
                print('  백업 %s → %s (%d행)' % (ln, bak, len(old)))
        df.to_file(tp, layer=ln, driver='GPKG')
        written.append('%s:%s(%d)' % (tp.name, ln, len(df)))
    print('GPKG %s' % ', '.join(written))

    # ── CSV
    def wcsv(name, rows_):
        if not rows_:
            return
        keys = list(dict.fromkeys(k for r in rows_ for k in r))
        with io.open(C.path(name), 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows_)
    wcsv('구간.csv', list(res.values()))
    wcsv('맨홀.csv', [{k: v for k, v in r.items() if k != 'geometry'} for r in mrows])
    wcsv('압송_구배구간.csv', [dict(펌프장=nm, **s) for nm, c in chains.items() for s in c.get('segs', [])])
    wcsv('펌프장.csv', pumps)
    print('CSV 구간·맨홀·압송_구배구간·펌프장')

    # ── makesw .flo
    if not a.no_flo:
        tpl = a.flo_template or cfg.get('route_design.flo_template')
        header, param = DEF_HEADER, DEF_PARAM
        if tpl:
            F = flospec.read_flo(str(cfg.path_of(tpl)))
            header, param = F['header'], F['param']
        else:
            print('  ! .flo 2행(강우강도식 계수)은 기본값 — 과업 지역 값으로: --flo-template 또는 route_design.flo_template')
        recs = []
        for pid, r in res.items():
            recs.append(dict(up=r['상류맨홀'], dn=r['하류맨홀'], L=r['연장'], ilu=r['상류관저고'], ild=r['하류관저고'],
                             gls=[r['상류지반고'] if r['상류지반고'] is not None else 0.0], D=r['관경']))
        if not a.gravity_only:
            for nm, ch in chains.items():
                segs, pts = ch['segs'], ch['pts']
                dn_final = net['force'][nm]['하류맨홀']
                Dflo = a.flo_force_d or int(round(ch['D'] * 1000))
                for k, sg in enumerate(segs):
                    up = nm if k == 0 else '%s%02d' % (nm, k)
                    dn = dn_final if k == len(segs) - 1 else '%s%02d' % (nm, k + 1)
                    gla = gl_at(pts, sg['a'])
                    gls = [gla] if k < len(segs) - 1 else [gla, gl_at(pts, sg['b']), gl_at(pts, sg['b'])]
                    recs.append(dict(up=up, dn=dn, L=sg['L'], ilu=sg['il_a'], ild=sg['il_b'], gls=gls, D=Dflo))
        ren = dict(kv.split('=', 1) for kv in a.outfall)
        ups = {r['up'] for r in recs}
        outs = sorted({r['dn'] for r in recs} - ups)
        if a.outfall_auto:
            for i, n in enumerate(outs):
                ren.setdefault(n, '방류-%d' % (100 + 2 * i))
        for r in recs:
            r['dn'] = ren.get(r['dn'], r['dn'])
            r['Q'] = load.get(r['up'], 0.0) / 86400.0
        recs = flospec.topo_sort(recs)
        n = a.flo_n if a.flo_n is not None else P['manning_n']
        fpath = C.path(a.flo_name)
        try:
            flospec.write_flo(str(fpath), header, param, recs, n=n)
            F = flospec.read_flo(str(fpath))
            f_, outf = flospec.validate(F)
            print('.flo %s — %d줄 · 방류구 %s · 자체유량 합 %.2f ㎥/일 · ERROR %d WARN %d'
                  % (fpath.name, len(recs), outf, sum(r['Q'] for r in recs) * 86400,
                     sum(1 for x in f_ if x[0] == 'ERROR'), sum(1 for x in f_ if x[0] == 'WARN')))
        except ValueError as e:
            print('  ★ .flo 생성 검증 실패 — 쓰지 않았다: %s' % e)

    # ── SWMM .inp
    if a.inp:
        write_inp(C, res, GL, net['node'], load)

    # ── DXF
    if a.dxf:
        write_dxf(C, cfg, res, net, a.dxf_template)


def write_inp(C, res, GL, node, load, rough=None, min_depth=0.30, of_suffix='_OF', of_len=1.0):
    """원 SWMM 생성기 방식: 맨홀 바닥 = 물리는 관저 최솟값, LINK_OFFSETS=ELEVATION(단차가 그대로 표현),
    말단마다 1 m 더미 유출관 + 전용 방류구(SWMM ERROR 141 회피). 압송은 넣지 않는다(펌프장 = 방류구)."""
    rough = rough or C.P['manning_n']
    pipes = list(res.values())
    ups = {p['상류맨홀'] for p in pipes}
    dns = {p['하류맨홀'] for p in pipes}
    nodes = ups | dns
    outfalls = sorted(dns - ups)
    inv = {}
    for p in pipes:
        for k, key in (('상류맨홀', '상류관저고'), ('하류맨홀', '하류관저고')):
            n, v = p[k], float(p[key])
            inv[n] = v if n not in inv else min(inv[n], v)
    junc = [(n, inv[n], max(float(GL[n]) - inv[n], min_depth) if GL.get(n) is not None else min_depth) for n in sorted(nodes)]
    outf = [(n + of_suffix, inv[n] - 0.02) for n in outfalls]
    cond = [dict(name=p['관번호'], u=p['상류맨홀'], d=p['하류맨홀'], L=float(p['연장']), ou=float(p['상류관저고']),
                 od=float(p['하류관저고']), D=float(p['관경']) / 1000.0) for p in sorted(pipes, key=lambda r: r['관번호'])]
    dmax = collections.defaultdict(float)
    for p in pipes:
        dmax[p['하류맨홀']] = max(dmax[p['하류맨홀']], float(p['관경']) / 1000.0)
    for n in outfalls:
        cond.append(dict(name=n + of_suffix, u=n, d=n + of_suffix, L=of_len, ou=inv[n], od=inv[n] - 0.02, D=dmax.get(n, 0.2)))
    dwf = {n: load[n] / 86400.0 for n in sorted(nodes) if n in load}
    L = []
    w = L.append
    w('[TITLE]')
    w('30-route-design %s — 자연유하 신설망 (makesw 대조용)' % C.dir.name)
    w('')
    w('[OPTIONS]')
    for k, v in (('FLOW_UNITS', 'CMS'), ('INFILTRATION', 'HORTON'), ('FLOW_ROUTING', 'DYNWAVE'),
                 ('LINK_OFFSETS', 'ELEVATION'), ('MIN_SLOPE', '0'), ('ALLOW_PONDING', 'NO'),
                 ('SKIP_STEADY_STATE', 'NO'), ('START_DATE', '01/01/2026'), ('START_TIME', '00:00:00'),
                 ('REPORT_START_DATE', '01/01/2026'), ('REPORT_START_TIME', '00:00:00'),
                 ('END_DATE', '01/02/2026'), ('END_TIME', '00:00:00'),
                 ('REPORT_STEP', '00:15:00'), ('WET_STEP', '00:05:00'), ('DRY_STEP', '01:00:00'),
                 ('ROUTING_STEP', '0:00:15'), ('INERTIAL_DAMPING', 'PARTIAL'),
                 ('NORMAL_FLOW_LIMITED', 'BOTH'), ('FORCE_MAIN_EQUATION', 'H-W'),
                 ('VARIABLE_STEP', '0.75'), ('LENGTHENING_STEP', '0'), ('MIN_SURFAREA', '1.167'),
                 ('MAX_TRIALS', '8'), ('HEAD_TOLERANCE', '0.0015'), ('THREADS', '1')):
        w('%-22s %s' % (k, v))
    w('')
    w('[JUNCTIONS]')
    w(';;Name           Elevation  MaxDepth   InitDepth  SurDepth   Aponded')
    for n, z, dep in junc:
        w('%-16s %-10.3f %-10.3f %-10.3f %-10.3f %-10.3f' % (n, z, dep, 0, 0, 0))
    w('')
    w('[OUTFALLS]')
    w(';;Name           Elevation  Type       Gated')
    for n, z in outf:
        w('%-16s %-10.3f %-10s %-8s' % (n, z, 'FREE', 'NO'))
    w('')
    w('[CONDUITS]')
    w(';;Name           FromNode         ToNode           Length     Roughness  InOffset   OutOffset')
    for c in cond:
        w('%-16s %-16s %-16s %-10.2f %-10.4f %-10.3f %-10.3f' % (c['name'], c['u'], c['d'], c['L'], rough, c['ou'], c['od']))
    w('')
    w('[XSECTIONS]')
    w(';;Link           Shape        Geom1      Geom2      Geom3      Geom4      Barrels')
    for c in cond:
        w('%-16s %-12s %-10.3f %-10.1f %-10.1f %-10.1f %-8d' % (c['name'], 'CIRCULAR', c['D'], 0, 0, 0, 1))
    w('')
    w('[DWF]')
    w(';;Node           Constituent      Baseline   Patterns')
    for n, q in sorted(dwf.items()):
        w('%-16s %-16s %-10.8f' % (n, 'FLOW', q))
    w('')
    w('[REPORT]')
    for k, v in (('INPUT', 'NO'), ('CONTROLS', 'NO'), ('SUBCATCHMENTS', 'NONE'), ('NODES', 'ALL'), ('LINKS', 'ALL')):
        w('%-19s %s' % (k, v))
    w('')
    w('[COORDINATES]')
    w(';;Node           X-Coord            Y-Coord')
    for n in [j[0] for j in junc] + [o[0] for o in outf]:
        xy = node.get(n)
        if xy is None and n.endswith(of_suffix):
            b = node.get(n[:-len(of_suffix)])
            xy = (b[0] + of_len, b[1]) if b else None
        if xy:
            w('%-16s %-18.3f %-18.3f' % (n, xy[0], xy[1]))
    w('')
    p = C.path('route_design.inp')
    io.open(p, 'w', encoding='utf-8', newline='\n').write('\n'.join(L))
    adv = [c['name'] for c in cond if c['od'] > c['ou'] + 0.001]
    print('.inp %s — JUNCTIONS %d / OUTFALLS %d / CONDUITS %d / DWF %d(합 %.2f ㎥/일) / 역구배 %d'
          % (p.name, len(junc), len(outf), len(cond), len(dwf), sum(dwf.values()) * 86400, len(adv)))


def write_dxf(C, cfg, res, net, template=None, trim=1.6, off=3.8, th=2.8):
    """원 노선 DXF 내보내기 방식: 자연유하는 맨홀 심볼 반경만큼 양끝 트림, 맨홀 = 블록(또는 원) 회전 = 유출 방향,
    관로명 = 관번호를 관 중점에서 진행방향 왼쪽 off m, 글자높이 th."""
    import ezdxf
    from shapely.geometry import LineString
    from shapely.ops import substring
    LY = dict(gravity='계획관로_자연유하', force='계획관로_압송', manhole='계획맨홀', label='관로명', block=None)
    LY.update(cfg.get('route_design.dxf_layers') or {})
    tpl = template or cfg.get('route_design.dxf_template')
    if tpl:
        doc = ezdxf.readfile(str(cfg.path_of(tpl)))
        msp = doc.modelspace()
        for e in list(msp):
            msp.delete_entity(e)
    else:
        doc = ezdxf.new('R2018', setup=True)
        msp = doc.modelspace()
    for k in ('gravity', 'force', 'manhole', 'label'):
        if LY[k] not in doc.layers:
            doc.layers.add(LY[k], color={'gravity': 1, 'force': 5, 'manhole': 3, 'label': 7}[k])
    outdir = collections.defaultdict(list)
    items = [(r['관번호'], r['상류맨홀'], LineString(net['seg'][r['관번호']]['coords']), False) for r in res.values()]
    items += [(nm, nm, LineString(c['pts']), True) for nm, c in net['force'].items()]
    nt = 0
    for pid, up, g, pump in items:
        cs = list(g.coords)
        ang = math.degrees(math.atan2(cs[-1][1] - cs[0][1], cs[-1][0] - cs[0][0])) % 360
        outdir[up].append(ang)
        drawn = g
        if not pump and g.length > 2 * trim + 1.0:
            drawn = substring(g, trim, g.length - trim)
            nt += 1
        msp.add_lwpolyline([(x, y) for x, y, *_ in drawn.coords], dxfattribs={'layer': LY['force' if pump else 'gravity']})
        mid = g.interpolate(g.length / 2)
        p1, p2 = g.interpolate(max(g.length / 2 - 1, 0)), g.interpolate(min(g.length / 2 + 1, g.length))
        vx, vy = p2.x - p1.x, p2.y - p1.y
        nn = math.hypot(vx, vy) or 1
        t = msp.add_text(str(pid), height=th, dxfattribs={'layer': LY['label'], 'rotation': ang})
        t.set_placement((mid.x - vy / nn * off, mid.y + vx / nn * off), align=ezdxf.enums.TextEntityAlignment.CENTER)
    nodes = {r['상류맨홀'] for r in res.values()} | {r['하류맨홀'] for r in res.values()} | set(net['force'])
    nb = 0
    for n in nodes:
        xy = net['node'].get(n)
        if not xy:
            continue
        ang = outdir[n][0] if outdir.get(n) else 0.0
        if LY.get('block') and LY['block'] in doc.blocks:
            msp.add_blockref(LY['block'], xy, dxfattribs={'layer': LY['manhole'], 'rotation': ang})
        else:
            msp.add_circle(xy, trim, dxfattribs={'layer': LY['manhole']})
        nb += 1
    p = C.path('route_design.dxf')
    doc.saveas(p)
    print('DXF %s — 관로 %d(트림 %d) · 맨홀 %d · 관로명 %d' % (p.name, len(items), nt, nb, len(items)))


if __name__ == '__main__':
    main()
