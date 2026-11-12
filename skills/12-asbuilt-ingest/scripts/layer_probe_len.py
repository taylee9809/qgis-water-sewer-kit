# -*- coding: utf-8 -*-
"""B1 — 준공 평면도에서 '관로 선형'이 어느 레이어인지 판별: 준공 연장값(asb_spec.json)과
길이가 일치(±0.25 m)하는 세그먼트 수가 많은 레이어 순으로 찍는다.

선행: asb_spec.py (종평면도 → asb_spec.json)
예)
  python -X utf8 layer_probe_len.py --dxf "준공 계획평면도.dxf"
  python -X utf8 layer_probe_len.py --dxf a.dxf --spec A지구_asb_spec.json
읽기 전용.
"""
import argparse
import collections
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')


def load_cfg(path):
    try:
        return kitconfig.load(path)
    except kitconfig.ConfigError:
        if path:
            raise
        print('(sewer.config.json 없음 — --spec 기본값은 현재 폴더)', file=sys.stderr)
        return None


def read_json(p):
    for enc in ('utf-8', 'cp949'):   # 예전 산출물은 cp949 로 저장된 것이 있다
        try:
            with open(p, encoding=enc) as f:
                return json.load(f)
        except UnicodeDecodeError:
            continue
    raise SystemExit('JSON 인코딩을 읽지 못했다: %s' % p)


def main():
    ap = argparse.ArgumentParser(description='준공 평면도 관로 선형 레이어 판별 (준공 연장값 일치 세그먼트 수)')
    ap.add_argument('--dxf', required=True, help='준공 계획평면도 DXF 경로')
    ap.add_argument('--spec', help='asb_spec.py 산출 JSON (기본 <outputs.root>/12-asbuilt/asb_spec.json)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    sp = Path(a.spec) if a.spec else ((cfg.out_dir('12-asbuilt') if cfg else Path.cwd()) / 'asb_spec.json')
    if not sp.exists():
        raise SystemExit('asb_spec.json 이 없다: %s — asb_spec.py 를 먼저 돌릴 것' % sp)
    spec = read_json(sp)
    Ls = sorted(set(v[0] for v in spec['pipe'].values() if v[0]))

    import ezdxf
    doc = ezdxf.readfile(a.dxf)
    seg = collections.defaultdict(list)

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
                    for i in range(len(pts) - 1):
                        d = math.dist(pts[i], pts[i + 1])
                        if d > 0.5:
                            seg[e.dxf.layer].append(d)
                elif t == 'INSERT' and depth < 2:
                    walk(doc.blocks[e.dxf.name], ox + e.dxf.insert[0], oy + e.dxf.insert[1], depth + 1)
            except Exception:
                pass

    walk(doc.modelspace())
    print('준공 연장값 %d종' % len(Ls))
    res = []
    for lay, ds in seg.items():
        hit = sum(1 for d in ds if any(abs(d - L) < 0.25 for L in Ls))
        if hit >= 3:
            res.append((hit, len(ds), lay))
    res.sort(reverse=True)
    print('%6s %7s  %s' % ('일치', '전체', '레이어'))
    for h, n, lay in res[:20]:
        print('%6d %7d  %s' % (h, n, lay))


if __name__ == '__main__':
    main()
