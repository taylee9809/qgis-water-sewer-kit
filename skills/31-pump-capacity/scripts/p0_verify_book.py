# -*- coding: utf-8 -*-
"""P0 원 계산서 재현 검증 — 사내 기계 용량계산서 워크북의 집계 행을 킷 산식으로 다시 계산해 값 대조.

새 프로젝트에서 원 계산서 양식을 처음 쓸 때 **먼저 이것부터** 돌린다. 산식이 같다는 게 확인돼야 p2·p6 결과를
그 양식과 나란히 놓을 수 있다. 워크북은 읽기만 한다(openpyxl data_only = 엑셀이 마지막에 저장한 값).

  python -X utf8 p0_verify_book.py "<원 계산서>.xlsx" --rows 5,6,7,10 --set q_min_m3min=0 ^
         --variant 6:q_roundup_digits=1 --variant 7:q_roundup_digits=1,head_margin_m=1.6,head_round=ceil ^
         --variant 10:qfix=0.1,Lrow=6

★ 원 계산서는 시트마다 식이 조금씩 다를 수 있다(펌프용량 ROUNDUP 자리수, 여유양정 상수·올림, 고정 용량,
  다른 행 연장 참조 같은 오기). 불일치가 나오면 그 행 상세 시트 식을 열어 보고 --variant 로 재현한 뒤,
  「재현 OK + 원본 식 변형 목록」을 보고하고 고칠 것은 따로 제안한다(원본 값을 맞추려고 킷 산식을 바꾸지 않는다).
--variant 행:키=값,…  키 = design.pump 키 · qfix(용량 고정) · Lrow(연장을 다른 행에서) · pump_dia(펌프측 관경)
--cols JSON          집계 열 지도 (기본: 이름 B, Q㎥/s F, q G, ha I, hf J, hl K, hv L, H M, 후단관경 N, LWL S, BOP V, 연장 W)
"""
import json

import pumplib as PL

COLS = dict(name='B', Qs='F', q='G', ha='I', hf='J', hl='K', hv='L', H='M', back='N', lwl='S', bop='V', L='W')


def main():
    ap = PL.base_parser(__doc__, net=False)
    ap.add_argument('book', help='원 계산서 xlsx')
    ap.add_argument('--sheet', default='집계')
    ap.add_argument('--rows', required=True, help='대조할 집계 행 번호, 예 5,6,7')
    ap.add_argument('--cols', help='열 지도 JSON 문자열 또는 파일')
    ap.add_argument('--pump-dia', type=float, default=None, help='펌프측(흡입~ELBOW) 관경 mm 고정 (기본: 토출구경 산식)')
    ap.add_argument('--variant', action='append', default=[], metavar='행:키=값,…')
    ap.add_argument('--tol', type=float, default=5e-3)
    args = ap.parse_args()
    cfg = PL.load_cfg(args, optional=True)
    import openpyxl
    wb = openpyxl.load_workbook(args.book, data_only=True, read_only=True)
    ws = wb[args.sheet]
    C = dict(COLS)
    if args.cols:
        C.update(json.loads(open(args.cols, encoding='utf-8').read() if args.cols.endswith('.json') else args.cols))
    var = {}
    for v in args.variant:
        row, _, body = v.partition(':')
        var[int(row)] = [kv for kv in body.split(',') if kv]
    cell = lambda col, r: ws['%s%d' % (col, r)].value

    mx, rows = {}, []
    for r in [int(x) for x in args.rows.split(',')]:
        kv = PL.parse_kv(var.get(r, []), cast=None)
        P = PL.params(cfg, args.set + ['%s=%s' % (k, v) for k, v in kv.items() if k not in ('qfix', 'Lrow', 'pump_dia')])
        Qs, lwl, bop, back = cell(C['Qs'], r), cell(C['lwl'], r), cell(C['bop'], r), cell(C['back'], r)
        L = cell(C['L'], int(kv['Lrow']) if 'Lrow' in kv else r)
        q = float(kv['qfix']) if 'qfix' in kv else PL.pump_q(Qs * 60, P)
        dp = float(kv.get('pump_dia') or args.pump_dia or PL.bore(q, P))
        h = PL.head_calc(q, bop - lwl, L, dp, back, P)
        mine = dict(q=q, ha=h['ha'], hf=h['hf'], hl=h['hl'], hv=h['hv'], H=h['H'])
        book = {k: cell(C[k], r) for k in mine}
        line = [str(cell(C['name'], r))]
        for k in mine:
            d = abs(float(book[k] or 0) - mine[k])
            mx[k] = max(mx.get(k, 0), d)
            line.append('%s %.4f/%.4f%s' % (k, float(book[k] or 0), mine[k], '' if d < args.tol else '★'))
        rows.append('  %d행 %s  [%s]' % (r, ' · '.join(line), ', '.join(var.get(r, [])) or '변형 없음'))
    print('원 계산서 %s 「%s」 — 계산서값/킷값' % (args.book, args.sheet))
    print('\n'.join(rows))
    bad = [k for k, v in mx.items() if v >= args.tol]
    print('최대차: ' + ', '.join('%s %.1e' % kv for kv in mx.items()))
    print('→ %s' % ('재현 OK' if not bad else '★ 불일치 %s — 상세 시트 식 확인 후 --variant' % bad))


if __name__ == '__main__':
    main()
