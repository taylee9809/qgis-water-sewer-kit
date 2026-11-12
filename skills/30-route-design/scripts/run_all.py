# -*- coding: utf-8 -*-
"""30 노선 설계 파이프라인 일괄 실행 → <outputs.root>/30-route/<run>/ + 설계요약.md

    python run_all.py                                   # r1 → r9 (설정에 없는 레이어 단계는 건너뜀)
    python run_all.py --run 대안B --only r5 r6 r7 r8    # 일부 단계만
    python run_all.py --run 완화2 --from main --only r5 r6 --set cover_relax_max_m=2.0   # 파라미터 스윕
    python run_all.py --step r5="--fixed-il 경계.csv --straighten XX-100-005:2" --step r8="--inp --dxf"
    python run_all.py --summary-only                    # 이미 돈 결과로 설계요약.md 만 다시

단계 인자는 --step 단계="인자들" 또는 설정 route_design.args = {"r5": ["--fixed-il", "경계.csv"], …}.
--set 은 모든 단계에 넘긴다(설계기준 덮어쓰기). --from 은 다른 run 의 중간 JSON 을 복사해 시작점으로 쓴다
(파라미터 스윕은 이렇게 run 을 갈라서 — 파일을 고쳐 가며 돌리지 않는다).
"""
import sys, os, io, json, shlex, shutil, subprocess, time, argparse
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routelib as R

HERE = Path(__file__).resolve().parent
STEPS = [('r1', 'r1_net.py', '노선 → 망', None),
         ('r2', 'r2_ground.py', '지반고·노폭·교량/하천', None),
         ('r3', 'r3_cross.py', '지장물·상수관 교차', None),
         ('r4', 'r4_load.py', '유량 배분', 'house'),
         ('r5', 'r5_profile.py', '종단(관저고)', None),
         ('r6', 'r6_hydraulic.py', '수리계산', None),
         ('r7', 'r7_force.py', '압송 종단·펌프장', None),
         ('r8', 'r8_export.py', '산출(GPKG·CSV·.flo)', None),
         ('r9', 'r9_existing.py', '기존관 통수능', 'asbuilt_pipe')]
JSONS = ['net.json', 'ground.json', 'cross.json', 'load.json', 'profile.json', 'design.json', 'force.json']


def summary(C):
    J = lambda n: C.load(n, required=False) or {}
    pro, des, fz, ex, net = J('profile.json'), J('design.json'), J('force.json'), J('existing.json'), J('net.json')
    res = des.get('res') or {}
    L = ['# 30 노선 설계 요약 — %s' % C.dir.name, '', '| | |', '|---|---|',
         '| 작성 | %s |' % R.stamp(), '| 설정 | `%s` |' % C.cfg.path.name, '']
    if res:
        g = list(res.values())
        dep = [(des['GL'][n] - des['IL'][n]) for n in des['IL'] if des['GL'].get(n) is not None]
        ck = pro.get('checks') or {}
        mn = C.P
        L += ['## 지표(원안과 같은 지표로 전후 비교 — SKILL.md 9절)', '',
              '| 지표 | 값 |', '|---|---|',
              '| 자연유하 | %d구간 %.1f m |' % (len(g), sum(r['연장'] for r in g)),
              '| 압송 | %d계통 %.1f m |' % (len(net.get('force') or {}), sum(c['L'] for c in (net.get('force') or {}).values())),
              '| 펌프장(신설) | %d개소 |' % len(fz.get('pumps') or []),
              '| 경사 %.0f‰ 미달(−0.02 허용) | %d |' % (mn['min_slope_permil'], len(ck.get('경사미달') or [])),
              '| 하류 토피 미달 | %d |' % len(ck.get('하류토피미달') or []),
              '| 맨홀 사이 토피 미달 절점(--mid %s%s) | %d점 / %d구간 |' % (
                  (pro.get('meta') or {}).get('mid'), ' → 관 보호공 대상' if (pro.get('meta') or {}).get('mid') == 'constrain' else ', 검사만',
                  len(ck.get('보호공') or []), len({q['관번호'] for q in ck.get('보호공') or []})),
              '| 지장물 위반 | %d |' % len(ck.get('지장물위반') or []),
              '| 심도 최대 / 평균 | %.2f / %.2f m |' % (max(dep or [0]), sum(dep) / len(dep) if dep else 0),
              '| 부관(단차 ≥ %.1f m) | %d개소 |' % (mn['drop_manhole_m'], sum(1 for d in ck.get('단차') or [] if d[0] >= mn['drop_manhole_m'])),
              '| 유속 %.1f m/s 미달 | %d구간 |' % (mn['min_velocity_mps'], sum(1 for r in g if r['실유속_mps'] < mn['min_velocity_mps'])),
              '']
        if pro.get('pump_diag'):
            L += ['## 자연유하 불가(펌프·노선변경·역사이펀 중 사람이 고른다)', '']
            for d in pro['pump_diag']:
                L.append('- `%s` ← `%s` 부족 %.2f m · 지반 최저 `%s` · 펌프 후보 `%s`'
                         % (d['경계'], d['유입관'], d['부족_m'], d['지반최저']['맨홀'], (d['펌프후보'] or {}).get('맨홀')))
            L.append('')
        if ck.get('고정관저_상한초과'):
            L += ['## 고정 관저가 기준 상한보다 높은 맨홀', '']
            L += ['- `%s` 고정 %.3f > 상한 %.3f (+%.3f m)' % (f['맨홀'], f['고정관저'], f['상한'], f['초과'])
                  for f in ck['고정관저_상한초과']]
            L.append('')
    if fz.get('pumps'):
        L += ['## 맨홀펌프장(신설만 — 기존은 준공 스펙)', '', '| 펌프장 | Q ㎥/일 | 유입관저 | 전양정 m | 용량 ㎥/min | 동력 kW | 압송 |',
              '|---|---|---|---|---|---|---|']
        for p in fz['pumps']:
            L.append('| %s | %.2f | %.2f | %d | %.2f | %.2f | D%d %.1f m → %s |'
                     % (p['펌프장'], p['누가유량_m3d'], p['유입관저고'], p['전양정H_m'], p['적용용량_m3min'],
                        p['적용동력_kW'], p['압송관경'], p['압송연장_m'], p['토출대상']))
        L.append('')
    if ex.get('rows'):
        pc = [r['유량비율_pct'] for r in ex['rows'] if r['유량비율_pct'] is not None]
        L += ['## 기존관 통수능', '', '- 대상 %d구간 · 유량비율 최대 %.2f %% · 만관초과 %d'
              % (len(ex['rows']), max(pc or [0]), sum(1 for r in ex['rows'] if r['수리판정'] == '만관초과')), '']
    L += ['## 설계기준 — 기준값과 자체 가정 구분', '', '| 키 | 값 | 출처 | 근거 |', '|---|---|---|---|']
    for k in R.DESIGN:
        t = {'KDS': '기준값', '설정': '설정', '인자': '인자', '가정': '**자체 가정**'}.get(C.src.get(k), C.src.get(k))
        L.append('| %s | %s | %s | %s |' % (k, C.P.get(k), t, R.DESIGN[k][1]))
    L += ['', '---', '', '스킬: `30-route-design` (qgis-water-sewer-kit) · 지장물·측량 공백 구간은 "교차 0" 이 아니라 "미측량".']
    p = C.path('설계요약.md')
    p.write_text('\n'.join(L), encoding='utf-8')
    return p


def main():
    ap = R.common_args(argparse.ArgumentParser(description='30 노선 설계 일괄'))
    ap.add_argument('--only', nargs='*', default=None, help='r1 … r9 일부만')
    ap.add_argument('--skip', nargs='*', default=[], help='건너뛸 단계')
    ap.add_argument('--from', dest='src_run', default=None, help='중간 JSON 을 복사해 올 run')
    ap.add_argument('--step', action='append', default=[], metavar='단계="인자"', help='단계별 인자')
    ap.add_argument('--summary-only', action='store_true')
    a = ap.parse_args()
    C = R.Ctx(a)
    cfg = C.cfg
    if a.src_run:
        src = cfg.out_dir(R.STEP_DIR, a.src_run)
        n = 0
        for j in JSONS:
            if (src / j).exists():
                shutil.copy(src / j, C.path(j))
                n += 1
        print('%s → %s 중간 JSON %d개 복사' % (a.src_run, C.dir.name, n))
    if not a.summary_only:
        sargs = {k: list(v) for k, v in (cfg.get('route_design.args') or {}).items()}
        for it in a.step:
            k, _, v = it.partition('=')
            sargs.setdefault(k.strip(), []).extend(shlex.split(v, posix=True))
        base = ['--config', str(cfg.path), '--run', C.dir.name] + sum((['--set', s] for s in a.set), [])
        for sid, script, label, need in STEPS:
            if (a.only and sid not in a.only) or sid in a.skip:
                continue
            if need and not cfg.has_layer(need) and sid not in sargs:
                print('[%s] %s — layers.%s 없음, 건너뜀' % (sid, label, need))
                continue
            cmd = [sys.executable, '-X', 'utf8', str(HERE / script)] + base + sargs.get(sid, [])
            print('\n' + '=' * 72 + '\n[%s] %s  %s' % (sid, label, ' '.join(sargs.get(sid, []))))
            t = time.time()
            r = subprocess.run(cmd, cwd=os.getcwd())
            print('[%s] %s (%.0fs)' % (sid, 'OK' if r.returncode == 0 else 'FAIL rc=%d' % r.returncode, time.time() - t))
            if r.returncode != 0:
                sys.exit(r.returncode)
    print('\n요약 → %s' % summary(C))


if __name__ == '__main__':
    main()
