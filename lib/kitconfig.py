"""qgis-water-sewer-kit 공용 설정 로더.

킷의 모든 스크립트는 경로·레이어명·필드명·DB 접속을 여기서 얻는다. 스크립트에 하드코딩하지 않는다.

설정 파일 = 프로젝트 루트의 `sewer.config.json` (00-onboard 스킬이 만든다).
찾는 순서: --config 인자 → 환경변수 SEWER_CONFIG → 현재 폴더에서 위로 올라가며 sewer.config.json.

스크립트에서 쓰는 법:
    import sys; from pathlib import Path
    sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
    import kitconfig
    cfg = kitconfig.load(args.config)
    pipe = cfg.read_layer('plan_pipe')
    col = cfg.field('plan_pipe', 'pipe_id')     # → '관번호'

비밀번호는 설정 파일에 적지 않는다. db.postgis.password_env 가 가리키는 환경변수에서 읽는다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_NAME = 'sewer.config.json'


class ConfigError(SystemExit):
    """설정 문제는 스택트레이스 없이 안내문만 찍고 끝낸다."""


def find_config(explicit=None, start=None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            p = p / CONFIG_NAME
        if not p.exists():
            raise ConfigError(f'설정 파일이 없다: {p}')
        return p
    env = os.environ.get('SEWER_CONFIG')
    if env:
        return find_config(env)
    d = Path(start or os.getcwd()).resolve()
    for q in (d, *d.parents):
        if (q / CONFIG_NAME).exists():
            return q / CONFIG_NAME
    raise ConfigError(
        f'{CONFIG_NAME} 를 찾지 못했다 ({d} 부터 위로 탐색).\n'
        '프로젝트 폴더 안에서 실행하거나 --config / SEWER_CONFIG 로 지정할 것.\n'
        '처음이면 Claude 에게 "이 프로젝트에 맞게 킷 세팅해줘"(00-onboard) 라고 한다.')


def _strip(o):
    """'_' 로 시작하는 설명용 키를 걷어낸다."""
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not str(k).startswith('_')}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


class Config:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.raw = json.loads(self.path.read_text(encoding='utf-8'))
        self.data = _strip(self.raw)
        root = self.get('project.root', '.') or '.'
        rp = Path(root)
        self.root = rp if rp.is_absolute() else (self.path.parent / rp).resolve()

    # ---------- 일반 값 ----------
    def get(self, dotted, default=None):
        cur = self.data
        for k in dotted.split('.'):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def require(self, dotted):
        v = self.get(dotted)
        if v in (None, '', [], {}):
            raise ConfigError(f'{self.path.name} 에 "{dotted}" 가 비어 있다. '
                              '00-onboard 로 채우거나 직접 적을 것.')
        return v

    def path_of(self, rel):
        """프로젝트 루트 기준 상대경로 → 절대경로. 절대경로는 그대로."""
        if rel in (None, ''):
            return None
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    # ---------- 레이어·필드 ----------
    def layer(self, key):
        """layers.<key> → {'kind':'gpkg','path':Path,'layer':str} | {'kind':'postgis','schema','layer','geom'}"""
        spec = self.require(f'layers.{key}')
        if isinstance(spec, str):
            spec = {'src': self.get('db.default_src', 'postgis'), 'layer': spec}
        src = spec.get('src', 'postgis')
        if src == 'postgis':
            return {'kind': 'postgis', 'schema': spec.get('schema', 'public'),
                    'layer': spec['layer'], 'geom': spec.get('geom', self.get('db.postgis.geom_col', 'geom'))}
        return {'kind': 'gpkg', 'path': self.path_of(src), 'layer': spec['layer']}

    def has_layer(self, key):
        return bool(self.get(f'layers.{key}'))

    def field(self, key, logical, default=None):
        f = self.get(f'fields.{key}.{logical}')
        if f:
            return f
        if default is not None:
            return default
        raise ConfigError(f'fields.{key}.{logical} 매핑이 없다 ({self.path.name}). '
                          'standard/data-standard.md 의 논리 필드표를 보고 채울 것.')

    def fields(self, key):
        return dict(self.get(f'fields.{key}', {}) or {})

    def value(self, logical, default=None):
        """values.<logical> — 코드값(예: kind_pressure='압송', status_abandon='기존폐쇄')."""
        return self.get(f'values.{logical}', default)

    # ---------- PostGIS ----------
    def pg_params(self):
        pg = self.require('db.postgis')
        pw_env = pg.get('password_env', 'SEWER_PG_PASSWORD')
        pw = os.environ.get(pw_env)
        if pw is None:
            raise ConfigError(f'PostGIS 비밀번호 환경변수 {pw_env} 가 없다. '
                              '설정 파일에는 비밀번호를 적지 않는다(환경변수로 줄 것).')
        return dict(host=pg.get('host', 'localhost'), port=int(pg.get('port', 5432)),
                    dbname=pg['dbname'], user=pg.get('user', 'postgres'), password=pw)

    def pg_connect(self):
        import psycopg2
        cn = psycopg2.connect(**self.pg_params())
        cn.set_client_encoding('UTF8')  # 안 하면 한글 컬럼명 쿼리가 깨진다
        return cn

    # ---------- 읽기 ----------
    def read_layer(self, key):
        """레이어 → GeoDataFrame (geopandas 필요). 걸러내기는 호출 쪽에서."""
        import geopandas as gpd
        L = self.layer(key)
        if L['kind'] == 'gpkg':
            if not L['path'].exists():
                raise ConfigError(f'layers.{key}: GPKG 가 없다 → {L["path"]}')
            return gpd.read_file(L['path'], layer=L['layer'])
        sql = f'select * from "{L["schema"]}"."{L["layer"]}"'
        with self.pg_connect() as cn:
            return gpd.read_postgis(sql, cn, geom_col=L['geom'])

    def out_dir(self, *parts):
        """산출물 폴더 (outputs.root 아래). 없으면 만든다. 원본 폴더에는 쓰지 않는다."""
        d = self.path_of(self.get('outputs.root', '_산출'))
        for p in parts:
            d = d / p
        d.mkdir(parents=True, exist_ok=True)
        return d


def load(explicit=None, start=None) -> Config:
    return Config(find_config(explicit, start))


def add_config_arg(ap):
    ap.add_argument('--config', default=None,
                    help=f'{CONFIG_NAME} 경로 (기본: 현재 폴더에서 위로 탐색, 또는 SEWER_CONFIG)')
    return ap


def fix_proj():
    """QGIS·PostGIS 설치가 깔아 둔 PROJ_DATA 와 rasterio/pyproj 가 충돌하는 문제 우회.
    rasterio/pyproj 를 import 하기 **전에** 부른다. 번들 proj.db 위치를 PROJ_DATA 로 강제."""
    import importlib.util
    for mod, subs in (('rasterio', ('proj_data',)), ('pyproj', ('proj_dir/share/proj',)), ('fiona', ('proj_data',))):
        spec = importlib.util.find_spec(mod)
        if not spec or not spec.submodule_search_locations:
            continue
        base = Path(list(spec.submodule_search_locations)[0])
        for s in subs:
            cand = base / s
            if (cand / 'proj.db').exists():
                os.environ['PROJ_DATA'] = os.environ['PROJ_LIB'] = str(cand)
                return str(cand)
    return None
