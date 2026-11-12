# -*- coding: utf-8 -*-
"""관번호 → 리(행정리) 매핑 JSON — 종단면도 A3 장별 제목(「리명 번호 | 관번호 범위」)용.

계획관로(layers.plan_pipe) 각 관의 **중점**(길이 1/2 지점)을 리 경계(layers.admin_ri) 폴리곤과
contains 로 판정하고, 안 걸리면 가장 가까운 리로 붙인다.
압송 중간노드 관번호(XX-A-01601 처럼 끝 번호가 3자리보다 긴 것)는 앞 3자리 축약키(XX-A-016)도
같이 넣는다 — 종단면도에는 명명 관번호만 찍히기 때문.

설정:
  layers.plan_pipe · fields.plan_pipe.pipe_id (district 는 선택 — --district 로 거를 때)
  layers.admin_ri  · fields.admin_ri.name (리 이름)
  선택: fields.admin_ri.kind + values.admin_ri_kind — 행정경계 한 레이어에 시·읍면·리가 섞여 있으면
        kind 필드가 그 값인 것만(예: 구분 = '리경계')
출력: <out>/ri_map.json  {관번호: [리, 지구]}

사용: python ri_map.py [--district OO --district OO(준공)] [--out 폴더]
"""
import argparse
import collections
import os

import profcommon as PC


def read_any(cfg, key, encoding=None):
    """kitconfig 레이어 → GeoDataFrame. SHP 는 .cpg 가 없으면 cp949 로 읽는다(한글 이름 깨짐 방지)."""
    import geopandas as gpd
    L = cfg.layer(key)
    if L['kind'] == 'postgis':
        return cfg.read_layer(key)
    path = str(L['path'])
    kw = {}
    if path.lower().endswith('.shp'):
        cpg = os.path.splitext(path)[0] + '.cpg'
        kw['encoding'] = encoding or (None if os.path.exists(cpg) else 'cp949')
        if kw['encoding'] is None:
            kw.pop('encoding')
        return gpd.read_file(path, **kw)
    return gpd.read_file(path, layer=L['layer'])


def main():
    ap = argparse.ArgumentParser(description='관번호 → 리 매핑 JSON (종단면도 A3 제목용)')
    PC.add_common_args(ap)
    ap.add_argument('--district', action='append', default=None,
                    help='이 지구 관로만(fields.plan_pipe.district 값, 여러 번 가능)')
    ap.add_argument('--encoding', default=None, help='리 경계 SHP 인코딩(기본: .cpg 없으면 cp949)')
    ap.add_argument('--name', default='ri_map.json', help='출력 파일 이름')
    a = ap.parse_args()
    PC.kitconfig.fix_proj()
    cfg = PC.load_cfg(a.config, required=True)
    pipe = cfg.read_layer('plan_pipe')
    f_id = cfg.field('plan_pipe', 'pipe_id')
    f_dist = cfg.fields('plan_pipe').get('district')
    if a.district:
        if not f_dist:
            raise SystemExit('--district 를 쓰려면 fields.plan_pipe.district 가 있어야 한다')
        pipe = pipe[pipe[f_dist].isin(a.district)]
    if not cfg.has_layer('admin_ri'):
        raise SystemExit('layers.admin_ri(리 경계 폴리곤)가 설정에 없다 — sewer.config.json 에 추가할 것')
    ri = read_any(cfg, 'admin_ri', a.encoding)
    f_nm = cfg.field('admin_ri', 'name')
    f_kind = cfg.fields('admin_ri').get('kind')
    v_kind = cfg.value('admin_ri_kind')
    if f_kind and v_kind:
        ri = ri[ri[f_kind] == v_kind]
    if ri.crs is not None and pipe.crs is not None and ri.crs != pipe.crs:
        ri = ri.to_crs(pipe.crs)
    ri = ri[ri.geometry.notna() & ~ri.geometry.is_empty].reset_index(drop=True)
    polys = list(zip(ri[f_nm].astype(str), ri.geometry))
    sidx = ri.sindex

    out, how = {}, collections.Counter()
    for _, r in pipe.iterrows():
        g = r.geometry
        if g is None or g.is_empty:
            how['도형없음'] += 1
            continue
        try:
            p = g.interpolate(g.length / 2.0)
        except Exception:
            p = g.representative_point()
        hit = None
        for k in sidx.query(p):
            if polys[int(k)][1].contains(p):
                hit = polys[int(k)][0]
                break
        if hit is None:
            k = sidx.nearest(p, return_all=False)
            k = int(k[1][0]) if hasattr(k, 'shape') and len(k.shape) == 2 else int(k)
            hit = polys[k][0]
            how['최근접'] += 1
        else:
            how['포함'] += 1
        t = str(r[f_id])
        d = str(r[f_dist]) if f_dist else ''
        out[t] = [hit, d]
        if '-' in t:
            pre, no = t.rsplit('-', 1)
            if len(no) > 3:                          # 압송 중간노드 → 명명 관번호 축약키
                out.setdefault('%s-%s' % (pre, no[:3]), [hit, d])
    od = PC.out_dir(cfg, a.out)
    path = od / a.name
    PC.write_json(path, out)
    cnt = collections.Counter(v[0] for v in out.values())
    print('관 %d본 → 키 %d개 (포함 %d · 최근접 %d · 도형없음 %d)'
          % (len(pipe), len(out), how['포함'], how['최근접'], how['도형없음']))
    print('리별:', ', '.join('%s %d' % kv for kv in cnt.most_common()))
    print('->', path)


if __name__ == '__main__':
    main()
