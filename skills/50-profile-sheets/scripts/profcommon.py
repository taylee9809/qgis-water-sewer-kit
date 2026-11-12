# -*- coding: utf-8 -*-
"""50-profile-sheets 공통 모듈 — 설정·DXF 읽기·글자 상자·안전한 글자 렌더링.

설정은 프로젝트의 `sewer.config.json` 의 `profile` 절(없으면 아래 DEFAULTS).
A3 분할·PDF 는 DXF 만 있으면 돌아가므로 설정 파일이 없어도 내장값으로 돈다.

★ 함정 — ezdxf matplotlib 출력기는 **기울어진 TEXT 의 글자 구멍(ㅎ·d·0·B …)을 칠해 버린다**
  (45° 인출 라벨의 '0' 이 검은 타원이 된다). 그래서 회전 TEXT 는 ezdxf 가 그리지 않게 가로채
  matplotlib 일반 글자(ax.text)로 다시 그린다 → `SafeTextFrontend` + `draw_text_chunks`.
  정렬·위치는 ezdxf 가 계산한 변환행렬을 그대로 쓰므로 자리는 원래와 같다.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:                                                    # 구형 파이썬/리다이렉트 대비
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents
                            if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

STEP_DIR = '50-profile'          # 산출 = <outputs.root>/50-profile/

# ───────────────────────────────────────────── 설정 기본값
# 값은 한 설계사 종단면도 서식(H=1:1000·V=1:200, 1 unit = 1 m) 예시다. 다른 서식이면
# sewer.config.json 의 profile 절에서 같은 키로 덮어쓴다(부분만 적어도 된다).
DEFAULTS = {
    'font': 'malgun.ttf',                 # 회전 글자·표제에 쓰는 글꼴(없으면 matplotlib 기본)
    'font_bold': 'malgunbd.ttf',          # PDF 딱지 글꼴
    'shx_fonts': ['TXT', 'ROMANS', 'WHGTXT', 'GHS', 'HANTXT', 'NGSW', 'APNORM', 'KSHP'],
    'strip': {                            # 원도(긴 스트립 종단면도 DXF) 서식
        'unit_h': 1000, 'unit_v': 200,    # 원도 축척 분모(1 unit = 1 m 이면 H=1:1000)
        'title_block': 'title',           # 계통(시트) 원점마다 INSERT 되는 표제 블록 이름
        'band_top': 105.0,                # 밴드 표(측점·거리·…·구배) 윗변 y
        'band_rows': [0, 15, 30, 45, 60, 75, 90, 105],
        'frame_top': 148.0,               # 밴드 + 관번호 막대 영역 상단 y (이 아래는 고정틀)
        'floor': 152.0,                   # 지형부를 내릴 수 있는 하한 y
        'band_left': 10.0,                # 밴드 표 좌변 = 계통 원점 − 10
        'station_y': 117.0,               # 측점 세로선 시작 y(= 표척 기준선)
        'pole_top': 267.0,                # 표제 블록의 표척 상단 y
        'pole_label_dx': -25.0,           # 표척 눈금값 TEXT x = 원점 + dx
        'title_w': 46.0,                  # 원점 왼쪽 표제란 폭
        'row_label_min_h': 20.0,          # 행(지구) 이름표 TEXT 최소 높이 — 여러 지구를 한 DXF 에 행으로 쌓은 경우
        'row_split': 0.5,                 # 행 경계 = 아래 행 y0 + (위 행 y0 − 아래 행 y0) × 이 값
        'pipe_pattern': r'^[A-Z]{1,5}-[A-Z0-9]{1,4}-\d{3,5}$',   # 관번호 글자(제목·리 매칭)
        'layers': {
            'profile': ['j_gh', 'j_pipe1', 'j_pipe2', 'j_sd', 'j_mh', 'j_old'],   # 지형·관로(기준고 산정)
            'annot': ['j_dir', 'j_text', 'j_mh', 'j_none', 'j_pd'],               # 인출·글자(오른쪽 넘침 허용)
            'band': ['j_table1', 'j_table2'],                                     # 밴드 표 가로선·테두리
            'station': 'j_table3',                                                # 측점 세로선·관번호 막대
            'memo': 'j_none',                                                     # 메모(분할에서 제외)
            'red': ['j_pipe1', 'j_pipe2', 'j_sd'],                                # PDF 에서 빨강
            'ground': 'j_gh', 'invert': 'j_pipe1', 'crown': 'j_pipe2', 'sub': 'j_sd',
            'manhole': 'j_mh', 'leader': 'j_dir', 'text': 'j_text', 'pole': 'j_pole',
            'old': 'j_old', 'box': 'j_BOX1', 'water': 'j_water',
        },
    },
    'a3': {                               # A3 분할 출력
        'page': [420.0, 297.0],
        'margin': [7.0, 7.0, 7.0, 30.0],  # 좌·우·하·상(상단은 표제 자리)
        'sheet_gap': 40.0,
        'scale_denoms': [1000, 1200, 1250, 1400, 1500, 1600, 1800, 2000, 2200, 2500],
        'vk': 1.0,                        # 지형부 세로 압축(1.0 = 원본). 2/3 이면 V 축척이 1.5 배 완만
        'vk_base': 119.0,                 # 압축 기준 y(표척 첫 눈금)
        'seg_gap': 12.0,                  # 한 장 안 계통 사이 여백(도면단위)
        'delta_step': 5.0,                # 기준고 이동 단위(5 unit = 1 m)
        'top_pad': 6.0, 'bot_pad': 6.0,
        'pump_key': '맨홀펌프장',          # 이 글자가 든 TEXT 는 키우고 노란 바탕
        'pump_mag': 1.70,
        'pump_rename': {},                # 종단 펌프명 → 조판 이름 (예: {"XX#1": "OO#1"})
        'join_key': '유입',                # 다른 노선 접속 라벨 → 분홍 바탕
        'district_suffix': '지구',         # 부제 "OO지구" (행 이름이 '관로' 로 끝나면 안 붙임)
        'row_labels': {},                 # 행 이름 → 부제 표기 (예: {"OO": "OO처리구역"})
        'ri_colors': {},                  # 리 이름 → "#RRGGBB" (없으면 팔레트 순서)
        'colors': {'red': '#FF0000', 'pump': '#FFE000', 'join': '#FFB6D9', 'frame': '#B0B0B0'},
    },
    'draw': {                             # profile_sheets.py 가 노선에서 새로 그릴 때
        'sheet_max_m': 500.0,             # 한 시트(계통) 최대 연장 — 넘으면 관 경계에서 나눈다
        'sheet_gap': 200.0,               # 시트 사이 x 간격(도면단위)
        'datum_below_m': 12.0,            # DATUM = floor(최저관저 − 관경) − 이 값
        'station_pitch_m': 20.0,          # 측점(NO.) 간격
        'manhole_inner_mm': 900,          # 맨홀 내경 기본값
        'manhole_type_mm': {'소형': 600, '1호': 900, '2호': 1200, '3호': 1500, '4호': 1800, '5호': 2100},
        'gravity_label': '신설오수, D={d}MM',
        'pressure_label': '신설압송, D={d}MM',
        'pdf_page_m': 550.0,              # 검토용 PDF 한 쪽 최대 연장
        'drop_note_m': 0.6,               # 이 이상 단차면 PDF 에 표기
        'pressure_cover_m': 1.2,          # 압송관 관저고가 비었을 때만: 관저 = 지반고 − 이 값 − 관경
    },
}

PALETTE = ['#1F6FB2', '#C1571A', '#2E8B57', '#6A4C93', '#B02A5B', '#0F766E',
           '#8A6D1F', '#3B5BA5', '#A03E2F', '#154E8A', '#246B3F', '#7A3B6B']


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_cfg(explicit=None, required=False):
    """sewer.config.json → kitconfig.Config. 설정이 없어도 되는 도구는 None 을 돌려받는다."""
    if explicit or required:
        return kitconfig.load(explicit)
    try:
        return kitconfig.load(None)
    except SystemExit:
        return None


def settings(cfg=None):
    """profile 절 = DEFAULTS ⊕ sewer.config.json profile."""
    raw = cfg.get('profile', {}) if cfg is not None else {}
    return deep_merge(DEFAULTS, raw or {})


def add_common_args(ap):
    kitconfig.add_config_arg(ap)
    ap.add_argument('--out', default=None,
                    help='산출 폴더 (기본: <outputs.root>/50-profile, 설정이 없으면 ./_산출/50-profile)')
    return ap


def out_dir(cfg, explicit=None, *sub):
    if explicit:
        d = Path(explicit)
    elif cfg is not None:
        d = cfg.out_dir(STEP_DIR)
    else:
        d = Path.cwd() / '_산출' / STEP_DIR
    for s in sub:
        d = d / s
    d.mkdir(parents=True, exist_ok=True)
    return d


def hex_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ───────────────────────────────────────────── DXF
def load_dxf(path):
    """DXF 읽기. ★ $DWGCODEPAGE 가 ANSI_949 여도 실제는 UTF-8 인 도면이 많다 → UTF-8 먼저."""
    import ezdxf
    try:
        with open(path, 'rt', encoding='utf-8') as f:
            return ezdxf.read(f)
    except UnicodeDecodeError:
        return ezdxf.readfile(path)


def setup_ezdxf_fonts(S):
    """SHX 글꼴(한글 깨짐) → TTF 로 대체."""
    from ezdxf.fonts import fonts as ezfonts
    for k in S.get('shx_fonts', []):
        ezfonts.SHX_FONTS[k] = S['font']
        ezfonts.SHX_FONTS[k + '.SHX'] = S['font']


HF = {0: (0.0, 1.0), 1: (-0.5, 0.5), 2: (-1.0, 0.0),
      3: (-0.5, 0.5), 4: (-0.5, 0.5), 5: (-0.5, 0.5)}
VF = {0: (-0.28, 1.0), 1: (0.0, 1.0), 2: (-0.5, 0.5), 3: (-1.0, 0.05)}


def txt_box(e):
    """TEXT 의 정렬을 반영한 근사 bbox (한글 1.0h, 영숫자 0.62h)."""
    h = e.dxf.height or 2.5
    t = e.dxf.text or ''
    w = sum((1.0 if ord(c) > 0x2000 else 0.62) for c in t) * h * 1.08
    ha, va = e.dxf.get('halign', 0), e.dxf.get('valign', 0)
    p = e.dxf.align_point if (ha or va) and e.dxf.hasattr('align_point') else e.dxf.insert
    f0, f1 = HF.get(ha, (0.0, 1.0))
    g0, g1 = VF.get(va, (-0.28, 1.0))
    r = math.radians(e.dxf.rotation or 0.0)
    c, s = math.cos(r), math.sin(r)
    xs, ys = [], []
    for lx, ly in ((f0 * w, g0 * h), (f1 * w, g0 * h), (f0 * w, g1 * h), (f1 * w, g1 * h)):
        xs.append(p.x + lx * c - ly * s)
        ys.append(p.y + lx * s + ly * c)
    return min(xs), max(xs), min(ys), max(ys)


def ent_box(e):
    t = e.dxftype()
    if t == 'LINE':
        a, b = e.dxf.start, e.dxf.end
        return min(a.x, b.x), max(a.x, b.x), min(a.y, b.y), max(a.y, b.y)
    if t == 'LWPOLYLINE':
        p = e.get_points('xy')
        if not p:
            return None
        xs = [q[0] for q in p]
        ys = [q[1] for q in p]
        return min(xs), max(xs), min(ys), max(ys)
    if t == 'CIRCLE':
        c, r = e.dxf.center, e.dxf.radius
        return c.x - r, c.x + r, c.y - r, c.y + r
    if t == 'ELLIPSE':
        c = e.dxf.center
        m = e.dxf.major_axis
        r = max(abs(m.x), abs(m.y), 1.0)
        return c.x - r, c.x + r, c.y - r, c.y + r
    if t == 'TEXT':
        return txt_box(e)
    return None


def anchor_y(e):
    t = e.dxftype()
    if t == 'LINE':
        return e.dxf.start.y
    if t == 'LWPOLYLINE':
        p = e.get_points('xy')
        return p[0][1] if p else 0.0
    if t in ('CIRCLE', 'ELLIPSE'):
        return e.dxf.center.y
    if e.dxf.hasattr('insert'):
        return e.dxf.insert.y
    return None


def anchor_x(e):
    t = e.dxftype()
    if t == 'LINE':
        return e.dxf.start.x
    if t == 'LWPOLYLINE':
        p = e.get_points('xy')
        return p[0][0] if p else 0.0
    if t in ('CIRCLE', 'ELLIPSE'):
        return e.dxf.center.x
    if e.dxf.hasattr('insert'):
        return e.dxf.insert.x
    return None


# ───────────────────────────────────────────── 글꼴
_FONT_PATHS = None
_CAP_RATIO = {}


def font_path(name):
    """글꼴 파일 이름(malgun.ttf, ARIALUNI.ttf …) → 전체 경로. 시스템·사용자 글꼴 폴더 검색."""
    global _FONT_PATHS
    if not name:
        return None
    if os.path.isabs(name) and os.path.exists(name):
        return name
    if _FONT_PATHS is None:
        from matplotlib import font_manager
        _FONT_PATHS = {}
        for p in font_manager.findSystemFonts():
            _FONT_PATHS.setdefault(os.path.basename(p).lower(), p)
    return _FONT_PATHS.get(os.path.basename(name).lower())


def font_props(name, fallback=None):
    from matplotlib.font_manager import FontProperties
    p = font_path(name) or font_path(fallback)
    return FontProperties(fname=p) if p else FontProperties()


def cap_ratio(fp):
    """글꼴 em 대비 대문자 높이(ezdxf 와 같은 정의: 'A' 상단 − 'x' 하단)."""
    key = fp.get_file() if hasattr(fp, 'get_file') else None
    key = key or fp.get_name()
    if key in _CAP_RATIO:
        return _CAP_RATIO[key]
    from matplotlib.textpath import TextPath
    try:
        a = TextPath((0, 0), 'A', size=1.0, prop=fp).vertices
        x = TextPath((0, 0), 'x', size=1.0, prop=fp).vertices
        r = float(a[:, 1].max() - x[:, 1].min())
        r = r if 0.4 < r < 1.0 else 0.72
    except Exception:
        r = 0.72
    _CAP_RATIO[key] = r
    return r


# ───────────────────────────────────────────── 회전 TEXT 안전 렌더링
def _make_safe_frontend():
    from ezdxf.addons.drawing import Frontend
    from ezdxf.addons.drawing.text import simplified_text_chunks
    from ezdxf.entities import Text

    class SafeTextFrontend(Frontend):
        """회전 TEXT 를 ezdxf 글리프 경로로 그리지 않고 sink 에 모은다(블록 안 글자 포함)."""

        def __init__(self, ctx, out, config, sink, min_angle=0.01):
            super().__init__(ctx, out, config)
            self._sink = sink
            self._min = min_angle

        def draw_text_entity_2d(self, entity, properties):
            if isinstance(entity, Text):
                ff = self.get_font_face(properties)
                chunks = list(simplified_text_chunks(entity, self.text_engine, font_face=ff))
                if chunks:
                    m = chunks[0][1]
                    ux = m.transform_direction((1, 0, 0))
                    ang = math.degrees(math.atan2(ux.y, ux.x))
                    if abs(((ang + 180.0) % 360.0) - 180.0) > self._min:
                        for line, mm, cap in chunks:
                            self._sink.append((line, mm, cap, properties.color,
                                               getattr(ff, 'filename', None)))
                        return
            super().draw_text_entity_2d(entity, properties)

    return SafeTextFrontend


_SAFE = None


def safe_frontend(ctx, backend, config, sink):
    global _SAFE
    if _SAFE is None:
        _SAFE = _make_safe_frontend()
    return _SAFE(ctx, backend, config, sink)


def draw_text_chunks(ax, sink, mm_per_unit=1.0, zorder=None, color=None, default_font='malgun.ttf'):
    """sink 의 회전 글자를 matplotlib 글자로 그린다.

    mm_per_unit: 도면 1 단위가 종이 몇 mm 인가(시트 DXF = 1.0, 원도 직접 렌더 = 축척 s).
    color: 그룹 색(None 이면 엔티티 색). 글자 폭 비율이 1 이 아니거나 기울임이 있으면
    TextPath 패치로 그린다(그것도 matplotlib 경로라 구멍이 제대로 빈다).
    """
    from matplotlib.patches import PathPatch
    from matplotlib.textpath import TextPath
    from matplotlib.transforms import Affine2D
    from matplotlib.path import Path as MPath
    arts = []
    for line, m, cap, col, ffile in sink:
        if not line.strip():
            continue
        fp = font_props(ffile, default_font)
        ratio = cap_ratio(fp)
        o = m.transform((0, 0, 0))
        ux = m.transform_direction((1, 0, 0))
        uy = m.transform_direction((0, 1, 0))
        sx, sy = math.hypot(ux.x, ux.y), math.hypot(uy.x, uy.y)
        ang = math.degrees(math.atan2(ux.y, ux.x))
        c = color or col or '#000000'
        if len(c) == 9:
            c = c[:7]
        dot = (ux.x * uy.x + ux.y * uy.y) / max(sx * sy, 1e-12)
        z = zorder if zorder is not None else 3
        if abs(sx / max(sy, 1e-12) - 1.0) < 0.02 and abs(dot) < 0.02:
            em_mm = cap * sy * mm_per_unit / ratio
            t = ax.text(o.x, o.y, line, rotation=ang, rotation_mode='anchor',
                        ha='left', va='baseline', fontsize=em_mm * 72.0 / 25.4,
                        fontproperties=fp, color=c, zorder=z, clip_on=True)
            t.set_fontsize(em_mm * 72.0 / 25.4)
            arts.append(t)
        else:
            tp = TextPath((0, 0), line, size=cap / ratio, prop=fp)
            a = Affine2D.from_values(m[0][0], m[0][1], m[1][0], m[1][1], m[3][0], m[3][1])
            path = MPath(a.transform(tp.vertices), tp.codes)
            arts.append(ax.add_patch(PathPatch(path, fc=c, ec='none', lw=0, zorder=z)))
    return arts


def render_group(ax, ctx, config, entities, mm_per_unit=1.0, text_color=None, default_font='malgun.ttf'):
    """한 색군을 ezdxf 로 그리고, 회전 글자는 matplotlib 글자로 그 위에 얹는다.
    반환: 이 호출이 ax 에 더한 아티스트 목록."""
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    n0 = {k: len(getattr(ax, k)) for k in ('collections', 'patches', 'lines', 'texts', 'images')}
    sink = []
    bk = MatplotlibBackend(ax, adjust_figure=False)   # adjust_figure=True 면 finalize 가 figure 크기를 바꾼다
    safe_frontend(ctx, bk, config, sink).draw_entities(entities)
    bk.finalize()
    new = []
    for k, n in n0.items():
        new.extend(list(getattr(ax, k))[n:])
    ztop = max([a.get_zorder() for a in new] + [0]) + 1
    new.extend(draw_text_chunks(ax, sink, mm_per_unit, ztop, text_color, default_font))
    return new, len(sink)


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
