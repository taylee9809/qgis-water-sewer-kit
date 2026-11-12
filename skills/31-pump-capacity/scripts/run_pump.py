# -*- coding: utf-8 -*-
"""31 일괄 — p1 망 → p2 신설 산정 → p3 기존 검토 (→ p6 계산서). 인자는 각 단계에 그대로 넘긴다.

  python -X utf8 run_pump.py --flo "<전체망>.flo" [--inlet 노드=관저고] [--specs 제원.json]
  python -X utf8 run_pump.py --book                       # 끝에 기본 서식 계산서 xlsx
  python -X utf8 run_pump.py --book --template "<원 계산서 양식>.xlsx"
p3 전용 인자(--scenario, --pipes)와 p2 의 --write 도 그대로 넘어간다(해당 단계에서만 쓰인다).
"""
import subprocess
import sys
from pathlib import Path

import pumplib as PL

HERE = Path(__file__).resolve().parent
P2_ONLY = {'--write'}
P3_ONLY = {'--scenario', '--pipes'}


def split(argv, only, takes_value):
    keep, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in only:
            i += 2 if a in takes_value else 1
            continue
        keep.append(a)
        i += 1
    return keep


def main():
    ap = PL.base_parser(__doc__)
    ap.add_argument('--book', action='store_true', help='끝에 p6 계산서 xlsx')
    ap.add_argument('--template', help='p6 원 계산서 양식 (Excel COM)')
    ap.add_argument('--write', action='store_true', help='p2 결과를 plan_pump 에 쓴다')
    ap.add_argument('--scenario', action='append', default=[])
    ap.add_argument('--pipes')
    args, _ = ap.parse_known_args()
    argv = [a for a in sys.argv[1:]]
    common = split(argv, P2_ONLY | P3_ONLY | {'--book', '--template'}, {'--scenario', '--pipes', '--template'})
    py = [sys.executable, '-X', 'utf8']
    cfg = PL.load_cfg(args, optional=bool((args.flo or args.net) and args.outdir))
    out = PL.out_dir(cfg, args)
    steps = [('p1_network.py', common),
             ('p2_new.py', common + (['--write'] if args.write else [])),
             ('p3_existing.py', common + ['--new-json', str(out / 'pump_new.json')]
              + sum((['--scenario', s] for s in args.scenario), []) + (['--pipes', args.pipes] if args.pipes else []))]
    if args.book:
        bk = []
        for flag in ('--config', '--outdir'):
            if getattr(args, flag[2:]):
                bk += [flag, getattr(args, flag[2:])]
        for s in args.set:
            bk += ['--set', s]
        steps.append(('p6_book.py', bk + (['--template', args.template] if args.template else [])))
    for name, a in steps:
        print('\n' + '=' * 20, name, ' '.join(a))
        r = subprocess.run(py + [str(HERE / name)] + a)
        if r.returncode:
            raise SystemExit('%s 실패 (코드 %d) — 여기서 멈춘다' % (name, r.returncode))
    print('\n산출 폴더: %s' % out)


if __name__ == '__main__':
    main()
