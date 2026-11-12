# -*- coding: utf-8 -*-
"""31-pump-capacity 공용 — 설정·산식·망(.flo / 계획관로 레이어)·펌프장 제원·레이어 쓰기.

산식은 '원 계산서 방식'(사내 기계 용량계산서 워크북)을 그대로 옮긴 것이다. 값은 전부
`design.pump.*`(설정) → `--set 키=값`(인자) 순으로 덮어쓴다. 내장값 = PUMP_DEFAULTS.

    유속     v  = q·4 / π / (D/1000)² / 60                 q ㎥/min, D mm → m/s   (π = 3.1415, 원 계산서 값)
    기계손실 hf = Σ 이형관 K·v²/2g + 직관 f·L/D·v²/2g + 토출구 K·vb²/2g + 편락관 K·(vb−v)²/2g
    관로손실 hl = 10.666·C^-1.85·(D/1000)^-4.87·(q/60)^1.85·L     (Hazen-Williams, C = 100)
    실양정   ha = 압송관 최상단 BOP − LWL
    전양정   H  = ROUND(ha + hf + hl + 여유 1.5, 0),  hv = H − (ha+hf+hl)
    축동력   P  = 0.163·γ·q·H / η × 여유율             (η = 0.23, 여유율 1.15) → 표준전동기표
    펌프용량 q  = MAX(ROUNDUP(시간최대 ㎥/min ÷ 가동대수, 2), 최소 0.10)
    HWL = 유입관저 − 0.05,  LWL = HWL − 유효수심 0.5,  저면 = LWL − 사수심 0.55
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import json
import math
import re
import sys
from pathlib import Path

KIT = next(p for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())
sys.path.insert(0, str(KIT / 'lib'))
import kitconfig  # noqa: E402
import flospec  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

OUT_SUB = '31-pump'

# ───────────────────────────────────────────── 내장값 (= 원 계산서 방식)
PUMP_DEFAULTS = {
    'q_min_m3min': 0.10,            # 제작사 최소 토출량
    'q_roundup_digits': 2,          # 펌프용량 ROUNDUP 자리수
    'n_duty': 1,                    # 가동대수 (예비 별도)
    'hwl_below_inlet_m': 0.05,      # HWL = 유입관저 − 이 값
    'effective_depth_m': 0.5,       # 유효수심 (HWL − LWL)
    'dead_depth_m': 0.55,           # 사수심 (LWL − 저면)
    'well_dia_m': 1.5,              # 흡수정(원형 맨홀) 내경
    'head_margin_m': 1.5,           # 여유양정
    'head_round': 'round',          # round = 엑셀 ROUND(,0) / ceil = 올림 / none = 반올림 안 함
    'efficiency': 0.23,             # 전효율 (0.1~0.2 ㎥/min 급 관행값)
    'power_margin': 1.15,           # 동력 여유율
    'specific_gravity': 1.0,
    'hw_c': 100,                    # Hazen-Williams C
    'pi': 3.1415,                   # 원 계산서가 π 대신 쓰는 값
    'g': 9.8,
    'bore_sizes_mm': [50, 65, 80, 100, 125, 150],
    'bore_velocity_mps': [1.5, 3.0],   # 토출구경 = 146·√(q/v) 가 이 유속 범위에 드는 최소 표준구경
    'bore_min_mm': 50,
    'existing_bore_mm': 50,          # 기존 펌프 토출구경을 모를 때
    'existing_back_dia_mm': None,    # 기존 후단 매설배관 관경. None = 망(압송관) 관경
    'max_starts_per_h': 10,          # 자체기준 (소형 수중펌프 제조사 통상 10~15 회/h)
    'alt_force_main_dia_mm': [80, 100, 150],
    'existing_head_check': 'no_margin',   # 기존 양정 판정: no_margin = ha+hf+hl ≤ 설치양정 / margin = ROUND(+여유) ≤ 설치양정
    # [축동력 하한 kW, 표준전동기 kW] — 축동력 ≥ 하한인 마지막 행 (엑셀 VLOOKUP 근사일치와 같다)
    'motor_table': [[0, 0.4], [0.35, 0.75], [0.75, 1.5], [1.5, 2.2], [2.2, 3.7], [3.7, 5.5], [5.5, 7.5],
                    [7.5, 11], [11, 15], [15, 22], [22, 30], [30, 37], [37, 45], [45, 55]],
    # on: pump = 펌프 토출구경 기준 / back = 후단 매설배관 기준 / reducer = (vb − v)² (편락관)
    'fittings': [
        {'name': '흡입구', 'k': 0.5, 'on': 'pump'},
        {'name': '직관', 'k': 0.05, 'on': 'pump', 'length_m': 5},
        {'name': '엘보', 'k': 0.17, 'on': 'pump'},
        {'name': 'TEE', 'k': 0.8, 'on': 'pump'},
        {'name': 'CHK V/V', 'k': 1.5, 'on': 'pump'},
        {'name': 'GATE V/V', 'k': 0.175, 'on': 'pump'},
        {'name': 'ELBOW', 'k': 0.17, 'on': 'pump'},
        {'name': '토출구', 'k': 1.0, 'on': 'back'},
        {'name': '편락관', 'k': 0.8, 'on': 'reducer'},
    ],
}

# asbuilt_pump 논리 필드 → 내장 기본 열이름 (설정 fields.asbuilt_pump 가 우선)
ASBUILT_PUMP_FIELDS = {
    'pump_id': '펌프장명', 'district': '지구', 'in_invert': '유입관저고', 'depth': '맨홀깊이',
    'q_m3min': '펌프토출량_m3min', 'static_head_m': '실양정', 'spec': '펌프규격', 'source': '자료출처',
    'head_m': '펌프전양정_m', 'power_kw': '펌프동력_kw', 'hwl': 'hwl', 'lwl': 'lwl', 'bottom': '저면고',
    'bop': '압송관최상단BOP', 'force_main_m': '압송연장_m', 'back_dia_mm': '후단관경_mm',
    'bore': '펌프토출구경', 'well_dia_m': '맨홀내경', 'effective_depth_m': '유효수심',
    'well_volume_m3': '유효저수량_m3', 'n_duty': '가동대수', 'n_standby': '예비대수', 'tag': '펌프기기번호',
    'ground': '지반고',
}

PUMP_LOGICAL = ('q_m3min', 'head_m', 'power_kw', 'hwl', 'lwl', 'bop', 'force_main_m', 'back_dia_mm',
                'bore', 'well_dia_m', 'effective_depth_m', 'well_volume_m3', 'n_duty', 'static_head_m',
                'in_invert', 'spec', 'district', 'bottom')


# ───────────────────────────────────────────── 설정·인자
def base_parser(desc, net=True):
    ap = argparse.ArgumentParser(description=desc, formatter_class=argparse.RawDescriptionHelpFormatter)
    kitconfig.add_config_arg(ap)
    ap.add_argument('--outdir', help='산출 폴더 (기본 <outputs.root>/31-pump)')
    ap.add_argument('--set', action='append', default=[], metavar='키=값',
                    help='design.pump.<키> 덮어쓰기 (값은 JSON, 예 --set efficiency=0.4 --set head_round=\'"ceil"\')')
    if net:
        ap.add_argument('--flo', action='append', default=[], metavar='파일.flo',
                        help='makesw .flo (여러 개면 합침). 없으면 layers.plan_pipe 로 망을 만든다')
        ap.add_argument('--net', help='p1_network.py 가 쓴 network.json 을 그대로 쓴다')
        ap.add_argument('--pump', action='append', default=[], metavar='노드',
                        help='펌프장으로 강제 지정할 노드 (자동판별에 안 걸리는 중계펌프장 등)')
        ap.add_argument('--existing', action='append', default=[], metavar='노드',
                        help='기존(준공) 펌프장 노드. 레이어 구분값으로도 잡힌다')
        ap.add_argument('--specs', help='기존 펌프 제원 파일(.json/.csv) — asbuilt_pump 레이어 대신·추가')
        ap.add_argument('--inlet', action='append', default=[], metavar='노드=관저고',
                        help='유입관저고 가정값 (망에 유입관이 없는 펌프장 등). 결과에 「가정」으로 남는다')
        ap.add_argument('--snap', type=float, default=0.5, help='레이어 망: 관 끝점 연결 허용거리 m (기본 0.5)')
    return ap


def load_cfg(args, optional=False):
    """설정 로드. optional 이면 못 찾아도 None (--flo·--outdir 만으로 도는 경우)."""
    try:
        return kitconfig.load(getattr(args, 'config', None))
    except SystemExit as e:
        if optional:
            print('※ sewer.config.json 없이 실행 — 내장값·인자만 쓴다 (%s)' % str(e).splitlines()[0])
            return None
        raise


def params(cfg, sets=()):
    P = json.loads(json.dumps(PUMP_DEFAULTS))
    if cfg is not None:
        user = cfg.get('design.pump', {}) or {}
        for k, v in user.items():
            P[k] = v
        P['_dmax'] = float(cfg.get('design.pressure_max_d_mm', 100) or 100)
        P['_vmin'] = float(cfg.get('design.min_velocity_mps', 0.6) or 0.6)
        P['_vmax'] = float(cfg.get('design.max_velocity_mps', 3.0) or 3.0)
        P['_manning_n'] = float(cfg.get('design.manning_n', 0.011) or 0.011)
    else:
        P.update(_dmax=100.0, _vmin=0.6, _vmax=3.0, _manning_n=0.011)
    for s in sets or ():
        k, _, v = s.partition('=')
        k = k.strip()
        try:
            P[k] = json.loads(v)
        except ValueError:
            P[k] = v
    return P


def out_dir(cfg, args, *parts):
    if getattr(args, 'outdir', None):
        d = Path(args.outdir)
        for p in parts:
            d = d / p
        d.mkdir(parents=True, exist_ok=True)
        return d
    if cfg is None:
        raise SystemExit('설정 파일이 없으면 --outdir 를 줘야 한다.')
    return cfg.out_dir(OUT_SUB, *parts)


def parse_kv(items, cast=float):
    out = {}
    for s in items or ():
        k, _, v = s.partition('=')
        out[k.strip()] = cast(v) if cast else v
    return out


# ───────────────────────────────────────────── 산식
def roundup(x, k):
    return math.ceil(x * 10 ** k - 1e-9) / 10 ** k


def xround(x):
    """엑셀 ROUND(x,0) — 0.5 는 올림 (파이썬 round 의 은행가 반올림과 다르다)."""
    return math.floor(x + 0.5)


def vel(q, d_mm, P):
    return q * 4 / P['pi'] / (d_mm / 1000) ** 2 / 60


def bore(q, P):
    vlo, vhi = P['bore_velocity_mps']
    lo, hi = 146 * math.sqrt(q / vhi), 146 * math.sqrt(q / vlo)
    inr = [s for s in P['bore_sizes_mm'] if lo <= s <= hi]
    if inr:
        return inr[0]
    big = [s for s in P['bore_sizes_mm'] if s >= max(lo, P['bore_min_mm'])]
    return big[0] if big else P['bore_sizes_mm'][-1]


def hf_items(q, d_pump, d_back, P):
    """기계배관 손실 항목 [(항목, 규격mm, K, 손실m)]."""
    g2 = 2 * P['g']
    v, vb = vel(q, d_pump, P), vel(q, d_back, P)
    out = []
    for f in P['fittings']:
        on, k = f.get('on', 'pump'), float(f['k'])
        if on == 'reducer':
            out.append((f['name'], d_back, k, k * (vb - v) ** 2 / g2))
            continue
        d, vv = (d_pump, v) if on == 'pump' else (d_back, vb)
        L = f.get('length_m')
        h = k * L / (d / 1000) * vv * vv / g2 if L else k * vv * vv / g2
        out.append((f['name'], d, k, h))
    return out


def hf(q, d_pump, d_back, P):
    return sum(x[-1] for x in hf_items(q, d_pump, d_back, P))


def hl_hw(q, d_mm, L, P):
    return 10.666 * float(P['hw_c']) ** -1.85 * (d_mm / 1000) ** -4.87 * (q / 60) ** 1.85 * L


def total_head(base, P, margin=None):
    m = base + (P['head_margin_m'] if margin is None else margin)
    mode = P.get('head_round', 'round')
    if mode == 'ceil':
        return math.ceil(m - 1e-9)
    if mode == 'none':
        return m
    return xround(m)


def shaft_kw(q, H, P):
    return 0.163 * P['specific_gravity'] * q * H / P['efficiency'] * P['power_margin']


def motor_kw(p, P):
    kw = None
    for lo, k in P['motor_table']:
        if p >= lo:
            kw = k
    return kw


def pump_q(Qm3min, P, n_duty=None, digits=None):
    n = n_duty or P['n_duty'] or 1
    d = P['q_roundup_digits'] if digits is None else digits
    return max(roundup(Qm3min / n, d), P['q_min_m3min'])


def well_volume(P, spec=None):
    spec = spec or {}
    if spec.get('well_volume_m3'):
        return float(spec['well_volume_m3'])
    D = float(spec.get('well_dia_m') or P['well_dia_m'])
    if spec.get('hwl') is not None and spec.get('lwl') is not None:
        h = float(spec['hwl']) - float(spec['lwl'])
    else:
        h = float(spec.get('effective_depth_m') or P['effective_depth_m'])
    return P['pi'] / 4 * D ** 2 * h


def cycles(V, q, Qm):
    """흡수정 운전 — 최소운전시간·최대기동횟수(유입 = q/2 일 때)·시간최대 유입 시 운전/정지/기동."""
    r = dict(t_min=V / q, N_max=60 / (4 * V / q))
    r['운전_min'] = V / (q - Qm) if Qm < q else None
    r['정지_min'] = V / Qm if Qm > 0 else None
    r['N_시간최대'] = 60 / (r['운전_min'] + r['정지_min']) if (Qm < q and Qm > 0) else None
    return r


def head_calc(q, ha, L, d_pump, d_back, P, margin=None):
    it = hf_items(q, d_pump, d_back, P)
    h_f = sum(x[-1] for x in it)
    h_l = hl_hw(q, d_back, L, P)
    base = ha + h_f + h_l
    H = total_head(base, P, margin)
    p = shaft_kw(q, H, P)
    return dict(ha=ha, hf=h_f, hl=h_l, base=base, H=H, hv=H - base, 축동력=p, 동력=motor_kw(p, P),
                v=vel(q, d_back, P), hf_items=it)


def manning_partial(D_mm, S, q_lps, n):
    """원형관 부분만관 — (만관유속, 만관유량 L/s, 실유속, 수심 m, 수심비, 사용률 %)."""
    Dm = D_mm / 1000
    I = max(S, 1e-6)
    Vf = (1 / n) * (Dm / 4) ** (2 / 3) * math.sqrt(I)
    A = math.pi * Dm * Dm / 4
    Qf = Vf * A * 1000
    if q_lps <= 0:
        return Vf, Qf, 0.0, 0.0, 0.0, 0.0
    t = min(q_lps / Qf, 0.999)
    lo, hi = 1e-9, 1.0
    for _ in range(80):
        m = (lo + hi) / 2
        th = 2 * math.acos(1 - 2 * m)
        if ((th - math.sin(th)) / (2 * math.pi)) * (1 - math.sin(th) / th) ** (2 / 3) < t:
            lo = m
        else:
            hi = m
    hd = (lo + hi) / 2
    th = 2 * math.acos(1 - 2 * hd)
    return Vf, Qf, Vf * (1 - math.sin(th) / th) ** (2 / 3), hd * Dm, hd, q_lps / Qf * 100


SPEC_RE = re.compile(r'(?:(?:(\d+)\s*A|(?:%%[cC]|Φ|ø|∅)\s*(\d+)\s*(?:mm|㎜)?)\s*[×xX\*]\s*)?'
                     r'([\d.]+)\s*(?:㎥|m3|m³)/min\s*[×xX\*]\s*H?\s*=?\s*([\d.,]+)\s*m\s*H?')
KW_RE = re.compile(r'([\d.]+)\s*kW', re.I)


def parse_spec(s):
    """'50A × 0.1㎥/min × 5mH' · '0.1㎥/min×H5.0m' · '%%C50mm x 0.05㎥/min x 5mH' · '0.13×9mH×1.5kW'
    → dict(bore, q, H, kW). 송풍기처럼 양정이 ㎜Aq 인 사양은 걸리지 않는다."""
    if not s:
        return {}
    s = str(s)
    m = SPEC_RE.search(s)
    out = {}
    if m:
        if m.group(1) or m.group(2):
            out['bore'] = int(m.group(1) or m.group(2))
        out['q'] = float(m.group(3))
        out['H'] = float(m.group(4).replace(',', ''))
    else:
        m2 = re.search(r'([\d.]+)\s*[×xX\*]\s*([\d.]+)\s*mH', s)
        if m2:
            out['q'], out['H'] = float(m2.group(1)), float(m2.group(2))
    k = KW_RE.search(s)
    if k:
        out['kW'] = float(k.group(1))
    return out


# ───────────────────────────────────────────── 망
def _f(v, d=None):
    try:
        if v is None or v == '':
            return d
        x = float(v)
        return d if x != x else x          # NaN → 기본값
    except (TypeError, ValueError):
        return d


def recs_from_flo(paths):
    recs, seen = [], set()
    for p in paths:
        F = flospec.read_flo(p)
        for r in F['recs']:
            if r.get('_err'):
                print('  ! %s %d행 건너뜀: %s' % (Path(p).name, r['_line'], r['_err']))
                continue
            if r['up'] in seen:
                continue
            seen.add(r['up'])
            recs.append(dict(up=r['up'], dn=r['dn'], L=_f(r['L'], 0.0), D=_f(r['D'].split()[0] if r['D'] else None, 0.0),
                             ilu=_f(r['ilu']), ild=_f(r['ild']), gl=_f(r['gls'][0]) if r['gls'] else None,
                             Q=_f(r['Q'], 0.0), src='flo'))
    return recs


def _endpoints(geom):
    if geom is None or geom.is_empty:
        return None, None
    if geom.geom_type.startswith('Multi'):
        parts = list(geom.geoms)
        return parts[0].coords[0][:2], parts[-1].coords[-1][:2]
    c = list(geom.coords)
    return c[0][:2], c[-1][:2]


def recs_from_layer(cfg, snap=0.5):
    """layers.plan_pipe → flo 와 같은 레코드. 노드 이름: us_mh/ds_mh 매핑이 있으면 그것,
    없으면 관 시점 = 관번호(관번호 = 시점 맨홀명 관행), 종점 = 그 끝점에서 시작하는 관의 관번호.
    유량: fields.plan_pipe.flow_m3d (누가유량) → 노드 자체유량으로 되돌려 쓴다."""
    gdf = cfg.read_layer('plan_pipe')
    F = cfg.fields('plan_pipe')
    need = ['pipe_id', 'dia_mm', 'us_invert', 'ds_invert', 'flow_m3d']
    miss = [k for k in need if not F.get(k)]
    if miss:
        raise SystemExit('plan_pipe 로 망을 만들려면 fields.plan_pipe.%s 매핑이 필요하다 (또는 --flo).' % '/'.join(miss))
    rows = []
    for _, r in gdf.iterrows():
        a, b = _endpoints(r.geometry)
        L = _f(r[F['length_m']]) if F.get('length_m') else None
        if L is None and r.geometry is not None:
            L = r.geometry.length
        rows.append(dict(id=str(r[F['pipe_id']]), a=a, b=b, L=L or 0.0, D=_f(r[F['dia_mm']], 0.0),
                         ilu=_f(r[F['us_invert']]), ild=_f(r[F['ds_invert']]),
                         gl=_f(r[F['us_ground']]) if F.get('us_ground') else None,
                         cum=_f(r[F['flow_m3d']]),
                         up=str(r[F['us_mh']]) if F.get('us_mh') else None,
                         dn=str(r[F['ds_mh']]) if F.get('ds_mh') else None))
    if not all(x['up'] and x['dn'] for x in rows):
        cell = lambda pt: (int(pt[0] // snap), int(pt[1] // snap))
        grid = collections.defaultdict(list)
        for x in rows:
            if x['a'] is not None:
                grid[cell(x['a'])].append(x)
        for x in rows:
            x['up'] = x['up'] or x['id']
            if x['dn']:
                continue
            hit, best = None, snap
            if x['b'] is not None:
                cx, cy = cell(x['b'])
                for dx in (-1, 0, 1):                      # 가장 가까운 시점 (격자 이웃 칸까지)
                    for dy in (-1, 0, 1):
                        for y in grid.get((cx + dx, cy + dy), ()):
                            dd = math.dist(x['b'], y['a'])
                            if y is not x and dd <= best:
                                hit, best = y, dd
            x['dn'] = hit['id'] if hit else 'END:' + x['id']
    ups = collections.defaultdict(list)
    for x in rows:
        ups[x['dn']].append(x)
    recs, nan, neg = [], 0, 0
    for x in rows:
        if x['cum'] is None:
            nan += 1
        selfq = (x['cum'] or 0.0) - sum(u['cum'] or 0.0 for u in ups[x['up']])
        if selfq < -1e-6:
            neg += 1
        recs.append(dict(up=x['up'], dn=x['dn'], L=x['L'], D=x['D'], ilu=x['ilu'], ild=x['ild'], gl=x['gl'],
                         Q=max(selfq, 0.0) / 86400, cum=None if x['cum'] is None else x['cum'] / 86400, src='layer'))
    if nan or neg:
        print('  ! plan_pipe 유량(%s): 결측 %d구간 · 상류 합보다 작은 구간 %d — 펌프장 Q 는 그 펌프 유출관의 누가유량을 '
              '그대로 쓰지만, 결측이면 역산 누가로 대신해 틀릴 수 있다. 수리계산 .flo 가 있으면 --flo 를 쓸 것.'
              % (F['flow_m3d'], nan, neg))
    return recs


def accumulate(recs):
    """노드 누가유량(㎥/s). 위상순(Kahn) — 순환이면 중단."""
    ups = collections.defaultdict(list)
    for r in recs:
        ups[r['dn']].append(r)
    selfQ = {r['up']: r['Q'] or 0.0 for r in recs}
    nodes = {x for r in recs for x in (r['up'], r['dn'])}
    indeg = {n: len(ups[n]) for n in nodes}
    down = {r['up']: r for r in recs}
    acc = {n: selfQ.get(n, 0.0) for n in nodes}
    ready = [n for n in nodes if indeg[n] == 0]
    done = 0
    while ready:
        n = ready.pop()
        done += 1
        r = down.get(n)
        if r:
            acc[r['dn']] += acc[n]
            indeg[r['dn']] -= 1
            if indeg[r['dn']] == 0:
                ready.append(r['dn'])
    if done != len(nodes):
        raise SystemExit('망에 순환이 있다 (위상정렬 %d / %d) — flospec 검증부터.' % (done, len(nodes)))
    return acc


def build_network(recs, P, force_pumps=(), existing=(), inlet=None, layer_inlet=None):
    """펌프장 판별·압송관 추적·누가유량.
    펌프장 = (D ≤ dmax 인 유출관) 이고 (유입관이 없거나 D > dmax 유입관이 있는 노드) ∪ 지정 노드 ∪ 기존 노드."""
    dmax = P['_dmax']
    down = {r['up']: r for r in recs}
    ups = collections.defaultdict(list)
    for r in recs:
        ups[r['dn']].append(r)
    acc = accumulate(recs)
    selfQ = {r['up']: r['Q'] or 0.0 for r in recs}
    pumps = set(n for n in list(force_pumps) + list(existing) if n in down)
    for n in list(force_pumps) + list(existing):
        if n not in down:
            print('  ! 지정 펌프장 %s 이(가) 망에 없다 — 건너뜀' % n)
    for r in recs:
        if r['D'] <= dmax and (not ups[r['up']] or any(x['D'] > dmax for x in ups[r['up']])):
            pumps.add(r['up'])
    inlet = inlet or {}
    layer_inlet = layer_inlet or {}
    exist = set(existing)
    st = []

    def walk(m, seen):
        for x in ups[m]:
            if x['up'] in pumps:
                seen.append(x['up'])
            else:
                walk(x['up'], seen)
        return seen

    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    for n in sorted(pumps, key=lambda x: (x in exist, x)):
        r0 = down[n]
        path, cur = [r0], r0
        while (cur['dn'] in down and down[cur['dn']]['D'] <= dmax and cur['dn'] not in pumps
               and not any(x['D'] > dmax for x in ups[cur['dn']])):
            cur = down[cur['dn']]
            path.append(cur)
        inv = lambda p: max(v for v in (p['ilu'], p['ild']) if v is not None)
        top_at = max(path, key=inv)
        grav_in = [x for x in ups[n] if x['D'] > dmax]
        fm_in = [x for x in ups[n] if x['D'] <= dmax]
        use_cum = r0.get('cum') is not None                          # 레이어 망: 유출관 누가유량 우선
        Qn = r0['cum'] if use_cum else acc[n]
        rec = dict(펌프장=n, 구분='기존' if n in exist else '신설', 토출대상=cur['dn'], 압송구간수=len(path),
                   압송연장=round(sum(p['L'] for p in path), 1), 압송관경=int(r0['D']),
                   압송최상단BOP=inv(top_at),
                   BOP위치=top_at['dn'] if (top_at['ild'] or -1e9) >= (top_at['ilu'] or -1e9) else top_at['up'],
                   토출점관저=cur['ild'], 지반고=r0['gl'], 유출관저고=r0['ilu'],
                   유입관=[x['up'] for x in grav_in], 유입압송=[x['up'] for x in fm_in],
                   유입관저고=min([x['ild'] for x in ups[n] if x['ild'] is not None], default=None),
                   자체유량_m3d=selfQ.get(n, 0.0) * 86400,
                   Q_m3s=Qn, Q_m3d=Qn * 86400, Q_m3min=Qn * 60, Q근거='유출관 누가유량' if use_cum else '망 누가')
        if n in inlet:
            rec['유입관저고'] = inlet[n]
            rec['유입관저고_가정'] = '인자 --inlet %s=%s (가정)' % (n, inlet[n])
        elif rec['유입관저고'] is None and layer_inlet.get(n) is not None:
            rec['유입관저고'] = layer_inlet[n]
            rec['유입관저고_가정'] = 'plan_pump.in_invert 값 (망에 유입관 없음)'
        rec['상류펌프장'] = walk(n, [])
        st.append(rec)
    return st


# ───────────────────────────────────────────── 레이어·제원
def _py(v):
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError, ImportError):
        pass
    return v.item() if hasattr(v, 'item') else v


def asbuilt_fields(cfg):
    F = dict(ASBUILT_PUMP_FIELDS)
    if cfg is not None:
        F.update(cfg.fields('asbuilt_pump'))
    return F


def read_rows(cfg, key):
    """레이어 → [dict(실제필드: 값)] + 점 좌표(_x, _y). 없으면 []."""
    if cfg is None or not cfg.has_layer(key):
        return []
    gdf = cfg.read_layer(key)
    out = []
    for _, r in gdf.iterrows():
        d = {c: _py(r[c]) for c in gdf.columns if c != gdf.geometry.name}
        g = r.geometry
        if g is not None and not g.is_empty:
            c = g.centroid
            d['_x'], d['_y'] = c.x, c.y
        out.append(d)
    return out


def pump_kind_existing(cfg):
    return (cfg.value('pump_existing', '기존') if cfg is not None else '기존') or '기존'


def plan_pump_rows(cfg):
    """plan_pump → {pump_id: dict(kind, in_invert, …)}."""
    rows = read_rows(cfg, 'plan_pump')
    if not rows:
        return {}
    F = cfg.fields('plan_pump')
    pid = F.get('pump_id')
    if not pid:
        return {}
    out = {}
    for r in rows:
        d = {lg: r.get(col) for lg, col in F.items() if col in r}
        out[str(r.get(pid))] = d
    return out


def specs_from_rows(rows, F):
    out = {}
    for r in rows:
        pid = r.get(F['pump_id'])
        if pid in (None, ''):
            continue
        d = {}
        for lg in PUMP_LOGICAL:
            col = F.get(lg)
            if col and col in r and r[col] not in (None, ''):
                d[lg] = r[col]
        sp = parse_spec(d.get('spec'))
        if 'q_m3min' not in d and 'q' in sp:
            d['q_m3min'] = sp['q']
        if 'head_m' not in d and 'H' in sp:
            d['head_m'] = sp['H']
        if 'power_kw' not in d and 'kW' in sp:
            d['power_kw'] = sp['kW']
        if 'bore' not in d and 'bore' in sp:
            d['bore'] = sp['bore']
        d['_src'] = 'layer'
        out[str(pid).split()[0]] = d
    return out


def load_specs(cfg, path=None):
    """기존 펌프 제원 {pump_id: {q_m3min, head_m, power_kw, lwl, hwl, bop, force_main_m, back_dia_mm, bore, …}}.
    순서: asbuilt_pump 레이어 → --specs 파일(같은 키는 파일이 이긴다)."""
    specs = {}
    if cfg is not None and cfg.has_layer('asbuilt_pump'):
        specs.update(specs_from_rows(read_rows(cfg, 'asbuilt_pump'), asbuilt_fields(cfg)))
    if path:
        p = Path(path)
        if p.suffix.lower() == '.json':
            data = json.loads(p.read_text(encoding='utf-8'))
            rows = data if isinstance(data, list) else [dict(pump_id=k, **v) for k, v in data.items()]
        else:
            with open(p, encoding='utf-8-sig') as f:
                rows = list(csv.DictReader(f))
        for r in rows:
            pid = str(r.get('pump_id') or '').strip()
            if not pid:
                continue
            d = {k: v for k, v in r.items() if k != 'pump_id' and v not in (None, '')}
            sp = parse_spec(d.get('spec'))
            for a, b in (('q_m3min', 'q'), ('head_m', 'H'), ('power_kw', 'kW'), ('bore', 'bore')):
                if a not in d and b in sp:
                    d[a] = sp[b]
            d['_src'] = p.name
            specs.setdefault(pid, {}).update(d)
    for d in specs.values():
        for k in list(d):
            if k in PUMP_LOGICAL and k not in ('spec', 'district'):
                v = _f(str(d[k]).replace('A', '').replace('D', '') if k in ('bore', 'back_dia_mm') else d[k])
                if v is not None:
                    d[k] = v
    return specs


def existing_names(cfg, specs, plan, extra=()):
    kind = pump_kind_existing(cfg)
    names = set(extra)
    for pid, d in plan.items():
        k = str(d.get('kind') or '')
        if k.startswith(kind):
            names.add(pid)
    return names | set(specs) if specs else names


def network_from_args(args, cfg, P):
    """--net / --flo / plan_pipe 레이어 → (stations, recs, meta)."""
    if getattr(args, 'net', None):
        J = json.loads(Path(args.net).read_text(encoding='utf-8'))
        return J['stations'], J.get('recs', []), J.get('meta', {})
    if args.flo:
        recs = recs_from_flo(args.flo)
        src = 'flo: ' + ', '.join(Path(p).name for p in args.flo)
    else:
        if cfg is None or not cfg.has_layer('plan_pipe'):
            raise SystemExit('--flo 도 없고 layers.plan_pipe 도 없다.')
        recs = recs_from_layer(cfg, args.snap)
        src = 'layer: plan_pipe'
    plan = plan_pump_rows(cfg) if cfg is not None else {}
    specs = load_specs(cfg, getattr(args, 'specs', None))
    nodes = {r['up'] for r in recs}
    exist = existing_names(cfg, specs, plan, args.existing)
    exist = {n for n in exist if n in nodes}
    inlet = parse_kv(args.inlet)
    layer_inlet = {k: _f(v.get('in_invert')) for k, v in plan.items()}
    st = build_network(recs, P, args.pump, sorted(exist), inlet, layer_inlet)
    meta = dict(source=src, records=len(recs), pumps=len(st), existing=sorted(exist),
                dmax=P['_dmax'], made=datetime.datetime.now().isoformat(timespec='seconds'))
    return st, recs, meta


# ───────────────────────────────────────────── 산출
def dump_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding='utf-8')


def dump_csv(path, rows, cols=None):
    rows = list(rows)
    if not rows:
        Path(path).write_text('', encoding='utf-8-sig')
        return
    cols = cols or [k for k in rows[0] if not isinstance(rows[0][k], (list, dict))]
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c)
                if isinstance(v, (list, tuple)):
                    v = ', '.join(map(str, v))
                elif isinstance(v, float):
                    v = round(v, 6)
                vals.append(v)
            w.writerow(vals)


def stamp():
    return datetime.date.today().strftime('%y%m%d')


# ───────────────────────────────────────────── 레이어 쓰기 (--write)
def _wkb_xy(b, o=0):
    """WKB 한 도형의 (x, y) 목록과 다음 오프셋 — Point/LineString/Polygon/Multi*, Z/M 무시."""
    import struct
    e = '<' if b[o] == 1 else '>'
    t = struct.unpack_from(e + 'I', b, o + 1)[0]
    o += 5
    dims = 2 + (1 if t & 0x80000000 else 0) + (1 if t & 0x40000000 else 0)
    if t & 0x20000000:
        o += 4
    t &= 0x0FFFFFFF
    if t >= 1000:
        dims += {1: 1, 2: 1, 3: 2}.get(t // 1000, 0)
        t %= 1000
    st = 8 * dims
    if t == 1:
        return [struct.unpack_from(e + 'dd', b, o)], o + st
    if t == 2:
        n = struct.unpack_from(e + 'I', b, o)[0]
        return [struct.unpack_from(e + 'dd', b, o + 4 + st * i) for i in range(n)], o + 4 + st * n
    if t == 3:
        nr, o, pts = struct.unpack_from(e + 'I', b, o)[0], o + 4, []
        for _ in range(nr):
            n = struct.unpack_from(e + 'I', b, o)[0]
            pts += [struct.unpack_from(e + 'dd', b, o + 4 + st * i) for i in range(n)]
            o += 4 + st * n
        return pts, o
    n, o, pts = struct.unpack_from(e + 'I', b, o)[0], o + 4, []
    for _ in range(n):
        q, o = _wkb_xy(b, o)
        pts += q
    return pts, o


def _gpkg_env(blob):
    """GPKG 도형 BLOB → (minx, maxx, miny, maxy) 또는 None(빈 도형)."""
    import struct
    if blob is None or len(blob) < 8 or blob[3] & 0x10:
        return None
    e = '<' if blob[3] & 1 else '>'
    ei = (blob[3] >> 1) & 7
    if ei:
        return struct.unpack_from(e + 'dddd', blob, 8)
    pts = _wkb_xy(bytes(blob), 8)[0]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def gpkg_connect(path):
    """sqlite3 로 GPKG 열기 + GDAL 이 만든 rtree 트리거가 부르는 ST_* 함수 등록.
    (등록 안 하면 속성만 UPDATE 해도 'no such function: ST_IsEmpty' 로 실패한다.)"""
    import sqlite3
    con = sqlite3.connect(str(path))
    env = lambda b, i: (lambda v: None if v is None else v[i])(_gpkg_env(b))
    con.create_function('ST_IsEmpty', 1, lambda b: 1 if _gpkg_env(b) is None else 0)
    con.create_function('ST_MinX', 1, lambda b: env(b, 0))
    con.create_function('ST_MaxX', 1, lambda b: env(b, 1))
    con.create_function('ST_MinY', 1, lambda b: env(b, 2))
    con.create_function('ST_MaxY', 1, lambda b: env(b, 3))
    return con


def write_layer(cfg, key, id_logical, updates, field_map, dry=False):
    """updates = {id: {논리필드: 값}} → 레이어 기존 행의 속성만 UPDATE (도형·새 행은 만들지 않는다).
    쓰기 전 백업: GPKG = 파일 사본(<out>/backup), PostGIS = <테이블>_bak_YYMMDD.
    반환: (갱신 행수, 없는 id 목록, 매핑 안 된/열 없는 논리필드)."""
    L = cfg.layer(key)
    idcol = field_map.get(id_logical)
    if not idcol:
        raise SystemExit('fields.%s.%s 매핑이 없다 — 쓸 행을 못 찾는다.' % (key, id_logical))
    logicals = sorted({k for u in updates.values() for k in u})
    if L['kind'] == 'gpkg':
        con = gpkg_connect(L['path'])
        cols = {r[1] for r in con.execute('pragma table_info("%s")' % L['layer'])}
    else:
        con = cfg.pg_connect()
        cu = con.cursor()
        cu.execute('select column_name from information_schema.columns where table_schema=%s and table_name=%s',
                   (L['schema'], L['layer']))
        cols = {r[0] for r in cu.fetchall()}
    usable = {lg: field_map[lg] for lg in logicals if field_map.get(lg) in cols}
    skipped = [lg for lg in logicals if lg not in usable]
    if dry or not usable:
        con.close()
        return 0, [], skipped
    n, missing = 0, []
    if L['kind'] == 'gpkg':
        con.close()
        bdir = cfg.out_dir(OUT_SUB, 'backup')
        bak = bdir / ('%s_bak_%s%s' % (L['path'].stem, stamp(), L['path'].suffix))
        if not bak.exists():                      # sqlite backup API — QGIS 가 열어 둔 WAL 내용까지 담는다
            import sqlite3
            src, dst = sqlite3.connect(str(L['path'])), sqlite3.connect(str(bak))
            src.backup(dst)
            dst.close()
            src.close()
        con = gpkg_connect(L['path'])
        for pid, u in updates.items():
            sets = [(usable[lg], v) for lg, v in u.items() if lg in usable]
            sql = 'update "%s" set %s where "%s" = ?' % (L['layer'], ', '.join('"%s" = ?' % c for c, _ in sets), idcol)
            cur = con.execute(sql, [v for _, v in sets] + [pid])
            if cur.rowcount:
                n += cur.rowcount
            else:
                missing.append(pid)
        con.commit()
        con.close()
        print('  GPKG 백업: %s' % bak)
    else:
        bak = '%s_bak_%s' % (L['layer'], stamp())
        cu.execute('select to_regclass(%s)', ('"%s"."%s"' % (L['schema'], bak),))
        if cu.fetchone()[0] is None:
            cu.execute('create table "%s"."%s" as select * from "%s"."%s"' % (L['schema'], bak, L['schema'], L['layer']))
        for pid, u in updates.items():
            sets = [(usable[lg], v) for lg, v in u.items() if lg in usable]
            sql = 'update "%s"."%s" set %s where "%s" = %%s' % (L['schema'], L['layer'],
                                                                ', '.join('"%s" = %%s' % c for c, _ in sets), idcol)
            cu.execute(sql, [v for _, v in sets] + [pid])
            if cu.rowcount:
                n += cu.rowcount
            else:
                missing.append(pid)
        con.commit()
        con.close()
        print('  PostGIS 백업 테이블: %s.%s' % (L['schema'], bak))
    return n, missing, skipped
