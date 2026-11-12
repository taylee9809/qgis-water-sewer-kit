# -*- coding: utf-8 -*-
"""노선별 종단면도 생성 — 계획관로 레이어 → ① 설계사 서식 스트립 DXF ② 검토용 PDF

① DXF: 설계사 종단면도 서식(H=1:1000·V=1:200, 1 unit = 1 m, Y = 117 + (표고 − DATUM) × 5)을 모사한
   긴 스트립. 시트(계통)마다 표제 블록 INSERT + 7단 밴드(측점·거리·추가거리·지반고·관저고·토피·구배)
   + 표척 + 지반선·관저/관정/관하단선 + 맨홀 2중 사각 + 45° 인출 라벨 + 관번호 막대.
   → 그대로 `a3_split.py` → `a3_pdf.py` 에 넣으면 A3 도면집이 된다.
② PDF: 노선마다 한 쪽(길면 나눔) 검토용 종단 — 지반선·관저선(단차 포함)·맨홀·구간 L/S·지장물·상수관 교차.

노선(계통) 만들기: 관번호 끝 번호를 뗀 앞부분(XX-A-001 → XX-A)이 같은 관을 도형 연결(상류 끝점 →
하류 끝점, --snap m 안)로 이어 체인을 만든다. 관로 도형은 상류 → 하류 방향이어야 한다(킷 규약).
노선 이름 규칙이 다르면 --route-re 로 바꾼다.

설정(sewer.config.json):
  layers.plan_pipe  fields: pipe_id · us_invert · ds_invert · dia_mm (필수)
                            us_ground · length_m · kind · slope_permil · obstacles · district (선택)
  layers.plan_manhole (선택) fields: mh_id · ground (지반고·맨홀명), mh_type(예: 1호맨홀) · inner_mm(내경)
  layers.plan_pump   (선택) fields: pump_id — 그 자리 맨홀 라벨을 '맨홀펌프장(이름)'으로
  layers.water_pipe  (선택) fields: dia_mm — PDF 에 상수관 교차 표시
  values.kind_pressure · design.pressure_max_d_mm — 압송 판정
  profile.strip.* · profile.draw.* — 서식 상수(profcommon.DEFAULTS)
지반고: 상류 끝은 plan_pipe.us_ground → 맨홀 지반고, 하류 끝은 맨홀 지반고 → 다음 관 us_ground 순.
        둘 다 없으면 그 측점 지반선은 비운다(값을 지어내지 않는다).

사용: python profile_sheets.py [--district OO] [--route XX-A] [--title-from <설계사 종단면도.dxf>] [--no-pdf]
출력: <out>/<이름>_종단스트립.dxf, <이름>_종단검토.pdf, <이름>_종단목록.json
"""
import argparse
import collections
import math
import re

import profcommon as PC

PERMIL = '‰'
OBS_RE = re.compile(r'^\s*(?P<kind>.*?)\s*(?P<spec>D[\d.]+(?:\s*[Xx×]\s*[\d.]+)?)?\s*@\s*(?P<s>-?[\d.]+)\s*'
                    r'\(\s*(?:EL)?\s*(?P<el>-?[\d.]+)\s*\)\s*$')


def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ───────────────────────────────────────────── 자료
def load_network(cfg, S, a):
    import shapely
    PC.kitconfig.fix_proj()
    pipe = cfg.read_layer('plan_pipe')
    F = cfg.fields('plan_pipe')
    for k in ('pipe_id', 'us_invert', 'ds_invert', 'dia_mm'):
        cfg.field('plan_pipe', k)                      # 없으면 안내 후 종료
    if a.district:
        if not F.get('district'):
            raise SystemExit('--district 를 쓰려면 fields.plan_pipe.district 가 있어야 한다')
        pipe = pipe[pipe[F['district']].isin(a.district)]
    press_word = cfg.value('kind_pressure', '압송')
    dmax = cfg.get('design.pressure_max_d_mm', 100)
    route_re = re.compile(a.route_re)
    snap = a.snap

    def key(pt):
        return (round(pt[0] / snap), round(pt[1] / snap))

    P = {}
    for _, r in pipe.iterrows():
        g = r.geometry
        if g is None or g.is_empty:
            continue
        if g.geom_type == 'MultiLineString':
            g = shapely.line_merge(g)
            if g.geom_type == 'MultiLineString':
                g = max(g.geoms, key=lambda q: q.length)
        cs = list(g.coords)
        pid = str(r[F['pipe_id']])
        m = route_re.match(pid)
        D = num(r[F['dia_mm']]) or 200.0
        kind = str(r[F['kind']]) if F.get('kind') else ''
        L = num(r[F['length_m']]) if F.get('length_m') else None
        L = L or g.length
        us, ds = num(r[F['us_invert']]), num(r[F['ds_invert']])
        S_ = num(r[F['slope_permil']]) if F.get('slope_permil') else None
        if S_ is None and us is not None and ds is not None and L:
            S_ = (us - ds) / L * 1000.0
        P[pid] = dict(id=pid, route=(m.group(1) if m else pid), up=key(cs[0]), dn=key(cs[-1]),
                      upxy=cs[0][:2], dnxy=cs[-1][:2], L=L, D=D, us=us, ds=ds, S=S_ or 0.0,
                      gl=num(r[F['us_ground']]) if F.get('us_ground') else None,
                      press=(kind == press_word) or (D <= dmax),
                      obs=str(r[F['obstacles']]) if F.get('obstacles') and r[F['obstacles']] else '',
                      geom=g)
    if a.route:
        P = {k: v for k, v in P.items() if any(v['route'].startswith(x) for x in a.route)}
    if not P:
        raise SystemExit('그릴 계획관로가 없다(--district/--route 조건 확인)')

    # 노드: 맨홀(지반고·이름·호수·내경) / 펌프장
    node = {}
    for p in P.values():
        for k, xy in ((p['up'], p['upxy']), (p['dn'], p['dnxy'])):
            node.setdefault(k, dict(xy=xy, name=None, gl=None, pump=None, mh_type=None, inner=None))
    for p in P.values():                     # 관번호 = 상류 맨홀 번호(킷 규약)
        node[p['up']]['name'] = node[p['up']]['name'] or p['id']
        if p['gl'] is not None:
            node[p['up']]['gl'] = p['gl']
    if cfg.has_layer('plan_manhole'):
        _attach_points(cfg, 'plan_manhole', node, snap_m=1.0, setter=_set_mh)
    if cfg.has_layer('plan_pump'):
        _attach_points(cfg, 'plan_pump', node, snap_m=2.0, setter=_set_pump)
    water = None
    if cfg.has_layer('water_pipe') and not a.no_water:
        try:
            water = cfg.read_layer('water_pipe')
            fw = cfg.fields('water_pipe')
            if a.water_status and fw.get('status'):
                water = water[water[fw['status']] == a.water_status]
            water = (water, fw.get('dia_mm'))
        except SystemExit as ex:
            print('상수관 레이어를 못 읽어 교차 표시는 건너뛴다:', ex)
            water = None
    return P, node, water


def _attach_points(cfg, key, node, snap_m, setter):
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    g = cfg.read_layer(key)
    F = cfg.fields(key)
    pts = []
    for _, r in g.iterrows():
        geom = r.geometry
        if geom is None or geom.is_empty:
            continue
        pts.append((geom.centroid if geom.geom_type != 'Point' else geom, r))
    if not pts:
        return
    tree = STRtree([q[0] for q in pts])
    for k, n in node.items():
        p = Point(n['xy'])
        i = tree.nearest(p)
        if i is None:
            continue
        geom, r = pts[int(i)]
        if geom.distance(p) <= snap_m:
            setter(n, r, F)


def _set_mh(n, r, F):
    if F.get('mh_id') and r[F['mh_id']]:
        n['name'] = str(r[F['mh_id']])
    if F.get('ground'):
        v = num(r[F['ground']])
        if v is not None:
            n['gl'] = v
    if F.get('mh_type') and r[F['mh_type']]:
        n['mh_type'] = str(r[F['mh_type']])
    if F.get('inner_mm'):
        n['inner'] = num(r[F['inner_mm']])


def _set_pump(n, r, F):
    n['pump'] = str(r[F['pump_id']]) if F.get('pump_id') and r[F['pump_id']] else '-'


def chains_of(P):
    by_route = collections.defaultdict(list)
    for p in P.values():
        by_route[p['route']].append(p)
    out = collections.OrderedDict()
    for rt in sorted(by_route):
        ps = sorted(by_route[rt], key=lambda q: q['id'])
        by_up = collections.defaultdict(list)
        for p in ps:
            by_up[p['up']].append(p)
        dns = {p['dn'] for p in ps}
        heads = [p for p in ps if p['up'] not in dns] or ps[:1]
        seen = set()
        k = 0
        for h in heads + ps:
            if h['id'] in seen:
                continue
            ch, cur = [], h
            while cur is not None and cur['id'] not in seen:
                seen.add(cur['id'])
                ch.append(cur)
                nxt = [q for q in by_up.get(cur['dn'], []) if q['id'] not in seen]
                cur = nxt[0] if nxt else None
            out[rt if k == 0 else '%s-%s' % (rt, chr(ord('A') + k))] = ch
            k += 1
    return out


def stations(ch, node, D_pitch, press_cover):
    """체인 → 측점(st)·구간(rc)·관종 구간(runs)."""
    def node_gl(k, fallback=None):
        g = node[k]['gl']
        return g if g is not None else fallback
    st, rc, runs = [], [], []
    p0 = ch[0]
    gl0 = node_gl(p0['up'], p0['gl'])
    il0 = p0['us'] if p0['us'] is not None else (gl0 - press_cover - p0['D'] / 1000.0 if gl0 is not None else None)
    st.append(dict(s=0.0, gl=gl0, il=il0, node=p0['up'], key=True, D=p0['D']))
    cum = 0.0
    for i, p in enumerate(ch):
        a = cum
        cum = round(cum + p['L'], 2)
        gla = node_gl(p['up'], p['gl'])
        nxt = ch[i + 1] if i + 1 < len(ch) else None
        glb = node_gl(p['dn'], nxt['gl'] if nxt else None)
        us = p['us'] if p['us'] is not None else (gla - press_cover - p['D'] / 1000.0 if gla is not None else None)
        ds = p['ds'] if p['ds'] is not None else (glb - press_cover - p['D'] / 1000.0 if glb is not None else None)
        if us is None or ds is None:
            raise SystemExit('%s: 관저고가 없고 지반고로도 못 메운다 — 관저고를 채울 것' % p['id'])
        t = math.ceil(a / D_pitch) * D_pitch
        while t < cum - 1e-6:
            if t > a + 1e-6:
                f = (t - a) / p['L']
                st.append(dict(s=round(t, 2),
                               gl=(round(gla + (glb - gla) * f, 2) if gla is not None and glb is not None else None),
                               il=round(us + (ds - us) * f, 3), node=None, key=False, D=p['D']))
            t += D_pitch
        il_out = (nxt['us'] if nxt and nxt['us'] is not None else ds)
        dh = round(ds - il_out, 3) if nxt else 0.0
        st.append(dict(s=cum, gl=glb, il=il_out if dh > 0.005 else ds, il_in=ds, dh=max(dh, 0.0),
                       node=p['dn'], key=True, D=p['D']))
        rc.append(dict(a=a, b=cum, L=p['L'], H=round(us - ds, 2), S=round(p['S'], 2), pid=p['id'],
                       press=p['press'], D=p['D']))
        if runs and runs[-1]['press'] == p['press'] and runs[-1]['D'] == p['D']:
            runs[-1]['b'] = cum
        else:
            runs.append(dict(a=a, b=cum, press=p['press'], D=p['D']))
    st.sort(key=lambda d: d['s'])
    ded = []
    for p in st:                                # 1 m 안 측점은 하나로(맨홀 우선)
        if ded and abs(ded[-1]['s'] - p['s']) < 1.0:
            if p['key'] and not ded[-1]['key']:
                ded[-1] = p
            elif p['key'] and ded[-1]['key']:
                for k, v in p.items():
                    if v is not None:
                        ded[-1][k] = v
            continue
        ded.append(p)
    grade = []                                  # 구배 밴드: 같은 경사 연속 구간 병합(단차 지점에서 끊음)
    for r in rc:
        if (grade and not r['press'] and not grade[-1]['press']
                and abs(grade[-1]['S'] - r['S']) < 0.20 and abs(grade[-1]['b'] - r['a']) < 0.01
                and not any(abs(q['s'] - r['a']) < 0.01 and q.get('dh', 0) > 0.005 for q in ded)):
            g = grade[-1]
            g['b'] = r['b']
            g['L'] = round(g['b'] - g['a'], 2)
            g['S'] = round((g['S'] * (g['L'] - r['L']) + r['S'] * r['L']) / g['L'], 2)
            g['H'] = round(g['S'] * g['L'] / 1000.0, 2)
        else:
            grade.append(dict(r))
    return ded, rc, runs, cum, grade


def obstacles(ch, rc):
    """plan_pipe.obstacles('종류 D규격@측점(저면EL)' 을 ' / ' 로 이음) → 체인 누가거리 목록."""
    out = []
    base = {r['pid']: r['a'] for r in rc}
    for p in ch:
        for part in (p['obs'] or '').split('/'):
            m = OBS_RE.match(part)
            if not m:
                continue
            spec = (m.group('spec') or '').upper().replace(' ', '').replace('×', 'X')
            el = float(m.group('el'))
            o = dict(s=base[p['id']] + float(m.group('s')), kind=m.group('kind') or '지장물', el=el, spec=spec)
            if 'X' in spec:
                w, h = [float(v) for v in spec[1:].split('X')[:2]]
                o.update(shape='box', w=w, h=h)
            elif spec:
                o.update(shape='pipe', dmm=float(spec[1:]) * (1000.0 if float(spec[1:]) < 10 else 1.0))
            else:
                o.update(shape='mark')
            out.append(o)
    return out


def water_crossings(ch, water, rc):
    if not water:
        return []
    gdf, f_d = water
    from shapely.strtree import STRtree
    geoms = list(gdf.geometry.values)
    tree = STRtree(geoms)
    base = {r['pid']: r['a'] for r in rc}
    out = []
    for p in ch:
        g = p['geom']
        for j in tree.query(g):
            j = int(j)
            if not g.intersects(geoms[j]):
                continue
            ip = g.intersection(geoms[j])
            pts = [ip] if ip.geom_type == 'Point' else [q for q in getattr(ip, 'geoms', []) if q.geom_type == 'Point']
            d = num(gdf.iloc[j][f_d]) if f_d else None
            for q in pts:
                out.append(dict(s=base[p['id']] + g.project(q), label='상수 D%d' % d if d else '상수관'))
    return out


def gl_at(st, s):
    for i in range(1, len(st)):
        a, b = st[i - 1], st[i]
        if a['s'] <= s <= b['s'] and a['gl'] is not None and b['gl'] is not None:
            f = (s - a['s']) / max(b['s'] - a['s'], 1e-9)
            return a['gl'] + (b['gl'] - a['gl']) * f
    return None


def split_sheets(rt, st, rc, runs, L, grade, obs, wat, maxl):
    cuts = [0.0]
    for r in rc:
        if r['b'] - cuts[-1] > maxl:
            cuts.append(r['a'] if r['a'] > cuts[-1] else r['b'])
    cuts.append(L)
    cuts = sorted(set(round(c, 2) for c in cuts))
    out = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        if b - a < 0.5:
            continue

        def clip(lst, _a=a, _b=b):
            return [dict(v, a=max(v['a'], _a), b=min(v['b'], _b), L=round(min(v['b'], _b) - max(v['a'], _a), 2))
                    for v in lst if v['b'] > _a + 0.2 and v['a'] < _b - 0.2]
        out.append(dict(rt=rt, a=a, b=b, part=(i + 1, len(cuts) - 1),
                        st=[p for p in st if a - 1e-6 <= p['s'] <= b + 1e-6],
                        rc=[r for r in rc if r['a'] >= a - 1e-6 and r['b'] <= b + 1e-6],
                        runs=clip(runs), grade=clip(grade),
                        obs=[o for o in obs if a - 1e-6 <= o['s'] <= b + 1e-6],
                        wat=[w for w in wat if a - 1e-6 <= w['s'] <= b + 1e-6]))
    return out


# ───────────────────────────────────────────── DXF (설계사 서식 스트립)
def make_doc(S, title_from):
    import ezdxf
    from ezdxf.addons import Importer
    st = S['strip']
    L = st['layers']
    doc = ezdxf.new('R2010', setup=True)
    doc.header['$INSUNITS'] = 6
    doc.styles.add('KIT', font=S['font'])
    if title_from:
        src = PC.load_dxf(title_from)
        imp = Importer(src, doc)
        imp.import_tables()
        imp.import_block(st['title_block'])
        imp.finalize()
    for ly, col in ((L['ground'], 3), (L['invert'], 1), (L['crown'], 1), (L['sub'], 1), (L['manhole'], 7),
                    (L['leader'], 7), (L['text'], 7), (L['pole'], 7), (L['old'], 4), (L['box'], 4),
                    (L['water'], 5), (L['memo'], 8)) + tuple((b, 7) for b in L['band']) + ((L['station'], 7),):
        if ly not in doc.layers:
            doc.layers.add(ly, color=col)
    if st['title_block'] not in doc.blocks:
        _own_title_block(doc, S)
    return doc


def _own_title_block(doc, S):
    """설계사 표제 블록을 못 가져올 때 같은 치수로 그린다(밴드 이름칸 + 표척 117~267)."""
    from ezdxf.enums import TextEntityAlignment as TA
    st = S['strip']
    L = st['layers']
    b = doc.blocks.new(st['title_block'])
    rows = st['band_rows']
    t1, t2 = L['band'][0], L['band'][-1]
    for y in (rows[0], rows[-1]):
        b.add_line((-28, y), (-10, y), dxfattribs={'layer': t1})
    for x in (-28, -10):
        b.add_line((x, rows[0]), (x, rows[-1]), dxfattribs={'layer': t1})
    for y in rows[1:-1]:
        b.add_line((-28, y), (-10, y), dxfattribs={'layer': t2})
    names = (('측 점',), ('거 리',), ('추 가', '거 리'), ('지반고',), ('관저고',), ('토 피',), ('구 배',))
    for k, nm in enumerate(names):
        if k + 1 >= len(rows):
            break
        xs = (-17.5,) if len(nm) == 1 else (-20.5, -14.5)     # 두 줄 이름은 세로 두 칸
        for x, t_ in zip(xs, nm):
            t = b.add_text(t_, height=3.0, dxfattribs={'layer': L['text'], 'rotation': 90, 'style': 'KIT'})
            t.set_placement((x, (rows[k] + rows[k + 1]) / 2.0), align=TA.MIDDLE_CENTER)
    y0, y1 = st['station_y'], st['pole_top']
    for x in (-10, -12):
        b.add_line((x, y0), (x, y1), dxfattribs={'layer': L['pole']})
    y = y0
    while y < y1 + 1e-6:
        b.add_line((-28, y), (-10, y), dxfattribs={'layer': L['pole']})
        y += 50.0
    y, flip = y0, True
    while y < y1 - 1e-6:
        x = -10.5 if flip else -11.5
        b.add_lwpolyline([(x, y, 1.0, 1.0), (x, min(y + 10.0, y1), 0, 0)], format='xyse',
                         dxfattribs={'layer': L['pole']})
        flip = not flip
        y += 10.0


def draw_dxf(doc, sheets, node, S):
    from ezdxf.enums import TextEntityAlignment as TA
    st_ = S['strip']
    dr = S['draw']
    L = st_['layers']
    msp = doc.modelspace()
    VS = st_['unit_h'] / st_['unit_v']
    YD = st_['station_y']
    BT = st_['band_top']
    rows = st_['band_rows']
    BANDY = {k: rows[i + 1] - 1.0 for i, k in enumerate(('측점', '거리', '추가거리', '지반고', '관저고', '토피'))}
    G_MID = (rows[-2] + rows[-1]) / 2.0
    tb = st_['title_block']

    def T(txt, xy, h, layer, align=TA.LEFT, rot=0.0):
        e = msp.add_text(str(txt), height=h, dxfattribs={'layer': layer, 'rotation': rot, 'style': 'KIT'})
        e.set_placement(xy, align=align)
        return e

    def Wd(s, h):
        return len(str(s)) * h * 0.62

    log = []
    x0 = 0.0
    for n, sh in enumerate(sheets, 1):
        st, rc, runs, grade = sh['st'], sh['rc'], sh['runs'], sh['grade']
        A, B = sh['a'], sh['b']
        dmax = max((p.get('D') or 200.0) for p in st) / 1000.0
        ils = [p['il'] for p in st if p['il'] is not None]
        zmin = min(ils) - dmax
        zmax = max([p['gl'] for p in st if p['gl'] is not None] + [z + dmax for z in ils])
        DAT = math.floor(zmin) - dr['datum_below_m']
        nmark = max(4, int(math.ceil((zmax - DAT) / 10.0)) + 1)

        def Y(z, _d=DAT):
            return YD + (z - _d) * VS

        def X(s, _x0=x0, _a=A):
            return _x0 + (s - _a)
        msp.add_blockref(tb, (x0, 0.0))
        if nmark > 4:                                   # 표척 상단 연장
            ytop = YD + 50.0 * (nmark - 1)
            for x in (x0 - 10, x0 - 12):
                msp.add_line((x, st_['pole_top']), (x, ytop), dxfattribs={'layer': L['pole']})
            yk = st_['pole_top'] + 50.0
            while yk <= ytop + 1e-6:
                msp.add_line((x0 - 28, yk), (x0 - 10, yk), dxfattribs={'layer': L['pole']})
                yk += 50.0
            yk, flip = st_['pole_top'], 0
            while yk < ytop - 1e-6:
                xx = x0 - 10.5 if flip % 2 == 0 else x0 - 11.5
                msp.add_lwpolyline([(xx, yk), (xx, min(yk + 10.0, ytop))], dxfattribs={'layer': L['pole']})
                yk += 10.0
                flip += 1
        T('H=1:%d' % st_['unit_h'], (x0 - 28, -4.5), 2.5, L['text'])
        T('%d장' % n, (x0 - 41, 0.0), 7.0, L['memo'], rot=90)
        T('V=1:%d' % st_['unit_v'], (x0 - 31, 0.0), 2.5, L['text'], rot=90)
        for k in range(nmark):                           # 표척 눈금값(a3_split 이 Δ 만큼 다시 쓴다)
            T('%.2f' % (DAT + 10 * k), (x0 + st_['pole_label_dx'], YD + 50 * k + 1.0), 3.0, L['text'])
        t1, t2 = L['band'][0], L['band'][-1]
        for y in (rows[0], BT):
            msp.add_line((x0 - 10, y), (X(B) + 10, y), dxfattribs={'layer': t1})
        for x in (x0 - 10, X(B) + 10):
            msp.add_line((x, rows[0]), (x, BT), dxfattribs={'layer': t1})
        for y in rows[1:-1]:
            msp.add_line((x0 - 10, y), (X(B) + 10, y), dxfattribs={'layer': t2})
        msp.add_line((x0, YD), (X(B), YD), dxfattribs={'layer': t2})

        for u in runs:                                   # 관종 구간(신설오수 D200 / 신설압송 D80)
            a, b = X(u['a']), X(u['b'])
            m = (a + b) / 2
            msp.add_lwpolyline([(a, 111, 0, 2.5, 0), (a + 2.5, 111, 0, 0, 0), (b - 2.5, 111, 2.5, 0, 0),
                                (b, 111, 0, 0, 0)], format='xyseb', dxfattribs={'layer': t1})
            T('L=%.2fM' % (u['b'] - u['a']), (m, 108.5), 3.0, L['text'], TA.MIDDLE_CENTER)
            lab = (dr['pressure_label'] if u['press'] else dr['gravity_label']).format(d=int(round(u['D'])))
            T(lab, (m, 113.5), 3.0, L['text'], TA.MIDDLE_CENTER)

        lastno = None
        pitch = dr['station_pitch_m']
        for p in st:
            x = X(p['s'])
            for ya in rows[:-2]:
                msp.add_line((x, ya - (0 if ya == rows[0] else 1)), (x, ya + 1), dxfattribs={'layer': L['station']})
            m20 = p['s'] % pitch
            if m20 < 0.005 or m20 > pitch - 0.005:
                lastno = int(round(p['s'] / pitch))
                lab = 'NO.%4d' % lastno
            else:
                base = (lastno if lastno is not None else int(p['s'] // pitch)) * pitch
                lab = '+%6.2f' % (p['s'] - base)
            if p['gl'] is not None:
                msp.add_line((x, YD), (x, Y(p['gl'])), dxfattribs={'layer': L['station']})
            T(lab, (x, BANDY['측점']), 2.5, L['text'], TA.MIDDLE_RIGHT, 90)
            T('%.2f' % p['s'], (x, BANDY['추가거리']), 2.5, L['text'], TA.MIDDLE_RIGHT, 90)
            if p.get('dh', 0.0) > 0.005:
                T('%.2f' % p['il_in'], (x - 1.25, BANDY['관저고']), 2.5, L['text'], TA.MIDDLE_RIGHT, 90)
                T('%.2f' % p['il'], (x + 1.25, BANDY['관저고']), 2.5, L['text'], TA.MIDDLE_RIGHT, 90)
            else:
                T('%.2f' % p['il'], (x, BANDY['관저고']), 2.5, L['text'], TA.MIDDLE_RIGHT, 90)
            if p['gl'] is not None:
                T('%.2f' % p['gl'], (x, BANDY['지반고']), 2.5, L['text'], TA.MIDDLE_RIGHT, 90)
                dd = (p.get('D') or 200.0) / 1000.0
                T('%.2f' % (p['gl'] - p.get('il_in', p['il']) - dd), (x, BANDY['토피']), 2.5, L['text'],
                  TA.MIDDLE_RIGHT, 90)
        for i in range(1, len(st)):
            T('%.2f' % (st[i]['s'] - st[i - 1]['s']), (X(st[i]['s']), BANDY['거리']), 2.5, L['text'],
              TA.MIDDLE_RIGHT, 90)

        for i in range(1, len(st)):                      # 지반선·관저선·관정선·관하단선
            a, b = st[i - 1], st[i]
            dA = (b.get('D') or a.get('D') or 200.0) / 1000.0   # 구간 (a, b] 는 b 가 속한 관
            za, zb = a['il'], b.get('il_in', b['il'])
            if a['gl'] is not None and b['gl'] is not None:
                msp.add_line((X(a['s']), Y(a['gl'])), (X(b['s']), Y(b['gl'])), dxfattribs={'layer': L['ground']})
            msp.add_line((X(a['s']), Y(za)), (X(b['s']), Y(zb)), dxfattribs={'layer': L['invert']})
            msp.add_line((X(a['s']), Y(za + dA)), (X(b['s']), Y(zb + dA)), dxfattribs={'layer': L['crown']})
            msp.add_line((X(a['s']), Y(za - dA)), (X(b['s']), Y(zb - dA)), dxfattribs={'layer': L['sub']})
            if b.get('dh', 0.0) > 0.005:                 # 단차(부관)
                x = X(b['s'])
                for dz, ly in ((0.0, L['invert']), (dA, L['crown']), (-dA, L['sub'])):
                    msp.add_line((x, Y(b['il_in'] + dz)), (x, Y(b['il'] + dz)), dxfattribs={'layer': ly})

        ils_at = {round(p['s'], 2): p['il'] for p in st}
        for s_ in sorted(set([r['a'] for r in grade] + [r['b'] for r in grade])):
            msp.add_circle((X(s_), G_MID), 7.5, dxfattribs={'layer': L['station']})
            if round(s_, 2) in ils_at:
                T('%.2f' % ils_at[round(s_, 2)], (X(s_), G_MID), 2.5, L['text'], TA.MIDDLE_CENTER, 90)
        pulled = []
        for r in grade:
            m = X((r['a'] + r['b']) / 2)
            s1 = 'L=%.2fM, H=%.2fM' % (r['L'], r['H'])
            s2 = 'S=%.2f%s' % (r['S'], PERMIL)
            if r['L'] - 15.0 >= max(Wd(s1, 2.0), Wd(s2, 2.0)):
                T(s1, (m, G_MID - 3.75), 2.0, L['text'], TA.MIDDLE_CENTER)
                T(s2, (m, G_MID + 3.75), 2.0, L['text'], TA.MIDDLE_CENTER)
            else:                                        # 폭이 모자라면 기준선 위로 인출
                lv = 0
                while any(abs(m - px) < 34 and lv == pl for px, pl in pulled):
                    lv += 1
                pulled.append((m, lv))
                dy = lv * 9.0
                msp.add_line((m, G_MID), (m, 122.5 + dy), dxfattribs={'layer': L['leader']})
                msp.add_line((m, 122.5 + dy), (m + 16, 122.5 + dy), dxfattribs={'layer': L['leader']})
                T('H=%.2fM' % r['H'], (m + 1, 118.5 + dy), 2.0, L['text'], TA.MIDDLE_LEFT)
                T('L=%.2fM' % r['L'], (m + 1, 121.0 + dy), 2.0, L['text'], TA.MIDDLE_LEFT)
                T(s2, (m + 1, 124.0 + dy), 2.0, L['text'], TA.MIDDLE_LEFT)

        for r in rc:                                      # 관번호 막대
            a, b = X(r['a']), X(r['b'])
            m = (a + b) / 2
            msp.add_lwpolyline([(a, 141, 0, 2.5, 0), (a + 2.5, 141, 0, 0, 0), (b - 2.5, 141, 2.5, 0, 0),
                                (b, 141, 0, 0, 0)], format='xyseb', dxfattribs={'layer': L['station']})
            T('L=%.2fM' % (r['b'] - r['a']), (m, 138.75), 2.5, L['text'], TA.MIDDLE_CENTER)
            T(r['pid'], (m, 143.25), 2.5, L['text'], TA.MIDDLE_CENTER)

        types = dr['manhole_type_mm']
        for p in st:                                      # 맨홀 + 45° 인출 라벨
            nd = p.get('node')
            if nd is None or p['gl'] is None:
                continue
            info = node[nd]
            inner = info['inner'] or next((v for k, v in types.items() if info['mh_type'] and k in info['mh_type']),
                                          dr['manhole_inner_mm'])
            inner = float(inner) / 1000.0
            outer = inner + 0.4
            low = min(p['il'], p.get('il_in', p['il']))
            gy, iy, x = Y(p['gl']), Y(low), X(p['s'])
            for hw, top, bot in ((outer / 2, gy, iy - 2.0), (inner / 2, gy - 1.0, iy - 1.0)):
                msp.add_lwpolyline([(x - hw, top), (x + hw, top), (x + hw, bot), (x - hw, bot), (x - hw, top)],
                                   dxfattribs={'layer': L['manhole']})
            top = gy + 43.5
            msp.add_line((x, gy), (x, top), dxfattribs={'layer': L['leader']})
            msp.add_line((x, top), (x + 23, top + 23), dxfattribs={'layer': L['leader']})
            if info['pump']:
                kind = S['a3']['pump_key']
                if info['pump'] not in ('-', info['name']):          # 펌프장 이름이 맨홀명과 다를 때만
                    kind += '(%s)' % info['pump']
            else:
                kind = info['mh_type'] or '1호맨홀'
                kind = kind if kind.endswith('맨홀') else kind + '맨홀'
            if p.get('dh', 0.0) > 0.05:
                kind += ' 부관(dh=%.2fM)' % p['dh']
            T('%s %s' % (info['name'] or '', kind), (x + 0.18, top + 2.65), 2.5, L['text'], TA.MIDDLE_LEFT, 45)
            T('H=%.2fM' % (p['gl'] - low), (x + 2.65, top + 0.18), 2.2, L['text'], TA.MIDDLE_LEFT, 45)
            no = int(p['s'] // pitch)
            T('NO.%4d+%5.2f' % (no, p['s'] - no * pitch), (x - 1.75, gy + 2.0), 2.5, L['text'], TA.MIDDLE_LEFT, 90)
            xy = info['xy']                              # 측량 표기: X = 북(y), Y = 동(x) — 메모 레이어(비출력)
            T('X=%.4f' % xy[1], (x + 1.75, gy + 2.0), 2.2, L['memo'], TA.MIDDLE_LEFT, 90)
            T('Y=%.4f' % xy[0], (x + 4.25, gy + 2.0), 2.2, L['memo'], TA.MIDDLE_LEFT, 90)

        nobs = 0
        for o in sh['obs']:                               # 지장물(plan_pipe.obstacles)
            x = X(o['s'])
            nobs += 1
            if o['shape'] == 'box':
                b0, top = o['el'], o['el'] + 0.25 + o['h'] + 0.25
                msp.add_lwpolyline([(x - o['w'] / 2, Y(b0)), (x + o['w'] / 2, Y(b0)), (x + o['w'] / 2, Y(top)),
                                    (x - o['w'] / 2, Y(top))], close=True, dxfattribs={'layer': L['box']})
                lab = '%s %s(저면 %.2f)' % (o['kind'] or 'BOX', o['spec'], o['el'])
            elif o['shape'] == 'pipe':
                inv = o['el'] + 0.30                       # 저면EL + 0.30 = 관저(makesw 규약)
                r = o['dmm'] / 2000.0
                msp.add_ellipse((x, Y(inv + r)), major_axis=(0, r * VS), ratio=min(1.0, 1.0 / VS),
                                dxfattribs={'layer': L['old']})
                lab = '%s D=%dMM(IL %.2f)' % (o['kind'] or '횡단관로', int(o['dmm']), inv)
            else:
                msp.add_circle((x, Y(o['el'])), 1.2, dxfattribs={'layer': L['old']})
                lab = '%s(EL %.2f)' % (o['kind'], o['el'])
            g = gl_at(st, o['s'])
            if g is not None:
                msp.add_line((x, Y(g) + 8), (x, Y(g) + 30), dxfattribs={'layer': L['old']})
                T(lab, (x + 1, Y(g) + 31), 2.2, L['old'], TA.MIDDLE_LEFT, 45)
        for w in sh['wat']:                                # 상수관 교차
            g = gl_at(st, w['s'])
            if g is None:
                continue
            x = X(w['s'])
            msp.add_line((x, Y(g) + 4), (x, Y(g) + 18), dxfattribs={'layer': L['water']})
            T(w['label'], (x - 1, Y(g) + 19), 2.0, L['water'], TA.MIDDLE_LEFT, 90)
        log.append(dict(sheet=n, route=sh['rt'], part='%d/%d' % sh['part'], a=round(A, 2), b=round(B, 2),
                        L=round(B - A, 2), manholes=len([p for p in st if p.get('node')]), pipes=len(rc),
                        obstacles=nobs, water=len(sh['wat']), datum=DAT, x0=round(x0, 2),
                        first=rc[0]['pid'] if rc else '', last=rc[-1]['pid'] if rc else ''))
        x0 = X(B) + dr['sheet_gap']
    return log


# ───────────────────────────────────────────── PDF (검토용)
def draw_pdf(pdf, title, st, rc, obs, wat, S):
    import matplotlib.pyplot as plt
    fp = PC.font_props(S['font'])
    plt.rcParams['font.family'] = fp.get_name()
    plt.rcParams['axes.unicode_minus'] = False
    s0 = st[0]['s']
    xs = [p['s'] - s0 for p in st]
    Ls = xs[-1]
    gl = [p['gl'] for p in st]
    il = [p['il'] for p in st] + [p['il_in'] for p in st if p.get('il_in') is not None]
    glv = [g for g in gl if g is not None]
    zmin = math.floor(min(il) - 1)
    zmax = math.ceil(max(glv + il) + 2)
    fig = plt.figure(figsize=(max(16, min(Ls / 30, 20)), 10))
    fig.suptitle(title, fontsize=13, fontweight='bold', x=0.01, ha='left')
    ax = fig.add_axes([0.05, 0.42, 0.93, 0.52])
    ax.set_xlim(-5, Ls + 5)
    ax.set_ylim(zmin, zmax)
    ax.set_ylabel('표고 (m)')
    ax.grid(True, lw=0.3, ls=':')
    gx = [x for x, g in zip(xs, gl) if g is not None]
    ax.plot(gx, glv, color='saddlebrown', lw=1.4, label='지반선')
    px, py = [], []
    for i, p in enumerate(st):
        if i > 0 and p.get('il_in') is not None:
            px.append(xs[i])
            py.append(p['il_in'])
        px.append(xs[i])
        py.append(p['il'])
    D = (rc[0]['D'] if rc else 200.0) / 1000.0
    ax.plot(px, py, color='navy', lw=1.6, label='관저선 (D%d)' % round(D * 1000))
    ax.plot(px, [y + D for y in py], color='navy', lw=0.8, ls='--', label='관정선')
    drop = S['draw']['drop_note_m']
    for i, p in enumerate(st):
        if not p.get('node'):
            continue
        if p['gl'] is not None:
            ax.add_patch(plt.Rectangle((xs[i] - 0.45, p['il'] - 0.1), 0.9, p['gl'] - p['il'] + 0.1,
                                       fill=False, ec='k', lw=0.7))
            ax.text(xs[i], p['gl'] + 0.35, p.get('name', ''), rotation=60, fontsize=7, ha='left', va='bottom')
        if p.get('dh', 0) >= drop:
            ax.text(xs[i], p['il'] - 0.5, '단차 %.2f' % p['dh'], fontsize=6.5, color='red', ha='center', va='top')
    for r in rc:
        xm = (r['a'] + r['b']) / 2 - s0
        ax.text(xm, gl_il_mid(st, (r['a'] + r['b']) / 2) - 0.25,
                'L=%.1fm  S=%.2f%s' % (r['L'], r['S'], PERMIL), fontsize=6.5, ha='center', va='top', color='navy')
    for o in obs:
        g = gl_at(st, o['s'])
        if g is None:
            continue
        x = o['s'] - s0
        ax.plot([x, x], [zmin, g], color='red', lw=0.6, ls='-.')
        ax.text(x, zmin + 0.15, '%s %s' % (o['kind'], o.get('spec', '')), rotation=90, fontsize=6, color='red',
                ha='center', va='bottom')
    for w in wat:
        g = gl_at(st, w['s'])
        if g is None:
            continue
        x = w['s'] - s0
        ax.plot([x, x], [zmin, g], color='dodgerblue', lw=0.6, ls='-.')
        ax.text(x, zmin + 0.15, w['label'], rotation=90, fontsize=6, color='dodgerblue', ha='center', va='bottom')
    ax.legend(loc='upper right', fontsize=8)
    tx = fig.add_axes([0.05, 0.04, 0.93, 0.33])
    tx.set_xlim(-5, Ls + 5)
    tx.set_ylim(0, 8)
    tx.axis('off')
    rows = ['측점', '구간거리', '누가거리', '지반고', '관저고', '토피', '맨홀번호']
    for i, nm in enumerate(rows):
        y = 7 - i
        tx.axhline(y + 0.5, color='k', lw=0.4)
        tx.text(-4, y, nm, fontsize=8, va='center', ha='left', fontweight='bold')
    tx.axhline(7 - len(rows) + 0.5, color='k', lw=0.4)
    prev = None
    for i, p in enumerate(st):
        if not p.get('node') and i not in (0, len(st) - 1):
            continue
        Lseg = (p['s'] - prev) if prev is not None else 0.0
        prev = p['s']
        dd = (p.get('D') or 200.0) / 1000.0
        vals = ['%.1f' % p['s'], '%.1f' % Lseg if Lseg else '', '%.1f' % (p['s'] - s0),
                '%.2f' % p['gl'] if p['gl'] is not None else '',
                ('%.2f' % p['il']) + (('/%.2f' % p['il_in']) if p.get('il_in') is not None
                                      and abs(p['il_in'] - p['il']) > 0.005 else ''),
                ('%.2f' % (p['gl'] - p.get('il_in', p['il']) - dd)) if p['gl'] is not None else '',
                p.get('name', '') or '']
        for k, v in enumerate(vals):
            tx.text(xs[i], 7 - k, v, fontsize=6.2, rotation=90 if k < 6 else 60, ha='center', va='center')
        tx.axvline(xs[i], color='gray', lw=0.3)
    pdf.savefig(fig)
    plt.close(fig)


def gl_il_mid(st, s):
    for i in range(1, len(st)):
        a, b = st[i - 1], st[i]
        if a['s'] <= s <= b['s']:
            f = (s - a['s']) / max(b['s'] - a['s'], 1e-9)
            return a['il'] + (b.get('il_in', b['il']) - a['il']) * f
    return st[-1]['il']


def main():
    ap = argparse.ArgumentParser(description='계획관로 → 노선별 종단면도(설계사 서식 스트립 DXF + 검토용 PDF)')
    PC.add_common_args(ap)
    ap.add_argument('--district', action='append', default=None, help='이 지구만(fields.plan_pipe.district, 여러 번)')
    ap.add_argument('--route', action='append', default=None, help='이 노선(관번호 앞부분)으로 시작하는 것만')
    ap.add_argument('--route-re', default=r'^(.*)-[^-]+$', help='관번호 → 노선 이름 정규식(1그룹)')
    ap.add_argument('--snap', type=float, default=0.5, help='관 끝점 연결 허용 거리(m)')
    ap.add_argument('--title-from', default=None, help='표제 블록·문자 스타일을 가져올 설계사 종단면도 DXF(읽기만)')
    ap.add_argument('--name', default='노선', help='산출 파일 이름 앞부분')
    ap.add_argument('--no-pdf', action='store_true', help='검토용 PDF 생략')
    ap.add_argument('--no-water', action='store_true', help='상수관 교차 표시 생략')
    ap.add_argument('--water-status', default=None, help='상수관 status 필드가 이 값인 것만(예: 사용)')
    a = ap.parse_args()
    cfg = PC.load_cfg(a.config, required=True)
    S = PC.settings(cfg)
    dr = S['draw']
    P, node, water = load_network(cfg, S, a)
    chains = chains_of(P)
    sheets, listing = [], []
    pdf_pages = []
    for rt, ch in chains.items():
        st, rc, runs, L, grade = stations(ch, node, dr['station_pitch_m'], dr.get('pressure_cover_m', 1.2))
        for p in st:
            if p.get('node') is not None:
                p['name'] = node[p['node']]['name']
        obs = obstacles(ch, rc)
        wat = water_crossings(ch, water, rc)
        sheets.extend(split_sheets(rt, st, rc, runs, L, grade, obs, wat, dr['sheet_max_m']))
        pdf_pages.append((rt, ch, st, rc, obs, wat, L))
    od = PC.out_dir(cfg, a.out)
    doc = make_doc(S, a.title_from)
    log = draw_dxf(doc, sheets, node, S)
    dxf_path = od / ('%s_종단스트립.dxf' % a.name)
    doc.saveas(dxf_path)
    print('%-4s %-14s %-5s %8s %8s %5s %5s %4s %4s %6s' % ('장', '노선', '부', '시점', '연장', '맨홀', '구간',
                                                       '지장', '상수', 'DATUM'))
    for r in log:
        print('%-4d %-14s %-5s %8.1f %8.1f %5d %5d %4d %4d %6d'
              % (r['sheet'], r['route'], r['part'], r['a'], r['L'], r['manholes'], r['pipes'], r['obstacles'],
                 r['water'], r['datum']))
    print('노선 %d · 시트 %d · 총연장 %.1f m' % (len(chains), len(log), sum(r['L'] for r in log)))
    PC.write_json(od / ('%s_종단목록.json' % a.name), log)
    print('->', dxf_path)
    if not a.no_pdf:
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib.backends.backend_pdf import PdfPages
        pdf_path = od / ('%s_종단검토.pdf' % a.name)
        lim = dr['pdf_page_m']
        with PdfPages(pdf_path) as pdf:
            for rt, ch, st, rc, obs, wat, L in pdf_pages:
                kinds = '압송' if all(p['press'] for p in ch) else ('자연유하' if not any(p['press'] for p in ch)
                                                                else '자연유하+압송')
                head = '종단면도 — %s (%s, %d구간 L=%.1fm)' % (rt, kinds, len(rc), L)
                n = max(1, math.ceil(L / lim))
                i0, k = 0, 0
                while i0 < len(st) - 1:
                    s_lim = st[i0]['s'] + lim
                    i1 = i0
                    while i1 + 1 < len(st) and st[i1 + 1]['s'] <= s_lim + 0.01:
                        i1 += 1
                    i1 = max(i1, i0 + 1)
                    k += 1
                    sub = st[i0:i1 + 1]
                    a_, b_ = sub[0]['s'], sub[-1]['s']
                    draw_pdf(pdf, head + ('' if n == 1 else '  (%d/%d) 누가 %.0f~%.0f m' % (k, n, a_, b_)), sub,
                             [r for r in rc if r['a'] >= a_ - 1e-6 and r['b'] <= b_ + 1e-6],
                             [o for o in obs if a_ <= o['s'] <= b_], [w for w in wat if a_ <= w['s'] <= b_], S)
                    i0 = i1
        print('->', pdf_path)


if __name__ == '__main__':
    main()
