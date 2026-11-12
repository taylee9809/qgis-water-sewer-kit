# -*- coding: utf-8 -*-
"""r3 지장물·상수관 교차  →  cross.json

    python r3_cross.py
    python r3_cross.py --cross-csv 교차표.csv        # 이미 만든 자연유하 교차표를 그대로(아래 형식)
    python r3_cross.py --force-json 압송교차.json     # 압송 교차 {펌프장:[{s,x,y,외저,관저,표기,형식,…}]}
    python r3_cross.py --no-layers                   # 레이어 교차 계산 없이 표만

레이어 교차
  layers.obstacle_line (11 스킬 중심선) — 필드 invert_start/invert_end(관저_시점/종점) · dia_mm · width_m · height_m · type · label
     교차점 관저 = 시점·종점 관저를 지장물 선 위 비율로 보간. 외저(저면) = 관저 − 부재두께
     (원형관 KS F 4403 관두께, BOX·개거 design.box_wall_m).
     관저 미상: 원형관 GL − unknown_cover_m − D · 복개 BOX GL − box_wall_m − H · 개방 측구(U·V·J) GL − H  → 출처 '규격추정'
  layers.water_pipe (선택) — 상수관 저면 = 교차점 GL − 토피(fields.water_pipe.cover 또는 design.water_cover_assume_m) − 관경
     관보호공 design.water_protect_m 을 붙인다.
같은 지장물을 비스듬히 두 번 자른 교차(간격 ≤ max(3, 폭×1.6+2))는 하나로 합친다(낮은 쪽).

교차표 CSV 형식(원 파이프라인과 같다): 관번호,x,관저,외저,구조물규격,관경_mm,형식,규격폭m,규격높이m,구분,출처
  x = 관 시점(상류)에서 교차점까지 m, 형식 = 원형관 / BOX / 개거 …
"""
import sys, os, re, math, collections, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R

RE_D = re.compile(r'%%C\s*(\d+)|[DΦφØ]\s*(\d{2,4})', re.I)
RE_WH = re.compile(r'(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)')
OPEN_WORDS = ('측구', '개거', '수로', 'U형', 'V형', 'J형')
BOX_WORDS = ('박스', 'BOX', '암거', '라멘', '교량', '복개')


def classify(typ, label, dia, w, h):
    """→ (형식, 개방여부, D mm, W m, H m)"""
    t = '%s %s' % (typ or '', label or '')
    lab = (label or '').strip()
    m = RE_WH.search(lab)
    if not dia:
        md = RE_D.search(lab)
        if md and not m:
            dia = float(md.group(1) or md.group(2))
    if m and not (w and h):
        w, h = float(m.group(1)), float(m.group(2))
    is_open = any(k in t for k in OPEN_WORDS) or lab[:1].upper() in ('U', 'V', 'J')
    is_box = is_open or any(k.lower() in t.lower() for k in BOX_WORDS) or bool(m) or (w and h and not dia)
    if '원형' in t or (dia and not is_box):
        return '원형관', False, dia, (dia / 1000.0 if dia else w), None
    return ('개거' if is_open else 'BOX'), is_open, 0.0, w, h


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 교차(cross.json)'))
    ap.add_argument('--cross-csv', default=None, help='자연유하 교차표 CSV(원 형식)')
    ap.add_argument('--force-json', default=None, help='압송 교차 JSON {펌프장:[…]}')
    ap.add_argument('--no-layers', action='store_true', help='레이어 교차 계산 생략')
    a = ap.parse_args()
    C = R.Ctx(a)
    cfg, P = C.cfg, C.P
    net = C.load('net.json')
    seg, force = net['seg'], net['force']
    gnd = C.load('ground.json', required=False) or {}
    GLn = gnd.get('GL', {})
    from shapely.geometry import LineString, Point

    OBS = collections.defaultdict(list)       # 자연유하 관번호 → 교차
    FOB = collections.defaultdict(list)       # 압송 펌프장 → 교차
    n_lay = collections.Counter()

    lines = [('G', pid, LineString(s['coords'])) for pid, s in seg.items()] + \
            [('F', nm, LineString(c['pts'])) for nm, c in force.items()]
    if lines and not a.no_layers and (cfg.has_layer('obstacle_line') or cfg.has_layer('water_pipe')):
        xs = [c for _, _, g in lines for c in (g.bounds[0], g.bounds[2])]
        ys = [c for _, _, g in lines for c in (g.bounds[1], g.bounds[3])]
        bbox = (min(xs) - 30, min(ys) - 30, max(xs) + 30, max(ys) + 30)
        G = R.Ground(cfg, bbox=bbox)

        def add(kind_, pid, g, s, rec):
            if kind_ == 'G':
                OBS[pid].append(rec)
            else:
                p = g.interpolate(s)
                FOB[pid].append(dict(s=round(s, 2), x=round(p.x, 3), y=round(p.y, 3), 외저=rec['bot'], 관저=rec['el'],
                                     표기=rec['규격'], 형식=rec['kind'] if rec['kind'] != 'BOX' else '박스',
                                     관경mm=rec['dmm'], 규격폭m=rec['w'], 규격높이m=rec['h'], 구분=rec['구분'],
                                     출처=rec['출처']))

        if cfg.has_layer('obstacle_line'):
            ob = R.read_layer(cfg, 'obstacle_line', bbox=bbox)
            f = lambda lg: R.fcol(cfg, 'obstacle_line', lg, ob)
            c_id, c_ty, c_lb = f('obs_id'), f('type'), f('label')
            c_d, c_w, c_h = f('dia_mm'), f('width_m'), f('height_m')
            c_i0, c_i1 = f('invert_start'), f('invert_end')
            c_gb = R.fcol(cfg, 'obstacle_line', 'class2', ob) or ('구분' if '구분' in ob.columns else None)
            for _, r in ob.iterrows():
                og = r.geometry
                if og is None or og.is_empty:
                    continue
                for kind_, pid, g in lines:
                    if not g.intersects(og):
                        continue
                    ip = g.intersection(og)
                    pts = [ip] if ip.geom_type == 'Point' else [q for q in getattr(ip, 'geoms', []) if q.geom_type == 'Point']
                    for q in pts:
                        s = g.project(q)
                        dia = R.val(r, c_d)
                        form, is_open, dmm, w, h = classify(R.val(r, c_ty), R.val(r, c_lb), float(dia) if dia else None,
                                                            R.val(r, c_w), R.val(r, c_h))
                        i0, i1 = R.val(r, c_i0), R.val(r, c_i1)
                        el, src = None, '레이어'
                        if i0 is not None and i1 is not None:
                            fr = og.project(q) / og.length if og.length else 0.0
                            el = float(i0) + (float(i1) - float(i0)) * fr
                        elif i0 is not None or i1 is not None:
                            el = float(i0 if i0 is not None else i1)
                        gl, _ = G.at(q.x, q.y)
                        if el is None:
                            if gl is None:
                                continue
                            if form == '원형관':
                                el = gl - P['unknown_cover_m'] - (dmm or 0) / 1000.0
                                src = '규격추정(GL-%.2f-D)' % P['unknown_cover_m']
                            elif is_open:
                                el = gl - (h or 0)
                                src = '규격추정(GL-0.00-H)'
                            else:
                                el = gl - P['box_wall_m'] - (h or 0)
                                src = '규격추정(GL-%.2f-H)' % P['box_wall_m']
                        bot = el - (R.pipe_wall_m(dmm) if form == '원형관' else P['box_wall_m'])
                        lab = R.val(r, c_lb) or ('D%d' % dmm if dmm else '%s%.1fx%.1f' % (form, w or 0, h or 0))
                        rec = dict(x=round(s, 2), kind='원형관' if form == '원형관' else 'BOX', 규격=str(lab),
                                   el=round(el, 3), bot=round(bot, 3), dmm=dmm or 0.0, w=w, h=h,
                                   구분=R.val(r, c_gb, ''), 출처=src, id=R.val(r, c_id), gl=gl)
                        add(kind_, pid, g, s, rec)
                        n_lay['지장물'] += 1

        if cfg.has_layer('water_pipe'):
            wp = R.read_layer(cfg, 'water_pipe', bbox=bbox)
            c_d = R.fcol(cfg, 'water_pipe', 'dia_mm', wp)
            c_cv = R.fcol(cfg, 'water_pipe', 'cover', wp)
            c_st = R.fcol(cfg, 'water_pipe', 'status', wp)
            act = cfg.value('water_status_active', None)
            if c_st and act:
                wp = wp[wp[c_st] == act]
            for _, r in wp.iterrows():
                og = r.geometry
                if og is None or og.is_empty:
                    continue
                for kind_, pid, g in lines:
                    if not g.intersects(og):
                        continue
                    ip = g.intersection(og)
                    pts = [ip] if ip.geom_type == 'Point' else [q for q in getattr(ip, 'geoms', []) if q.geom_type == 'Point']
                    for q in pts:
                        s = g.project(q)
                        if kind_ == 'G':
                            sg = seg[pid]
                            glu, gld = GLn.get(sg['상류맨홀']), GLn.get(sg['하류맨홀'])
                            gl = (glu + (gld - glu) * s / sg['연장']) if (glu is not None and gld is not None and sg['연장']) \
                                else G.at(q.x, q.y)[0]
                        else:
                            gl = G.at(q.x, q.y)[0]
                        if gl is None:
                            continue
                        dw = float(R.val(r, c_d) or 75) / 1000.0
                        cv = R.val(r, c_cv)
                        cv = float(cv) if cv is not None else P['water_cover_assume_m']
                        bot = gl - cv - dw
                        rec = dict(x=round(s, 2), kind='상수관', 규격='상수D%d' % round(dw * 1000), el=round(bot, 3),
                                   bot=round(bot, 3), dmm=dw * 1000, w=dw, h=None, 구분='상수관',
                                   출처='토피 %s %.2f' % ('필드' if R.val(r, c_cv) is not None else '가정', cv),
                                   prot=P['water_protect_m'], gl=round(gl, 2))
                        add(kind_, pid, g, s, rec)
                        n_lay['상수관'] += 1

    # ── 같은 지장물 이중 교차 병합
    def merge(lst, key_s):
        lst = sorted(lst, key=lambda o: o[key_s])
        out = []
        for o in lst:
            prev = out[-1] if out else None
            same = prev is not None and prev.get('id') is not None and prev.get('id') == o.get('id')
            wdt = (o.get('w') or o.get('규격폭m') or 0) or 0
            if same and abs(o[key_s] - prev[key_s]) <= max(3.0, wdt * 1.6 + 2):
                lowk = 'bot' if 'bot' in o else '외저'
                if o[lowk] < prev[lowk]:
                    out[-1] = o
                continue
            out.append(o)
        return out
    nb = sum(len(v) for v in OBS.values()) + sum(len(v) for v in FOB.values())
    for k in list(OBS):
        OBS[k] = merge(OBS[k], 'x')
    for k in list(FOB):
        FOB[k] = merge(FOB[k], 's')
    na = sum(len(v) for v in OBS.values()) + sum(len(v) for v in FOB.values())
    if n_lay:
        print('레이어 교차 %s → 병합 후 %d건 (이중교차 %d건 합침)' % (dict(n_lay), na, nb - na))

    # ── 교차표 CSV (원 파이프라인 형식)
    if a.cross_csv:
        n = 0
        for r in R.load_any(a.cross_csv):
            pid = r.get('관번호')
            if pid not in seg:
                continue
            try:
                x = float(r['x'])
                el, bot = float(r['관저']), float(r['외저'])
            except (KeyError, TypeError, ValueError):
                continue
            x = max(0.0, min(x, seg[pid]['연장']))
            spec = (r.get('구조물규격') or '').strip()
            try:
                dmm = float(r.get('관경_mm') or 0)
            except ValueError:
                dmm = 0
            form = (r.get('형식') or '').strip()
            kind = 'BOX' if form and form != '원형관' else ('BOX' if spec.lower().startswith('box') else '원형관')

            def _f(k):
                try:
                    v = float(r.get(k) or 0)
                    return v if v > 0 else None
                except ValueError:
                    return None
            if not spec:
                spec = 'D%d' % dmm if dmm else 'D?'
            OBS[pid].append(dict(x=round(x, 2), kind=kind, 규격=spec, el=el, bot=round(bot, 3), dmm=dmm,
                                 w=_f('규격폭m'), h=_f('규격높이m'), 구분=r.get('구분', ''), 출처=r.get('출처', '')))
            n += 1
        print('교차표 CSV %d건 반영' % n)
    if a.force_json:
        fj = R.load_any(a.force_json)
        for k, v in fj.items():
            FOB[k].extend(v)
        print('압송 교차 JSON %d건 반영' % sum(len(v) for v in fj.values()))

    out = dict(meta=dict(created=R.stamp(), step='r3_cross', cross_csv=a.cross_csv, force_json=a.force_json,
                         params={k: P[k] for k in ('obstacle_sep_m', 'box_protect_m', 'box_wall_m', 'unknown_cover_m',
                                                   'water_cover_assume_m', 'water_protect_m')}),
               gravity={k: v for k, v in OBS.items() if v}, force={k: v for k, v in FOB.items() if v})
    print('자연유하 교차 %d건(%d구간) · 압송 교차 %d건'
          % (sum(len(v) for v in OBS.values()), len([k for k in OBS if OBS[k]]), sum(len(v) for v in FOB.values())))
    print('→ %s' % C.save('cross.json', out))


if __name__ == '__main__':
    main()
