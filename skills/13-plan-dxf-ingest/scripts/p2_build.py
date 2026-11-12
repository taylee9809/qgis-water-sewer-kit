# -*- coding: utf-8 -*-
"""13 단계 2 — 레코드 구축 (평면 DXF 스캔 + makesw .flo/.OUT → 노선·계획맨홀·맨홀펌프장 레코드).

두 경로
  ① attrib  : 평면도에 속성맨홀 블록(ATTRIB 로 상류·하류맨홀·관저고·지반고·관경·관종·맨홀 호수)이 있을 때.
              관로 도형 = 상류노드 → 하류노드 직선. 속성은 평면 ATTRIB, 연장·유량은 flo, 수리값은 OUT.
  ② polyline: 속성 블록이 없는 경량 평면도. 관로 폴리라인 + 관로명 TEXT 를 전역최적배정으로 짝짓고,
              끝점을 맨홀 심볼에 스냅해 노드를 만든다. 속성은 전부 flo/OUT.
  --mode auto(기본) 는 속성맨홀 이름이 금회 flo 구간의 절반 이상과 겹치면 attrib, 아니면 polyline.

교차검증: 평면↔flo 관번호·하류맨홀·관경·관저고·지반고, 도형↔flo 연장, 종단 DXF 로 flo↔도면 번호 별칭 확인.
산출: build.json(레코드) + build_checks.csv(불일치·주의 목록). 쓰기는 p3_apply.py.

    python p2_build.py -d YYMMDD --prefix XX                     # 납품 폴더 하나로(스캔이 없으면 먼저 스캔)
    python p2_build.py -d YYMMDD --prefix XX --district OO --asof 2026-01-31 --source "설계사 3차"
    python p2_build.py --dxf 평면.dxf --flo A.flo --flo B.flo --out A.OUT --out B.OUT --district-by-flo A.flo=OO
"""
import argparse, collections, math, re, sys
from pathlib import Path

import ingestlib as L


# ───────────────────────────────────────────── 공통

class Ctx:
    pass


def resolve(a):
    cfg, C = L.load(a.config)
    X = Ctx()
    X.cfg, X.C, X.a = cfg, C, a
    dv = L.find_delivery(cfg, a.delivery) if (a.delivery or not (a.dxf or a.scan)) else None
    ins = L.collect_inputs(cfg, C, dv) if dv else {'plan': [], 'profile': [], 'flo': [], 'out': []}
    X.delivery = dv
    X.dxf = Path(a.dxf) if a.dxf else (ins['plan'][0] if ins['plan'] else None)
    X.flo = [Path(p) for p in a.flo] if a.flo else ins['flo']
    X.out = [] if a.no_out else ([Path(p) for p in a.out] if a.out else ins['out'])
    X.profile = Path(a.profile) if a.profile else (ins['profile'][0] if ins['profile'] else None)
    name = a.outdir or (dv.name if dv else (X.dxf.stem if X.dxf else Path(a.scan).parent.name))
    X.od = L.outdir(cfg, name)
    if not X.flo:
        raise SystemExit('.flo 가 없다 — --flo 로 주거나 납품 폴더를 확인할 것.')
    return X


def get_scan(X):
    a = X.a
    if a.scan:
        return L.load_json(a.scan)
    p = X.od / 'scan.json'
    if p.exists() and not a.rescan:
        S = L.load_json(p)
        if S.get('dxf_conf') != X.C['dxf']:
            print('plan_ingest.dxf 설정이 스캔 때와 달라 다시 스캔한다')
        elif X.dxf is None or Path(S['path']) == X.dxf:
            print('스캔 재사용 %s (%s)' % (p, S.get('scanned')))
            return S
    if X.dxf is None:
        raise SystemExit('평면 DXF 가 없다 — --dxf 또는 -d 로 줄 것.')
    import p1_scan
    return p1_scan.run_scan(X.cfg, X.C, X.dxf, X.od)


def setup_naming(X):
    a, C = X.a, X.C
    X.prefixes = a.prefix or C.get('prefixes') or []
    X.dist_by_flo = dict(kv.split('=', 1) for kv in (a.district_by_flo or []))
    X.area_by_flo = dict(kv.split('=', 1) for kv in (a.area_by_flo or []))
    X.KG = X.cfg.value('kind_gravity', '자연유하')
    X.KP = X.cfg.value('kind_pressure', '압송')
    X.KE = X.cfg.value('kind_existing', '기존')
    X.pmax = X.cfg.get('design.pressure_max_d_mm', 100)
    X.asof = a.asof or L.now()[:10]
    X.source = a.source or ('%s 평면도·%s' % (X.delivery.name if X.delivery else (X.dxf.name if X.dxf else ''),
                                             '·'.join(p.name for p in X.flo + X.out)))
    X.T = C['tolerance']


def district_of(X, name, flo_file=None):
    if flo_file and flo_file in X.dist_by_flo:
        return X.dist_by_flo[flo_file]
    v = L.by_prefix(name, X.C.get('district_by_prefix'))
    return v if v is not None else (X.a.district or X.C.get('district'))


def area_of(X, name, flo_file=None):
    if flo_file and flo_file in X.area_by_flo:
        return X.area_by_flo[flo_file]
    v = L.by_prefix(name, X.C.get('area_by_prefix'))
    return v if v is not None else (X.a.area or X.C.get('area'))


def is_pressure_d(X, rec):
    d = L.fnum((rec.get('D') or '').split()[0] if rec.get('D') else None)
    return d is not None and d <= X.pmax


def obstacle_pool(X, S):
    """기타데이터 블록(종단 지장물 속성) → 관로명별 목록. 도면 이중 작도는 좌표 제외 속성키로 1개만."""
    ob = X.C['dxf'].get('obstacle_block')
    tg = X.C['obstacle_tags']
    ETC = collections.defaultdict(list)
    for r in S['attribs']:
        if r['block'] == ob:
            ETC[r['attrs'].get(tg['pipe'], '')].append(r)
    for k in list(ETC):
        seen, u = set(), []
        for r in ETC[k]:
            at = r['attrs']
            key = (at.get(tg['kind']), at.get(tg['dia']), at.get(tg['box']), at.get(tg['invert']))
            if key not in seen:
                seen.add(key)
                u.append(r)
        ETC[k] = u
    return ETC


def join_obstacles(X, ETC, up, toks, stat, unmatched):
    """flo 지장물 토큰(측점·저면EL) ↔ 평면 기타데이터(관종·관경·관저고) 결합.
    원형관은 저면EL+0.30 = 관저고(makesw 규약, ±0.12), 사각형은 관저고−저면EL 0.2~0.95 중 0.5 에 가까운 것."""
    tg = X.C['obstacle_tags']
    real = [t for t in toks if t['pre'] != 'G']
    pool = list(ETC.get(up, []))
    used, items = set(), []
    for t in sorted(real, key=lambda t: t['측점']):
        best, bd = None, None
        for i, e in enumerate(pool):
            if i in used:
                continue
            il = L.fnum(e['attrs'].get(tg['invert']))
            if il is None:
                continue
            dd = abs(il - (t['저면EL'] + 0.30))
            if bd is None or dd < bd:
                best, bd = i, dd
        pick = None
        if best is not None and bd <= 0.12:
            pick = best
            stat['EL매칭(원형)'] += 1
        else:
            b2, d2 = None, None
            for i, e in enumerate(pool):
                if i in used:
                    continue
                at = e['attrs']
                if not (at.get(tg['kind']) or at.get(tg['box'])):
                    continue
                il = L.fnum(at.get(tg['invert']))
                if il is None:
                    continue
                gap = il - t['저면EL']
                if 0.2 <= gap <= 0.95 and (d2 is None or abs(gap - 0.5) < d2):
                    b2, d2 = i, abs(gap - 0.5)
            if b2 is not None:
                pick = b2
                stat['EL매칭(사각)'] += 1
        if pick is not None:
            used.add(pick)
            at = pool[pick]['attrs']
            kind = at.get(tg['kind']) or '지장물'
            spec = at.get(tg['dia']) or at.get(tg['box']) or ''
        else:
            kind, spec = '미기재', ''
            stat['미매칭'] += 1
            unmatched.append((up, t['측점'], t['저면EL'], t['크기']))
        items.append(dict(측점=t['측점'], 저면EL=t['저면EL'], 크기=t['크기'], 종류=kind, 규격=spec))
    return items


def obstacle_text(items):
    return ' / '.join('%s%s@%.1f(%.2f)' % (it['종류'], (' D%s' % it['규격']) if it['규격'] else '',
                                           it['측점'], it['저면EL'])
                      for it in sorted(items, key=lambda x: x['측점'])) or None


# ───────────────────────────────────────────── ① 속성맨홀 경로

def build_attrib(X, S, FLO, OUT, checks):
    C, T, tol = X.C, X.C['attrib'], X.T
    ab = [a for a in S['attribs'] if a['block'] == C['dxf']['attrib_block']]
    ALL = collections.defaultdict(list)
    for a in ab:
        n = (a['attrs'].get(T['up']) or '').strip()
        if n:
            ALL[n].append(a)

    def dn_of(at):
        for tag in T['down'] if isinstance(T['down'], list) else [T['down']]:
            v = (at.get(tag) or '').strip()
            if v:
                return v
        return None

    SEG, fb = {}, collections.Counter()
    for n, lst in ALL.items():
        if not L.match_prefix(n, X.prefixes):
            continue
        t = lst[0]['attrs']
        r = FLO.get(n)
        raw = dict(D=L.fnum(t.get(T['dia'])), GL=L.fnum(t.get(T['ground'])),
                   ILu=L.fnum(t.get(T['us_invert'])), ILd=L.fnum(t.get(T['ds_invert'])))
        v = dict(raw)
        if r:                                         # ATTRIB 가 비었을 때만 flo 로 메운다
            fv = dict(D=L.fnum(r['D'].split()[0]) if r.get('D') else None,
                      GL=L.fnum(r['gls'][0]) if r['gls'] else None,
                      ILu=L.fnum(r['ilu']), ILd=L.fnum(r['ild']))
            for k in ('D', 'GL', 'ILu', 'ILd'):
                if v[k] is None and fv[k] is not None:
                    v[k] = fv[k]
                    fb[k] += 1
        kraw = (t.get(T['kind']) or '').strip()
        sem = C['kind_map'].get(kraw)
        if sem is None:
            sem = 'pressure' if (v['D'] is not None and v['D'] <= X.pmax) else 'gravity'
            fb['관종→관경판정'] += 1
        SEG[n] = dict(name=n, dn=dn_of(t), kind=sem, kind_raw=kraw, mh=(t.get(T['mh_type']) or '').strip(),
                      x=lst[0]['x'], y=lst[0]['y'], raw=raw, copies=[(q['x'], q['y']) for q in lst], recs=lst, **v)
    dup = {n: len(s['copies']) for n, s in SEG.items() if len(s['copies']) > 1}
    print('속성맨홀 %d (금회 접두 %s → %d, 같은 이름 중복 %d)%s'
          % (len(ab), X.prefixes or '전체', len(SEG), len(dup), (' ATTRIB 빈칸을 flo 로 메움 %s' % dict(fb)) if fb else ''))
    for n, c in sorted(dup.items()):
        checks.append(('속성맨홀중복', n, c, '', '하류노드로 쓸 때는 상류에 가까운 것, 상류노드는 첫 번째'))
    print('  관종:', collections.Counter(s['kind_raw'] for s in SEG.values()).most_common())
    print('  맨홀:', collections.Counter(s['mh'] for s in SEG.values()).most_common())

    # ── 평면 ↔ flo 교차검증 (접두를 안 주면 flo 관번호의 첫 토큰을 금회 범위로 본다 — 보고용)
    flo_t = {n for n in FLO if L.match_prefix(n, X.prefixes)}
    tok = {n.split('-')[0] for n in FLO}

    def scope(n):
        return L.match_prefix(n, X.prefixes) if X.prefixes else n.split('-')[0] in tok

    bad = []
    for n, s in sorted(SEG.items()):
        r = FLO.get(n)
        if not r:
            if scope(n):
                bad.append(('flo미수록', n, '', ''))
            continue
        if s['dn'] != r['dn']:
            bad.append(('하류불일치', n, s['dn'], r['dn']))
        fD = L.fnum((r['D'] or '').split()[0]) if r.get('D') else None
        for lab, a_, b_ in (('관경', s['raw']['D'], fD), ('관저고1', s['raw']['ILu'], L.fnum(r['ilu'])),
                            ('관저고2', s['raw']['ILd'], L.fnum(r['ild'])),
                            ('지반고', s['raw']['GL'], L.fnum(r['gls'][0]) if r['gls'] else None)):
            if a_ is not None and b_ is not None and abs(a_ - b_) > tol['attr']:
                bad.append((lab + '불일치', n, a_, b_))
    flo_only = sorted(flo_t - set(SEG))
    print('평면↔flo 대조 불일치 %d건 / flo 에만 있는 금회 구간 %d' % (len(bad), len(flo_only)))
    nf = [b[1] for b in bad if b[0] == 'flo미수록']
    if nf:
        grp = collections.Counter(n.rsplit('-', 1)[0] for n in nf)
        print('   flo미수록(평면 속성맨홀에만) %d — 계열별 %s' % (len(nf), grp.most_common(12)))
        print('     (다른 회차·기존망이면 정상. 금회 것이면 flo 누락 — 설계사 확인)')
    for b in [b for b in bad if b[0] != 'flo미수록'][:20]:
        print('   !', b)
    for b in bad:
        checks.append(('평면↔flo ' + b[0], b[1], b[2], b[3], ''))
    for n in flo_only:
        checks.append(('flo에만', n, FLO[n]['dn'], FLO[n]['L'], '평면 속성맨홀에 없음 — 도형 없음'))
    alias_candidates(X, SEG, FLO, flo_only, checks)

    P = sorted(n for n in SEG if n in FLO)

    # ── 금회 밖 하류노드 좌표
    ext_layers = set(C['dxf'].get('ext_node_layers') or C['dxf']['pipe_layers'])
    PL = [g for g in S['pipes'] if g['layer'] in ext_layers]
    roles = C['dxf']['mh_layers']
    EXMH = [(m['x'], m['y']) for m in S['symbols'] if roles.get(m['layer']) == 'existing']

    def by_polyline(n, p0, Lf):
        cand = []
        for g in PL:
            for a_, b_ in ((g['pts'][0], g['pts'][-1]), (g['pts'][-1], g['pts'][0])):
                if math.dist(a_, p0) > tol['node_m']:
                    continue
                Lg = math.dist(a_, b_)
                if not Lg:
                    continue
                P_ = (p0[0] + (b_[0] - a_[0]) / Lg * Lf, p0[1] + (b_[1] - a_[1]) / Lg * Lf)
                ds, q = min((math.dist(P_, m), m) for m in EXMH) if EXMH else (None, None)
                cand.append((ds if ds is not None else 9e9, P_, q))
        if not cand:
            return None
        ds, P_, q = min(cand)
        if ds <= tol['node_m']:
            return (round(q[0], 4), round(q[1], 4)), '기존맨홀 심볼 스냅 %.2f m' % ds
        return (round(P_[0], 4), round(P_[1], 4)), '심볼 미발견(최근접 %.1f m) — 연장추정' % ds

    def by_attrib(dn, p0):
        lst = ALL.get(dn)
        if not lst:
            return None
        q = min(((c['x'], c['y']) for c in lst), key=lambda c: math.dist(c, p0))
        return (q[0], q[1]), '속성맨홀 좌표(상류에서 %.1f m)' % math.dist(q, p0)

    XY, ext = {}, {}
    for n in P:
        dn = SEG[n]['dn']
        if dn is None or dn in SEG or dn in ext:
            continue
        p0 = (SEG[n]['x'], SEG[n]['y'])
        Lf = float(FLO[n]['L'])
        order = (by_attrib, by_polyline) if X.ext_node == 'attrib' else (by_polyline, by_attrib)
        got = None
        for fn in order:
            got = fn(n, p0, Lf) if fn is by_polyline else fn(dn, p0)
            if got:
                break
        if got:
            ext[dn] = got[0]
            print('금회 밖 하류노드 %-14s ← %s (flo %.1f m) : %s' % (dn, n, Lf, got[1]))
            checks.append(('금회밖하류노드', dn, n, '%.4f,%.4f' % got[0], got[1]))
        else:
            print('   ! 금회 밖 하류노드 %s ← %s : 좌표 못 구함(폴리라인·속성맨홀 모두 없음)' % (dn, n))
            checks.append(('하류노드좌표없음', dn, n, '', '도형이 한 점뿐 — 확인'))

    def xy_of(name, near=None):
        if name in SEG:
            cp = SEG[name]['copies']
            if near is not None and len(cp) > 1:
                return min(cp, key=lambda c: math.dist(c, near))
            return cp[0]
        return ext.get(name)

    # ── 압송 체인 (맨홀펌프장마다 다음 펌프장·자연유하·금회 밖에서 끊는다 — 2단 압송)
    def role(n):
        return (C['mh_types'].get(SEG[n]['mh']) or {}).get('role') if n in SEG else None

    PUMPS = sorted(n for n in SEG if role(n) == 'pump' and scope(n))
    chains, inchain = {}, set()
    for pn in PUMPS:
        if pn not in FLO:
            checks.append(('펌프장flo없음', pn, '', '', '맨홀펌프장인데 flo 구간이 없다'))
            continue
        ch, cur = [], pn
        while True:
            ch.append(cur)
            nx = SEG[cur]['dn']
            if nx not in SEG or nx not in FLO or SEG[nx]['kind'] != 'pressure' or role(nx) == 'pump':
                break
            cur = nx
        chains[pn] = ch
        inchain.update(ch)
    allp = {n for n in P if SEG[n]['kind'] == 'pressure'}
    orphan = sorted(allp - inchain)
    print('\n맨홀펌프장 %d: %s' % (len(PUMPS), PUMPS))
    for pn, ch in chains.items():
        print('  %-14s %2d구간 → 토출 %s' % (pn, len(ch), SEG[ch[-1]]['dn']))
    print('  압송 구간 %d / 체인에 담긴 것 %d / 체인 밖 %s' % (len(allp), len(allp & inchain), orphan or '없음'))
    for n in orphan:
        checks.append(('체인밖압송', n, SEG[n]['dn'], '', '펌프장에서 이어지지 않는 압송 구간 — 개별 행으로 둠'))

    ETC = obstacle_pool(X, S)
    ostat, ounm = collections.Counter(), []
    fold = X.pressure == 'fold'

    def obs(n, offset=0.0):
        it = join_obstacles(X, ETC, n, L.flo_tokens(FLO[n]), ostat, ounm)
        for x in it:
            x['측점'] = round(x['측점'] + offset, 2)
        return it

    # ── 노선 레코드
    pipes = []
    for n in P:
        s, r = SEG[n], FLO[n]
        if fold and s['kind'] == 'pressure' and n in inchain and n not in chains:
            continue                                   # 펌프행에 흡수
        chain_row = fold and n in chains
        items = []
        if chain_row:
            ch = chains[n]
            Lf = round(sum(float(FLO[k]['L']) for k in ch), 1)
            ILu, ILd = SEG[ch[0]]['ILu'], SEG[ch[-1]]['ILd']
            prof, acc = [], 0.0
            for k in ch:
                prof.append('%.2f:%.2f' % (acc, SEG[k]['ILu']))
                items += obs(k, acc)
                acc += float(FLO[k]['L'])
            prof.append('%.2f:%.2f' % (acc, ILd))
            profile = ';'.join(prof)
            pts, prev = [], None
            for k in ch + [SEG[ch[-1]]['dn']]:
                q = xy_of(k, prev)
                if q:
                    pts.append(q)
                    prev = q
            slope = round((ILd - ILu) / Lf * 1000, 2) if (Lf and ILu is not None and ILd is not None) else None
            kind, dn = X.KP, SEG[ch[-1]]['dn']
        else:
            Lf = float(r['L'])
            ILu, ILd = s['ILu'], s['ILd']
            profile = None
            p0 = xy_of(n)
            pts = [p for p in (p0, xy_of(s['dn'], p0)) if p]
            slope = round((ILu - ILd) / Lf * 1000, 2) if (Lf and ILu is not None and ILd is not None) else None
            kind = {'pressure': X.KP, 'existing': X.KE}.get(s['kind'], X.KG)
            dn = s['dn']
            items = obs(n)
        D, GL = s['D'], s['GL']
        h = L.hyd(OUT, n, null=(kind == X.KP and X.pressure_hyd == 'null'))
        Lg = round(L.polylen(pts), 2) if len(pts) > 1 else None
        pipes.append(dict(
            area=area_of(X, n, r['_file']), district=district_of(X, n, r['_file']), kind=kind, pipe_id=n,
            us_mh=n, ds_mh=dn, length_m=Lf, dia_mm=D, us_invert=ILu, ds_invert=ILd, us_ground=GL,
            slope_permil=slope,
            us_cover=round(GL - ILu - (D or 0) / 1000, 2) if (GL is not None and ILu is not None) else None,
            self_flow_m3d=round(float(r['Q']) * 86400, 2) if r.get('Q') else 0.0,
            flow_m3d=h['flow_m3d'], velocity_mps=h['velocity_mps'], judge=h['judge'],
            full_flow_m3d=h['full_flow_m3d'], flow_depth_m=h['flow_depth_m'], depth_ratio_pct=h['depth_ratio_pct'],
            pressure_profile=profile, obstacles=obstacle_text(items), obstacle_count=len(items),
            geom_length_m=Lg, length_diff_m=round(Lg - Lf, 2) if Lg is not None else None,
            flo_name=(r['flo_up'] if r['flo_up'] != n else None),
            asof=X.asof, source=X.source, _geom=[list(p) for p in pts]))
    if ostat or ounm:
        print('지장물 토큰 결합: %s' % dict(ostat))
        for u in ounm:
            checks.append(('지장물미매칭', u[0], u[1], u[2], '크기 %.2f — 평면 기타데이터에 짝 없음' % u[3]))
    for p in pipes:
        if p['flo_name']:
            checks.append(('번호별칭', p['pipe_id'], p['flo_name'], '', 'flo 번호 → 도면 번호(--rename) 채택'))

    # ── 계획맨홀 (금회 노선에 걸린 노드만)
    inflow = collections.defaultdict(list)
    for n in P:
        if SEG[n]['dn'] and SEG[n]['ILd'] is not None:
            inflow[SEG[n]['dn']].append(SEG[n]['ILd'])
    upstart = {}
    for p in pipes:
        upstart.setdefault(p['ds_mh'], p)
    nodes = []
    for n in P:
        for k in (n, SEG[n]['dn']):
            if k and k not in nodes:
                nodes.append(k)
    mhs, skip = [], collections.Counter()
    roles_w = set(C['write_mh_roles'])
    for n in sorted(nodes):
        if n not in SEG:
            continue
        s = SEG[n]
        near = (upstart[n]['_geom'][0] if n in upstart and upstart[n]['_geom'] else None)
        mt, GL, IL, xy = s['mh'], s['GL'], s['ILu'], (s['x'], s['y'])
        if len(s['recs']) > 1 and near is not None:  # 같은 이름이 여럿이면 들어오는 관에 가까운 것의 속성
            q = min(s['recs'], key=lambda c: math.dist((c['x'], c['y']), near))
            at = q['attrs']
            mt, xy = (at.get(T['mh_type']) or '').strip(), (q['x'], q['y'])
            GL, IL = L.fnum(at.get(T['ground'])), L.fnum(at.get(T['us_invert']))
        spec = C['mh_types'].get(mt)
        if not spec or spec.get('role') not in roles_w:
            skip[mt or '(빈칸)'] += 1
            continue
        ins = [v for v in inflow.get(n, []) if v is not None]
        f = FLO[n]['_file'] if n in FLO else (FLO[upstart[n]['pipe_id']]['_file'] if n in upstart else None)
        mhs.append(dict(area=area_of(X, n, f), district=district_of(X, n, f), mh_id=n,
                        kind=C['mh_kind'][spec['role']], mh_type=spec.get('type'),
                        inner_mm=spec.get('inner_mm'), outer_mm=spec.get('outer_mm'), ground=GL, out_invert=IL,
                        depth=round(GL - IL, 2) if (GL is not None and IL is not None) else None,
                        drop=round(min(ins) - IL, 2) if (ins and IL is not None) else None,
                        connect_to=None, note=None, asof=X.asof, source=X.source, _xy=list(xy)))
    print('계획맨홀 %d %s / 제외(호수) %s' % (len(mhs), collections.Counter(m['kind'] for m in mhs).most_common(),
                                         dict(skip)))

    # ── 맨홀펌프장
    pumps = []
    PC = C['pump']
    for pn in sorted(chains):
        ch, s = chains[pn], SEG[pn]
        ils = [SEG[k]['ILu'] for k in ch] + [SEG[ch[-1]]['ILd']]
        ils = [v for v in ils if v is not None]
        ins = [v for v in inflow.get(pn, []) if v is not None]
        uin = min(ins) if ins else None
        Q = L.hyd(OUT, pn)['flow_m3d']
        f = FLO[pn]['_file']
        pumps.append(dict(area=area_of(X, pn, f), district=district_of(X, pn, f), pump_id=pn, mh_id=pn,
                          kind=PC['kind'], discharge_to=SEG[ch[-1]]['dn'], flow_m3d=Q,
                          peak_m3min=round(Q * PC['peak_factor'] / 1440, 4) if Q else None,
                          force_main_m=round(sum(float(FLO[k]['L']) for k in ch), 1), in_invert=uin,
                          depth_m=round(s['GL'] - uin, 2) if (uin is not None and s['GL']) else None,
                          top_bop=round(max(ils), 2) if ils else None, mh_spec=PC['spec'], inner_mm=PC['inner_mm'],
                          basis=PC['basis_note'], asof=X.asof, source=X.source, _xy=[s['x'], s['y']]))
    return pipes, mhs, pumps, {k: v for k, v in chains.items()}


def alias_candidates(X, SEG, FLO, flo_only, checks):
    """flo 에만 있는 번호 ↔ 평면에만 있는 번호가 관저고까지 같으면 별칭 후보(flo 가 옛 번호를 남긴 경우).
    종단 DXF 가 있으면 어느 번호가 종단에 찍혀 있는지로 권고한다. 자동 적용은 안 한다(--rename)."""
    plan_only = [n for n in SEG if n not in FLO]
    if not flo_only or not plan_only:
        return
    tol = X.T['alias_il']
    prof = None
    if X.profile and X.profile.is_file():
        print('종단 DXF 관번호 글자 확인: %s' % X.profile.name)
        prof = L.scan_profile_names(X.profile, X.C)
    for f in flo_only:
        r = FLO[f]
        fu, fd = L.fnum(r['ilu']), L.fnum(r['ild'])
        for p in plan_only:
            s = SEG[p]
            if None in (fu, fd, s['raw']['ILu'], s['raw']['ILd']):
                continue
            if abs(fu - s['raw']['ILu']) <= tol and abs(fd - s['raw']['ILd']) <= tol:
                if prof is None:
                    adv = '종단 DXF 없음 — 종단에 어느 번호가 있는지 확인'
                else:
                    adv = ('종단에 도면번호만 있음 → --rename %s=%s 권고' % (f, p) if (p in prof and f not in prof) else
                           '종단에 flo 번호만 있음 → 평면 번호 오기 의심' if (f in prof and p not in prof) else
                           '종단 판단 불가(둘 다/둘 다 없음)')
                print('   별칭 후보 flo %s ↔ 평면 %s (관저고 일치) — %s' % (f, p, adv))
                checks.append(('별칭후보', p, f, '', adv))


# ───────────────────────────────────────────── ② 폴리라인 경로

def assign(cost):
    try:
        from scipy.optimize import linear_sum_assignment
        return linear_sum_assignment(cost)
    except ImportError:
        print('   (scipy 없음 — 탐욕 배정으로 대신한다. 결과가 다를 수 있으니 pip install scipy 권장)')
        pairs = sorted((c, i, j) for i, row in enumerate(cost) for j, c in enumerate(row))
        ui, uj, ri, ci = set(), set(), [], []
        for c, i, j in pairs:
            if i not in ui and j not in uj:
                ui.add(i)
                uj.add(j)
                ri.append(i)
                ci.append(j)
        return ri, ci


def build_polyline(X, S, FLO, OUT, checks):
    C, tol = X.C, X.T
    roles = C['dxf']['mh_layers']
    prs = set(C['dxf']['pressure_layers'])
    mh = [dict(role=roles[m['layer']], x=float(m['x']), y=float(m['y']), name=None, synth=False)
          for m in S['symbols'] if m['layer'] in roles]
    pl = [dict(pressure=(g['layer'] in prs), pts=[tuple(p) for p in g['pts']])
          for g in S['pipes'] if g['layer'] in set(C['dxf']['pipe_layers'])]
    txt = [t for t in S['labels'] if L.match_prefix(t['name'], X.prefixes)]
    print('DXF: 맨홀 심볼 %d / 관로명 %d / 관로 폴리라인 %d' % (len(mh), len(txt), len(pl)))
    print('  심볼:', collections.Counter(m['role'] for m in mh).most_common())

    # ── 관번호 = 라벨 ↔ 폴리라인 중점 전역최적배정
    MP = [L.midpoint(g['pts']) for g in pl]
    cost = [[math.dist(p, (t['x'], t['y'])) for t in txt] for p in MP]
    ri, ci = assign(cost) if (pl and txt) else ([], [])
    for i, j in zip(ri, ci):
        pl[i]['name'] = txt[j]['name']
        pl[i]['label_d'] = round(cost[i][j], 2)
    nolab = [i for i, g in enumerate(pl) if not g.get('name')]
    ds = sorted(g['label_d'] for g in pl if g.get('name'))
    if ds:
        print('관번호 배정 %d / 라벨거리 중앙 %.2f · 최대 %.2f m' % (len(ds), ds[len(ds) // 2], ds[-1]))
    for i in nolab:
        checks.append(('라벨없는관로', '', '', '%.1f,%.1f' % MP[i], '관로명 라벨이 모자라 이름을 못 붙임 — 제외'))
    pl = [g for g in pl if g.get('name')]
    dupn = [k for k, c in collections.Counter(g['name'] for g in pl).items() if c > 1]
    for k in dupn:
        checks.append(('관번호중복배정', k, '', '', '같은 라벨이 둘 이상의 폴리라인에'))

    BYNAME = {g['name']: g for g in pl}

    def chain_of(up):
        """압송 폴리라인이 펌프장별 1개로 그려졌을 때 flo 세분구간을 따라간다(자기 폴리라인이 있는 노드에서 끊음)."""
        ch, cur = [up], up
        while True:
            nx = FLO.get(cur, {}).get('dn')
            if nx is None or nx not in FLO or nx in BYNAME or not L.match_prefix(nx, X.prefixes) \
                    or not is_pressure_d(X, FLO[nx]):
                break
            ch.append(nx)
            cur = nx
        return ch

    # ── 도면연장(트림 복원) ↔ flo 연장
    mism = []
    for g in pl:
        r = FLO.get(g['name'])
        g['chain'] = chain_of(g['name']) if (g['pressure'] and r) else [g['name']]
        Lf = sum(float(FLO[k]['L']) for k in g['chain']) if r else None
        g['L_dwg'] = L.polylen(g['pts']) + (tol['trim_pressure_m'] if g['pressure'] else tol['trim_m'])
        g['L_d'] = round(g['L_dwg'] - Lf, 1) if Lf is not None else None
        if r and abs(g['L_d']) > tol['len_poly_m']:
            mism.append((g['name'], round(g['L_dwg'], 1), round(Lf, 1), g['L_d'], g['pressure']))
    nf = [g['name'] for g in pl if g['name'] not in FLO]
    print('연장 교차검증: %d/%d 일치(±%.1f m) / flo미수록 %d'
          % (len(pl) - len(mism) - len(nf), len(pl), tol['len_poly_m'], len(nf)))
    for m in mism:
        print('   ! 연장불일치 %-14s 도면 %7.1f / flo %7.1f (%+.1f) 압송=%s' % m)
        checks.append(('연장불일치', m[0], m[1], m[2], '차 %+.1f (트림 복원 후)' % m[3]))
    for n in nf:
        checks.append(('flo미수록', n, '', '', '라벨은 있으나 flo 에 없음'))
    drawn = {k for g in pl for k in g['chain']}
    flo_only = sorted(n for n in FLO if L.match_prefix(n, X.prefixes) and n not in drawn)
    if flo_only:
        print('flo 에만 있는 금회 구간(평면에 관로·라벨 없음) %d: %s' % (len(flo_only), flo_only[:10]))
    for n in flo_only:
        checks.append(('flo에만', n, FLO[n]['dn'], FLO[n]['L'], '평면에 관로·라벨 없음 — 도형 없음'))

    # ── 끝점 → 맨홀 심볼 스냅 (심볼 없는 끝점은 폴리라인 끝점으로 노드 보완)
    def snap(p):
        i = min(range(len(mh)), key=lambda k: math.dist((mh[k]['x'], mh[k]['y']), p))
        return i, math.dist((mh[i]['x'], mh[i]['y']), p)

    nosym = []
    for g in pl:
        if not mh:
            mh.append(dict(role='plan', x=g['pts'][0][0], y=g['pts'][0][1], name=None, synth=True))
        (i0, d0), (i1, d1) = snap(g['pts'][0]), snap(g['pts'][-1])
        if d0 > tol['snap_m'] or (i0 == i1 and d0 > d1):
            mh.append(dict(role='plan', x=g['pts'][0][0], y=g['pts'][0][1], name=None, synth=True))
            nosym.append((g['name'], '상류', round(d0, 2), g['pressure']))
            i0, d0 = len(mh) - 1, 0.0
        if d1 > tol['snap_m'] or i0 == i1:
            mh.append(dict(role='plan', x=g['pts'][-1][0], y=g['pts'][-1][1], name=None, synth=True))
            nosym.append((g['name'], '하류', round(d1, 2), g['pressure']))
            i1, d1 = len(mh) - 1, 0.0
        g['n0'], g['n1'], g['d0'], g['d1'] = i0, i1, round(d0, 2), round(d1, 2)
    print('끝점 스냅: 맨홀 심볼 없는 끝점 %d (폴리라인 끝점으로 보완)' % len(nosym))
    for r in nosym:
        checks.append(('맨홀심볼없음', r[0], r[1], r[2], '최근접 심볼 %.1f m — 끝점으로 노드 보완' % r[2]))

    # ── 유향 — flo 의 하류 구간이 붙은 쪽이 하류단 (압송은 체인 끝 노드 기준)
    for g in pl:
        r = FLO.get(g['name'])
        dn = (FLO[g['chain'][-1]]['dn'] if r else None)
        nxt = BYNAME.get(dn)
        if nxt and nxt['n0'] in (g['n0'], g['n1']):
            if g['n0'] == nxt['n0']:
                g['pts'] = g['pts'][::-1]
                g['n0'], g['n1'] = g['n1'], g['n0']
        elif nxt and nxt['n1'] in (g['n0'], g['n1']):
            if g['n0'] == nxt['n1']:
                g['pts'] = g['pts'][::-1]
                g['n0'], g['n1'] = g['n1'], g['n0']
        g['dn'] = dn
    for g in pl:
        mh[g['n0']]['name'] = g['name']
    for g in pl:
        if mh[g['n1']]['name'] is None and g['dn']:
            mh[g['n1']]['name'] = g['dn']

    # ── 기존맨홀 번호 — 금회 접속점에서 flo 기존 체인을 연장거리로 걸어간다
    ADJ = collections.defaultdict(list)
    if X.prefixes:
        for up, r in FLO.items():
            if not L.match_prefix(up, X.prefixes):
                ADJ[up].append((r['dn'], float(r['L'])))
                ADJ[r['dn']].append((up, float(r['L'])))
    unnamed = [i for i, m in enumerate(mh) if m['role'] == 'existing' and not m['name']]
    named = {m['name']: i for i, m in enumerate(mh) if m['name']}
    queue = [n for n in named if X.prefixes and not L.match_prefix(n, X.prefixes)]
    walked = 0
    while queue:
        n = queue.pop()
        i = named[n]
        for nb, Ln in ADJ.get(n, []):
            if nb in named:
                continue
            cand = [(abs(math.dist((mh[k]['x'], mh[k]['y']), (mh[i]['x'], mh[i]['y'])) - Ln), k)
                    for k in unnamed if not mh[k]['name']]
            cand = [c for c in cand if c[0] <= tol['len_poly_m']]
            if cand:
                cand.sort()
                k = cand[0][1]
                mh[k]['name'] = nb
                named[nb] = k
                queue.append(nb)
                walked += 1
    print('기존맨홀 체인추적: %d개 번호부여 / 번호미상 기존맨홀 %d'
          % (walked, sum(1 for m in mh if m['role'] == 'existing' and not m['name'])))

    # ── 노선 레코드
    pipes = []
    for g in pl:
        up = g['name']
        r = FLO.get(up)
        dn = mh[g['n1']]['name'] or g['dn']
        P0, P1 = (mh[g['n0']]['x'], mh[g['n0']]['y']), (mh[g['n1']]['x'], mh[g['n1']]['y'])
        mid = []
        for p in g['pts'][1:-1]:                       # 화살촉 정점 제거, 실굴절만
            Ls = math.dist(P0, P1)
            d = (abs((P1[0] - P0[0]) * (P0[1] - p[1]) - (P0[0] - p[0]) * (P1[1] - P0[1])) / Ls) if Ls else 0
            if d > tol['bend_m']:
                mid.append(list(p))
        geom = [list(P0)] + mid + [list(P1)]
        ch = g['chain']
        GL = L.fnum(r['gls'][0]) if (r and r['gls']) else None
        ILu = L.fnum(r['ilu']) if r else None
        ILd = L.fnum(FLO[ch[-1]]['ild']) if r else None
        D = L.fnum((r['D'] or '').split()[0]) if (r and r.get('D')) else None
        Lf = (round(sum(float(FLO[k]['L']) for k in ch), 1) if len(ch) > 1 else float(r['L'])) if r else None
        profile = None
        if r and len(ch) > 1:
            prof, acc = [], 0.0
            for k in ch:
                prof.append('%.2f:%.2f' % (acc, float(FLO[k]['ilu'])))
                acc += float(FLO[k]['L'])
            prof.append('%.2f:%.2f' % (acc, ILd))
            profile = ';'.join(prof)
            slope = round((ILd - ILu) / Lf * 1000, 2) if Lf else None
        else:
            slope = round((ILu - ILd) / Lf * 1000, 2) if (Lf and ILu is not None and ILd is not None) else None
        h = L.hyd(OUT, up, null=(g['pressure'] and X.pressure_hyd == 'null'))
        Lg = round(L.polylen(geom), 2)
        f = r['_file'] if r else None
        pipes.append(dict(
            area=area_of(X, up, f), district=district_of(X, up, f), kind=(X.KP if g['pressure'] else X.KG),
            pipe_id=up, us_mh=up, ds_mh=dn, length_m=Lf, dia_mm=D, us_invert=ILu, ds_invert=ILd, us_ground=GL,
            slope_permil=slope,
            us_cover=round(GL - ILu - (D or 0) / 1000, 2) if (GL is not None and ILu is not None) else None,
            self_flow_m3d=round(float(r['Q']) * 86400, 2) if (r and r['Q']) else None,
            flow_m3d=h['flow_m3d'], velocity_mps=h['velocity_mps'], judge=h['judge'],
            full_flow_m3d=h['full_flow_m3d'], flow_depth_m=h['flow_depth_m'], depth_ratio_pct=h['depth_ratio_pct'],
            pressure_profile=profile, obstacles=None, obstacle_count=None,
            geom_length_m=Lg, length_diff_m=round(Lg - Lf, 2) if Lf is not None else None,
            label_dist_m=g['label_d'], snap_us_m=g['d0'], snap_ds_m=g['d1'],
            flo_name=(r['flo_up'] if (r and r['flo_up'] != up) else None),
            asof=X.asof, source=X.source, _geom=geom))

    # ── 연결성
    ups = {p['us_mh'] for p in pipes}
    tails = sorted({p['ds_mh'] for p in pipes if p['ds_mh']} - ups)
    print('연결성: 노드 %d / 하류 종점 %d %s' % (len(ups | {p['ds_mh'] for p in pipes}), len(tails), tails[:10]))

    # ── 맨홀 레코드
    inflow = collections.defaultdict(list)
    for p in pipes:
        inflow[p['ds_mh']].append(p['ds_invert'])
    outil = {p['us_mh']: p['us_invert'] for p in pipes}
    glmap = {p['us_mh']: p['us_ground'] for p in pipes}
    spec_of = {}
    for k, v in C['mh_types'].items():
        spec_of.setdefault(v.get('role'), v)
    roles_w = set(C['write_mh_roles'])
    mhs, skip = [], collections.Counter()
    for m in mh:
        n = m['name']
        if not n:
            skip['번호미상'] += 1
            continue
        if m['role'] not in roles_w:
            skip[m['role']] += 1
            continue
        G, IL = glmap.get(n), outil.get(n)
        ins = [v for v in inflow.get(n, []) if v is not None]
        sp = spec_of.get(m['role'], {})
        f = FLO[n]['_file'] if n in FLO else None
        mhs.append(dict(area=area_of(X, n, f), district=district_of(X, n, f), mh_id=n, kind=C['mh_kind'][m['role']],
                        mh_type=None, inner_mm=sp.get('inner_mm'), outer_mm=sp.get('outer_mm'), ground=G, out_invert=IL,
                        depth=round(G - IL, 2) if (G is not None and IL is not None) else None,
                        drop=round(min(ins) - IL, 2) if (ins and IL is not None) else None, connect_to=None,
                        note=('평면도에 맨홀 심볼 없음 — 관로 끝점으로 보완' if m['synth'] else None),
                        asof=X.asof, source=X.source, _xy=[m['x'], m['y']]))
    print('계획맨홀 %d %s / 제외 %s' % (len(mhs), collections.Counter(r['kind'] for r in mhs).most_common(), dict(skip)))

    # ── 맨홀펌프장 (펌프장 심볼 노드 → flo 압송 체인)
    pnodes = {m['name']: m for m in mh if m['role'] == 'pump' and m['name']}
    pumps, chains = [], {}
    PC = C['pump']
    for pn in sorted(pnodes):
        if pn not in FLO:
            checks.append(('펌프장flo없음', pn, '', '', '펌프장 심볼 노드인데 flo 구간이 없다'))
            continue
        ch, cur = [], pn
        while True:
            ch.append(cur)
            nx = FLO[cur]['dn']
            if nx not in FLO or not L.match_prefix(nx, X.prefixes) or not is_pressure_d(X, FLO[nx]) or nx in pnodes:
                break
            cur = nx
        chains[pn] = ch
        ils = [L.fnum(FLO[k]['ilu']) for k in ch] + [L.fnum(FLO[ch[-1]]['ild'])]
        ils = [v for v in ils if v is not None]
        ins = [L.fnum(r['ild']) for r in FLO.values() if r['dn'] == pn and L.match_prefix(r['up'], X.prefixes)]
        ins = [v for v in ins if v is not None]
        uin = min(ins) if ins else None
        GL = L.fnum(FLO[pn]['gls'][0]) if FLO[pn]['gls'] else None
        Q = L.hyd(OUT, pn)['flow_m3d']
        m = pnodes[pn]
        pumps.append(dict(area=area_of(X, pn, FLO[pn]['_file']), district=district_of(X, pn, FLO[pn]['_file']),
                          pump_id=pn, mh_id=pn, kind=PC['kind'], discharge_to=FLO[ch[-1]]['dn'], flow_m3d=Q,
                          peak_m3min=round(Q * PC['peak_factor'] / 1440, 4) if Q else None,
                          force_main_m=round(sum(float(FLO[k]['L']) for k in ch), 1), in_invert=uin,
                          depth_m=round(GL - uin, 2) if (uin is not None and GL) else None,
                          top_bop=round(max(ils), 2) if ils else None, mh_spec=PC['spec'], inner_mm=PC['inner_mm'],
                          basis=PC['basis_note'], asof=X.asof, source=X.source, _xy=[m['x'], m['y']]))
    unn = [(m['role'], round(m['x'], 2), round(m['y'], 2)) for m in mh if not m['name']]
    for u in unn:
        checks.append(('번호미상노드', '', u[0], '%.2f,%.2f' % (u[1], u[2]), '어느 관로에도 이름이 안 붙은 심볼'))
    return pipes, mhs, pumps, chains


# ───────────────────────────────────────────── main

def summary(pipes, mhs, pumps, X):
    print('\n노선 %d %s' % (len(pipes), collections.Counter(p['kind'] for p in pipes).most_common()))
    tot = lambda k: sum(p['length_m'] or 0 for p in pipes if (k is None or p['kind'] == k))
    print('  연장합 %.1f m (%s %.1f / %s %.1f)' % (tot(None), X.KG, tot(X.KG), X.KP, tot(X.KP)))
    print('  관경:', collections.Counter(p['dia_mm'] for p in pipes).most_common())
    print('  판정:', collections.Counter(p['judge'] for p in pipes).most_common())
    print('  자체유량 합 %.1f ㎥/일' % sum(p['self_flow_m3d'] or 0 for p in pipes))
    ld = sorted((abs(p['length_diff_m']), p['pipe_id']) for p in pipes if p['length_diff_m'] is not None)
    if ld:
        print('  도형 vs flo 연장차 최대 %.2f m (%s) / %.1f m 초과 %d건'
              % (ld[-1][0], ld[-1][1], X.T['len_m'], sum(1 for d, _ in ld if d > X.T['len_m'])))
    nog = [p['pipe_id'] for p in pipes if len(p['_geom']) < 2]
    print('  도형결손:', nog or '없음')
    for p in pumps:
        print('  펌프장 %-14s 압송 %6.1f m / 유입IL %s / 최상단BOP %s / 토출 %s / 누가 %s ㎥/일'
              % (p['pump_id'], p['force_main_m'], p['in_invert'], p['top_bop'], p['discharge_to'], p['flow_m3d']))


def main(argv=None):
    ap = argparse.ArgumentParser(description='13 레코드 구축 (평면 DXF + flo/OUT)')
    L.common_args(ap)
    ap.add_argument('--scan', default=None, help='p1_scan.py 의 scan.json (생략: 산출폴더 scan.json, 없으면 스캔)')
    ap.add_argument('--dxf', default=None, help='평면 DXF (스캔이 없을 때)')
    ap.add_argument('--rescan', action='store_true', help='산출 폴더의 scan.json 을 쓰지 않고 다시 스캔')
    ap.add_argument('--flo', action='append', help='.flo (여러 개 가능). 생략하면 납품 폴더에서')
    ap.add_argument('--out', action='append', help='.OUT (여러 개 가능). 생략하면 납품 폴더에서')
    ap.add_argument('--no-out', action='store_true', help='.OUT 수리값을 쓰지 않는다(유량 재조정 후 직접 계산할 때)')
    ap.add_argument('--profile', default=None, help='종단 DXF — flo↔도면 번호 별칭 확인용(생략하면 납품 폴더에서)')
    ap.add_argument('--mode', choices=('auto', 'attrib', 'polyline'), default='auto')
    ap.add_argument('--prefix', action='append', help='금회 관번호 접두(여러 개 가능). 생략: plan_ingest.prefixes, 없으면 전체')
    ap.add_argument('--district', default=None, help='지구 값(기본). plan_ingest.district_by_prefix 가 먼저')
    ap.add_argument('--area', default=None, help='처리구역 값(기본). plan_ingest.area_by_prefix 가 먼저')
    ap.add_argument('--district-by-flo', action='append', help='flo파일명=지구 (flo 파일별 지구, 가장 먼저)')
    ap.add_argument('--area-by-flo', action='append', help='flo파일명=처리구역')
    ap.add_argument('--asof', default=None, help='자료기준일 값(기본: 오늘 YYYY-MM-DD). 다음 회차 교체의 열쇠가 된다')
    ap.add_argument('--source', default=None, help='출처 값(기본: 납품폴더명 + 입력 파일명)')
    ap.add_argument('--pressure', choices=('fold', 'split'), default=None,
                    help='압송: fold=펌프장별 1행(기본, plan_ingest.pressure) / split=세분구간 그대로')
    ap.add_argument('--ext-node', choices=('polyline', 'attrib'), default=None,
                    help='금회 밖 하류노드 좌표 찾는 순서(기본 plan_ingest.ext_node)')
    ap.add_argument('--pressure-hyd', choices=('keep', 'null'), default=None,
                    help='압송 행의 OUT 수리값(유량·유속·판정): keep=그대로(기본) / null=비움 — '
                         'makesw 는 압송을 자연유하식으로 풀어 값이 믿을 만하지 않다')
    ap.add_argument('--rename', action='append', help='flo번호=도면번호 (flo 가 옛 번호를 남겼을 때)')
    a = ap.parse_args(argv)

    X = resolve(a)
    setup_naming(X)
    X.pressure = a.pressure or X.C.get('pressure', 'fold')
    X.ext_node = a.ext_node or X.C.get('ext_node', 'polyline')
    X.pressure_hyd = a.pressure_hyd or X.C.get('pressure_hyd', 'keep')
    rename = L.parse_rename(a.rename)
    S = get_scan(X)
    FLO, ERR, BYFILE = L.load_flo(X.flo, rename)
    OUT = L.parse_out(X.out, X.C, rename) if X.out else {}
    print('=' * 78)
    print('flo %s → 구간 %d (파싱오류 %d) / OUT %s → %d' % ([p.name for p in X.flo], len(FLO), len(ERR),
                                                    [p.name for p in X.out], len(OUT)))
    pre = collections.Counter(re.split(r'-', n)[0] for n in FLO)
    print('  flo 접두: %s   금회 접두: %s' % (pre.most_common(12), X.prefixes or '(전체)'))
    if not X.prefixes and len(pre) > 1:
        print('  ※ 접두가 여럿이다 — 기존망이 섞여 있으면 --prefix 로 금회만 고를 것')
    checks = [('flo파싱오류', '%s:%s' % (f, ln), '', '', m) for f, ln, m in ERR]

    ab = [x for x in S['attribs'] if x['block'] == X.C['dxf']['attrib_block']]
    up = X.C['attrib']['up']
    names = {(x['attrs'].get(up) or '').strip() for x in ab}
    flo_t = {n for n in FLO if L.match_prefix(n, X.prefixes)}
    hit = len(names & flo_t)
    mode = a.mode
    if mode == 'auto':
        mode = 'attrib' if (flo_t and hit >= 0.5 * len(flo_t)) else 'polyline'
        print('경로 자동 선택: %s (속성맨홀 이름 ∩ 금회 flo = %d / %d)' % (mode, hit, len(flo_t)))
    print('=' * 78)
    if mode == 'attrib':
        pipes, mhs, pumps, chains = build_attrib(X, S, FLO, OUT, checks)
    else:
        pipes, mhs, pumps, chains = build_polyline(X, S, FLO, OUT, checks)
    summary(pipes, mhs, pumps, X)

    meta = dict(mode=mode, prefixes=X.prefixes, pressure=X.pressure, ext_node=X.ext_node,
                pressure_hyd=X.pressure_hyd, asof=X.asof,
                source=X.source, rename=rename, created=L.now(),
                inputs=dict(delivery=str(X.delivery) if X.delivery else None, plan=S['path'],
                            flo=[str(p) for p in X.flo], out=[str(p) for p in X.out],
                            profile=str(X.profile) if X.profile else None),
                counts=dict(pipes=len(pipes), manholes=len(mhs), pumps=len(pumps), checks=len(checks)))
    L.save_json(X.od / 'build.json', dict(meta=meta, pipes=pipes, manholes=mhs, pumps=pumps, chains=chains))
    L.save_csv(X.od / 'build_checks.csv', ['항목', '관번호', '값1', '값2', '비고'], checks)
    print('\n저장 %s\n     %s (확인 %d건: %s)' % (X.od / 'build.json', X.od / 'build_checks.csv', len(checks),
                                            dict(collections.Counter(c[0] for c in checks))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
