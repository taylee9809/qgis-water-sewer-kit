# -*- coding: utf-8 -*-
"""배수설비 가구 반영/제외 관리 ① — 가구 레이어 → 엑셀 편집표.

원본(마스터)은 레이어(설정 layers.house)이고 엑셀은 '편집 창구'일 뿐이다. 사람이 엑셀에서
반영여부(포함/제외)·제외사유·점유량_m3d·배정맨홀을 적으면 house_xlsx_import.py 가 레이어에 되돌려 넣는다.

시트
  배수설비가구 — fid(레이어 키, 고치지 말 것) + 레이어 필드 + X·Y. 노란 칸 = 편집 대상(점유량_m3d·반영여부·제외사유)
  요약        — --group-by(기본 처리구역·기존_신규)별 건수·Q 합·점유량 합·반영 건수·반영 Q 합
  사용법      — 편집 규칙
관리 필드(반영여부·제외사유·검토일·점유량_m3d)가 레이어에 없으면 표에는 빈 열(반영여부 = 포함)로 내고,
레이어에는 import 의 --write 때 더한다(이 스크립트는 레이어를 고치지 않는다).

필드: fields.house.{house_id, district, new_or_old, q_m3d, assigned_mh} (없으면 배수설비명·지구·기존_신규·Q_m3d·배정맨홀)
산출: --out (기본 <outputs.root>/10-intake/배수설비가구_반영관리_YYMMDD.xlsx). 이미 있으면 멈춘다(--replace 로 덮어씀).

예)
  python -X utf8 house_xlsx_export.py
  python -X utf8 house_xlsx_export.py --cols 처리구역 지구 배수설비명 기존_신규 Q_m3d 점유량_m3d 반영여부 제외사유 배정맨홀 비고
"""
import argparse
import collections
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / 'lib' for p in Path(__file__).resolve().parents if (p / 'lib' / 'kitconfig.py').exists())))
import kitconfig  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8')

MGMT = [('반영여부', 'str'), ('제외사유', 'str'), ('검토일', 'str'), ('점유량_m3d', 'float')]
EDIT = ('점유량_m3d', '반영여부', '제외사유')


def read_house(cfg):
    """→ (필드 목록, [(fid, props, (x, y))]) — fid 는 레이어의 실제 키."""
    L = cfg.layer('house')
    if L['kind'] == 'gpkg':
        import pyogrio
        df = pyogrio.read_dataframe(L['path'], layer=L['layer'], fid_as_index=True)
        fids = [int(i) for i in df.index]
    else:
        df = cfg.read_layer('house')
        if 'fid' not in df.columns:
            raise kitconfig.ConfigError('PostGIS 가구 표에 fid 열이 없다 — 키 열이 있어야 엑셀과 되맞출 수 있다')
        fids = [int(v) for v in df['fid']]
        df = df.drop(columns=['fid'])
    fields = [c for c in df.columns if c != df.geometry.name]
    rows = []
    for fid, (_, r) in zip(fids, df.iterrows()):
        g = r[df.geometry.name]
        if g is not None and g.geom_type == 'MultiPoint':
            g = g.geoms[0]
        props = {c: (None if (not isinstance(r[c], str) and r[c] != r[c]) else r[c]) for c in fields}
        rows.append((fid, props, (g.x, g.y) if g is not None else (None, None)))
    return fields, rows


def py(v):
    if v is None:
        return None
    if hasattr(v, 'item'):
        v = v.item()
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cols', nargs='+', help='엑셀에 낼 필드(순서대로). 기본 = 레이어 필드 순서 + 빠진 관리 필드')
    ap.add_argument('--group-by', nargs='+', help='요약 시트 묶음 필드 (기본 처리구역·기존_신규 중 있는 것)')
    ap.add_argument('--out', help='엑셀 경로 (기본 <outputs.root>/10-intake/배수설비가구_반영관리_YYMMDD.xlsx)')
    ap.add_argument('--replace', action='store_true', help='--out 이 있으면 덮어쓴다')
    kitconfig.add_config_arg(ap)
    a = ap.parse_args()

    cfg = kitconfig.load(a.config)
    f_id = cfg.field('house', 'house_id', default='배수설비명')
    f_new = cfg.field('house', 'new_or_old', default='기존_신규')
    f_q = cfg.field('house', 'q_m3d', default='Q_m3d')
    fields, rows = read_house(cfg)
    L = cfg.layer('house')
    print('%s %d건 읽음' % (L['layer'], len(rows)))
    missing = [m for m, _ in MGMT if m not in fields]
    if missing:
        print('레이어에 없는 관리 필드: %s — 표에는 빈 열로 내고, import --write 때 레이어에 더한다' % missing)
    for _fid, p, _xy in rows:
        p.setdefault('반영여부', '포함')
        if not p.get('반영여부'):
            p['반영여부'] = '포함'
        for k in ('제외사유', '검토일', '점유량_m3d'):
            p.setdefault(k, None)
    body = a.cols or (fields + [m for m in missing])
    if f_id not in body:
        ap.error('--cols 에 가구 이름 필드(%s)가 있어야 한다 — import 가 fid 와 함께 대조한다' % f_id)
    COLS = ['fid'] + body + ['X', 'Y']
    out = Path(a.out) if a.out else cfg.out_dir('10-intake') / ('배수설비가구_반영관리_%s.xlsx' % datetime.date.today().strftime('%y%m%d'))
    if out.exists() and not a.replace:
        raise SystemExit('이미 있다: %s — 덮어쓰려면 --replace (사람이 고친 표일 수 있으니 확인)' % out)

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = '배수설비가구'
    hdr = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', fgColor='2F5597')
    edit = PatternFill('solid', fgColor='FFF2CC')
    ws.append(COLS)
    for i, _ in enumerate(COLS, 1):
        c0 = ws.cell(row=1, column=i)
        c0.font = hdr
        c0.fill = fill
        c0.alignment = Alignment(horizontal='center')
    for fid, p, (x, y) in rows:
        ws.append([fid] + [py(p.get(k)) for k in body] + [round(x, 2) if x else None, round(y, 2) if y else None])
    for col in EDIT:
        if col in COLS:
            j = COLS.index(col) + 1
            for i in range(2, len(rows) + 2):
                ws.cell(row=i, column=j).fill = edit
    ws.freeze_panes = 'D2'
    ws.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(COLS)), len(rows) + 1)
    for i, c0 in enumerate(COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(28, len(str(c0)) + 8))

    # 요약
    gb = a.group_by or [c for c in ('처리구역', f_new) if c in fields] or [f_new]
    s = wb.create_sheet('요약')
    s.append(gb + ['건수', '%s 합' % f_q, '점유량 합', '반영건수', '반영 Q합'])
    for c0 in s[1]:
        c0.font = hdr
        c0.fill = fill
    agg = collections.defaultdict(lambda: [0, 0.0, 0.0, 0, 0.0])
    for _fid, p, _xy in rows:
        k = tuple(str(p.get(g)) for g in gb)
        g_ = agg[k]
        g_[0] += 1
        g_[1] += (p.get(f_q) or 0)
        g_[2] += (p.get('점유량_m3d') or 0)
        if (p.get('반영여부') or '포함') == '포함':
            g_[3] += 1
            g_[4] += (p.get(f_q) or 0)
    for k in sorted(agg):
        g_ = agg[k]
        s.append(list(k) + [g_[0], round(g_[1], 2), round(g_[2], 2), g_[3], round(g_[4], 2)])
    s.append([])
    s.append(['합계'] + [''] * (len(gb) - 1) + [sum(g_[0] for g_ in agg.values()),
                                                round(sum(g_[1] for g_ in agg.values()), 2),
                                                round(sum(g_[2] for g_ in agg.values()), 2),
                                                sum(g_[3] for g_ in agg.values()),
                                                round(sum(g_[4] for g_ in agg.values()), 2)])
    for i in range(1, len(gb) + 6):
        s.column_dimensions[get_column_letter(i)].width = 16

    h = wb.create_sheet('사용법')
    for t in [
        ['배수설비가구 반영관리표 사용법'],
        [],
        ['1. 노란색 칸만 고치면 됩니다 — 반영여부 / 제외사유 / 점유량_m3d'],
        ['2. 제외할 가구는  반영여부 칸에  제외  라고 적고 제외사유를 쓰세요'],
        ['3. 특정 맨홀에 직접 넣을 점유량은  점유량_m3d  칸에 숫자를 적으세요'],
        ['   (그 행의 배정맨홀 칸에 들어갈 맨홀번호를 적어두면 그 맨홀로 들어갑니다)'],
        ['4. 저장 후  house_xlsx_import.py  를 돌리면 레이어에 반영됩니다 (먼저 보고만, 확인 후 --write)'],
        [],
        ['fid 열은 건드리지 마세요 — 레이어와 연결하는 키입니다'],
    ]:
        h.append(t)
    h.column_dimensions['A'].width = 80
    h['A1'].font = Font(bold=True, size=13)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print('\n엑셀 저장: %s' % out)
    print('  시트: 배수설비가구(%d행) / 요약 / 사용법' % len(rows))
    print('  편집 대상(노란칸): %s' % ' · '.join(c for c in EDIT if c in COLS))


if __name__ == '__main__':
    main()
