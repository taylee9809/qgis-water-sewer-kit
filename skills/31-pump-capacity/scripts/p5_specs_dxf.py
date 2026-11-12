# -*- coding: utf-8 -*-
"""P5 기계 준공도 DXF → 기존 맨홀펌프장 제원 (기기목록 + 펌프장 제원표).

① 기기목록 — '기기번호' 머리글을 찾아 열 위치(기기번호·기기명·사양/형식/규격·동력(kW)·수량 또는 가동/예비/계)를 잡고,
   기기번호 칸의 글자를 행 기준(앵커)으로 삼아 그 행의 사양 글자에서 토출구경·토출량·양정을 읽는다.
   사양 글자 형식: '50A × 0.1㎥/min × 5mH' · '0.1㎥/min×H5.0m' 등 (㎥/min 과 m 양정이 있는 행만 = 펌프).
   행 배정: 사양·동력·수량 글자는 「같은 높이이거나 바로 위에 있는 앵커」, 형식 글자는 가장 가까운 앵커.
② 제원표 — 제목 '<펌프장> … 펌프장 제원표'(--title-re) 아래 창에서 머리글 행(맨 위)과 값 행(맨 아래)을
   x 가 가까운 것끼리 짝짓는다. 가운데 행(단위·맨홀명)은 머리글 꼬리표로 붙인다. 같은 머리글이 두 번이면 _2.

★ 기기목록에는 대개 **펌프장 이름이 없다**(맨홀펌프 #1~#n 뿐). 기기 ↔ 펌프장 대응은 도면 근거를 확인하고
  --map 기기번호=펌프장 으로 준다. 편철 순서로 짐작했다면 그렇게 기록한다(결과 '대응근거').
★ 제원표의 유출관저고를 압송관 최상단 BOP 로 쓴다 — 압송관 정점이 더 높으면 토목 종단도로 확인할 것.
★ 기기목록 양정(mH)은 설계값이다. 납품 명판(기자재 준공도서)이 다를 수 있다 → 명판이 있으면 그 값이 우선.

산출: <out>/specs_dxf/equipment.csv · station_tables.csv · specs_from_dxf.json(= p3·p4 의 --specs 형식)
--write: layers.asbuilt_pump 의 같은 펌프장 행에 제원을 쓴다(백업 후, 있는 열만).
예)
  python -X utf8 p5_specs_dxf.py "<기계 준공도>.dxf" --map "P-101 A/B=XX-A-006" --map "P-102 A/B=XX-A-011"
"""
import json
import re
from pathlib import Path

import pumplib as PL

LABELS = [  # (정규식, 논리키) — 공백 뺀 머리글에 적용
    (r'^지반고', 'ground'), (r'^유입관경', 'in_dia'), (r'^유입관저고', 'in_invert'), (r'^유출관경', 'out_dia'),
    (r'^유출관저고', 'out_invert'), (r'^유출관연장|^압송관연장|^토출관연장', 'force_main_m'),
    (r'^시간최대', 'q_hmax_m3s'), (r'^H\.?W\.?L', 'hwl'), (r'^L\.?W\.?L', 'lwl'), (r'^저면', 'bottom'),
    (r'^맨홀내경|^내경', 'well_dia'),
]
NUM = re.compile(r'^-?\d+(\.\d+)?$')


def texts(path, depth=3):
    import ezdxf
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        from ezdxf import recover
        doc, _ = recover.readfile(path)
    out = []

    def add(e):
        t = e.dxftype()
        if t == 'TEXT':
            out.append((e.dxf.text, e.dxf.insert[0], e.dxf.insert[1], e.dxf.get('height', 1.0) or 1.0, e.dxf.layer))
        elif t == 'MTEXT':
            out.append((e.plain_text(), e.dxf.insert[0], e.dxf.insert[1], e.dxf.get('char_height', 1.0) or 1.0,
                        e.dxf.layer))
        elif t == 'ATTRIB':
            out.append((e.dxf.text, e.dxf.insert[0], e.dxf.insert[1], e.dxf.get('height', 1.0) or 1.0, e.dxf.layer))

    def walk(ents, d):
        for e in ents:
            t = e.dxftype()
            if t == 'INSERT':
                for a in e.attribs:
                    add(a)
                if d < depth:
                    try:
                        walk(list(e.virtual_entities()), d + 1)
                    except Exception:
                        pass
            else:
                add(e)
    walk(doc.modelspace(), 0)
    return [dict(s=(s or '').strip(), x=x, y=y, h=h, layer=ly) for s, x, y, h, ly in out if (s or '').strip()]


def nz(s):
    return re.sub(r'\s+', '', s)


def equipment(T):
    rows = []
    for hd in [t for t in T if re.fullmatch(r'기\s*기\s*번\s*호', t['s'])]:
        h, x0, y0 = hd['h'], hd['x'], hd['y']
        band = [t for t in T if abs(t['y'] - y0) <= 3 * h and x0 - 2 * h <= t['x'] <= x0 + 150 * h]

        def colx(rx):
            c = [t['x'] for t in band if re.search(rx, nz(t['s']))]
            return min(c, key=lambda v: abs(v - x0)) if c else None
        X = dict(name=colx(r'^기기명'), spec=colx(r'사양|형식및규격|^규격'), kw=colx(r'^\(?kW\)?$|동력'),
                 qty=colx(r'^수량$|^수$'), duty=colx(r'^가동$'), stby=colx(r'^예비$'), total=colx(r'^계$'),
                 remark=colx(r'^비고'))
        xr = (X['remark'] or x0 + 100 * h) + 20 * h
        body = [t for t in T if t['y'] < y0 - 0.3 * h and t['y'] > y0 - 1500 * h and x0 - 10 * h <= t['x'] <= xr]
        anchors = sorted([t for t in body if abs(t['x'] - x0) <= 6 * h], key=lambda t: -t['y'])
        if not anchors:
            continue
        # 표 아래 끝: 앵커 간격이 평균의 4배 넘게 벌어지면 끊는다
        gaps = [anchors[i]['y'] - anchors[i + 1]['y'] for i in range(len(anchors) - 1)]
        if gaps:
            med = sorted(gaps)[len(gaps) // 2]
            for i, g in enumerate(gaps):
                if g > 4 * med:
                    anchors = anchors[:i + 1]
                    break
        ybot = anchors[-1]['y'] - 4 * h
        body = [t for t in body if t['y'] >= ybot]
        by = {id(a): [] for a in anchors}
        for t in body:
            if t in anchors:
                continue
            if re.search(r'형식', t['s']):
                a = min(anchors, key=lambda a: abs(a['y'] - t['y']))
            else:
                cand = [a for a in anchors if a['y'] >= t['y'] - 0.5 * h]
                if not cand:
                    continue
                a = min(cand, key=lambda a: a['y'] - t['y'])
            by[id(a)].append(t)

        def near(ts, x, tol, rx=None):
            c = [t for t in ts if x is not None and abs(t['x'] - x) <= tol and (rx is None or re.search(rx, t['s']))]
            return min(c, key=lambda t: abs(t['x'] - x))['s'] if c else None
        for a in anchors:
            ts = by[id(a)]
            spec = next((t['s'] for t in ts if PL.SPEC_RE.search(t['s'])), None)
            if not spec:
                continue
            sp = PL.parse_spec(spec)
            same = [t for t in ts if abs(t['y'] - a['y']) <= 0.3 * h]
            name = near(same, X['name'], 12 * h) if X['name'] else None
            r = dict(기기번호=a['s'], 기기명=name, 형식=next((re.sub(r'^[·\s]*형식\s*:\s*', '', t['s'])
                                                       for t in ts if re.search(r'형식', t['s'])), None),
                     사양=re.sub(r'^[·\s]*(규격|용량)\s*:\s*', '', spec), 토출구경=sp.get('bore'), 토출량_m3min=sp.get('q'),
                     양정_m=sp.get('H'), 동력_kW=None, 가동=None, 예비=None, 계=None, 표_x=round(x0), 표_y=round(y0))
            kw = near(ts, X['kw'], 4 * h, r'^\d+(\.\d+)?$')
            r['동력_kW'] = float(kw) if kw else sp.get('kW')
            if X['duty'] is not None:
                for k, xk in (('가동', X['duty']), ('예비', X['stby']), ('계', X['total'])):
                    v = near(ts, xk, 3 * h, r'^\d+$')
                    r[k] = int(v) if v else None
            else:
                v = near(ts, X['qty'], 4 * h, r'^\d+(\(\d+\))?$')
                if v:
                    m = re.match(r'^(\d+)(?:\((\d+)\))?$', v)
                    r['계'] = int(m.group(1))
                    r['예비'] = int(m.group(2)) if m.group(2) else 0
                    r['가동'] = r['계'] - r['예비']
            rows.append(r)
    return rows


def station_tables(T, title_re, wl, wr, depth):
    out = []
    for tt in [t for t in T if re.search(title_re, t['s'])]:
        m = re.search(title_re, tt['s'])
        sid = m.group('id') if 'id' in m.groupdict() else tt['s']
        H = tt['h']
        win = [t for t in T if t is not tt and tt['x'] - wl * H <= t['x'] <= tt['x'] + wr * H
               and tt['y'] - depth * H <= t['y'] < tt['y'] - 0.3 * H]
        if not win:
            continue
        ys = sorted({round(t['y'], 1) for t in win}, reverse=True)
        rows_y = []
        for y in ys:
            if rows_y and abs(rows_y[-1][-1] - y) <= 0.3 * min(t['h'] for t in win):
                rows_y[-1].append(y)
            else:
                rows_y.append([y])
        grp = [[t for t in win if round(t['y'], 1) in set(r)] for r in rows_y]
        if len(grp) < 2:
            continue
        lab, val, mid = grp[0], grp[-1], [t for g in grp[1:-1] for t in g]
        rec, seen = dict(펌프장=sid, _title=tt['s']), {}
        for v in sorted(val, key=lambda t: t['x']):
            L = min(lab, key=lambda t: abs(t['x'] - v['x']))
            key = nz(L['s'])
            sub = [t for t in mid if abs(t['x'] - L['x']) <= 3 * H]
            tag = min(sub, key=lambda t: abs(t['x'] - L['x']))['s'] if sub else ''
            lg = next((k for rx, k in LABELS if re.search(rx, key, re.I)), key)
            seen[lg] = seen.get(lg, 0) + 1
            if seen[lg] > 1:
                lg = '%s_%d' % (lg, seen[lg])
            rec[lg] = float(v['s']) if NUM.match(v['s']) else v['s']
            if tag and not re.fullmatch(r'\(?(m|mm|㎥/sec|㎥/s|㎥/min)\)?', tag):
                rec[lg + '_꼬리표'] = tag
        out.append(rec)
    return out


def main():
    ap = PL.base_parser(__doc__, net=False)
    ap.add_argument('dxf', nargs='+', help='기계 준공도 DXF')
    ap.add_argument('--map', action='append', default=[], metavar='기기번호=펌프장',
                    help='기기목록 행 ↔ 펌프장 (도면 근거 확인 후). 여러 번')
    ap.add_argument('--map-note', default='--map 지정', help='대응 근거 메모(결과에 남는다)')
    ap.add_argument('--title-re', default=r'(?P<id>\S+)\s+.*펌프장\s*제원표',
                    help='제원표 제목 정규식 (이름 그룹 id = 펌프장)')
    ap.add_argument('--window', default='40,50,8', help='제원표 창 = 제목 글자높이의 배수 왼쪽,오른쪽,아래 (기본 40,50,8)')
    ap.add_argument('--write', action='store_true', help='asbuilt_pump 레이어에 쓴다(백업 후)')
    args = ap.parse_args()
    cfg = PL.load_cfg(args, optional=bool(args.outdir))
    P = PL.params(cfg, args.set)
    out = PL.out_dir(cfg, args, 'specs_dxf')
    wl, wr, dp = (float(v) for v in args.window.split(','))

    eq, tb = [], []
    for p in args.dxf:
        T = texts(p)
        e = equipment(T)
        s = station_tables(T, args.title_re, wl, wr, dp)
        for r in e + s:
            r['도면'] = Path(p).name
        eq += e
        tb += s
        print('%s — 글자 %d · 기기목록 펌프 %d행 · 제원표 %d개' % (Path(p).name, len(T), len(e), len(s)))
    PL.dump_csv(out / 'equipment.csv', eq)
    cols = sorted({k for r in tb for k in r}, key=lambda k: (k not in ('펌프장', '도면'), k))
    PL.dump_csv(out / 'station_tables.csv', tb, cols)

    print('\n기기목록(펌프):')
    for r in eq:
        print('  %-12s %-14s %-24s q=%s H=%s %sA kW=%s 가동/예비/계=%s/%s/%s'
              % (r['기기번호'], r['기기명'] or '-', r['사양'], r['토출량_m3min'], r['양정_m'], r['토출구경'],
                 r['동력_kW'], r['가동'], r['예비'], r['계']))
    print('\n제원표:')
    for r in tb:
        print('  ' + ', '.join('%s=%s' % (k, v) for k, v in r.items() if not k.startswith('_') and k != '도면'))

    mp = PL.parse_kv(args.map, cast=None)
    specs = {}
    for r in tb:
        d = specs.setdefault(r['펌프장'], {})
        for k_in, k_out in (('ground', 'ground'), ('in_invert', 'in_invert'), ('out_invert', 'bop'),
                            ('force_main_m', 'force_main_m'), ('hwl', 'hwl'), ('lwl', 'lwl'), ('bottom', 'bottom')):
            if isinstance(r.get(k_in), float):
                d[k_out] = r[k_in]
        if isinstance(r.get('out_dia'), str) and re.search(r'\d+', r['out_dia']):
            d['back_dia_mm'] = float(re.search(r'\d+', r['out_dia']).group())
        if isinstance(r.get('q_hmax_m3s'), float):
            d['q_hmax_m3s'] = r['q_hmax_m3s']
        if 'hwl' in d and 'lwl' in d:
            d['effective_depth_m'] = round(d['hwl'] - d['lwl'], 3)
        d['_src'] = '%s 제원표' % r['도면']
    for r in eq:
        st = mp.get(r['기기번호'])
        if not st:
            continue
        d = specs.setdefault(st, {})
        d.update(q_m3min=r['토출량_m3min'], head_m=r['양정_m'], power_kw=r['동력_kW'], tag=r['기기번호'],
                 spec=r['사양'], 대응근거=args.map_note)
        if r['토출구경']:
            d['bore'] = r['토출구경']
        if r['가동']:
            d['n_duty'] = r['가동']
        if r['예비'] is not None:
            d['n_standby'] = r['예비']
        d['_src'] = (d.get('_src', '') + ' + 기기목록').strip(' +')
    unmapped = [r['기기번호'] for r in eq if r['기기번호'] not in mp]
    PL.dump_json(out / 'specs_from_dxf.json', specs)
    if unmapped:
        print('\n★ 펌프장과 짝짓지 않은 기기 %d행: %s — 기기목록엔 개소명이 없다. 도면 근거 확인 후 --map'
              % (len(unmapped), ', '.join(unmapped)))
    print('\n제원(--specs 형식) %d개소 → %s' % (len(specs), out / 'specs_from_dxf.json'))

    if args.write:
        if cfg is None or not cfg.has_layer('asbuilt_pump'):
            raise SystemExit('--write: layers.asbuilt_pump 가 없다.')
        F = PL.asbuilt_fields(cfg)
        up = {k: {lg: v for lg, v in d.items() if not lg.startswith('_') and lg != '대응근거'} for k, d in specs.items()}
        n, miss, skip = PL.write_layer(cfg, 'asbuilt_pump', 'pump_id', up, F)
        print('asbuilt_pump 갱신 %d행 · 레이어에 없는 펌프장 %s · 안 쓴 항목(열 없음) %s' % (n, miss, skip))


if __name__ == '__main__':
    main()
