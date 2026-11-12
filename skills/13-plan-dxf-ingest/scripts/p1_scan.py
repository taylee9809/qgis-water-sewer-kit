# -*- coding: utf-8 -*-
"""13 단계 1 — 평면 DXF 스트리밍 스캔.

200 MB 대 전도면 DXF 도 통째로 읽지 않고 group code 쌍으로 한 번 훑는다.
  수집: 레이어 census · INSERT 블록 · 속성 블록 ATTRIB(속성맨홀·기타데이터) · 관로 폴리라인 · 맨홀 심볼 · 관로명 TEXT
  산출: <outputs.root>/13-plan-ingest/<납품폴더명>/scan.json + census.txt

처음 보는 설계사 도면이면 census 를 보고 plan_ingest.dxf / plan_ingest.attrib 에 그 도면의
레이어·블록·태그 이름을 적은 뒤 다시 돌린다(코드는 고치지 않는다).

    python p1_scan.py -d YYMMDD                 # 납품 폴더(날짜·폴더명 일부·경로)
    python p1_scan.py --dxf "<경로>/평면도.dxf"   # 파일 직접
"""
import argparse, collections, sys, time
from pathlib import Path

import ingestlib as L


def census_text(S, C):
    out = []
    w = out.append
    w('평면 DXF : %s  (인코딩 %s)' % (S['path'], S['encoding']))
    w('\n=== 레이어 census (상위 60: 엔티티형 레이어 개수) ===')
    for t, lay, c in S['census'][:60]:
        w('  %-12s %-40s %7d' % (t, lay, c))
    w('\n=== INSERT 블록 (상위 30: 블록 레이어 개수) ===')
    for nm, lay, c in S['blocks'][:30]:
        w('  %-28s %-28s %6d' % (nm, lay, c))
    if S['tags']:
        w('\n=== 블록별 ATTRIB 태그 ===')
        for b, t, c in S['tags'][:60]:
            w('  %-16s %-16s %6d' % (b, t, c))
    w('\n수집: 관로 폴리라인 %d / 맨홀 심볼 %d / 관로명 TEXT %d / 속성블록 %d'
      % (len(S['pipes']), len(S['symbols']), len(S['labels']), len(S['attribs'])))
    w('  관로 레이어별 : %s' % collections.Counter(g['layer'] for g in S['pipes']).most_common())
    w('  심볼 레이어별 : %s' % collections.Counter(m['layer'] for m in S['symbols']).most_common())
    w('  관로명 접두   : %s' % collections.Counter(t['name'].split('-')[0] for t in S['labels']).most_common(15))
    up = C['attrib']['up']
    ab = [a for a in S['attribs'] if a['block'] == C['dxf']['attrib_block']]
    if ab:
        pre = collections.Counter((a['attrs'].get(up) or '').split('-')[0] for a in ab)
        w('  속성맨홀 %d — 상류(%s) 접두: %s' % (len(ab), up, pre.most_common(15)))
        mt = collections.Counter(a['attrs'].get(C['attrib']['mh_type']) for a in ab)
        kd = collections.Counter(a['attrs'].get(C['attrib']['kind']) for a in ab)
        w('    맨홀 호수 값: %s' % mt.most_common())
        w('    관종 값     : %s' % kd.most_common())
        w('→ 속성맨홀 경로(attrib) 를 쓸 수 있다. 금회 관번호 접두를 --prefix 로 고른다.')
    else:
        w('→ 속성 블록 없음 — 폴리라인+관로명 경로(polyline). 레이어 이름이 다르면 plan_ingest.dxf 를 고친다.')
    unk = [k for k in C['mh_types'] if ab and k not in {a['attrs'].get(C['attrib']['mh_type']) for a in ab}]
    if unk:
        w('  (설정 mh_types 중 도면에 없는 값: %s)' % unk)
    return '\n'.join(out)


def run_scan(cfg, C, dxf, od):
    t0 = time.time()
    print('스캔 %s …' % dxf)
    S = L.scan_plan(dxf, C)
    S['scanned'] = L.now()
    L.save_json(od / 'scan.json', S)
    txt = census_text(S, C)
    (od / 'census.txt').write_text(txt + '\n', encoding='utf-8')
    print(txt)
    print('\n저장 %s (%.0f s)' % (od / 'scan.json', time.time() - t0))
    return S


def main(argv=None):
    ap = argparse.ArgumentParser(description='13 평면 DXF 스트리밍 스캔')
    L.common_args(ap)
    ap.add_argument('--dxf', default=None, help='평면 DXF 경로(주면 납품 폴더 탐색 생략)')
    a = ap.parse_args(argv)
    cfg, C = L.load(a.config)
    if a.dxf:
        dxf = Path(a.dxf)
        name = a.outdir or dxf.stem
    else:
        dv = L.find_delivery(cfg, a.delivery)
        ins = L.collect_inputs(cfg, C, dv)
        if not ins['plan']:
            raise SystemExit('납품 폴더에 평면 DXF 가 없다: %s (plan_ingest.input_patterns.plan 확인)' % dv)
        dxf = ins['plan'][0]
        name = a.outdir or dv.name
        if len(ins['plan']) > 1:
            print('평면 DXF 후보 %d개 — 첫 번째를 쓴다(다르면 --dxf): %s' % (len(ins['plan']), [p.name for p in ins['plan']]))
    if not dxf.is_file():
        raise SystemExit('DXF 가 없다: %s' % dxf)
    od = L.outdir(cfg, name)
    run_scan(cfg, C, dxf, od)
    return 0


if __name__ == '__main__':
    sys.exit(main())
