# -*- coding: utf-8 -*-
"""P1 망 → 맨홀펌프장 목록·압송관 추적·누가유량.

입력(앞의 것 우선)
  --flo 파일.flo [--flo …]   makesw 전체망 (Q= ㎥/s 가 노드 시간최대 유입)
  (없으면) layers.plan_pipe   fields.plan_pipe.{pipe_id,dia_mm,us_invert,ds_invert,flow_m3d[,length_m,us_ground,us_mh,ds_mh]}
                              us_mh/ds_mh 가 없으면 관 끝점 연결(--snap)로 계통을 만든다(관번호 = 시점 맨홀명 관행).

펌프장 판별: 유출관 D ≤ design.pressure_max_d_mm 이고 (유입관이 없거나 그보다 큰 자연유하 유입관이 있는) 노드
             ∪ --pump 지정 ∪ 기존 펌프장(plan_pump 구분값 values.pump_existing / asbuilt_pump / --specs / --existing).
압송관 추적: 다음 펌프장·자연유하 합류 전까지(2단 압송에서 연장이 부풀지 않게 끊는다).
  압송최상단 BOP = 그 구간 관저고 최대값, 압송연장 = 구간 연장 합.

산출: <out>/network.json (stations + recs) · stations.csv
예)
  python -X utf8 p1_network.py --flo "<수리계산>/전체망.flo" --inlet XX-100-001=22.48
  python -X utf8 p1_network.py                      # plan_pipe 레이어로
"""
import collections
import pumplib as PL


def main():
    ap = PL.base_parser(__doc__)
    ap.add_argument('--check-flo', action='store_true', help='.flo 를 flospec 으로 검증하고 결과도 찍는다')
    args = ap.parse_args()
    cfg = PL.load_cfg(args, optional=bool(args.flo and args.outdir))
    P = PL.params(cfg, args.set)
    if args.check_flo:
        for p in args.flo:
            f, outf = PL.flospec.validate(PL.flospec.read_flo(p))
            ne = sum(1 for s, _, _ in f if s == 'ERROR')
            print('flospec %s: ERROR %d / WARN %d / 방류구 %s' % (p, ne, len(f) - ne, outf))
    st, recs, meta = PL.network_from_args(args, cfg, P)
    out = PL.out_dir(cfg, args)
    PL.dump_json(out / 'network.json', dict(meta=meta, stations=st, recs=recs))
    PL.dump_csv(out / 'stations.csv', st)
    c = collections.Counter(s['구분'] for s in st)
    print('망 %s — 구간 %d, 펌프장 %d (신설 %d · 기존 %d), D≤%g 압송' % (meta['source'], meta['records'], len(st),
                                                               c['신설'], c['기존'], P['_dmax']))
    for s in st:
        print('  %-16s %s Q=%8.2f ㎥/d (%.4f ㎥/min)  압송 D%d L=%7.1f m  BOP=%.2f  유입INV=%s → %s  상류펌프=%s%s'
              % (s['펌프장'], s['구분'], s['Q_m3d'], s['Q_m3min'], s['압송관경'], s['압송연장'], s['압송최상단BOP'],
                 '%.2f' % s['유입관저고'] if s['유입관저고'] is not None else '없음', s['토출대상'],
                 s['상류펌프장'] or '-', '  [%s]' % s['유입관저고_가정'] if s.get('유입관저고_가정') else ''))
    miss = [s['펌프장'] for s in st if s['구분'] == '신설' and s['유입관저고'] is None]
    if miss:
        print('\n★ 유입관저고를 정할 수 없는 신설 펌프장 %d개소: %s → --inlet 노드=관저고 로 가정값을 주거나 망을 보완'
              % (len(miss), ', '.join(miss)))
    print('\n저장: %s' % (out / 'network.json'))


if __name__ == '__main__':
    main()
