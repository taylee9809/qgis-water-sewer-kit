# -*- coding: utf-8 -*-
"""11-survey-to-qgis 킷 배관 — 설정(sewer.config.json)·명령줄 인자·산출 폴더.

geopandas·fiona 를 import 하지 않는다(01·02 도 이 모듈을 쓰므로). GPKG 읽기·쓰기는 _common.

경로·레이어·필드를 정하는 순서 (모든 스크립트 공통)
  1. 명령줄 인자 (--gpkg / --poly / --cl / --route / --src …)  ← 주면 이게 우선
  2. sewer.config.json  (--config → 환경변수 SEWER_CONFIG → 현재 폴더에서 위로 탐색)
산출물(CSV·확인용 GPKG·그림)은 <outputs.root>/11-survey/ 아래. --outdir 로 바꿀 수 있다.
인자만으로 경로를 다 주면 설정 파일 없이도 돈다(그때 산출 폴더는 --outdir 필수).

쓰는 설정 키 (README 「설정」 절)
  layers.obstacle_poly / obstacle_line          지장물 정본 폴리곤·중심선 (GPKG)
  layers.obstacle_review / obstacle_review_line 확인필요 폴리곤·중심선 (GPKG)
  layers.plan_pipe  + fields.plan_pipe.pipe_id / district    설계 노선(관번호·지구)
  layers.parcel     + fields.parcel.jibun                    지적(지번 끝 글자 = 지목)
  fields.obstacle_poly.fix_note                              보정 기록 필드
  survey.dxf_dir / design_check_gpkg / build_gpkg / current_districts / flo / parcel_encoding
  project.crs / outputs.root
"""
import os
import re
import sys
import datetime
from pathlib import Path

_LIB = next((p / 'lib' for p in Path(__file__).resolve().parents
             if (p / 'lib' / 'kitconfig.py').exists()), None)
if _LIB is None:
    raise SystemExit('kitconfig.py 를 찾지 못했다 — 이 tools 폴더는 qgis-water-sewer-kit 안'
                     '(skills/11-survey-to-qgis/tools)에 그대로 있어야 한다.')
sys.path.insert(0, str(_LIB))
import kitconfig                                           # noqa: E402

STEP = '11-survey'                 # 산출 하위 폴더
LIGHT_DXF = 'survey_light.dxf'     # 01 이 만들고 02~14 가 기본으로 읽는 경량 DXF
BUILD_GPKG = '지장물_빌드.gpkg'     # survey.build_gpkg 가 없을 때 02 가 쓰는 자리
DEF_REVIEW_POLY = '확인필요_폴리곤'
DEF_REVIEW_CL = '확인필요_중심선'
NOTE_RE = re.compile(r'^보정_\d{6}$')   # 보정 기록 필드 이름 규칙 (data-standard: 보정_YYMMDD)

# 킷 표준 출력 스키마 — 이 도구가 만들고 읽는 지장물 필드명 (README 「출력 스키마」)
STD_FIELDS = {'obs_id': '지장물번호', 'class': '분류', 'type': '종류', 'label': '표기',
              'width_m': '규격_폭m', 'height_m': '규격_깊이m', 'dia_mm': '관경mm'}

# 값을 받는 플래그 — 위치 인자(01 의 원본 DXF, 09 의 반경·번호)를 가려낼 때 건너뛴다
VALUE_FLAGS = {
    '--config', '--outdir', '--out', '--add', '--src', '--gpkg', '--poly', '--cl', '--ratio',
    '--wmax', '--tol', '--near', '--dup', '--srcgpkg', '--route', '--route-layer', '--radius',
    '--district', '--r', '--ids', '--reason', '--from-csv', '--col', '--max-area', '--verified',
    '--from-gpkg', '--from-poly', '--from-cl', '--jijeok', '--export-to', '--adopt', '--from-13',
    '--label', '--prev', '--suf', '--note-field', '--log', '--qc', '--prof', '--chk'}


# ────────────────────────────────────────────────────────── 인자
def argval(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def has(flag):
    return flag in sys.argv


def positionals():
    """플래그와 그 값을 뺀 나머지 인자."""
    out, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a.startswith('--'):
            skip = a in VALUE_FLAGS
            continue
        out.append(a)
    return out


# ────────────────────────────────────────────────────────── 설정
_CFG = None
_CFG_ERR = None


def cfg(required=True):
    """sewer.config.json (한 번만 읽는다). required=False 면 없을 때 None."""
    global _CFG, _CFG_ERR
    if _CFG is None and _CFG_ERR is None:
        try:
            _CFG = kitconfig.load(argval('--config'))
        except kitconfig.ConfigError as e:
            _CFG_ERR = e
    if _CFG is None and required:
        raise _CFG_ERR
    return _CFG


def fld(key, logical, default):
    """논리 필드 → 실제 필드명. 설정에 매핑이 없으면 default(우리 팀 이름)."""
    c = cfg(required=False)
    return c.field(key, logical, default) if c is not None else default


def epsg():
    """project.crs ('EPSG:5186') → 5186. 설정이 없으면 5186."""
    c = cfg(required=False)
    s = str((c.get('project.crs') if c is not None else '') or '')
    m = re.search(r'(\d{4,6})\s*$', s)
    return int(m.group(1)) if m else 5186


def cfg_path(dotted):
    """설정의 경로 값(프로젝트 루트 기준) → 절대경로 문자열. 없으면 None."""
    c = cfg(required=False)
    if c is None or not c.get(dotted):
        return None
    return str(c.path_of(c.get(dotted)))


# ────────────────────────────────────────────────────────── 산출 폴더
def out_dir(*parts):
    d = argval('--outdir')
    if d:
        p = Path(d).joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return str(p)
    return str(cfg().out_dir(STEP, *parts))


def out_path(name):
    return os.path.join(out_dir(), name)


def light_dxf():
    """경량 DXF — --src 우선, 없으면 <산출>/survey_light.dxf (01 의 기본 출력)."""
    return argval('--src') or out_path(LIGHT_DXF)


def build_gpkg():
    """02 빌드 GPKG(지장물_폴리곤_<접미> 레이어들이 쌓이는 곳). 생성방식(관경산출) 참조원."""
    return cfg_path('survey.build_gpkg') or out_path(BUILD_GPKG)


def stamp6():
    return datetime.datetime.now().strftime('%y%m%d')


# ────────────────────────────────────────────────────────── 레이어
def gpkg_layer(key, required=True):
    """layers.<key> → (gpkg 경로, 레이어명). 지장물 레이어는 GPKG 만 받는다
       (fiona append·sqlite3 트리거 우회로 직접 고치기 때문)."""
    c = cfg(required=required)
    if c is None or not c.has_layer(key):
        if required:
            raise kitconfig.ConfigError(
                f'layers.{key} 가 설정에 없다. 명령줄(--gpkg/--poly/--cl)로 주거나 '
                f'sewer.config.json 에 적을 것.')
        return None, None
    L = c.layer(key)
    if L['kind'] != 'gpkg':
        raise kitconfig.ConfigError(
            f'layers.{key} 가 PostGIS 다. 11-survey-to-qgis 는 지장물 레이어를 GPKG 로만 '
            f'읽고 고친다 — GPKG 로 내보내 그 경로를 적을 것.')
    return str(L['path']), L['layer']


_SCHEMA_CHECKED = False


def _check_schema():
    """fields.obstacle_poly 가 킷 표준 출력 스키마와 다르면 경고만 한다."""
    global _SCHEMA_CHECKED
    if _SCHEMA_CHECKED:
        return
    _SCHEMA_CHECKED = True
    c = cfg(required=False)
    if c is None:
        return
    diff = [(k, v, c.fields('obstacle_poly').get(k)) for k, v in STD_FIELDS.items()
            if c.fields('obstacle_poly').get(k) not in (None, v)]
    if diff:
        print('! fields.obstacle_poly 가 킷 표준 출력 스키마와 다르다 — 이 도구는 표준 이름을 쓴다: '
              + ', '.join('%s=%s(설정 %s)' % d for d in diff))


def obstacle_src(review=None, need_cl=True, required=True):
    """지장물 대상 → (gpkg, 폴리곤 레이어, 중심선 레이어).
       인자 --gpkg/--poly/--cl 우선. review=True(또는 --review) 면 확인필요 레이어.
       required=False 면 설정이 없을 때 (None, None, None)."""
    if review is None:
        review = has('--review')
    kp, kl = (('obstacle_review', 'obstacle_review_line') if review
              else ('obstacle_poly', 'obstacle_line'))
    g, p, l = argval('--gpkg'), argval('--poly'), argval('--cl')
    if not (g and p):
        cg, cp = gpkg_layer(kp, required=required)
        if cg is None and not g:
            return None, None, None
        g, p = g or cg, p or cp
        if not p and review:
            p = DEF_REVIEW_POLY
    if not l:
        c = cfg(required=False)
        if c is not None and c.has_layer(kl):
            lg, l = gpkg_layer(kl)
            if os.path.normcase(os.path.abspath(lg)) != os.path.normcase(os.path.abspath(g)):
                print('! layers.%s 가 %s 와 다른 GPKG 다 — 이 도구는 폴리곤과 같은 GPKG 의 '
                      '"%s" 레이어로 본다' % (kl, kp, l))
        elif review:
            l = DEF_REVIEW_CL
        elif need_cl:
            raise kitconfig.ConfigError(
                'layers.obstacle_line(지장물 중심선)이 설정에 없다 — --cl 로 주거나 설정할 것.')
    _check_schema()
    return g, p, l


def review_names():
    """확인필요 레이어 (gpkg 또는 None, 폴리곤 레이어명, 중심선 레이어명)."""
    g, p = gpkg_layer('obstacle_review', required=False)
    c = cfg(required=False)
    l = None
    if c is not None and c.has_layer('obstacle_review_line'):
        _, l = gpkg_layer('obstacle_review_line')
    return g, p or DEF_REVIEW_POLY, l or DEF_REVIEW_CL


def note_field():
    """보정 기록을 **쓸** 필드: --note-field > fields.obstacle_poly.fix_note > 보정_<오늘 YYMMDD>."""
    return (argval('--note-field') or fld('obstacle_poly', 'fix_note', None)
            or '보정_' + stamp6())


def note_fields(cols):
    """보정 기록을 **읽을** 필드들: 지정(--note-field·fix_note)이 있으면 그것만,
       없으면 레이어의 보정_YYMMDD 필드 전부."""
    f = argval('--note-field') or fld('obstacle_poly', 'fix_note', None)
    if f:
        return [f]
    return [c for c in cols if NOTE_RE.match(str(c))]


def note_text(pr, fields_):
    return ' | '.join(str(pr.get(f)) for f in fields_ if pr.get(f))


def route_spec():
    """설계 노선(관번호가 붙은 LineString) 레이어 spec.
       --route <gpkg> [--route-layer 이름] > survey.design_check_gpkg > layers.plan_pipe"""
    c = cfg(required=False)
    lay = argval('--route-layer')
    if not lay and c is not None and c.has_layer('plan_pipe'):
        lay = c.layer('plan_pipe')['layer']
    g = argval('--route') or cfg_path('survey.design_check_gpkg')
    if g:
        if not lay:
            raise kitconfig.ConfigError('노선 레이어명을 모른다 — --route-layer 를 주거나 '
                                        'layers.plan_pipe 를 설정할 것.')
        return {'kind': 'gpkg', 'path': g, 'layer': lay}
    c = cfg()
    if not c.has_layer('plan_pipe'):
        raise kitconfig.ConfigError('설계 노선이 없다 — --route 를 주거나 layers.plan_pipe / '
                                    'survey.design_check_gpkg 를 설정할 것.')
    L = c.layer('plan_pipe')
    return dict(L, layer=lay) if lay else L


def districts():
    """'금회' 지구 — --district 지구A,지구B > survey.current_districts. 비면 전체 노선이 금회."""
    v = argval('--district')
    if v is not None:
        return tuple(x.strip() for x in v.split(',') if x.strip())
    c = cfg(required=False)
    ds = (c.get('survey.current_districts') if c is not None else None) or []
    if isinstance(ds, str):
        ds = [ds]
    return tuple(ds)


def flo_from_cfg():
    """survey.flo — 설계사 종단 .flo 경로 목록(프로젝트 루트 기준)."""
    c = cfg(required=False)
    v = (c.get('survey.flo') if c is not None else None) or []
    if isinstance(v, str):
        v = [v]
    return [str(c.path_of(x)) for x in v]
