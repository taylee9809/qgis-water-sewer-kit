# -*- coding: utf-8 -*-
"""P6 (선택) 맨홀펌프장 용량계산서 xlsx — p2(신설)·p3(기존) 결과로.

두 방식
  기본        openpyxl 로 새 통합문서(값만): 검토개요 · 집계 · 손실항목 · 흡수정_운전 · 압송관경_대안 · 기존펌프장_검토
  --template  사내 원 계산서 양식(xlsx)을 Excel COM 으로 복제 — 집계 시트 + 상세 시트 1장을 펌프장마다 복사하고
              수식은 그대로 둔 채 입력 칸만 채운다. 엑셀이 다시 계산한 값을 킷 엔진값과 대조해 찍는다.
              ★ 회사 양식 파일은 킷에 넣지 않는다 — 경로를 인자로 준다. 양식은 복사본만 연다(원본은 손대지 않음).
              ★ Excel·pywin32 가 없는 PC 에서는 이 방식만 안 된다(엔진 p1~p5 는 그대로 돈다).

--cellmap 파일.json  양식 칸 위치(아래 TEMPLATE_MAP 과 같은 구조). 기본값 = 우리 원 계산서 양식
                     (집계 5행부터, 상세 시트 '맨홀펌프장 #1' 의 입력·결과 칸, 템플릿 보정 목록).
산출: <out>/맨홀펌프장_용량계산서_YYMMDD.xlsx (--name 으로 바꿈)
"""
import json
import re
import shutil
from pathlib import Path

import pumplib as PL

# ── 원 계산서 양식 칸 지도 (다른 양식이면 --cellmap 으로 덮어쓴다)
TEMPLATE_MAP = {
    'agg_sheet': '집계', 'detail_sheet': '맨홀펌프장 #1', 'first_row': 5,
    'agg_delete_rows': [9, 8, 7, 6],        # 양식에 들어 있던 예시 행(아래부터 지움). 첫 행 서식은 남겨 복사
    'agg_clear': 'AA1:AF60',
    'agg_headers': {'O2': '시점\n지반고\n(GL)', 'P2': '유입부\n(INV)', 'Q2': '유출부\n(INV)', 'X2': '비고',
                    'Y2': '펌프\n구경', 'Y3': 'A', 'Z2': '적용\n동력', 'Z3': 'kW'},
    'agg_title': 'B1',
    # 집계 열: 값(v:키) 또는 수식(f:틀). {k}=행, {sh}=상세시트명, {hwl} {eff} {dead}=설정값
    'agg_cols': {
        'A': 'v:no', 'B': 'v:펌프장', 'D': 'f:=E{k}*1440', 'E': 'f:=F{k}*60', 'F': 'v:Q_m3s',
        'G': "f:='{sh}'!L21", 'H': 'v:대수', 'I': 'f:=V{k}-S{k}', 'J': "f:='{sh}'!U35", 'K': "f:='{sh}'!U36",
        'L': "f:='{sh}'!U37", 'M': 'f:=SUM(I{k}:L{k})', 'N': 'v:압송관경', 'O': 'v:지반고', 'P': 'v:유입관저고',
        'Q': 'v:유출관저고', 'R': 'f:=P{k}-{hwl}', 'S': 'f:=R{k}-{eff}', 'T': 'f:=R{k}-S{k}', 'U': 'f:=S{k}-{dead}',
        'V': 'v:압송최상단BOP', 'W': 'v:압송연장', 'X': 'v:비고', 'Y': "f:='{sh}'!Q30", 'Z': "f:='{sh}'!H75"},
    # 상세 시트 입력 칸
    'detail_cells': {'R10': 'f:=집계!F{k}', 'U41': 'f:=집계!V{k}', 'U42': 'f:=집계!S{k}', 'L63': 'f:=집계!W{k}',
                     'Q30': 'v:토출구경', 'Q31': 'v:압송관경', 'F5': 'v:설명'},
    # 양식 보정(우리 양식의 결함): 최소용량, 후단관경 참조, 옥외관로 hl 식의 관경 하드코딩, 전동기표 행 부족
    'patches': [
        ['L21', '=MAX(ROUNDUP(H20,{digits}),{qmin})'], ['N21', '㎥/min.대로 결정 (제작사 최소 {qmin} ㎥/min)'],
        ['J52', '=Q30'], ['J59', '=Q31'], ['J61', '=Q31'], ['J63', '=Q31'],
        ['X63', '=10.666*({C}^(-1.85))*((J63/1000)^(-4.87))*(N63/60)^(1.85)*L63'], ['AE62', '=X63'],
        ['H75', '=VLOOKUP(S73,$AD$67:$AF${motor_last},3)']],
    'motor_table_at': [67, 30],             # 전동기표 [첫 행, 첫 열(AD=30)] — [하한, 상한, kW] 3열로 다시 씀
    # 결과 대조용 (집계 열)
    'check': {'q': 'G', 'ha': 'I', 'hf': 'J', 'hl': 'K', 'hv': 'L', 'H': 'M', 'bore': 'Y', 'kW': 'Z'},
}


def sheet_name(s):
    return re.sub(r'[\[\]:*?/\\]', '_', str(s))[:31]


def desc(r):
    src = ', '.join((r.get('유입관') or []) + (r.get('유입압송') or [])) or (r.get('유입관저고_가정') or '-')
    return ('배수구역에서 차집된 오수를 자연유하로 배수가 가능한 구역까지 압송하기 위해 설치한다. '
            '(유입 : %s → 토출 : %s, 압송 D%s L=%.1f m)' % (src, r['토출대상'], r['압송관경'], r['압송연장']))


def notes(P):
    return [
        '※ 유량 : 전체망 수리계산(.flo Q= 또는 계획관로 누가유량)을 펌프장까지 누가한 시간최대',
        '※ HWL = 유입관저고(최저) − %g, LWL = HWL − %g(유효수심), 바닥고 = LWL − %g(사수심)'
        % (P['hwl_below_inlet_m'], P['effective_depth_m'], P['dead_depth_m']),
        '※ 압송관 최상단고 = 다음 펌프장·자연유하 합류 전까지 압송 구간 관저고 최대값, 압송연장 = 같은 구간 연장 합',
        '※ 펌프구경 = 146√(q/v), v %g~%g m/s 범위 내 최소 표준구경 · 후단 매설배관 = 압송관경' % tuple(P['bore_velocity_mps']),
        '※ 전양정 = ROUND(ha+hf+hl+%g, 0) · 동력 = 0.163·q·H/%g×%g → 표준전동기'
        % (P['head_margin_m'], P['efficiency'], P['power_margin']),
        '※ 자체 가정(기준 아님): HWL·유효수심·사수심·여유양정·전효율·동력여유·기동 허용 %g회/h' % P['max_starts_per_h'],
    ]


def motor_rows(P):
    t = P['motor_table']
    return [(lo, (t[i + 1][0] - 0.001) if i + 1 < len(t) else lo * 1.25, kw) for i, (lo, kw) in enumerate(t)]


# ───────────────────────────────────────────── 기본(openpyxl)
def plain_book(path, new, ex, P, title):
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    wb = openpyxl.Workbook()
    thin = Side(style='thin')
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    hfill = PatternFill('solid', fgColor='DDE6EF')

    def table(ws, r0, hdr, rows, fmt='0.000'):
        for j, h in enumerate(hdr):
            c = ws.cell(r0, 1 + j, h)
            c.font, c.fill, c.border = Font(bold=True), hfill, bd
            c.alignment = Alignment(wrap_text=True, horizontal='center', vertical='center')
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                if isinstance(v, (list, tuple)):
                    v = ', '.join(map(str, v)) or '-'
                c = ws.cell(r0 + 1 + i, 1 + j, v)
                c.border = bd
                if isinstance(v, float):
                    c.number_format = fmt
        ws.row_dimensions[r0].height = 42
        for j in range(len(hdr)):
            ws.column_dimensions[openpyxl.utils.get_column_letter(1 + j)].width = 16 if j == 0 else 11
        return r0 + 1 + len(rows)

    ws = wb.active
    ws.title = '검토개요'
    lines = ['● %s' % title, '', '1. 대상 : 신설 %d개소 · 기존 %d개소(준공 제원 고정, 유량만 갱신)' % (len(new), len({r['펌프장'] for r in ex})),
             '2. 산정 방식 : 원 계산서 방식 (킷 31-pump-capacity, design.pump.*)'] + notes(P)
    for i, t in enumerate(lines):
        ws.cell(1 + i, 1, t)
    ws['A1'].font = Font(bold=True, size=14)
    ws.column_dimensions['A'].width = 140

    ws = wb.create_sheet('집계')
    ws['A1'] = '● 신설 맨홀펌프장 용량'
    hdr = ['펌프장', '시간최대\n(㎥/일)', '시간최대\n(㎥/min)', '펌프용량\nq', '토출구경\n(A)', '압송관경\n(mm)',
           '유입관저고', 'HWL', 'LWL', '저면고', '압송최상단\nBOP', '압송연장\n(m)', '실양정\nha', '기계손실\nhf',
           '관로손실\nhl', '여유양정\nhv', '전양정\nH', '축동력\n(kW)', '적용동력\n(kW)', '압송유속\n(m/s)', '토출대상', '비고']
    rows = [[r['펌프장'], r['Q_m3d'], r['Q_m3min'], r['펌프용량'], r['토출구경'], r['압송관경'], r['유입관저고'],
             r['HWL'], r['LWL'], r['저면고'], r['압송최상단BOP'], r['압송연장'], r['ha'], r['hf'], r['hl'], r['hv'],
             r['H'], r['축동력'], r['동력'], r['v_압송'], r['토출대상'], r.get('유입관저고_가정') or r.get('판정')]
            for r in new if 'H' in r]
    n = table(ws, 3, hdr, rows)
    for j, t in enumerate(notes(P)):
        ws.cell(n + 2 + j, 1, t)

    ws = wb.create_sheet('손실항목')
    ws['A1'] = '● 기계배관 손실 (펌프측 = 토출구경, 토출구·편락관 = 후단 매설배관)'
    rows = [[r['펌프장'], a, b, c, d] for r in new for (a, b, c, d) in r.get('hf_items', [])]
    table(ws, 3, ['펌프장', '항목', '규격(mm)', 'K(f)', '손실(m)'], rows, '0.0000')

    ws = wb.create_sheet('흡수정_운전')
    ws['A1'] = '● 흡수정 운전 (유효용량 V, 펌프용량 q, 시간최대 Q)'
    hdr = ['펌프장', 'q\n(㎥/min)', 'Q\n(㎥/min)', 'V\n(㎥)', '최소운전\nV/q (min)', '최대기동\n60/(4V/q)',
           '판정\n(≤%g회/h)' % P['max_starts_per_h'], '운전\nV/(q−Q)', '정지\nV/Q', '기동\n(회/h)', '상류 펌프장',
           '직렬 순간유입', '직렬 여유\nq/순간유입']
    rows = [[r['펌프장'], r['펌프용량'], r['Q_m3min'], r['유효용량_m3'], r['t_min'], r['N_max'],
             'OK' if r['N_max'] <= P['max_starts_per_h'] else '기동과다', r['운전_min'] or '연속', r['정지_min'],
             r['N_시간최대'], r['상류펌프장'], r.get('직렬유입_max'), r.get('직렬_여유')] for r in new if 'H' in r]
    table(ws, 3, hdr, rows)

    ws = wb.create_sheet('압송관경_대안')
    ds = P['alt_force_main_dia_mm']
    ws['A1'] = '● 압송관경 비교 — 유속 %g~%g m/s' % (P['_vmin'], P['_vmax'])
    hdr = ['펌프장', 'q', 'L (m)', 'ha', 'hf'] + [x % d for d in ds for x in ('D%d 유속', 'D%d hl', 'D%d H', 'D%d kW')]
    rows = [[r['펌프장'], r['펌프용량'], r['압송연장'], r['ha'], r['hf']]
            + [r['alt_D%d' % d][k] for d in ds for k in ('v', 'hl', 'H', 'kW')] for r in new if 'H' in r]
    table(ws, 3, hdr, rows)

    if ex:
        ws = wb.create_sheet('기존펌프장_검토')
        ws['A1'] = '● 기존 맨홀펌프장 검토 (준공 제원 고정, 유량만 갱신)'
        hdr = ['시나리오', '펌프장', '설치용량', '설치양정', '설치동력', '시간최대\n(㎥/일)', '시간최대\n(㎥/min)',
               '여유배', '사용률\n(%)', '실양정', '기계손실', '관로손실', '소요양정', '소요전양정\n(여유포함)',
               '압송유속', '최소운전\n(min)', '최대기동\n(회/h)', '저류여유\n(㎥/h)', '상류 펌프장', '판정', '제원출처']
        rows = [[r['시나리오'], r['펌프장'], r.get('설치용량'), r.get('설치양정'), r.get('설치동력'), r['Q_m3d'],
                 r['Q_m3min'], r.get('여유배'), r.get('사용률_pct'), r.get('실양정'), r.get('hf'), r.get('hl'),
                 r.get('소요양정'), r.get('소요전양정'), r.get('압송유속'), r.get('t_min'), r.get('N_max'),
                 r.get('저류여유_m3h'), r.get('상류펌프장'), r.get('판정'), r.get('제원출처')] for r in ex]
        n = table(ws, 3, hdr, rows)
        ws.cell(n + 1, 1, '※ 1차 판정 = 설치 q ≥ 시간최대(KDS 61 45 00 표 1.1.1). 저류여유(V + q·60 − 1시간 유입)는 참고.')
    wb.save(path)


# ───────────────────────────────────────────── 양식 복제(Excel COM)
def template_book(path, tpl, new, ex, P, M, title):
    try:
        import win32com.client as w32
    except ImportError:
        raise SystemExit('--template 은 Excel + pywin32 가 필요하다. 없으면 --template 없이 기본 xlsx 로.')
    tmp = Path(path).resolve().with_name("_template_copy.xlsx")
    shutil.copy2(tpl, tmp)
    fmt = dict(hwl=P['hwl_below_inlet_m'], eff=P['effective_depth_m'], dead=P['dead_depth_m'],
               digits=P['q_roundup_digits'], qmin=P['q_min_m3min'], C=P['hw_c'])
    mt = motor_rows(P)
    fmt['motor_last'] = M['motor_table_at'][0] + len(mt) - 1
    names = [sheet_name(r['펌프장']) for r in new]
    xl = w32.DispatchEx('Excel.Application')
    xl.Visible = False
    xl.DisplayAlerts = False
    xl.ScreenUpdating = False
    res = {}
    try:
        src = xl.Workbooks.Open(str(tmp))
        src.Worksheets([M['agg_sheet'], M['detail_sheet']]).Copy()
        wb = xl.ActiveWorkbook
        src.Close(False)
        agg = wb.Worksheets(M['agg_sheet'])
        tp = wb.Worksheets(M['detail_sheet'])
        for cell, f in M['patches']:
            tp.Range(cell).Formula = f.format(**fmt)
        r0, c0 = M['motor_table_at']
        for i, (a, b, c) in enumerate(mt):
            tp.Cells(r0 + i, c0).Value = a
            tp.Cells(r0 + i, c0 + 1).Value = b
            tp.Cells(r0 + i, c0 + 2).Value = c
        fr = M['first_row']
        for rr in M['agg_delete_rows']:
            agg.Rows(rr).Delete()
        if len(new) > 1:
            agg.Rows('%d:%d' % (fr + 1, fr + len(new) - 1)).Insert()
            agg.Rows(fr).Copy()
            agg.Rows('%d:%d' % (fr + 1, fr + len(new) - 1)).PasteSpecial(-4122)
        if M.get('agg_clear'):
            agg.Range(M['agg_clear']).Clear()
        for cell, t in M['agg_headers'].items():
            agg.Range(cell).Value = t
        agg.Range(M['agg_title']).Value = '● %s' % title

        def put(ws, cell, spec, r, k, sh):
            kind, _, body = spec.partition(':')
            if kind == 'v':
                v = dict(r, no=k - fr + 1, 대수=int(P['n_duty']) + 1, 비고=('%s' % r['토출대상']) +
                         (' / 유입INV %s' % r['유입관저고_가정'] if r.get('유입관저고_가정') else ''),
                         설명=desc(r)).get(body)
                ws.Range(cell).Value = ', '.join(v) if isinstance(v, list) else v
            elif not ("'{sh}'" in body and sh is None):
                ws.Range(cell).Formula = body.format(k=k, sh=sh, **fmt)
        for i, r in enumerate(new):                       # 1) 집계 값·자기 수식
            k = fr + i
            for col, spec in M['agg_cols'].items():
                if "{sh}" not in spec:
                    put(agg, '%s%d' % (col, k), spec, r, k, None)
        for i, r in enumerate(new):                       # 2) 상세 시트 복제
            k = fr + i
            tp.Copy(None, wb.Worksheets(wb.Worksheets.Count))
            ws = wb.Worksheets(wb.Worksheets.Count)
            ws.Name = names[i]
            for cell, spec in M['detail_cells'].items():
                put(ws, cell, spec, r, k, names[i])
        tp.Delete()
        for i, r in enumerate(new):                       # 3) 시트가 생긴 뒤 교차참조 (먼저 넣으면 #REF!)
            k = fr + i
            for col, spec in M['agg_cols'].items():
                if "{sh}" in spec:
                    put(agg, '%s%d' % (col, k), spec, r, k, names[i])
        last = fr + len(new) - 1
        for j, t in enumerate(notes(P)):
            agg.Cells(last + 3 + j, 2).Value = t

        if ex:                                            # 기존 검토 — 값
            ws = wb.Worksheets.Add(None, agg)
            ws.Name = '기존펌프장_검토'
            hdr = ['시나리오', '펌프장', '설치용량', '설치양정', '설치동력', '시간최대(㎥/일)', '시간최대(㎥/min)', '여유배',
                   '사용률(%)', '실양정', '기계손실', '관로손실', '소요양정', '소요전양정', '압송유속', '최소운전(min)',
                   '최대기동(회/h)', '판정']
            ws.Range('A1').Value = '● 기존 맨홀펌프장 검토 (준공 제원 고정, 유량만 갱신)'
            for j, h in enumerate(hdr):
                ws.Cells(3, 1 + j).Value = h
            for i, r in enumerate(ex):
                vals = [r['시나리오'], r['펌프장'], r.get('설치용량'), r.get('설치양정'), r.get('설치동력'), r['Q_m3d'],
                        r['Q_m3min'], r.get('여유배'), r.get('사용률_pct'), r.get('실양정'), r.get('hf'), r.get('hl'),
                        r.get('소요양정'), r.get('소요전양정'), r.get('압송유속'), r.get('t_min'), r.get('N_max'), r.get('판정')]
                for j, v in enumerate(vals):
                    ws.Cells(4 + i, 1 + j).Value = v
        ws = wb.Worksheets.Add(None, wb.Worksheets(wb.Worksheets.Count))   # 흡수정 — 집계 참조 수식
        ws.Name = '흡수정_운전'
        ws.Range('A1').Value = '● 흡수정(내경 %g m) 운전 검토' % P['well_dia_m']
        hdr = ['펌프장', 'q', 'Q', 'V', 'V/q', '60/(4V/q)', '판정', 'V/(q−Q)', 'V/Q', '기동(회/h)']
        for j, h in enumerate(hdr):
            ws.Cells(3, 1 + j).Value = h
        for i, r in enumerate(new):
            k, a = 4 + i, fr + i
            ws.Cells(k, 1).Value = r['펌프장']
            ws.Cells(k, 2).Formula = '=%s!G%d' % (M['agg_sheet'], a)
            ws.Cells(k, 3).Formula = '=%s!E%d' % (M['agg_sheet'], a)
            ws.Cells(k, 4).Formula = '=PI()/4*%g^2*%s!T%d' % (P['well_dia_m'], M['agg_sheet'], a)
            ws.Cells(k, 5).Formula = '=D%d/B%d' % (k, k)
            ws.Cells(k, 6).Formula = '=60/(4*D%d/B%d)' % (k, k)
            ws.Cells(k, 7).Formula = '=IF(F%d<=%g,"OK","기동과다")' % (k, P['max_starts_per_h'])
            ws.Cells(k, 8).Formula = '=IF(C%d<B%d,D%d/(B%d-C%d),"연속")' % (k, k, k, k, k)
            ws.Cells(k, 9).Formula = '=IF(C%d>0,D%d/C%d,"-")' % (k, k, k)
            ws.Cells(k, 10).Formula = '=IF(C%d<B%d,60/(H%d+I%d),"연속운전")' % (k, k, k, k)
        agg.Activate()
        xl.CalculateFull()
        wb.SaveAs(str(Path(path).resolve()), 51)
        for i, r in enumerate(new):
            k = fr + i
            res[r['펌프장']] = {key: agg.Range('%s%d' % (col, k)).Value for key, col in M['check'].items()}
        wb.Close(True)
    finally:
        xl.Quit()
        try:
            tmp.unlink()
        except OSError:
            pass
    return res


def main():
    ap = PL.base_parser(__doc__, net=False)
    ap.add_argument('--new-json', help='p2 산출 pump_new.json (기본 <out>/pump_new.json)')
    ap.add_argument('--existing-json', help='p3 산출 pump_existing.json (기본 <out>/pump_existing.json 이 있으면)')
    ap.add_argument('--template', help='원 계산서 양식 xlsx (Excel COM 복제). 없으면 기본 xlsx')
    ap.add_argument('--cellmap', help='양식 칸 지도 JSON (기본 = 우리 원 계산서 양식)')
    ap.add_argument('--name', help='산출 파일 이름 (기본 맨홀펌프장_용량계산서_YYMMDD.xlsx)')
    ap.add_argument('--title', default='맨홀펌프장 용량계산서', help='제목 줄')
    args = ap.parse_args()
    cfg = PL.load_cfg(args, optional=bool(args.outdir))
    P = PL.params(cfg, args.set)
    out = PL.out_dir(cfg, args)
    nj = Path(args.new_json) if args.new_json else out / 'pump_new.json'
    ej = Path(args.existing_json) if args.existing_json else out / 'pump_existing.json'
    new = [r for r in json.loads(nj.read_text(encoding='utf-8'))['stations'] if 'H' in r]
    ex = json.loads(ej.read_text(encoding='utf-8'))['stations'] if ej.exists() else []
    path = out / (args.name or '맨홀펌프장_용량계산서_%s.xlsx' % PL.stamp())
    if not args.template:
        plain_book(path, new, ex, P, args.title)
        print('저장(기본 서식, 값): %s — 신설 %d · 기존 %d행' % (path, len(new), len(ex)))
        return
    M = dict(TEMPLATE_MAP)
    if args.cellmap:
        M.update(json.loads(Path(args.cellmap).read_text(encoding='utf-8')))
    res = template_book(path, args.template, new, ex, P, M, args.title)
    key = dict(q='펌프용량', ha='ha', hf='hf', hl='hl', hv='hv', H='H', bore='토출구경', kW='동력')
    mx = {}
    for r in new:
        for k, v in res.get(r['펌프장'], {}).items():
            try:
                mx[k] = max(mx.get(k, 0.0), abs(float(v) - float(r[key[k]])))
            except (TypeError, ValueError):
                mx[k] = float('nan')
    print('저장(양식 복제): %s — 신설 %d개소' % (path, len(new)))
    print('엑셀 재계산값 ↔ 킷 엔진 최대차: ' + ', '.join('%s %.1e' % kv for kv in mx.items()))
    bad = [k for k, v in mx.items() if not v < 1e-6]
    print('  → %s' % ('일치' if not bad else '★ 불일치 %s — 양식 식 변형(시트별 ROUNDUP 자리·여유양정 등) 확인' % bad))


if __name__ == '__main__':
    main()
