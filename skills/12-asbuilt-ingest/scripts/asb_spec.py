# -*- coding: utf-8 -*-
"""B3 — 준공 종평면도 평면부: 관번호↔연장↔관경, 맨홀명↔깊이(H=) 를 인접쌍으로 추출해 원본 기준표(JSON)로 저장.

산출 JSON {"pipe": {관번호: [L, D]}, "man": {맨홀명: H}} 은 asb_offset_diff.py(DB diff)와
layer_probe_len.py(관로 선형 레이어 판별)가 읽는다.

레이어명은 설계사 도면 규격마다 다르다 → sewer.config.json asbuilt.dxf_layers.{pipe_no,pipe_len,pipe_dia,mh_text}
또는 인자로. 먼저 layer_probe_color.py 로 레이어 목록을 본다.

예)
  python -X utf8 asb_spec.py --dxf "준공 종평면도.dxf"            → <outputs.root>/12-asbuilt/asb_spec.json
  python -X utf8 asb_spec.py --dxf a.dxf --out A지구_asb_spec.json
DXF 는 읽기만 한다.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

# 설계사 도면 규격 기본값(한 설계사 예). asbuilt.dxf_layers.* 또는 인자로 바꾼다.
DEF_LAYERS = {'pipe_no': '0_신설계획오수관-관번호', 'pipe_len': '0_신설계획오수관-연장',
              'pipe_dia': '0_신설계획오수관-관경', 'mh_text': '종평용맨홀텍스트'}


def load_cfg(path):
    try:
        return kitconfig.load(path)
    except kitconfig.ConfigError:
        if path:
            raise
        print('(sewer.config.json 없음 — 기본 레이어명·현재 폴더 사용)', file=sys.stderr)
        return None


def opt(cfg, dotted, default=None):
    return cfg.get(dotted, default) if cfg else default


def main():
    ap = argparse.ArgumentParser(description='준공 종평면도 평면부 제원표(관번호·연장·관경, 맨홀명·깊이) 추출')
    ap.add_argument('--dxf', required=True, help='준공 종평면도 DXF 경로')
    ap.add_argument('--out', help='산출 JSON (기본 <outputs.root>/12-asbuilt/asb_spec.json, 설정 없으면 현재 폴더)')
    ap.add_argument('--pipe-no-layer', help='관번호 TEXT 레이어 (기본 asbuilt.dxf_layers.pipe_no)')
    ap.add_argument('--pipe-len-layer', help='연장(L=) TEXT 레이어 (기본 asbuilt.dxf_layers.pipe_len)')
    ap.add_argument('--pipe-dia-layer', help='관경(D=) TEXT 레이어 (기본 asbuilt.dxf_layers.pipe_dia)')
    ap.add_argument('--mh-text-layer', help='맨홀명·H= TEXT 레이어 (기본 asbuilt.dxf_layers.mh_text)')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = load_cfg(a.config)
    lay = {k: opt(cfg, 'asbuilt.dxf_layers.' + k, v) for k, v in DEF_LAYERS.items()}
    for k, v in (('pipe_no', a.pipe_no_layer), ('pipe_len', a.pipe_len_layer),
                 ('pipe_dia', a.pipe_dia_layer), ('mh_text', a.mh_text_layer)):
        if v:
            lay[k] = v
    out = Path(a.out) if a.out else ((cfg.out_dir('12-asbuilt') if cfg else Path.cwd()) / 'asb_spec.json')

    import ezdxf
    doc = ezdxf.readfile(a.dxf)
    it = []

    def walk(layout, ox=0.0, oy=0.0, depth=0):
        for e in layout:
            t = e.dxftype()
            try:
                if t == 'TEXT':
                    it.append((e.dxf.layer, e.dxf.insert[0] + ox, e.dxf.insert[1] + oy, e.dxf.text.strip()))
                elif t == 'INSERT' and depth < 3:
                    walk(doc.blocks[e.dxf.name], ox + e.dxf.insert[0], oy + e.dxf.insert[1], depth + 1)
            except Exception:
                pass

    walk(doc.modelspace())
    pn = [i for i in it if i[0] == lay['pipe_no']]
    pl = [i for i in it if i[0] == lay['pipe_len']]
    pd = [i for i in it if i[0] == lay['pipe_dia']]
    mh = [i for i in it if i[0] == lay['mh_text'] and not i[3].upper().startswith('H=')]
    hh = [i for i in it if i[0] == lay['mh_text'] and i[3].upper().startswith('H=')]
    print('관번호 %d / 연장 %d / 관경 %d / 맨홀 %d / H= %d' % (len(pn), len(pl), len(pd), len(mh), len(hh)))
    if not pn or not mh:
        print('  ★ 관번호 또는 맨홀 라벨이 0건 — layer_probe_color.py 로 레이어명을 확인하고 '
              'asbuilt.dxf_layers 또는 --*-layer 인자를 고칠 것')

    pipe = {}
    for _lay, x, y, t in pn:
        cl = sorted((math.hypot(q[1] - x, q[2] - y), q[3]) for q in pl)
        cd = sorted((math.hypot(q[1] - x, q[2] - y), q[3]) for q in pd)
        L = float(re.sub(r'[^\d.]', '', cl[0][1])) if cl and cl[0][0] < 25 else None
        D = float(re.sub(r'[^\d.]', '', cd[0][1])) if cd and cd[0][0] < 25 else None
        if t in pipe:
            if pipe[t] != (L, D):
                print('  ! 관번호 %s 중복 라벨 값상이: %s vs %s' % (t, pipe[t], (L, D)))
        pipe[t] = (L, D)
    man = {}
    for _lay, x, y, t in mh:
        ch = sorted((math.hypot(q[1] - x, q[2] - y), q[3]) for q in hh)
        H = float(re.sub(r'[^\d.]', '', ch[0][1])) if ch and ch[0][0] < 20 else None
        if t in man and man[t] != H:
            print('  ! 맨홀 %s 중복 라벨 깊이상이: %s vs %s' % (t, man[t], H))
        man[t] = H
    print('\n=== 준공 원본 관로 제원 (%d) ===' % len(pipe))
    for k in sorted(pipe):
        print('  %-14s L=%-8s D=%s' % (k, pipe[k][0], pipe[k][1]))
    print('\n=== 준공 원본 맨홀 깊이 (%d) ===' % len(man))
    for k in sorted(man):
        print('  %-16s H=%s' % (k, man[k]))
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'pipe': pipe, 'man': man}, f, ensure_ascii=False, indent=1)
    print('\n저장: %s' % out)


if __name__ == '__main__':
    main()
