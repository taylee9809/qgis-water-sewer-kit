# -*- coding: utf-8 -*-
"""B1·B3 — 준공 종평면도: 종단 밴드(누가/지반고/관저고/깊이 …)와 관번호·맨홀명 라벨을 x 순으로 조립.

밴드 행의 y 좌표는 도면마다 다르다. CAD 나 layer_probe_* 결과로 밴드 행 y 를 재서
--band 이름=Y 로 주거나 sewer.config.json 의 asbuilt.profile.bands 에 적는다.
기준값(--anchor, 예: 어떤 맨홀의 지반고 60.21)을 주면 그 x 부근 종단을 표로 조립한다.

예)
  python -X utf8 asb_bands.py --dxf "준공 종평면도.dxf" ^
      --band 누가=1385 --band 지반고=1400 --band 관저고=1415 --band 깊이=1430 ^
      --label-y 1450 1700 --anchor 60.21
읽기 전용 — 아무 파일도 쓰지 않는다.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

# 설계사 도면 규격 기본값(한 설계사 예). 그 팀 도면에 맞게 asbuilt.dxf_layers.* 또는 인자로 바꾼다.
DEF_LAYERS = {'pipe_no': '0_신설계획오수관-관번호', 'pipe_name': '!!_계획오수관로명', 'mh_text': '종평용맨홀텍스트'}
VALUE_RE = r'-?\d+\.\d{2}'   # 밴드 숫자(소수 둘째 자리) 판정


def load_cfg(path):
    """설정은 선택 — 없으면 기본 레이어명으로 돈다(B1 판독은 설정 전에도 돌릴 수 있게)."""
    try:
        return kitconfig.load(path)
    except kitconfig.ConfigError:
        if path:
            raise
        print('(sewer.config.json 없음 — 인자·기본 레이어명만 사용)', file=sys.stderr)
        return None


def opt(cfg, dotted, default=None):
    return cfg.get(dotted, default) if cfg else default


def parse_bands(specs):
    bands = {}
    for s in specs:
        if '=' not in s:
            raise SystemExit('--band 는 이름=Y 형식: %s' % s)
        k, v = s.split('=', 1)
        bands[k.strip()] = float(v)
    return bands


def main():
    ap = argparse.ArgumentParser(description='준공 종평면도 종단 밴드·라벨을 x 순으로 조립 (읽기 전용)')
    ap.add_argument('--dxf', required=True, help='준공 종평면도 DXF 경로')
    ap.add_argument('--band', action='append', metavar='이름=Y',
                    help='밴드 행 y 좌표. 여러 번 준다(순서 = 출력 순서). 없으면 asbuilt.profile.bands')
    ap.add_argument('--band-tol', type=float, default=None, help='밴드 y 허용차 (기본 asbuilt.profile.band_tol 또는 4)')
    ap.add_argument('--label-y', nargs=2, type=float, metavar=('LO', 'HI'),
                    help='종단 영역 라벨(관번호·맨홀명)의 y 범위. 없으면 asbuilt.profile.label_y, 그것도 없으면 라벨 생략')
    ap.add_argument('--label-layers', nargs='+', metavar='LAYER',
                    help='라벨 레이어 (기본 asbuilt.dxf_layers 의 pipe_no·pipe_name·mh_text)')
    ap.add_argument('--anchor', help='기준 밴드에서 찾을 값(표기 그대로, 예 60.21). 그 x 부근을 조립')
    ap.add_argument('--anchor-band', default='지반고', help='--anchor 를 찾을 밴드 이름 (기본 지반고)')
    ap.add_argument('--span', type=float, default=420.0, help='기준 x 에서 조립할 거리 (기본 420)')
    ap.add_argument('--cols', nargs='+', metavar='밴드', help='조립표에 찍을 밴드 (기본: 전부)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    prof = opt(cfg, 'asbuilt.profile', {}) or {}
    BAND = parse_bands(a.band) if a.band else dict(prof.get('bands') or {})
    if not BAND:
        ap.error('밴드 y 좌표가 없다 — --band 이름=Y 로 주거나 asbuilt.profile.bands 에 적을 것')
    tol = a.band_tol if a.band_tol is not None else float(prof.get('band_tol', 4))
    label_y = a.label_y or prof.get('label_y')
    lay = {k: opt(cfg, 'asbuilt.dxf_layers.' + k, v) for k, v in DEF_LAYERS.items()}
    label_layers = a.label_layers or [lay['pipe_no'], lay['pipe_name'], lay['mh_text']]
    cols = a.cols or list(BAND)

    import ezdxf
    doc = ezdxf.readfile(a.dxf)
    items = []

    def walk(layout, ox=0.0, oy=0.0, depth=0):
        for e in layout:
            t = e.dxftype()
            try:
                if t in ('TEXT', 'MTEXT'):
                    s = (e.dxf.text if t == 'TEXT' else e.text).strip()
                    items.append((e.dxf.insert[0] + ox, e.dxf.insert[1] + oy, s, e.dxf.layer))
                elif t == 'INSERT':
                    for at in e.attribs:
                        items.append((at.dxf.insert[0] + ox, at.dxf.insert[1] + oy, at.dxf.text.strip(),
                                      'ATTR:' + e.dxf.name))
                    if depth < 3:
                        walk(doc.blocks[e.dxf.name], ox + e.dxf.insert[0], oy + e.dxf.insert[1], depth + 1)
            except Exception:
                pass

    walk(doc.modelspace())
    rows = {k: sorted([(i[0], i[2]) for i in items if abs(i[1] - y) < tol and re.fullmatch(VALUE_RE, i[2])])
            for k, y in BAND.items()}
    for k in rows:
        print(k, len(rows[k]))
    if label_y:
        lo, hi = label_y
        lab = sorted([(i[0], i[2], i[3]) for i in items if lo < i[1] < hi and i[3] in label_layers])
        print('라벨', len(lab))
    else:
        lab = []
        print('라벨 (--label-y 미지정 — 생략)')
    print()
    if not a.anchor:
        print('--anchor 값을 주면 그 x 부근 종단을 조립한다 (예: 시점 맨홀 지반고).')
        return
    if a.anchor_band not in rows:
        ap.error('--anchor-band %s 가 --band 에 없다' % a.anchor_band)
    anchors = [x for x, v in rows[a.anchor_band] if v == a.anchor]
    print('%s %s 위치:' % (a.anchor_band, a.anchor), anchors)

    def near(k, x, tol=3.0):
        c = [(abs(p - x), v) for p, v in rows[k] if abs(p - x) <= tol]
        c.sort()
        return c[0][1] if c else ''

    S = a.span
    for x0 in anchors:
        print('\n===== x=%.1f 부근 종단 조립 (x +0~+%g) =====' % (x0, S))
        xs = sorted(set(x for x, v in rows[a.anchor_band] if x0 - 20 <= x <= x0 + S))
        print('%10s' % 'x' + ''.join(' %9s' % c for c in cols))
        for x in xs:
            print('%10.1f' % x + ''.join(' %9s' % near(c, x) for c in cols))
        print('  라벨:', ' | '.join('%.0f:%s' % (l[0], l[1]) for l in lab if x0 - 30 <= l[0] <= x0 + S + 10))


if __name__ == '__main__':
    main()
