# -*- coding: utf-8 -*-
"""B6 — 준공도 판독 결과를 준공 DB(asbuilt_* 논리 레이어)에 적재한다.

기본은 **보고(dry-run)** — DB 는 건드리지 않고, 적재할 행을 보고서와 미리보기 GPKG 로 낸다.
사람이 미리보기를 QGIS 로 보고 확인한 뒤 `--write` 를 붙이면 대상 레이어를 백업하고 그 지구 행을 넣는다.

입력 (12 스킬 앞 단계 산출 + 원본 DXF, 전부 읽기만)
  --spec         asb_spec.py 산출 JSON — 관번호↔L·D, 맨홀명↔H (원본 기준표, 이름 집합의 기준)
  --profile-dxf  종평면도 DXF — 밴드(asbuilt.profile.bands)의 지반고·관저고 열, 종단 라벨로 맨홀·관로 이름 확정
  --plan-dxf     평면도 DXF — 맨홀 심볼 위치, 맨홀명 지시선 라벨, 구분별 관로 선형 레이어, 관번호 라벨
  --bs           bs_features.py 산출 JSON — 오수받이(점)·배수설비 연결관(선). 이미 오프셋이 더해진 좌표
  오프셋          asbuilt.offsets.<지구> 또는 --offset DX DY (평면도 좌표에 더한다)

판독 규칙 (12 스킬 사례에서 나온 것)
  - 맨홀 이름은 **종단도로 확정**: 종단 라벨을 맨홀 열(맨홀IL 값과 관저고가 같은 열)에 붙일 때
    지반고 − 관저고 = 깊이(H) 를 함께 본다. 비스듬히 내려가는 라벨은 글자 끝이 노드 쪽이다.
  - 지반고·유출관저고 = 종단 값, 맨홀깊이 = 제원표 H(평면부 라벨, 4단계 diff 기준과 같음 · --depth-from profile 이면 종단 H).
    셋이 안 맞으면(원본 도면 불일치) 값은 그대로 두고 보고서에 '원본 불일치' 로 올린다.
  - 연장 = 종단 관번호 바로 아래 L= 라벨 > 제원표 L > 평면 라벨 > 도형 길이 (제원표와 다르면 보고).
  - 평면 맨홀 심볼은 동심원(폴리라인 2~3겹)이라 --cluster(0.3 m) 안을 한 개소로 묶는다.
  - 관로 선형은 구분별 레이어(신설·기존활용·기존폐쇄 …)의 폴리라인을 맨홀 위치에서 자른다(기존폐쇄는 중간에서 안 자름).
    폴리라인 끝 = 관 경계로 보고 잇지 않는다. 양 끝 맨홀은 아는데 한 관으로 안 맞는 선은 종단 구간 사슬의
    연장 합이 도형 길이와 맞으면 그 수열대로 자른다(분할 길이 수열 규칙).
  - 관번호는 평면 라벨(거리 + 연장 차이)로 붙이고, 끝점 맨홀 이름은 **인접 관로 공유점**으로 전파한다
    (지시선 라벨 맨홀에서 시작해 종단의 상류·하류 맨홀을 따라감). 끝점만 보고 이름을 찍지 않는다.
  - 도형 방향은 종단의 상류 → 하류로 맞춘다. 종단에 없는 관로(기존폐쇄 등)는 도면 방향 그대로, 유향 미확인으로 남긴다.
  - 관경 ≤ design.pressure_max_d_mm 이면 압송(values.kind_pressure), 아니면 자연유하.
  - 값은 원본 그대로 넣는다(설계 판단을 섞지 않는다). 못 정한 것은 비워 두고 보고서에 올린다.

산출
  <outputs.root>/12-asbuilt/asb_load_<지구>.txt            보고서(건수·필수 결측·불일치·판정 못 한 것)
  <outputs.root>/12-asbuilt/asb_load_<지구>_preview.gpkg   미리보기(표준 스키마 + 판독메모 열) — QGIS 로 눈 확인

쓰기 (--write)
  대상 = 설정 layers.asbuilt_manhole · asbuilt_pipe · asbuilt_inlet · asbuilt_service_pipe (있는 것만)
  - 쓰기 전 백업: GPKG 는 파일째 <이름>_bak_YYMMDD.gpkg, PostGIS 는 <표>_bak_YYMMDD (이미 있으면 _HHMM 덧붙임)
  - 그 지구 행이 이미 있으면 멈춘다. --replace 면 그 지구 행만 지우고 다시 넣는다.
  - PostGIS 는 fid 시퀀스를 max(fid) 로 맞춘 뒤 넣는다(복사·복원한 표에서 PK 충돌 나는 함정).
  - 필드 이름은 fields.<레이어>.<논리> → 없으면 표준 스키마 이름. 대상 표에 없는 열은 건너뛰고 보고.
  - PostGIS 비밀번호는 db.postgis.password_env 가 가리키는 환경변수로만 받는다.

예)
  python -X utf8 asb_load.py --district A지구 --spec A지구_asb_spec.json ^
         --profile-dxf "준공 종평면도.dxf" --plan-dxf "배수설비 평면도.dxf" --bs bs_cmp.json
  python -X utf8 asb_load.py ...(같은 인자)... --write            # 확인 후 실제 적재
  python -X utf8 asb_load.py ...(같은 인자)... --write --replace  # 그 지구를 지우고 다시 적재
"""
import argparse
import datetime
import json
import math
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

KIT = next(p for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())
sys.path.insert(0, str(KIT / 'lib'))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

SCHEMA = json.loads((KIT / 'templates' / 'db_schema.json').read_text(encoding='utf-8'))
NUM_RE = re.compile(r'-?\d+\.\d+')          # 밴드 값(소수 둘째 자리 등)
H_RE = re.compile(r'^H=(\d+(?:\.\d+)?)', re.I)  # 맨홀 깊이 라벨(H=2.23M)
MHIL_DX = 6.0                                 # 맨홀IL 밴드 글자 ↔ 맨홀 열 x 허용(값이 같아야 하므로 넉넉히)
ANY_NUM = re.compile(r'-?\d+(?:\.\d+)?')     # 라벨 값(L=110.50m, D=200)

# 설계사 도면 규격 기본값(한 설계사 예). asbuilt.dxf_layers.* 또는 인자로 바꾼다.
DEF_LAYERS = {'pipe_no': '0_신설계획오수관-관번호', 'pipe_len': '0_신설계획오수관-연장',
              'pipe_dia': '0_신설계획오수관-관경', 'mh_text': '종평용맨홀텍스트'}
DEF_MH_SYMBOLS = {'신설맨홀': '신설', '기존맨홀': '기존활용'}                 # 평면 맨홀 심볼 레이어 → 구분
DEF_PIPE_LINES = {'PIPE': '신설', '부분보수': '기존활용', '기존활용': '기존활용', '기존폐쇄': '기존폐쇄'}  # 관로 선형 레이어 → 구분

TARGETS = ('asbuilt_manhole', 'asbuilt_pipe', 'asbuilt_inlet', 'asbuilt_service_pipe')


# ───────────────────────── 공통 ─────────────────────────
class Log:
    """콘솔 + 보고서 파일 동시 기록."""

    def __init__(self):
        self.lines = []

    def __call__(self, *a):
        s = ' '.join(str(x) for x in a)
        print(s)
        self.lines.append(s)

    def save(self, path):
        Path(path).write_text('\n'.join(self.lines) + '\n', encoding='utf-8')


def table_def(key):
    for t in SCHEMA['tables']:
        if t['key'] == key:
            return t
    raise SystemExit('표준 스키마에 %s 가 없다' % key)


def read_json(p):
    for enc in ('utf-8', 'cp949'):   # 예전 산출물은 cp949 로 저장된 것이 있다
        try:
            with open(p, encoding=enc) as f:
                return json.load(f)
        except UnicodeDecodeError:
            continue
    raise SystemExit('JSON 인코딩을 읽지 못했다: %s' % p)


def kv_list(items, what):
    """['레이어=값', …] → dict. 값이 없으면 None."""
    out = {}
    for s in items or []:
        if '=' in s:
            k, v = s.split('=', 1)
            out[k.strip()] = v.strip()
        else:
            out[s.strip()] = None
    if items and not out:
        raise SystemExit('%s 형식: 이름=값' % what)
    return out


def num(s):
    m = ANY_NUM.search(s.replace(',', ''))
    return float(m.group()) if m else None


def r2(v):
    return None if v is None else round(v, 2)


# ───────────────────────── DXF 읽기 ─────────────────────────
def poly_points(e):
    t = e.dxftype()
    try:
        if t == 'LINE':
            return [(e.dxf.start[0], e.dxf.start[1]), (e.dxf.end[0], e.dxf.end[1])]
        if t == 'LWPOLYLINE':
            pts = [(p[0], p[1]) for p in e.get_points('xy')]
            if e.closed and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            return pts
        if t == 'POLYLINE':
            return [(v.dxf.location[0], v.dxf.location[1]) for v in e.vertices]
    except Exception:
        return None
    return None


def text_of(e):
    t = e.dxftype()
    if t == 'TEXT':
        return e.dxf.text.strip()
    if t == 'MTEXT':
        try:
            return e.plain_text().strip()
        except Exception:
            return e.text.strip()
    return None


def text_width(e, s):
    """글자 폭 추정(도면 단위). 글꼴이 없으면 글자 수 × 높이 × 폭계수."""
    try:
        from ezdxf.tools import text_size
        w = text_size.text_size(e).width
        if w and w > 0:
            return w
    except Exception:
        pass
    h = e.dxf.get('height', 1.0) or 1.0
    return len(s) * h * (e.dxf.get('width', 1.0) or 1.0)


def text_xs(e, x, ox, s, rot, w):
    """(시작 x, 가운데 x, 끝 x, 끝-시작 y). 정렬점(가운데·오른쪽·맞춤 정렬)이 있으면 그것을 믿고, 없으면 추정 폭으로."""
    c, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    h, v = e.dxf.get('halign', 0), e.dxf.get('valign', 0)
    ap = e.dxf.get('align_point', None)
    if ap is not None and (h or v):
        ax = ap[0] + ox
        if h in (1, 4):
            return ax - w / 2 * c, ax, ax + w / 2 * c, w * sn
        if h == 2:
            return ax - w * c, ax - w / 2 * c, ax, w * sn
        if h in (3, 5):
            return x, (x + ax) / 2, ax, w * sn
    return x, x + w / 2 * c, x + w * c, w * sn


def profile_texts(doc):
    """종평면도 글자: (x, y, text, layer, rot, 시작x, 가운데x, 끝x). 블록 안 글자도 이동량만 더한다(asb_bands 와 같은 방식)."""
    items = []

    def walk(layout, ox=0.0, oy=0.0, depth=0):
        for e in layout:
            t = e.dxftype()
            try:
                if t in ('TEXT', 'MTEXT'):
                    s = text_of(e)
                    rot = e.dxf.get('rotation', 0.0) or 0.0
                    w = text_width(e, s) if (t == 'TEXT' and s) else 0.0
                    x = e.dxf.insert[0] + ox
                    xs = text_xs(e, x, ox, s, rot, w) if t == 'TEXT' else (x, x, x, 0.0)
                    items.append((x, e.dxf.insert[1] + oy, s, e.dxf.layer, rot) + tuple(xs)
                                 + (e.dxf.get('height', 1.0) or 1.0,))
                elif t == 'INSERT':
                    for at in e.attribs:
                        x = at.dxf.insert[0] + ox
                        items.append((x, at.dxf.insert[1] + oy, at.dxf.text.strip(), e.dxf.layer,
                                      at.dxf.get('rotation', 0.0) or 0.0, x, x, x, 0.0, at.dxf.get('height', 1.0) or 1.0))
                    if depth < 3:
                        walk(doc.blocks[e.dxf.name], ox + e.dxf.insert[0], oy + e.dxf.insert[1], depth + 1)
            except Exception:
                pass

    walk(doc.modelspace())
    return items


def label_anchor(rot, x, y, sx, ex, dy):
    """종단 맨홀 라벨이 가리키는 점 (x, y). 비스듬히 내려가는 글자(회전 180~360°)는 끝이, 나머지는 시작이 노드 쪽이다."""
    return (ex, y + dy) if math.sin(math.radians(rot)) < -0.1 else (sx, y)


def longest_name(text, names):
    """글자 안에 들어 있는 이름 중 가장 긴 것 (XX-002 와 XX-002(접합) 구분)."""
    best = None
    for n in names:
        if n and n in text and (best is None or len(n) > len(best)):
            best = n
    return best


# ───────────────────────── 종단 판독 ─────────────────────────
def read_profile(path, bands, tol, gl_band, il_band, mhil_band, mh_label_y, pipe_label_y, mh_names, pipe_names,
                 S_MAN, label_dx, drop_dx, skip_re, log):
    """종단 → 노드(맨홀) 지반고·관저고, 구간(관로) 상·하류.

    노드 = 지반고·관저고가 함께 찍힌 열. 맨홀IL 밴드(맨홀 자리에만 값이 있는 행)가 있으면 그 값과 관저고가 같은 열만
    (근접 측점 열은 글자 겹침 때문에 2 단위쯤 옆에 찍혀 x 만으로는 못 가른다). 맨홀 라벨(제원표 이름을 포함한 글자)은
    글자 끝(비스듬히 내려가는 글자) 또는 시작 x 에서 label_dx 안의 노드 중 |x 차| + 5×|지반고 − 관저고 − H| 가
    가장 작은 열에 붙인다(★ 맨홀 이름은 종단으로 확정 — 깊이 대조). H= 라벨은 이름 라벨 바로 아래 줄(같은 회전)과 짝짓는다.
    낙차 맨홀은 유입 관저고가 지반고 없는 열(왼쪽 drop_dx 이내)에 따로 찍힌다 → 유입값으로 본다.
    프로파일 경계 = 지반고 밴드 행의 머리글(밴드 이름 글자) x.
    """
    import ezdxf
    doc = ezdxf.readfile(str(path))
    items = profile_texts(doc)
    gy, iy = bands[gl_band], bands[il_band]
    gl_cols = sorted((x, float(s)) for x, y, s, *_ in items if s and abs(y - gy) < tol and NUM_RE.fullmatch(s))
    il_cols = sorted((x, float(s)) for x, y, s, *_ in items if s and abs(y - iy) < tol and NUM_RE.fullmatch(s))
    mh_cols = None
    if mhil_band and mhil_band in bands:
        my = bands[mhil_band]
        mh_cols = sorted((x, float(s)) for x, y, s, *_ in items if s and abs(y - my) < tol and NUM_RE.fullmatch(s))
    head_word = gl_band.replace(' ', '')
    heads = sorted(x for x, y, s, *_ in items if s and abs(y - gy) < tol + 2 and head_word in s.replace(' ', ''))
    log('  종단 밴드: 지반고 %d · 관저고 %d 열%s, 프로파일 머리글 %d'
        % (len(gl_cols), len(il_cols), (' · %s %d' % (mhil_band, len(mh_cols))) if mh_cols is not None else '', len(heads)))
    if not gl_cols or not il_cols:
        raise SystemExit('  ★ 지반고/관저고 밴드 값이 0 — asbuilt.profile.bands 의 y 값을 asb_bands.py 로 확인할 것')

    def prof_id(x):
        k = 0
        for h in heads:
            if x >= h:
                k += 1
        return k

    def il_at(x, lo, hi):
        return [v for xx, v in il_cols if lo <= xx - x <= hi]

    gl_x = [x for x, _ in gl_cols]

    def has_gl(x):
        return any(abs(g - x) < 0.3 for g in gl_x)

    def is_mh_col(gx):
        if mh_cols is None:
            return True
        ils = il_at(gx, -0.3, 0.3)
        return any(abs(mx - gx) <= MHIL_DX and any(abs(v - mv) < 0.006 for v in ils) for mx, mv in mh_cols)

    all_cols = [(gx, g) for gx, g in gl_cols if il_at(gx, -0.3, 0.3)]
    cols = [c for c in all_cols if is_mh_col(c[0])]
    if mh_cols is not None:
        log('  맨홀 열 %d (지반고·관저고 열 %d 중 %s 값과 관저고가 같은 열)' % (len(cols), len(all_cols), mhil_band))

    def score_cols(ax, H, pool):
        out = []
        for gx, g in pool:
            if abs(gx - ax) > label_dx:
                continue
            dh = abs(g - min(il_at(gx, -0.3, 0.3)) - H) if H is not None else 0.0
            out.append((abs(gx - ax) + 5 * dh, gx, g))
        return out

    # 맨홀 라벨 → 노드 (점수 낮은 짝부터, 한 열에 한 이름)
    lo, hi = mh_label_y
    names_l, hlabs = [], []
    for x, y, s, _l, rot, sx, _cx, ex, dy, h in items:
        if not s or not (lo <= y <= hi):
            continue
        mH = H_RE.match(s.replace(' ', ''))
        if mH:
            hlabs.append(((x, y, rot), float(mH.group(1))))
            continue
        nm = longest_name(s, mh_names)
        if not nm or (skip_re and skip_re.search(s.replace(nm, ''))):
            continue
        i = s.find(nm)
        if i > 0 and s[i - 1] == '(':          # '(이름)…' 은 다른 맨홀·관로를 가리키는 주기
            continue
        names_l.append((nm, label_anchor(rot, x, y, sx, ex, dy), (x, y, rot, h, math.hypot(ex - sx, dy))))
    # H= 라벨 ↔ 이름 라벨: 같은 회전, 이름 글자 바로 아래 줄(글자 좌표계에서 0~3줄 아래, 글자 길이 안)인 것
    pair = []
    for i, (_n, _a, (x0, y0, r0, h0, w0)) in enumerate(names_l):
        c, sn = math.cos(math.radians(r0)), math.sin(math.radians(r0))
        for k, ((x1, y1, r1), _v) in enumerate(hlabs):
            if abs((r1 - r0 + 180) % 360 - 180) > 1.0:
                continue
            dx, dy_ = x1 - x0, y1 - y0
            along, perp = dx * c + dy_ * sn, -dx * sn + dy_ * c
            if -3.0 * h0 <= perp <= 0.2 * h0 and -2 * h0 <= along <= w0 + 2 * h0:
                pair.append((abs(perp + 1.4 * h0) + 0.05 * abs(along), i, k))
    lab_H, used_h = {}, set()
    for _sc, i, k in sorted(pair):
        if i in lab_H or k in used_h:
            continue
        lab_H[i] = hlabs[k][1]
        used_h.add(k)
    cand = []
    for i, (nm, (ax, _ay), _geo) in enumerate(names_l):
        H = lab_H.get(i, S_MAN.get(nm))
        sc = score_cols(ax, H, cols)
        if not sc and mh_cols is not None:
            sc = score_cols(ax, H, all_cols)
            if sc:
                log('  ! 종단 라벨 %s (글자 x=%.1f): %s 값 맞는 열이 없어 지반고·관저고 열로 대신' % (nm, ax, mhil_band))
        for c, gx, g in sc:
            cand.append((c, nm, round(ax, 1), gx, g, lab_H.get(i)))
        if not sc:
            log('  ! 종단 라벨 %s (글자 x=%.1f) 근처 %.1f 안에 맨홀 열 없음' % (nm, ax, label_dx))
    cand.sort()
    taken_col, taken_lab, nodes = set(), set(), []
    for sc, nm, ax, gx, g, hl in cand:
        if (nm, ax) in taken_lab or round(gx, 2) in taken_col:
            continue
        taken_lab.add((nm, ax))
        taken_col.add(round(gx, 2))
        out = il_at(gx, -0.3, 0.3)
        arr = [v for xx, v in il_cols if -drop_dx <= xx - gx < -0.3 and not has_gl(xx)]
        il_out = min(out) if out else None
        il_in = max(arr) if arr else il_out
        nodes.append(dict(name=nm, x=gx, gl=g, il_out=il_out, il_in=il_in, prof=prof_id(gx), H=hl))
    lost = sorted(set((c[1], c[2]) for c in cand) - taken_lab)
    for nm, ax in lost:
        log('  ! 종단 라벨 %s (x=%.1f): 후보 열을 다른 라벨이 차지 — 판정 보류' % (nm, ax))

    # 관로 라벨 → 구간
    plo, phi = pipe_label_y
    plabels, llabels = [], []
    for x, y, s, _l, _r, _sx, cx, _ex, _dy, _h in items:
        if not s or not (plo - 4 <= y <= phi):
            continue
        m = re.match(r'^L\s*=\s*(\d+(?:\.\d+)?)', s.replace(' ', ''), re.I)
        if m:
            llabels.append((cx, float(m.group(1))))
            continue
        if not (plo <= y <= phi):
            continue
        nm = s if s in pipe_names else longest_name(s, pipe_names)
        if nm:
            plabels.append((cx, nm))
    plabels = sorted(set((round(x, 1), n) for x, n in plabels))
    plen = {}
    for x, n in plabels:
        near = sorted((abs(lx - x), L) for lx, L in llabels if abs(lx - x) <= 1.5)
        if near:
            plen.setdefault(n, near[0][1])

    spans = {}
    issues = []
    byprof = defaultdict(list)
    for n in nodes:
        byprof[n['prof']].append(n)
    for pid, ns in byprof.items():
        ns.sort(key=lambda n: n['x'])
        for a, b in zip(ns, ns[1:]):
            labs = [n for x, n in plabels if a['x'] - 0.5 < x < b['x'] - 0.5 and prof_id(x) == pid]
            labs = sorted(set(labs))
            if not labs:
                continue
            if len(labs) > 1:
                issues.append('구간 %s~%s 에 관번호 라벨 %d개 %s — 판정 보류' % (a['name'], b['name'], len(labs), labs))
                continue
            p = labs[0]
            a_il = a['il_out'] if a['il_out'] is not None else a['il_in']
            b_il = b['il_in'] if b['il_in'] is not None else b['il_out']
            if a_il is not None and b_il is not None and b_il > a_il:
                us, ds, us_il, ds_il = b, a, (b['il_out'] if b['il_out'] is not None else b['il_in']), a['il_in']
            else:
                us, ds, us_il, ds_il = a, b, a_il, b_il
            rec = dict(us=us['name'], ds=ds['name'], us_il=us_il, ds_il=ds_il, us_gl=us['gl'], ds_gl=ds['gl'],
                       span=abs(b['x'] - a['x']), prof=pid, L=plen.get(p))
            if p in spans and (spans[p]['us'], spans[p]['ds']) != (rec['us'], rec['ds']):
                issues.append('관번호 %s 가 종단 두 곳에서 다른 구간: %s→%s / %s→%s'
                              % (p, spans[p]['us'], spans[p]['ds'], rec['us'], rec['ds']))
                continue
            spans[p] = rec
    return nodes, spans, issues


# ───────────────────────── 평면 판독 ─────────────────────────
def cluster(points, tol):
    """[(x, y, tag)] → 동심원 묶음 [(cx, cy, tag, n)]"""
    cl = []   # [sx, sy, n, tag]
    for x, y, tag in points:
        for c in cl:
            if math.hypot(c[0] / c[2] - x, c[1] / c[2] - y) <= tol:
                c[0] += x
                c[1] += y
                c[2] += 1
                break
        else:
            cl.append([x, y, 1, tag])
    return [(c[0] / c[2], c[1] / c[2], c[3], c[2]) for c in cl]


def read_plan(path, mh_layers, pipe_layers, lay):
    import ezdxf
    doc = ezdxf.readfile(str(path))
    sym, lines, leaders, texts = [], defaultdict(list), [], []
    for e in doc.modelspace():
        t = e.dxftype()
        L = e.dxf.layer
        if t in ('TEXT', 'MTEXT'):
            s = text_of(e)
            if s:
                texts.append((L, e.dxf.insert[0], e.dxf.insert[1], s))
            continue
        if L in mh_layers:
            if t == 'INSERT':
                sym.append((e.dxf.insert[0], e.dxf.insert[1], L))
            elif t in ('CIRCLE', 'ARC', 'ELLIPSE'):
                sym.append((e.dxf.center[0], e.dxf.center[1], L))
            else:
                pts = poly_points(e)
                if pts:
                    q = pts[:-1] if len(pts) > 2 and pts[0] == pts[-1] else pts
                    sym.append((sum(p[0] for p in q) / len(q), sum(p[1] for p in q) / len(q), L))
        elif L in pipe_layers:
            pts = poly_points(e)
            if pts and len(pts) >= 2:
                lines[L].append(pts)
        elif L == lay['mh_text']:
            pts = poly_points(e)
            if pts and len(pts) >= 2:
                leaders.append(pts)
    return sym, lines, leaders, texts


def plan_names(texts, leaders, mh_text_layer, alias, accept, leader_tol):
    """맨홀명 라벨 → (이름, 가리키는 점, 지시선 있음 여부). 지시선 = 글자 가까이 지나는 폴리라인, 먼 쪽 끝이 맨홀."""
    from shapely.geometry import LineString, Point
    lines = [LineString(p) for p in leaders if len(set(p)) >= 2]
    out, skipped = [], []
    for L, x, y, s in texts:
        if L != mh_text_layer or s.upper().startswith('H='):
            continue
        nm = alias.get(s, s)
        if accept is not None and nm not in accept:
            skipped.append(s)
            continue
        pt = Point(x, y)
        best = None
        for ln in lines:
            d = ln.distance(pt)
            if d <= leader_tol and (best is None or d < best[0]):
                best = (d, ln)
        if best:
            a, b = best[1].coords[0], best[1].coords[-1]
            far = a if math.hypot(a[0] - x, a[1] - y) > math.hypot(b[0] - x, b[1] - y) else b
            out.append((nm, far, True))
        else:
            out.append((nm, (x, y), False))
    return out, skipped


def plan_pipe_labels(texts, lay, near=25.0):
    """관번호 라벨 + 가장 가까운 L=·D= (asb_spec 과 같은 인접쌍 규칙)."""
    pn = [t for t in texts if t[0] == lay['pipe_no']]
    pl = [t for t in texts if t[0] == lay['pipe_len']]
    pd = [t for t in texts if t[0] == lay['pipe_dia']]
    out = []
    for _L, x, y, s in pn:
        cl = sorted((math.hypot(q[1] - x, q[2] - y), q[3]) for q in pl)
        cd = sorted((math.hypot(q[1] - x, q[2] - y), q[3]) for q in pd)
        Lv = num(cl[0][1]) if cl and cl[0][0] < near else None
        Dv = num(cd[0][1]) if cd and cd[0][0] < near else None
        out.append((s.strip(), x, y, Lv, Dv))
    return out


def split_lines(lines, mh_pts, snap, min_len, no_cut=()):
    """구분별 폴리라인 → 맨홀 위치에서 자른 구간 [(status_layer, LineString, a_id, b_id)].
    no_cut 레이어(기존폐쇄)는 중간 맨홀에서 자르지 않는다 — 신설관이 옛 관 위를 지나 맨홀이 옛 관 선 위에 놓인다."""
    from shapely.geometry import LineString, Point
    from shapely.ops import substring
    segs, dropped = [], 0
    for lay, plist in lines.items():
        uniq, keys = [], set()
        for p in plist:
            if len(set(p)) < 2:
                continue
            ls = LineString(p)
            k = tuple(sorted([tuple(round(c, 2) for c in p[0]), tuple(round(c, 2) for c in p[-1])])) + (round(ls.length, 2),)
            if ls.length < min_len:
                dropped += 1
                continue
            if k in keys:
                continue
            keys.add(k)
            uniq.append(ls)
        if not uniq:
            continue
        for part in uniq:          # 폴리라인 끝 = 관 경계(맨홀 심볼이 없는 폐쇄관 접합점도) — 잇지 않는다
            cuts = {}
            for i, (mx, my) in enumerate(mh_pts):
                pt = Point(mx, my)
                if part.distance(pt) <= snap:
                    d = round(part.project(pt), 3)
                    if lay in no_cut and snap < d < part.length - snap:
                        continue
                    cuts[d] = i
            pos = sorted(set([0.0, round(part.length, 3)] + list(cuts)))
            merged = []
            for p in pos:
                if merged and p - merged[-1] < 0.5:
                    if p in cuts:
                        merged[-1] = p
                    continue
                merged.append(p)
            for a, b in zip(merged, merged[1:]):
                g = substring(part, a, b)
                if g.length < max(0.5, snap):     # 맨홀 허용 반경 안 끝 조각
                    continue

                def end_id(d, xy):
                    if d in cuts:
                        return cuts[d]
                    pt = Point(xy)
                    best = min(((pt.distance(Point(m)), i) for i, m in enumerate(mh_pts)), default=(None, None))
                    return best[1] if best[0] is not None and best[0] <= snap else None
                segs.append([lay, g, end_id(a, g.coords[0]), end_id(b, g.coords[-1])])
    return segs, dropped


# ───────────────────────── 조립 ─────────────────────────
def build(a, cfg, log):
    from shapely.geometry import LineString, Point
    from shapely.ops import linemerge

    spec = read_json(a.spec)
    S_PIPE = {k: (v[0], v[1]) for k, v in spec.get('pipe', {}).items()}
    S_MAN = dict(spec.get('man', {}))
    log('■ 제원표 %s — 관로 %d · 맨홀 %d' % (Path(a.spec).name, len(S_PIPE), len(S_MAN)))

    # 설정·인자
    get = (lambda k, d=None: cfg.get(k, d)) if cfg else (lambda k, d=None: d)
    lay = {k: get('asbuilt.dxf_layers.' + k, v) for k, v in DEF_LAYERS.items()}
    for k in DEF_LAYERS:
        v = getattr(a, k + '_layer', None)
        if v:
            lay[k] = v
    mh_layers = kv_list(a.mh_layer, '--mh-layer') or dict(get('asbuilt.dxf_layers.mh_symbols', DEF_MH_SYMBOLS))
    pipe_layers = kv_list(a.pipe_layer, '--pipe-layer') or dict(get('asbuilt.dxf_layers.pipe_lines', DEF_PIPE_LINES))
    alias = kv_list(a.alias, '--alias')
    status_new = get('values.status_new', '신설') if cfg else '신설'
    for d in (mh_layers, pipe_layers):
        for k, v in d.items():
            d[k] = v or status_new
    kind_g = cfg.value('kind_gravity', '자연유하') if cfg else '자연유하'
    kind_p = cfg.value('kind_pressure', '압송') if cfg else '압송'
    pmax = float(get('design.pressure_max_d_mm', 100))
    ox, oy = a.offset_xy

    # 1) 종단
    nodes, spans, p_issues = [], {}, []
    if a.profile_dxf:
        prof = get('asbuilt.profile', {}) or {}
        bands = {k: float(v) for k, v in (prof.get('bands') or {}).items()}
        for s in a.band or []:
            k, v = s.split('=', 1)
            bands[k.strip()] = float(v)
        gl_band = a.gl_band or prof.get('gl_band', '지반고')
        il_band = a.il_band or prof.get('il_band', '관저고')
        if gl_band not in bands or il_band not in bands:
            raise SystemExit('밴드 %s·%s 의 y 가 없다 — asbuilt.profile.bands 또는 --band 이름=Y' % (gl_band, il_band))
        tol = a.band_tol if a.band_tol is not None else float(prof.get('band_tol', 4))
        mly = a.mh_label_y or prof.get('mh_label_y') or prof.get('label_y')
        ply = a.pipe_label_y or prof.get('pipe_label_y') or prof.get('label_y')
        if not mly or not ply:
            raise SystemExit('종단 라벨 y 범위가 없다 — --mh-label-y / --pipe-label-y 또는 asbuilt.profile.mh_label_y · '
                             'pipe_label_y (공통이면 label_y). asb_bands.py 로 잰다')
        mhil = a.mhil_band or prof.get('mhil_band') or ('맨홀IL' if '맨홀IL' in bands else None)
        skip = a.mh_label_skip if a.mh_label_skip is not None else prof.get('mh_label_skip', '유입|유출')
        log('■ 종단 %s' % Path(a.profile_dxf).name)
        nodes, spans, p_issues = read_profile(a.profile_dxf, bands, tol, gl_band, il_band, mhil, mly, ply,
                                              set(S_MAN), set(S_PIPE), S_MAN, a.label_dx, a.drop_dx,
                                              re.compile(skip) if skip else None, log)
        log('  맨홀 노드 %d (이름 %d종) · 관로 구간 %d' % (len(nodes), len(set(n['name'] for n in nodes)), len(spans)))
    else:
        log('■ 종단 없음(--profile-dxf) — 지반고·관저고·상하류는 비고, 맨홀깊이는 제원표 H')

    # 맨홀별 종단 값 모으기
    MH = {}
    for nm in S_MAN:
        ns = [n for n in nodes if n['name'] == nm]
        gls = Counter(n['gl'] for n in ns if n['gl'] is not None)
        ils = [v for n in ns for v in (n['il_out'], n['il_in']) if v is not None]
        hps = Counter(n['H'] for n in ns if n.get('H') is not None)
        Hp = hps.most_common(1)[0][0] if hps else None
        Hs = S_MAN.get(nm)
        H = (Hp if Hp is not None else Hs) if a.depth_from == 'profile' else (Hs if Hs is not None else Hp)
        gl = il_out = None
        if gls and ils:
            if H is not None:     # 종단에 같은 맨홀이 여러 번(본선·지선) — 깊이 H 와 맞는 짝을 고른다
                gl, il_out = min(((g, v) for g in gls for v in ils), key=lambda t: (round(abs(t[0] - t[1] - H), 3), t[1]))
                if abs(gl - il_out - H) > 0.015:
                    gl, il_out = gls.most_common(1)[0][0], min(ils)
            else:
                gl, il_out = gls.most_common(1)[0][0], min(ils)
        elif gls:
            gl = gls.most_common(1)[0][0]
        depth = H if H is not None else (r2(gl - il_out) if (gl is not None and il_out is not None) else None)
        memo = []
        if len(gls) > 1:
            memo.append('종단 지반고 %d가지 %s' % (len(gls), sorted(gls)))
        if len(hps) > 1:
            memo.append('종단 H 라벨 %d가지 %s' % (len(hps), sorted(hps)))
        if Hp is not None and Hs is not None and abs(Hp - Hs) > 0.005:
            memo.append('H 라벨 제원표(평면부) %.2f ↔ 종단 %.2f — %s 채택' % (Hs, Hp, '종단' if H == Hp else '제원표'))
        if H is not None and gl is not None and il_out is not None and abs(gl - il_out - H) > 0.015:
            memo.append('원본 불일치: 지반고 %.2f − 관저고 %.2f = %.2f ≠ H %.2f' % (gl, il_out, gl - il_out, H))
        if not ns and a.profile_dxf:
            memo.append('종단에서 못 찾음')
        MH[nm] = dict(gl=gl, il_out=il_out, depth=depth, memo=memo)

    # 2) 평면
    if not a.plan_dxf:
        raise SystemExit('--plan-dxf 가 없다 — 위치(도형)를 얻을 평면도 DXF 가 필요하다')
    log('■ 평면 %s (오프셋 %+.2f, %+.2f)' % (Path(a.plan_dxf).name, ox, oy))
    sym, lines, leaders, texts = read_plan(a.plan_dxf, mh_layers, pipe_layers, lay)
    cl = cluster(sym, a.cluster)
    cnt = Counter(c[2] for c in cl)
    log('  맨홀 심볼 %d → %d 개소 %s' % (len(sym), len(cl), dict(cnt)))
    mh_xy = [(c[0], c[1]) for c in cl]
    mh_lay = [c[2] for c in cl]
    mh_src = ['심볼'] * len(cl)
    accept = set(S_MAN) if S_MAN else None
    labs, skipped = plan_names(texts, leaders, lay['mh_text'], alias, accept, a.leader_tol)
    if skipped:
        log('  제원표에 없는 평면 맨홀 라벨 %d개 무시 (--alias 원래=제원표이름 으로 잇는다): %s'
            % (len(skipped), sorted(set(skipped))[:15]))
    name = [None] * len(cl)
    m_issue = []
    for nm, (x, y), has_leader in labs:
        rad = a.snap if has_leader else a.label_radius
        best = min(((math.hypot(mx - x, my - y), i) for i, (mx, my) in enumerate(mh_xy)), default=(None, None))
        if best[0] is not None and best[0] <= rad:
            i = best[1]
            if name[i] and name[i] != nm:
                m_issue.append('심볼 한 곳에 라벨 두 개: %s / %s' % (name[i], nm))
                continue
            name[i] = nm
        elif has_leader:      # 심볼이 없는 맨홀 — 지시선 끝점에 만든다
            mh_xy.append((x, y))
            mh_lay.append(None)
            mh_src.append('지시선끝')
            name.append(nm)
        else:
            m_issue.append('라벨 %s: 지시선도 %.0f m 안 심볼도 없음 — 위치 못 정함' % (nm, rad))
    dup = [n for n, c in Counter(n for n in name if n).items() if c > 1]
    for n in dup:
        m_issue.append('같은 이름이 평면 두 곳: %s' % n)
    seeded = sum(1 for n in name if n)
    log('  평면 라벨로 이름 붙은 맨홀 %d (지시선 끝점에 새로 만든 것 %d)' % (seeded, mh_src.count('지시선끝')))

    # 관로 선형 → 구간
    st_abandon = cfg.value('status_abandon', '기존폐쇄') if cfg else '기존폐쇄'
    segs, dropped = split_lines(lines, mh_xy, a.snap, a.min_pipe_len,
                                no_cut={k for k, v in pipe_layers.items() if v == st_abandon})
    log('  관로 선형 %s → 구간 %d (짧은 표시선 %d개 제외 < %.1f m)'
        % ({k: len(v) for k, v in lines.items()}, len(segs), dropped, a.min_pipe_len))

    # 관번호 라벨 → 구간 (거리 + 연장 차)
    plabs = plan_pipe_labels(texts, lay)
    pairs = []
    for li, (pn, x, y, Lv, _D) in enumerate(plabs):
        if S_PIPE and pn not in S_PIPE:
            continue
        L0 = Lv if Lv else (S_PIPE.get(pn, (None, None))[0])
        for si, sg in enumerate(segs):
            d = sg[1].distance(Point(x, y))
            if d <= a.label_radius:
                pairs.append((d + (a.len_weight * abs(sg[1].length - L0) if L0 else 0), li, si))
    pairs.sort()
    seg_name, seg_cost, used = {}, {}, set()
    for c, li, si in pairs:
        if li in used:
            continue
        pn = plabs[li][0]
        if si in seg_name:
            if seg_name[si] == pn:
                used.add(li)
            continue
        if pn in seg_name.values():
            other = [s for s, n in seg_name.items() if n == pn][0]
            touch = segs[other][1].distance(segs[si][1]) < 0.01
            if not touch:
                continue
        seg_name[si] = pn
        seg_cost[si] = c
        used.add(li)

    # 3) 이름 전파 — 종단 상류·하류 맨홀 ↔ 구간 끝점 공유. 끝점만 보고 이름을 찍지 않고, 이름 아는 맨홀에서 이어 간다
    def nm_idx(nm):
        return [i for i, n in enumerate(name) if n == nm]

    orient = {}

    def propagate():
        for _ in range(100):
            changed = False
            for si, pn in list(seg_name.items()):
                sp = spans.get(pn)
                if not sp or segs[si] is None:
                    continue
                ai, bi = segs[si][2], segs[si][3]
                na = name[ai] if ai is not None else None
                nb = name[bi] if bi is not None else None
                if na == sp['us'] or nb == sp['ds']:
                    o = 1
                elif na == sp['ds'] or nb == sp['us']:
                    o = -1
                else:
                    continue
                if orient.get(si) != o:
                    orient[si] = o
                    changed = True
                up, dn = (ai, bi) if o == 1 else (bi, ai)
                for mid, want in ((up, sp['us']), (dn, sp['ds'])):
                    if mid is None or name[mid] == want:
                        continue
                    if name[mid] is None and not nm_idx(want):
                        name[mid] = want
                        mh_src[mid] += '+이름전파'
                        changed = True
                    elif name[mid] is not None and name[mid] != want:
                        m_issue.append('구간 %s 끝점 맨홀 이름 충돌: 평면 %s ↔ 종단 %s' % (pn, name[mid], want))
            # 끝점 두 맨홀 이름 ↔ 종단 구간: 관번호 없는 구간 채우기, 라벨 관번호가 종단과 어긋나면 종단을 따른다
            for si, sg in enumerate(segs):
                if sg is None or sg[2] is None or sg[3] is None:
                    continue
                na, nb = name[sg[2]], name[sg[3]]
                if not na or not nb:
                    continue
                cur = seg_name.get(si)
                if cur in spans and {spans[cur]['us'], spans[cur]['ds']} == {na, nb}:
                    continue
                for pn, sp in spans.items():
                    if {sp['us'], sp['ds']} == {na, nb}:
                        if pn in seg_name.values():
                            other = [k for k, v in seg_name.items() if v == pn][0]
                            seg_name.pop(other)
                            orient.pop(other, None)
                        if cur:
                            m_issue.append('평면 라벨 관번호 %s → 끝점 맨홀(%s, %s)로 종단 %s 로 바꿈' % (cur, na, nb, pn))
                        seg_name[si] = pn
                        seg_cost[si] = -1
                        orient.pop(si, None)
                        changed = True
                        break
            if not changed:
                break

    def chain_split():
        """양 끝 맨홀 이름은 있는데 한 관로로 안 맞는 구간 → 종단 구간 사슬(연장 합 ≈ 도형 길이)로 자른다.
        (분할 길이 수열이 어느 끝에서 맞는지로 확정 — 사례: 15/43/19). 자른 점은 가까운 꼭짓점(1.5 m)에 붙인다."""
        from shapely.ops import substring
        nxt = defaultdict(list)
        for pn, sp in spans.items():
            nxt[sp['us']].append((sp['ds'], pn, S_PIPE.get(pn, (None, None))[0] or sp['span']))

        def paths(A, B, depth=0, seen=()):
            if depth > 6:
                return
            for ds, pn, L in nxt.get(A, []):
                if ds in seen:
                    continue
                if ds == B:
                    yield [(pn, L, ds)]
                elif not nm_idx(ds):
                    for rest in paths(ds, B, depth + 1, seen + (ds,)):
                        yield [(pn, L, ds)] + rest

        n_split = 0
        for si in range(len(segs)):
            sg = segs[si]
            if sg is None or sg[2] is None or sg[3] is None:
                continue
            na, nb = name[sg[2]], name[sg[3]]
            if not na or not nb or any({sp['us'], sp['ds']} == {na, nb} for sp in spans.values()):
                continue
            g = sg[1]
            for A, B, rev in ((na, nb, False), (nb, na, True)):
                hit = None
                for path in paths(A, B):
                    tot = sum(L for _p, L, _d in path)
                    if len(path) > 1 and abs(tot - g.length) <= max(1.0, 0.02 * g.length):
                        hit = path
                        break
                if not hit:
                    continue
                gg = LineString(list(g.coords)[::-1]) if rev else g
                k = gg.length / sum(L for _p, L, _d in hit)
                cuts, acc = [], 0.0
                for _p, L, _d in hit[:-1]:
                    acc += L * k
                    vs = [(abs(gg.project(Point(c)) - acc), gg.project(Point(c))) for c in gg.coords]
                    dv, dpos = min(vs)
                    cuts.append(dpos if dv <= 1.5 else acc)
                pos = [0.0] + cuts + [gg.length]
                ids = [sg[3] if rev else sg[2]]
                for (pn, L, ds), d in zip(hit[:-1], cuts):
                    xy = gg.interpolate(d).coords[0]
                    mh_xy.append(xy)
                    mh_lay.append(None)
                    mh_src.append('분할점(연장 수열)')
                    name.append(ds)
                    ids.append(len(mh_xy) - 1)
                ids.append(sg[2] if rev else sg[3])
                pieces = []
                for j, (pn, L, ds) in enumerate(hit):
                    pieces.append([sg[0], substring(gg, pos[j], pos[j + 1]), ids[j], ids[j + 1]])
                old = seg_name.pop(si, None)
                orient.pop(si, None)
                segs[si] = pieces[0]
                idxs = [si]
                for pc in pieces[1:]:
                    segs.append(pc)
                    idxs.append(len(segs) - 1)
                for j, (pn, L, ds) in zip(idxs, hit):
                    if pn in seg_name.values():
                        other = [kk for kk, v in seg_name.items() if v == pn][0]
                        seg_name.pop(other)
                    seg_name[j] = pn
                    seg_cost[j] = -2
                    orient[j] = 1
                m_issue.append('구간 %s~%s (%.1f m, 라벨 %s) → 종단 사슬 %s 로 분할'
                               % (A, B, g.length, old, ' / '.join('%s %.1f' % (p_, L) for p_, L, _d in hit)))
                n_split += 1
                break
        return n_split

    propagate()
    for _ in range(5):
        if not chain_split():
            break
        propagate()
    m_issue = list(dict.fromkeys(m_issue))

    # 같은 관번호 구간이 이어져 있으면 합친다
    byname = defaultdict(list)
    for si, pn in seg_name.items():
        byname[pn].append(si)
    for pn, sis in byname.items():
        if len(sis) > 1:
            g = linemerge([segs[s][1] for s in sis])
            if g.geom_type == 'LineString':
                keep = sis[0]
                segs[keep][1] = g
                c0, c1 = Point(g.coords[0]), Point(g.coords[-1])
                ends = [(segs[s][2], segs[s][1].coords[0]) for s in sis] + [(segs[s][3], segs[s][1].coords[-1]) for s in sis]
                segs[keep][2] = next((e for e, xy in ends if e is not None and Point(xy).distance(c0) < 0.01), None)
                segs[keep][3] = next((e for e, xy in ends if e is not None and Point(xy).distance(c1) < 0.01), None)
                for s in sis[1:]:
                    seg_name.pop(s, None)
                    segs[s] = None
                log('  관번호 %s: 이어진 구간 %d개를 하나로' % (pn, len(sis)))

    # 4) 행 만들기 (좌표 = 평면 + 오프셋)
    def off(xy):
        return (xy[0] + ox, xy[1] + oy)

    src_plan = Path(a.plan_dxf).name
    src_prof = Path(a.profile_dxf).name if a.profile_dxf else None
    rows_mh = []
    for i, (x, y) in enumerate(mh_xy):
        nm = name[i]
        info = MH.get(nm) if nm else None
        st = mh_layers.get(mh_lay[i], status_new) if mh_lay[i] else status_new
        src = '%s [%s]' % (src_plan, mh_lay[i] or '지시선 끝점')
        if info and src_prof and info['gl'] is not None:
            src += ' · %s 종단' % src_prof
        memo = list(info['memo']) if info else []
        if mh_src[i] != '심볼':
            pos, _, how = mh_src[i].partition('+')
            if pos != '심볼':
                memo.append('위치=' + pos)
            if how:
                memo.append('이름=인접 관로 공유점으로 전파(종단 상·하류)')
        rows_mh.append(dict(geom=Point(off((x, y))), 맨홀번호=nm, 지구=a.district, 구분=st,
                            지반고=info['gl'] if info else None,
                            맨홀깊이=info['depth'] if info else None,
                            유출관저고=info['il_out'] if info else None, 자료출처=src, 판독메모=' / '.join(memo)))

    # bs 점 중 맨홀로 넣을 묶음(소형맨홀 등)
    bs = read_json(a.bs) if a.bs else {}
    bs_mh = kv_list(a.bs_manhole_group, '--bs-manhole-group') or {k: None for k in (get('asbuilt.load.bs_manhole_groups', []) or [])}
    for g, st in bs_mh.items():
        for x, y in bs.get(g, []):
            rows_mh.append(dict(geom=Point(x, y), 맨홀번호=None, 지구=a.district, 구분=st or status_new,
                                지반고=None, 맨홀깊이=None, 유출관저고=None,
                                자료출처='%s [%s]' % (Path(a.bs).name, g), 판독메모='배수설비 평면도 점'))

    rows_pipe, p_memo_all = [], []
    named_mh = {n: i for i, n in enumerate(name) if n}
    for si, sg in enumerate(segs):
        if sg is None:
            continue
        lay_, g, ai, bi = sg
        pn = seg_name.get(si)
        if pn is None and not a.keep_unnamed:
            continue
        sp = spans.get(pn) if pn else None
        o = orient.get(si)
        memo = []
        if o == -1:
            g = LineString(list(g.coords)[::-1])
            ai, bi = bi, ai
        if sp is None or o is None:
            memo.append('유향 미확인(도면 방향)')
        Lspec, Dspec = S_PIPE.get(pn, (None, None)) if pn else (None, None)
        lab = next((p for p in plabs if p[0] == pn), None) if pn else None
        Lprof = sp.get('L') if sp else None
        L = Lprof or Lspec or (lab[3] if lab and lab[3] else r2(g.length))
        if Lprof and Lspec and abs(Lprof - Lspec) > 0.05:
            memo.append('연장 종단 %.1f 채택(제원표 %.1f — 평면 라벨 짝 오류 의심)' % (Lprof, Lspec))
        D = Dspec if Dspec else (lab[4] if lab else None)
        if L and abs(g.length - L) > max(1.0, 0.05 * L):
            memo.append('도형 %.1f m ≠ 연장 %.1f m' % (g.length, L))
        if sp and L and abs(sp['span'] - L) > max(1.0, 0.03 * L):
            memo.append('종단 간격 %.1f m ≠ 연장 %.1f m' % (sp['span'], L))
        us = sp['us'] if sp and o else (name[ai] if ai is not None else None)
        ds = sp['ds'] if sp and o else (name[bi] if bi is not None else None)
        us_il = sp['us_il'] if sp and o else None
        ds_il = sp['ds_il'] if sp and o else None
        us_gl = MH.get(us, {}).get('gl') if us else None
        ds_gl = MH.get(ds, {}).get('gl') if ds else None
        slope = r2((us_il - ds_il) / L * 1000) if (us_il is not None and ds_il is not None and L) else None
        if pn is None:
            memo.append('관번호 라벨 없음')
        elif seg_cost.get(si) == -1:
            memo.append('관번호=끝점 맨홀 두 개로 종단에서 찾음')
        elif seg_cost.get(si) == -2:
            memo.append('종단 연장 수열로 분할')
        kind = kind_p if (D and D <= pmax) else kind_g
        src = '%s [%s]' % (src_plan, lay_)
        if sp and o:
            src += ' · %s 종단' % src_prof
        rows_pipe.append(dict(geom=LineString([off(c) for c in g.coords]), 관번호=pn, 지구=a.district,
                              구분=pipe_layers.get(lay_, status_new), **{'자연유하/압송': kind},
                              관경=D, 연장=L, 상류맨홀=us, 하류맨홀=ds, 상류지반고=us_gl, 하류지반고=ds_gl,
                              상류관저고=us_il, 하류관저고=ds_il, 구배=slope, 관종=None, 자료출처=src,
                              판독메모=' / '.join(memo)))

    # 배수설비: 오수받이·연결관
    rows_inlet, rows_svc = [], []
    if bs:
        def is_line(v):
            return bool(v) and isinstance(v[0], list) and v[0] and isinstance(v[0][0], list)
        groups_pt = [k for k, v in bs.items() if not is_line(v) and k not in bs_mh]
        groups_ln = [k for k, v in bs.items() if is_line(v)]
        inl = kv_list(a.inlet_group, '--inlet-group') or {k: None for k in groups_pt}
        svc = kv_list(a.service_group, '--service-group') or {k: None for k in groups_ln}
        dia_map = get('asbuilt.load.service_dia', {}) or {}
        for g, kind_ in inl.items():
            for x, y in bs.get(g, []):
                rows_inlet.append(dict(geom=Point(x, y), 구분=kind_ or g.replace('블록:', ''), 지구=a.district,
                                       자료출처='%s [%s]' % (Path(a.bs).name, g)))
        mh_named_pts = [(r['geom'], r['맨홀번호']) for r in rows_mh if r['맨홀번호']]
        for g, val in svc.items():
            kind_, dia = (val.split(':', 1) + [None])[:2] if val else (g, None)
            dia = float(dia) if dia else dia_map.get(g)
            for pts, _len in bs.get(g, []):
                ln = LineString(pts)
                con = None
                for end in (Point(pts[0]), Point(pts[-1])):
                    near = min(((end.distance(p), n) for p, n in mh_named_pts), default=(None, None))
                    if near[0] is not None and near[0] <= a.service_snap:
                        con = near[1]
                        break
                rows_svc.append(dict(geom=ln, 구분=kind_ or g, 관경=dia, 접속맨홀=con, 접속가옥=None, 지구=a.district))

    # 5) 보고
    log('\n■ 적재 예정')
    by = Counter(r['구분'] for r in rows_mh)
    log('  맨홀 %d (%s) — 이름 %d / 제원표 %d' % (len(rows_mh), dict(by), sum(1 for r in rows_mh if r['맨홀번호']), len(S_MAN)))
    by = Counter(r['구분'] for r in rows_pipe)
    log('  관로 %d (%s) — 관번호 %d / 제원표 %d · 종단 구간 %d'
        % (len(rows_pipe), dict(by), sum(1 for r in rows_pipe if r['관번호']), len(S_PIPE), len(spans)))
    log('  오수받이 %d · 배수설비관 %d (접속맨홀 %d)' % (len(rows_inlet), len(rows_svc), sum(1 for r in rows_svc if r['접속맨홀'])))
    got_mh = set(r['맨홀번호'] for r in rows_mh if r['맨홀번호'])
    got_p = set(r['관번호'] for r in rows_pipe if r['관번호'])
    log('  제원표 맨홀 중 위치 못 정한 것 %d: %s' % (len(set(S_MAN) - got_mh), sorted(set(S_MAN) - got_mh)))
    log('  제원표 관로 중 선형 못 찾은 것 %d: %s' % (len(set(S_PIPE) - got_p), sorted(set(S_PIPE) - got_p)))
    log('  종단 구간 있는데 평면 구간 방향 못 정한 것: %s'
        % sorted(r['관번호'] for r in rows_pipe if r['관번호'] in spans and '유향 미확인' in r['판독메모']))
    for s in p_issues + m_issue:
        log('  ! ' + s)
    memo_m = [(r['맨홀번호'], r['판독메모']) for r in rows_mh if r['판독메모'] and r['맨홀번호']]
    if memo_m:
        log('\n■ 맨홀 판독메모 (%d)' % len(memo_m))
        for n, m in sorted(memo_m, key=lambda t: str(t[0])):
            log('  %-18s %s' % (n, m))
    memo_p = [(r['관번호'], r['판독메모']) for r in rows_pipe if r['판독메모']]
    if memo_p:
        log('\n■ 관로 판독메모 (%d)' % len(memo_p))
        for n, m in sorted(memo_p, key=lambda t: str(t[0])):
            log('  %-14s %s' % (n, m))
    return {'asbuilt_manhole': rows_mh, 'asbuilt_pipe': rows_pipe,
            'asbuilt_inlet': rows_inlet, 'asbuilt_service_pipe': rows_svc}


# ───────────────────────── 필드 이름·필수 ─────────────────────────
def field_map(cfg, key):
    """표준 스키마 이름 → 대상 실제 이름 (fields.<key>.<논리> 가 있으면 그것)."""
    m = {}
    for f in table_def(key)['fields']:
        std, logical = f[0], f[3]
        actual = cfg.field(key, logical, default=std) if (cfg and logical) else std
        m[std] = actual
    return m


def required_missing(key, rows):
    req = [f[0] for f in table_def(key)['fields'] if f[2]]
    miss = Counter()
    for r in rows:
        for c in req:
            if r.get(c) in (None, ''):
                miss[c] += 1
    return miss


def to_gdf(key, rows, crs, fmap=None, keep_memo=True):
    import geopandas as gpd
    t = table_def(key)
    cols = [f[0] for f in t['fields']]
    typ = {f[0]: f[1] for f in t['fields']}
    data = []
    for r in rows:
        d = {c: r.get(c) for c in cols}
        if keep_memo:
            d['판독메모'] = r.get('판독메모', '')
        d['geometry'] = r['geom']
        data.append(d)
    gdf = gpd.GeoDataFrame(data, columns=cols + (['판독메모'] if keep_memo else []) + ['geometry'],
                           geometry='geometry', crs=crs)
    for c in cols:
        if typ[c] == 'REAL':
            gdf[c] = gdf[c].astype('float64')
        elif typ[c] == 'INTEGER':
            gdf[c] = gdf[c].astype('Int64')
        else:
            gdf[c] = gdf[c].astype('object')
    if fmap:
        gdf = gdf.rename(columns=fmap)
    return gdf


# ───────────────────────── 쓰기 ─────────────────────────
def stamp():
    return datetime.datetime.now().strftime('%y%m%d')


def write_gpkg(cfg, plan, district, replace, log):
    import pyogrio
    by_path = defaultdict(list)
    for key, (L, gdf) in plan.items():
        by_path[str(L['path'])].append((key, L, gdf))
    for path, items in by_path.items():
        p = Path(path)
        if not p.exists():
            raise SystemExit('대상 GPKG 가 없다: %s' % p)
        # 기존 행 확인
        cn = sqlite3.connect(p)
        try:
            for key, L, gdf in items:
                dcol = cfg.field(key, 'district', default='지구')
                cols = [r[1] for r in cn.execute('pragma table_info("%s")' % L['layer'])]
                if not cols:
                    raise SystemExit('대상 GPKG 에 레이어 %s 가 없다 (%s)' % (L['layer'], p.name))
                n = cn.execute('select count(*) from "%s" where "%s"=?' % (L['layer'], dcol), (district,)).fetchone()[0] if dcol in cols else 0
                if n and not replace:
                    raise SystemExit('%s 에 지구 %s 행이 이미 %d건 — 다시 넣으려면 --replace' % (L['layer'], district, n))
        finally:
            cn.close()
        bak = p.with_name('%s_bak_%s%s' % (p.stem, stamp(), p.suffix))
        if bak.exists():
            bak = p.with_name('%s_bak_%s%s' % (p.stem, datetime.datetime.now().strftime('%y%m%d_%H%M%S'), p.suffix))
        shutil.copy2(p, bak)
        log('  백업: %s' % bak)
        cn = sqlite3.connect(p)
        try:
            for key, L, gdf in items:
                dcol = cfg.field(key, 'district', default='지구')
                cols = [r[1] for r in cn.execute('pragma table_info("%s")' % L['layer'])]
                if replace and dcol in cols:
                    k = cn.execute('delete from "%s" where "%s"=?' % (L['layer'], dcol), (district,)).rowcount
                    log('  %s: 지구 %s 기존 %d행 삭제' % (L['layer'], district, k))
            cn.commit()
        finally:
            cn.close()
        for key, L, gdf in items:
            info = pyogrio.read_info(p, layer=L['layer'])
            have = set(info['fields'])
            drop = [c for c in gdf.columns if c != 'geometry' and c not in have]
            if drop:
                log('  %s: 대상에 없는 열 건너뜀 %s' % (L['layer'], drop))
            g = gdf[[c for c in gdf.columns if c == 'geometry' or c in have]]
            if len(g):
                pyogrio.write_dataframe(g, p, layer=L['layer'], append=True)
            cn = sqlite3.connect(p)
            dcol = cfg.field(key, 'district', default='지구')
            n = cn.execute('select count(*) from "%s" where "%s"=?' % (L['layer'], dcol), (district,)).fetchone()[0]
            cn.close()
            log('  %s ← %d행 (적재 후 지구 %s %d행)' % (L['layer'], len(g), district, n))


def write_postgis(cfg, plan, district, replace, skip_incomplete, log):
    cn = cfg.pg_connect()
    srid = int(str(cfg.get('project.crs', 'EPSG:5186')).split(':')[-1])
    try:
        cu = cn.cursor()
        jobs = []
        for key, (L, gdf) in plan.items():
            sch, tb, gcol = L['schema'], L['layer'], L['geom']
            cu.execute('select column_name, is_nullable, column_default from information_schema.columns '
                       'where table_schema=%s and table_name=%s', (sch, tb))
            meta = {r[0]: (r[1], r[2]) for r in cu.fetchall()}
            if not meta:
                raise SystemExit('PostGIS 에 %s.%s 가 없다' % (sch, tb))
            cu.execute('select type from geometry_columns where f_table_schema=%s and f_table_name=%s and f_geometry_column=%s',
                       (sch, tb, gcol))
            gt = (cu.fetchone() or ['GEOMETRY'])[0].upper()
            dcol = cfg.field(key, 'district', default='지구')
            cu.execute('select count(*) from "%s"."%s" where "%s"=%%s' % (sch, tb, dcol), (district,))
            n = cu.fetchone()[0]
            if n and not replace:
                raise SystemExit('%s.%s 에 지구 %s 행이 이미 %d건 — 다시 넣으려면 --replace' % (sch, tb, district, n))
            cols = [c for c in gdf.columns if c != 'geometry' and c in meta]
            drop = [c for c in gdf.columns if c != 'geometry' and c not in meta]
            notnull = [c for c, (nul, dflt) in meta.items() if nul == 'NO' and dflt is None and c not in (gcol,)]
            rows, skipped = [], 0
            for _, r in gdf.iterrows():
                vals = [None if (v is None or (isinstance(v, float) and math.isnan(v))) else v for v in (r[c] for c in cols)]
                miss = [c for c in notnull if c in cols and vals[cols.index(c)] is None] + \
                       [c for c in notnull if c not in cols and c != 'fid']
                if miss:
                    if not skip_incomplete:
                        raise SystemExit('%s.%s: NOT NULL 열 %s 이 빈 행이 있다 — 보고서의 필수 결측을 채우거나 '
                                         '--skip-incomplete 로 그 행을 빼고 넣을 것' % (sch, tb, miss))
                    skipped += 1
                    continue
                rows.append((vals, r['geometry'].wkt))
            jobs.append((key, sch, tb, gcol, gt, dcol, cols, drop, rows, skipped))
        # 백업 → 삭제 → 시퀀스 → 넣기 (한 트랜잭션)
        for key, sch, tb, gcol, gt, dcol, cols, drop, rows, skipped in jobs:
            bak = '%s_bak_%s' % (tb, stamp())
            cu.execute('select to_regclass(%s)', ('"%s"."%s"' % (sch, bak),))
            if cu.fetchone()[0]:
                bak = '%s_bak_%s' % (tb, datetime.datetime.now().strftime('%y%m%d_%H%M%S'))
            cu.execute('create table "%s"."%s" as table "%s"."%s"' % (sch, bak, sch, tb))
            log('  백업: %s.%s' % (sch, bak))
            if replace:
                cu.execute('delete from "%s"."%s" where "%s"=%%s' % (sch, tb, dcol), (district,))
                log('  %s: 지구 %s 기존 %d행 삭제' % (tb, district, cu.rowcount))
            cu.execute("select pg_get_serial_sequence(%s, 'fid')", ('"%s"."%s"' % (sch, tb),))
            seq = cu.fetchone()
            if seq and seq[0]:
                cu.execute('select setval(%%s, coalesce((select max(fid) from "%s"."%s"), 0) + 1, false)' % (sch, tb), (seq[0],))
            gexpr = 'ST_GeomFromText(%s, %s)'
            if gt.startswith('MULTI'):
                gexpr = 'ST_Multi(%s)' % gexpr
            sql = 'insert into "%s"."%s" (%s, "%s") values (%s, %s)' % (
                sch, tb, ', '.join('"%s"' % c for c in cols), gcol, ', '.join(['%s'] * len(cols)), gexpr)
            for vals, wkt in rows:
                cu.execute(sql, vals + [wkt, srid])
            if drop:
                log('  %s: 대상에 없는 열 건너뜀 %s' % (tb, drop))
            log('  %s ← %d행%s' % (tb, len(rows), (' (필수 결측 %d행 뺌)' % skipped) if skipped else ''))
        cn.commit()
    except BaseException:
        cn.rollback()
        raise
    finally:
        cn.close()


# ───────────────────────── main ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description='준공도 판독 결과 → 준공 DB(asbuilt_*) 적재. 기본은 보고만(dry-run), --write 로 실제 적재',
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog='판독 규칙·산출·쓰기 절차는 파일 머리 설명(docstring)과 SKILL.md 6단계 참고')
    g = ap.add_argument_group('입력')
    g.add_argument('--district', required=True, help='지구 이름 (지구 필드 값, asbuilt.offsets 키)')
    g.add_argument('--spec', help='asb_spec.py 산출 JSON (기본 <outputs.root>/12-asbuilt/asb_spec.json)')
    g.add_argument('--profile-dxf', help='준공 종평면도 DXF (지반고·관저고·상하류). 없으면 그 값들은 비운다')
    g.add_argument('--plan-dxf', required=True, help='평면도 DXF (맨홀 심볼·맨홀명 지시선·관로 선형·관번호 라벨)')
    g.add_argument('--bs', help='bs_features.py 산출 JSON (오수받이·배수설비 연결관). 없으면 그 레이어는 건너뜀')
    g.add_argument('--offset', nargs=2, type=float, metavar=('DX', 'DY'), help='평면도 좌표에 더할 값 (기본 asbuilt.offsets.<지구>)')
    g = ap.add_argument_group('평면도 레이어 (기본 asbuilt.dxf_layers.*)')
    g.add_argument('--mh-layer', action='append', metavar='레이어=구분', help='맨홀 심볼 레이어와 구분 코드. 여러 번 (기본 mh_symbols)')
    g.add_argument('--pipe-layer', action='append', metavar='레이어=구분', help='관로 선형 레이어와 구분 코드. 여러 번 (기본 pipe_lines)')
    g.add_argument('--mh-text-layer', help='맨홀명·지시선 레이어 (기본 mh_text)')
    g.add_argument('--pipe-no-layer', help='관번호 TEXT 레이어 (기본 pipe_no)')
    g.add_argument('--pipe-len-layer', help='연장(L=) 레이어 (기본 pipe_len)')
    g.add_argument('--pipe-dia-layer', help='관경(D=) 레이어 (기본 pipe_dia)')
    g.add_argument('--alias', action='append', metavar='평면이름=제원표이름', help='평면 라벨 이름이 제원표와 다를 때. 여러 번')
    g = ap.add_argument_group('종단 (기본 asbuilt.profile.*)')
    g.add_argument('--band', action='append', metavar='이름=Y', help='밴드 행 y (기본 bands)')
    g.add_argument('--band-tol', type=float, help='밴드 y 허용차 (기본 band_tol 또는 4)')
    g.add_argument('--gl-band', help='지반고 밴드 이름 (기본 gl_band 또는 "지반고")')
    g.add_argument('--il-band', help='관저고 밴드 이름 (기본 il_band 또는 "관저고")')
    g.add_argument('--mh-label-y', nargs=2, type=float, metavar=('LO', 'HI'), help='종단 맨홀명 라벨 y 범위 (기본 mh_label_y 또는 label_y)')
    g.add_argument('--pipe-label-y', nargs=2, type=float, metavar=('LO', 'HI'), help='종단 관번호 라벨 y 범위 (기본 pipe_label_y 또는 label_y)')
    g.add_argument('--mhil-band', help='맨홀 위치에만 값이 찍히는 밴드 이름 (기본 mhil_band, bands 에 "맨홀IL" 이 있으면 그것)')
    g.add_argument('--mh-label-skip', help='맨홀 라벨에서 뺄 글자 정규식(유입·유출 주기 등, 기본 mh_label_skip 또는 "유입|유출")')
    g.add_argument('--depth-from', choices=('spec', 'profile'), default='spec',
                   help='맨홀깊이 출처: spec = 제원표(평면부 H= 라벨, 4단계 diff 기준과 같음, 기본) / profile = 종단 H= 라벨')
    g.add_argument('--label-dx', type=float, default=8.0, help='맨홀 라벨 끝 x ↔ 맨홀 열 허용 (기본 8)')
    g.add_argument('--drop-dx', type=float, default=3.5, help='낙차 맨홀 유입 관저고 열을 찾을 왼쪽 거리 (기본 3.5)')
    g = ap.add_argument_group('배수설비 (bs_features 산출)')
    g.add_argument('--inlet-group', action='append', metavar='묶음[=구분]', help='asbuilt_inlet 로 넣을 점 묶음 (기본: 점 묶음 전부)')
    g.add_argument('--service-group', action='append', metavar='묶음[=구분[:관경]]', help='asbuilt_service_pipe 로 넣을 선 묶음 (기본: 선 묶음 전부)')
    g.add_argument('--bs-manhole-group', action='append', metavar='묶음[=구분]',
                   help='asbuilt_manhole 로 넣을 점 묶음(소형맨홀 등, 이름 없음). 기본 asbuilt.load.bs_manhole_groups')
    g = ap.add_argument_group('허용치')
    g.add_argument('--cluster', type=float, default=0.3, help='동심원 심볼 묶음 반경 m (기본 0.3)')
    g.add_argument('--snap', type=float, default=1.0, help='관로 끝점·지시선 끝 ↔ 맨홀 허용 m (기본 1.0)')
    g.add_argument('--leader-tol', type=float, default=3.0, help='맨홀명 글자 ↔ 지시선 허용 (기본 3)')
    g.add_argument('--label-radius', type=float, default=25.0, help='관번호 라벨 ↔ 관로 구간 탐색 반경 (기본 25)')
    g.add_argument('--len-weight', type=float, default=2.0, help='라벨 배정 비용에서 연장 차 가중 (기본 2)')
    g.add_argument('--min-pipe-len', type=float, default=3.5, help='이보다 짧은 선은 표시선(폐쇄 X 등)으로 보고 뺀다 (기본 3.5 m)')
    g.add_argument('--service-snap', type=float, default=2.0, help='연결관 끝 ↔ 이름 있는 맨홀 허용 m (기본 2)')
    g.add_argument('--keep-unnamed', action='store_true', help='관번호를 못 붙인 관로 구간도 넣는다(관번호 비움)')
    g = ap.add_argument_group('쓰기')
    g.add_argument('--write', action='store_true', help='실제로 DB 에 쓴다 (없으면 보고·미리보기만)')
    g.add_argument('--replace', action='store_true', help='그 지구 행이 있으면 지우고 다시 넣는다 (--write 와 함께)')
    g.add_argument('--skip-incomplete', action='store_true', help='PostGIS NOT NULL 열이 빈 행은 빼고 넣는다')
    g.add_argument('--only', nargs='+', choices=TARGETS, help='이 레이어만 쓴다')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = kitconfig.load(a.config)
    if a.offset:
        a.offset_xy = tuple(a.offset)
    else:
        offs = cfg.get('asbuilt.offsets', {}) or {}
        if a.district not in offs:
            ap.error('asbuilt.offsets 에 "%s" 가 없다 — asb_offset_diff.py --pair 로 산정해 적거나 --offset DX DY' % a.district)
        a.offset_xy = tuple(offs[a.district])
    out = cfg.out_dir('12-asbuilt')
    a.spec = a.spec or str(out / 'asb_spec.json')
    if not Path(a.spec).exists():
        raise SystemExit('asb_spec.json 이 없다: %s — asb_spec.py 를 먼저 돌릴 것' % a.spec)

    log = Log()
    log('asb_load %s — 지구 %s  (%s)' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), a.district,
                                         '쓰기' if a.write else '보고만, DB 미변경'))
    rows = build(a, cfg, log)

    crs = cfg.get('project.crs', 'EPSG:5186')
    targets = [k for k in TARGETS if (not a.only or k in a.only)]
    log('\n■ 필수 필드 결측 (표준 스키마 기준)')
    for k in targets:
        miss = required_missing(k, rows[k])
        log('  %-22s %4d행  %s' % (k, len(rows[k]), dict(miss) if miss else '결측 없음'))

    prev = out / ('asb_load_%s_preview.gpkg' % a.district)
    if prev.exists():
        prev.unlink()
    import pyogrio
    for k in targets:
        if rows[k]:
            pyogrio.write_dataframe(to_gdf(k, rows[k], crs), prev, layer=table_def(k)['name'])
    log('\n미리보기: %s  (표준 스키마 이름 + 판독메모 열)' % prev)

    if a.write:
        plan, kinds = {}, set()
        for k in targets:
            if not rows[k]:
                continue
            if not cfg.has_layer(k):
                log('  (layers.%s 없음 — 건너뜀)' % k)
                continue
            L = cfg.layer(k)
            kinds.add(L['kind'])
            plan[k] = (L, to_gdf(k, rows[k], crs, fmap=field_map(cfg, k), keep_memo=False))
        log('\n■ 쓰기')
        if 'gpkg' in kinds:
            write_gpkg(cfg, {k: v for k, v in plan.items() if v[0]['kind'] == 'gpkg'}, a.district, a.replace, log)
        if 'postgis' in kinds:
            write_postgis(cfg, {k: v for k, v in plan.items() if v[0]['kind'] == 'postgis'}, a.district,
                          a.replace, a.skip_incomplete, log)
        log('적재 끝 — asb_offset_diff.py --district %s 로 4단계 합격 기준을 다시 확인할 것' % a.district)
    else:
        log('\n(보고만 — DB 는 바꾸지 않았다. 미리보기를 확인한 뒤 --write)')
    rep = out / ('asb_load_%s.txt' % a.district)
    log.save(rep)
    print('보고서: %s' % rep)


if __name__ == '__main__':
    main()
