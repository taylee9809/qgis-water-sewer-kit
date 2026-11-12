# -*- coding: utf-8 -*-
"""B1 — 준공도 레이어·색(ACI) 판독: 신설/기존활용/기존폐쇄 구분 체계를 먼저 파악한다.

- 지정 레이어(관로·관번호·맨홀·오수받이 …)의 레이어 색
- 이름에 '폐쇄/기존/활용' 이 들어간 레이어와 색
- 관번호 라벨 TEXT 의 실제 색 분포 (예: 노란색(2) 라벨 = 기존폐쇄 인지)

예)
  python -X utf8 layer_probe_color.py "종평면도=준공 종평면도.dxf" "배수설비=배수설비 평면도.dxf"
  python -X utf8 layer_probe_color.py a.dxf --label-layer 관번호 --layers 오수관 신설맨홀
인자는 '태그=경로' 또는 경로(태그 = 파일명). 읽기 전용.
"""
import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

ACI = {1: '빨강', 2: '노랑', 3: '초록', 4: '청록', 5: '파랑', 6: '자홍', 7: '흰/검', 8: '회색', 256: 'BYLAYER', 0: 'BYBLOCK'}
# 설계사 도면 규격 기본값(한 설계사 예). asbuilt.dxf_layers.probe / pipe_no 또는 인자로 바꾼다.
DEF_PROBE = ['0_신설계획오수관', '0_신설계획오수관-관번호', '오수관로라인', '기존맨홀',
             '신설맨홀', '신설오수맨홀', '0_신설오수받이', '0_소형오수맨홀']
DEF_LABEL = '0_신설계획오수관-관번호'
DEF_KEYWORDS = ['폐쇄', '기존', '활용']


def load_cfg(path):
    try:
        return kitconfig.load(path)
    except kitconfig.ConfigError:
        if path:
            raise
        print('(sewer.config.json 없음 — 인자·기본 레이어명만 사용)', file=sys.stderr)
        return None


def opt(cfg, dotted, default=None):
    return cfg.get(dotted, default) if cfg else default


def parse_file(s):
    if '=' in s:
        tag, p = s.split('=', 1)
        if tag and not any(c in tag for c in '/\\:.'):
            return tag, p
    return Path(s).stem, s


def main():
    ap = argparse.ArgumentParser(description='준공도 DXF 레이어 색·관번호 라벨 색 분포 판독 (읽기 전용)')
    ap.add_argument('dxf', nargs='+', help="DXF 경로 또는 '태그=경로' (여러 개)")
    ap.add_argument('--layers', nargs='+', metavar='LAYER',
                    help='색을 볼 레이어 (기본 asbuilt.dxf_layers.probe, 없으면 내장 예시 목록)')
    ap.add_argument('--label-layer', help='관번호 라벨 레이어 (기본 asbuilt.dxf_layers.pipe_no)')
    ap.add_argument('--keywords', nargs='+', default=DEF_KEYWORDS,
                    help="구분 체계 레이어를 찾을 단어 (기본 '폐쇄 기존 활용')")
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    probe = a.layers or opt(cfg, 'asbuilt.dxf_layers.probe', DEF_PROBE)
    label_layer = a.label_layer or opt(cfg, 'asbuilt.dxf_layers.pipe_no', DEF_LABEL)

    import ezdxf
    for tag, P in (parse_file(s) for s in a.dxf):
        doc = ezdxf.readfile(P)
        laycol = {l.dxf.name: l.dxf.color for l in doc.layers}
        print('\n===== %s =====' % tag)
        for nm in probe:
            if nm in laycol:
                c = laycol[nm]
                print('  레이어 %-24s color=%-4s %s' % (nm, c, ACI.get(abs(c), '')))
        # 구분 체계(기존폐쇄·기존활용 …) 비슷한 레이어
        for n, c in sorted(laycol.items()):
            if any(k in n for k in a.keywords):
                print('  * %-28s color=%-4s %s' % (n, c, ACI.get(abs(c), '')))
        # 관번호 라벨의 실제 색
        out = collections.defaultdict(list)

        def walk(layout, depth=0):
            for e in layout:
                t = e.dxftype()
                try:
                    if t == 'TEXT' and e.dxf.layer == label_layer:
                        c = e.dxf.color
                        eff = laycol.get(e.dxf.layer, 7) if c == 256 else c
                        out[(eff, ACI.get(abs(eff), ''))].append(e.dxf.text.strip())
                    elif t == 'INSERT' and depth < 2:
                        walk(doc.blocks[e.dxf.name], depth + 1)
                except Exception:
                    pass
        walk(doc.modelspace())
        print('  관번호 라벨 색 분포:')
        if not out:
            print('    (라벨 레이어 %s 에 TEXT 없음 — --label-layer 확인)' % label_layer)
        for k, v in sorted(out.items()):
            print('    color=%-4s %-6s  %2d개  %s' % (k[0], k[1], len(v), ' '.join(sorted(set(v)))[:170]))


if __name__ == '__main__':
    main()
