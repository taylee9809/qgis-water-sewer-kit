"""90-kit-feedback 관리자용 — 모든 팀 fork 에서 킷 수정과 사용 기록을 모아 보고서를 만든다. 읽기 전용(팀 저장소에 쓰지 않는다).

    python -X utf8 collect_feedback.py                       # gh 로 이 저장소의 fork 목록을 읽어 모은다
    python -X utf8 collect_feedback.py --forks <git URL 또는 폴더> ...   # 목록을 직접 줄 때(시험용)
    python -X utf8 collect_feedback.py --out <폴더>

관리자 킷 작업본(main)에서 돌린다. 각 fork 의 local·usage 브랜치를 refs/feedback/<팀>/… 로 받아 온다(main 은 안 건드린다).
산출 <out>/<YYMMDD_HHMM>/
  report.md          팀별 요약 · 스킬 사용 순위 · 실패 순위 · 팀별 킷 수정 목록 · 설정 모양
  patches/<팀>.patch 팀별 main 에 없는 커밋 — 받아들일 것은 `git cherry-pick refs/feedback/<팀>/local~N` 또는 `git am`
기본 <out> = 킷 폴더 옆 `<킷이름>-feedback`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]


def git(*args, check=False):
    p = subprocess.run(['git', '-C', str(KIT), *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and p.returncode:
        raise SystemExit(f'git {" ".join(args)}: {p.stderr.strip()[:300]}')
    return p


def fork_list(repo):
    p = subprocess.run(['gh', 'api', f'repos/{repo}/forks', '--paginate', '--jq', '.[] | [.owner.login, .clone_url] | @tsv'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if p.returncode:
        raise SystemExit(f'fork 목록 실패(gh 로그인?): {p.stderr.strip()[:200]}')
    return [tuple(l.split('\t')) for l in p.stdout.splitlines() if '\t' in l]


def origin_repo():
    url = git('remote', 'get-url', 'origin').stdout.strip().removesuffix('.git')
    for pre in ('https://github.com/', 'git@github.com:'):
        if url.startswith(pre):
            return url[len(pre):]
    return url


def safe(name):
    return re.sub(r'[^\w.-]+', '_', name)[:40] or 'team'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo', default=None, help='owner/name (기본: origin)')
    ap.add_argument('--forks', nargs='*', default=None, help='fork git URL·폴더를 직접 줄 때')
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--out', default=str(KIT.parent / f'{KIT.name}-feedback'))
    a = ap.parse_args()

    git('fetch', '-q', 'origin')
    forks = [(Path(f).name if Path(f).exists() else f.rstrip('/').split('/')[-2], f) for f in a.forks] \
        if a.forks is not None else fork_list(a.repo or origin_repo())
    out = Path(a.out) / dt.datetime.now().strftime('%y%m%d_%H%M')
    (out / 'patches').mkdir(parents=True, exist_ok=True)

    teams = []
    skill_use, skill_read, script_runs, fails, edits = Counter(), Counter(), Counter(), Counter(), Counter()
    fail_teams = defaultdict(set)
    layer_kinds, design_vals = Counter(), defaultdict(Counter)
    for owner, url in forks:
        key = safe(owner)
        ref = f'refs/feedback/{key}'
        got = {}
        for br in ('local', 'usage'):
            p = git('fetch', '-q', '--no-tags', url, f'+refs/heads/{br}:{ref}/{br}')
            got[br] = p.returncode == 0
        t = {'owner': owner, 'key': key, 'team': '', 'sessions': 0, 'last': '', 'commits': [], 'stat': '', 'runs': 0, 'fails': 0}
        if got['usage']:
            tj = git('show', f'{ref}/usage:team.json').stdout
            try:
                t['team'] = json.loads(tj).get('team', '')
            except Exception:
                pass
            for l in git('show', f'{ref}/usage:usage.jsonl').stdout.splitlines():
                try:
                    r = json.loads(l)
                except Exception:
                    continue
                t['sessions'] += 1
                t['last'] = max(t['last'], r.get('date', ''))
                for s, n in (r.get('skills') or {}).items():
                    skill_use[s] += n
                for s, n in (r.get('reads') or {}).items():
                    skill_read[s] += n
                for f, n in (r.get('edits') or {}).items():
                    edits[f] += n
                for run in r.get('runs') or []:
                    script_runs[run['script']] += 1
                    t['runs'] += 1
                    if not run.get('ok', True):
                        t['fails'] += 1
                        k = (run['script'], run.get('err', '')[:120])
                        fails[k] += 1
                        fail_teams[k].add(t['team'] or key)
                cf = r.get('config') or {}
                for lk, spec in (cf.get('layers') or {}).items():
                    layer_kinds[(lk, spec.get('kind'))] += 1
                for dk, dv in (cf.get('design') or {}).items():
                    design_vals[dk][dv] += 1
        if got['local']:
            c = git('log', '--reverse', '--format=%h %ad %s', '--date=short', f'{a.base}..{ref}/local').stdout.strip()
            t['commits'] = c.splitlines() if c else []
            if t['commits']:
                t['stat'] = git('diff', '--stat', f'{a.base}...{ref}/local').stdout.strip()
                (out / 'patches' / f'{key}.patch').write_text(
                    git('format-patch', '--stdout', f'{a.base}..{ref}/local').stdout, encoding='utf-8')
        if got['local'] or got['usage']:
            teams.append(t)

    R = [f'# 킷 피드백 모음 — {dt.date.today()}', '',
         f'fork {len(forks)}개 중 기록이 있는 팀 {len(teams)}개. 기준 `{a.base}` = {git("rev-parse", "--short", a.base).stdout.strip()}', '',
         '## 팀별', '', '| 팀 | GitHub | 세션 | 마지막 | 스크립트 실행 | 실패 | main 에 없는 킷 수정 |', '|---|---|---|---|---|---|---|']
    R += [f'| {t["team"] or "-"} | {t["owner"]} | {t["sessions"]} | {t["last"] or "-"} | {t["runs"]} | {t["fails"]} | {len(t["commits"])} |'
          for t in sorted(teams, key=lambda t: t['last'], reverse=True)]
    R += ['', '## 스킬 사용', '', '| 스킬 | 호출 | 문서 읽기 |', '|---|---|---|']
    for s in sorted(set(skill_use) | set(skill_read), key=lambda s: -(skill_use[s] * 3 + skill_read[s])):
        R.append(f'| {s} | {skill_use[s]} | {skill_read[s]} |')
    R += ['', '## 스크립트 실행', '', '| 스크립트 | 실행 |', '|---|---|'] + [f'| {s} | {n} |' for s, n in script_runs.most_common(30)]
    R += ['', '## 실패 (같은 오류끼리)', '', '| 스크립트 | 마지막 오류 줄 | 횟수 | 팀 |', '|---|---|---|---|']
    R += [f'| {s} | `{e.replace("|", "/")}` | {n} | {", ".join(sorted(fail_teams[(s, e)]))} |' for (s, e), n in fails.most_common(30)]
    R += ['', '## 팀이 고친 킷 파일(편집 횟수)', ''] + [f'- `{f}` {n}' for f, n in edits.most_common(30)]
    R += ['', '## 팀별 킷 수정', '']
    for t in teams:
        if not t['commits']:
            continue
        R += [f'### {t["team"] or t["owner"]} ({t["owner"]}) — {len(t["commits"])}건 · `patches/{t["key"]}.patch`', '',
              '```', *t['commits'], '```', '', '```', t['stat'], '```', '',
              f'받기: `git cherry-pick <커밋>` (커밋은 `refs/feedback/{t["key"]}/local` 에 있다) 또는 `git am patches/{t["key"]}.patch`', '']
    R += ['## 설정 모양', '', '레이어 출처 종류:', ''] + [f'- {k} = {kind} ({n}세션)' for (k, kind), n in sorted(layer_kinds.items())]
    R += ['', 'design 값(기본과 다를 수 있음):', ''] + [f'- {k}: ' + ', '.join(f'{v} ({n})' for v, n in c.most_common())
                                                for k, c in sorted(design_vals.items())]
    (out / 'report.md').write_text('\n'.join(R) + '\n', encoding='utf-8')
    print('\n'.join(R[:12 + len(teams)]))
    print(f'\n→ {out / "report.md"}')


if __name__ == '__main__':
    main()
