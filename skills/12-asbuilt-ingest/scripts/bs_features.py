# -*- coding: utf-8 -*-
"""B3·B4 — 배수설비 평면도 원본 → 오수받이·소형맨홀(개소)·옥내/옥외 연결관(선) 좌표 추출.

좌표 오프셋(도면 → 프로젝트 좌표계)을 더해 JSON 으로 저장한다. DB 의 asbuilt_inlet·asbuilt_service_pipe
와 개수·위치(0.1 m 이내) 대조에 쓴다.

오프셋: --offset DX DY  또는  --district 지구명 (sewer.config.json asbuilt.offsets.<지구명>)
레이어: asbuilt.dxf_layers.{inlet_points,inlet_blocks,service_lines} 또는 인자.
예)
  python -X utf8 bs_features.py --dxf "배수설비 평면도.dxf" --district A지구
  python -X utf8 bs_features.py --dxf a.dxf --offset 0 0 --point-layers 오수받이 소형맨홀 --line-layers 연결관
산출: <outputs.root>/12-asbuilt/bs_cmp.json (또는 --out). DXF 는 읽기만 한다.
"""
import argparse
import collections
import io
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

# 설계사 도면 규격 기본값(한 설계사 예). asbuilt.dxf_layers.* 또는 인자로 바꾼다.
DEF_POINT_LAYERS = ['0_신설오수받이', '0_소형오수맨홀']   # 폐합선 → 중심점 → 1.5 m 내 통합
DEF_BLOCKS = ['오수받이']                                # 블록 삽입점
DEF_LINE_LAYERS = ['0_옥내연결관', '0_옥외연결관']         # 연결관 선


def load_cfg(path):
    try:
        return kitconfig.load(path)
    except kitconfig.ConfigError:
        if path:
            raise
        print('(sewer.config.json 없음 — 인자·기본 레이어명·현재 폴더 사용)', file=sys.stderr)
        return None


def opt(cfg, dotted, default=None):
    return cfg.get(dotted, default) if cfg else default


def main():
    ap = argparse.ArgumentParser(description='배수설비 평면도 → 오수받이/연결관/소형맨홀 좌표 추출')
    ap.add_argument('--dxf', required=True, help='배수설비 평면도 DXF 경로')
    ap.add_argument('--offset', nargs=2, type=float, metavar=('DX', 'DY'), help='도면 좌표에 더할 오프셋')
    ap.add_argument('--district', help='asbuilt.offsets 에서 오프셋을 꺼낼 지구명')
    ap.add_argument('--point-layers', nargs='+', metavar='LAYER', help='개소(폐합선) 레이어 (기본 asbuilt.dxf_layers.inlet_points)')
    ap.add_argument('--blocks', nargs='+', metavar='BLOCK', help='개소 블록명 (기본 asbuilt.dxf_layers.inlet_blocks)')
    ap.add_argument('--line-layers', nargs='+', metavar='LAYER', help='연결관 레이어 (기본 asbuilt.dxf_layers.service_lines)')
    ap.add_argument('--out', help='산출 JSON (기본 <outputs.root>/12-asbuilt/bs_cmp.json)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    if a.offset:
        OX, OY = a.offset
    elif a.district:
        offs = opt(cfg, 'asbuilt.offsets', {}) or {}
        if a.district not in offs:
            ap.error('asbuilt.offsets 에 "%s" 가 없다 — asb_offset_diff.py 로 산정해 적거나 --offset 으로 줄 것' % a.district)
        OX, OY = offs[a.district]
    else:
        ap.error('--offset DX DY 또는 --district 지구명 이 필요하다 (도면이 이미 프로젝트 좌표면 --offset 0 0)')
    point_layers = a.point_layers or opt(cfg, 'asbuilt.dxf_layers.inlet_points', DEF_POINT_LAYERS)
    blocks = a.blocks or opt(cfg, 'asbuilt.dxf_layers.inlet_blocks', DEF_BLOCKS)
    line_layers = a.line_layers or opt(cfg, 'asbuilt.dxf_layers.service_lines', DEF_LINE_LAYERS)
    out = Path(a.out) if a.out else ((cfg.out_dir('12-asbuilt') if cfg else Path.cwd()) / 'bs_cmp.json')
    print('오프셋 (%+.2f, %+.2f)' % (OX, OY))

    import ezdxf
    doc = ezdxf.readfile(a.dxf)
    L = collections.defaultdict(list)   # layer -> polylines
    B = collections.defaultdict(list)   # block -> points

    def walk(layout, ox=0.0, oy=0.0, depth=0):
        for e in layout:
            t = e.dxftype()
            try:
                if t in ('LWPOLYLINE', 'POLYLINE', 'LINE'):
                    if t == 'LINE':
                        pts = [(e.dxf.start[0] + ox, e.dxf.start[1] + oy), (e.dxf.end[0] + ox, e.dxf.end[1] + oy)]
                    elif t == 'LWPOLYLINE':
                        pts = [(p[0] + ox, p[1] + oy) for p in e.get_points('xy')]
                    else:
                        pts = [(v.dxf.location[0] + ox, v.dxf.location[1] + oy) for v in e.vertices]
                    L[e.dxf.layer].append(pts)
                elif t == 'INSERT':
                    B[e.dxf.name].append((e.dxf.insert[0] + ox, e.dxf.insert[1] + oy))
                    if depth < 2:
                        walk(doc.blocks[e.dxf.name], ox + e.dxf.insert[0], oy + e.dxf.insert[1], depth + 1)
            except Exception:
                pass

    walk(doc.modelspace())

    def cen(pts):
        return (sum(p[0] for p in pts) / len(pts) + OX, sum(p[1] for p in pts) / len(pts) + OY)

    def plen(pts):
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    res = {}
    for lay in point_layers:
        pts = [cen(p) for p in L[lay]]
        # 1.5m 내 통합
        uniq = []
        for p in pts:
            if not any(math.dist(p, q) < 1.5 for q in uniq):
                uniq.append(p)
        res[lay] = uniq
        print('%-20s 선 %3d → 개소 %3d' % (lay, len(L[lay]), len(uniq)))
    for b in blocks:
        pts = [(x + OX, y + OY) for x, y in B[b]]
        res['블록:' + b] = pts
        print('%-20s 블록 %3d' % ('블록 ' + b, len(pts)))
    for lay in line_layers:
        segs = [([(x + OX, y + OY) for x, y in p], round(plen(p), 2)) for p in L[lay]]
        res[lay] = segs
        if segs:
            print('%-20s 선 %3d  총 %.1f m  (개별 %.1f~%.1f)' % (lay, len(segs), sum(s[1] for s in segs),
                                                             min(s[1] for s in segs), max(s[1] for s in segs)))
        else:
            print('%-20s 선   0  (레이어명 확인)' % lay)
    with io.open(out, 'w', encoding='utf-8') as f:
        json.dump({k: (v if k not in line_layers else [[p[0], p[1]] for p in v]) for k, v in res.items()},
                  f, ensure_ascii=False, default=str)
    if point_layers and res.get(point_layers[0]):
        print('\n좌표 예시 (%s 상위 5):' % point_layers[0])
        for p in res[point_layers[0]][:5]:
            print('   (%.2f, %.2f)' % p)
    print('\n저장: %s' % out)


if __name__ == '__main__':
    main()
