# -*- coding: utf-8 -*-
"""01 — 측량현황도 DXF(100 MB급) → 지장물 작업에 필요한 레이어만 남긴 경량 DXF.

왜 필요한가: 100 MB 통짜를 ezdxf 로 읽으면 MemoryError 가 난다(전체 텍스트 split 방식).
필요한 레이어만 남기면 5 MB 내외로 줄고 1~2 초에 읽힌다.

규칙(지키지 않으면 깨진다)
  · code 0 청크 단위로 자른다.
  · SECTION/ENDSEC/EOF/TABLE/LAYER/BLOCK 등 구조 청크는 무조건 통과시킨다.
  · POLYLINE/INSERT 의 하위 VERTEX/ATTRIB/SEQEND 는 부모의 판정을 이어받는다.
  · 310(바이너리 미리보기) 태그는 버린다.

사용
  python -X utf8 01_filter_dxf.py "<원본.dxf>" [--config sewer.config.json]
                                  [--out <경량.dxf>] [--add 레이어명,레이어명]
  --out 기본: <outputs.root>/11-survey/survey_light.dxf  (02~14 가 기본으로 읽는 자리)
  원본 경로를 안 주면 survey.dxf_dir 아래 DXF 목록(최근 것부터)만 보여 준다.
  남길 레이어(KEEP)는 측량사 작도 규약이다 — 빠진 레이어는 --add 로.
"""
import os
import sys
import collections
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _kit import argval, positionals, out_path, cfg_path, LIGHT_DXF   # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

KEEP = {'배수시설물', '배수시설물-매립', '배수시설물-추진', 'TEXT', '상세', '교량',
        'S.오수맨홀', 'S.정화조', '구조물', '0'}
CHILD = {'VERTEX', 'ATTRIB', 'SEQEND'}
STRUCT = {'SECTION', 'ENDSEC', 'EOF', 'TABLE', 'ENDTAB', 'LAYER', 'STYLE', 'LTYPE',
          'VPORT', 'APPID', 'DIMSTYLE', 'UCS', 'VIEW', 'BLOCK_RECORD', 'BLOCK', 'ENDBLK',
          'CLASS', 'DICTIONARY', 'ACDBDICTIONARYWDFLT'}


def list_candidates():
    """survey.dxf_dir 아래 DXF (최근 수정 순 20개)."""
    d = cfg_path('survey.dxf_dir')
    if not d or not os.path.isdir(d):
        return
    fs = [p for p in Path(d).rglob('*.dxf') if p.name != LIGHT_DXF]
    fs.sort(key=lambda p: -p.stat().st_mtime)
    print('survey.dxf_dir (%s) 의 DXF %d개 — 최근 것부터:' % (d, len(fs)))
    for p in fs[:20]:
        print('  %6.1f MB  %s' % (p.stat().st_size / 1e6, p))


def main():
    pos = positionals()
    if not pos:
        print(__doc__)
        list_candidates()
        return
    src = pos[0]
    dst = argval('--out') or out_path(LIGHT_DXF)
    keep = set(KEEP)
    if '--add' in sys.argv:
        keep |= {x.strip() for x in sys.argv[sys.argv.index('--add') + 1].split(',') if x.strip()}

    lay_all, kept = collections.Counter(), collections.Counter()
    out = open(dst, 'w', encoding='utf-8', newline='\n')
    f = open(src, 'r', encoding='utf-8', errors='replace')
    it = iter(f)
    chunk, cur_lay, parent_keep, n = [], None, None, 0

    def flush():
        nonlocal chunk, cur_lay, parent_keep
        if not chunk:
            return
        t = chunk[0][1]
        if t in STRUCT:
            ok = True
        elif t in CHILD:
            ok = bool(parent_keep)
        else:
            lay_all[(t, cur_lay or '')] += 1
            ok = (cur_lay or '') in keep
            parent_keep = ok
        if ok:
            if t not in CHILD and t not in STRUCT:
                kept[(t, cur_lay or '')] += 1
            for code, val in chunk:
                if code in ('310', '1004'):
                    continue
                out.write('%s\n%s\n' % (code, val))
        chunk, cur_lay = [], None

    for code in it:
        try:
            val = next(it).rstrip('\r\n')
        except StopIteration:
            break
        code = code.strip()
        n += 1
        if code == '0':
            flush()
            chunk = [(code, val)]
        else:
            chunk.append((code, val))
            if code == '8':
                cur_lay = val.strip()
    flush()
    out.close()
    f.close()

    print('코드쌍 %d → %s (%.1f MB)' % (n, dst, os.path.getsize(dst) / 1e6))
    print('\n남긴 엔티티:')
    for (t, l), c in kept.most_common(30):
        print('  %-12s %-24s %8d' % (t, l, c))
    print('\n원본 레이어 상위 15(참고 — 빠진 게 있으면 --add 로 추가):')
    for (t, l), c in lay_all.most_common(15):
        print('  %-12s %-24s %8d' % (t, l, c))


if __name__ == '__main__':
    main()
