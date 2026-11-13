"""90-kit-feedback 공유 설정 — 설치 때 한 번. 사람이 동의한 뒤에만 Claude 가 돌린다.

    python -X utf8 setup_share.py --share yes --team <팀 별칭>     # 켜기: fork 원격 + 훅 + share.json
    python -X utf8 setup_share.py --share no                         # 끄기: 훅 떼고 기록·보내기 중지
    python -X utf8 setup_share.py --check                            # 지금 상태

켜면
  1. 관리자 저장소를 이 사람 GitHub 계정으로 fork 하고 킷 폴더에 `fork` 원격을 단다(gh 로그인 필요, 한 번).
     git 이 gh 자격증명을 쓰도록 `gh auth setup-git` 도 한다.
  2. <킷>/.kit-usage/share.json 을 쓴다(팀 별칭 — 회사·사람 이름 말고 아무 별칭).
  3. Claude Code 사용자 설정(~/.claude/settings.json)에 SessionStart·SessionEnd 훅을 더한다(기존 설정은 그대로, 백업 남김).
     훅은 kit_usage.py 를 불러 대화 기록에서 킷에 관한 것만 뽑아 기록하고, 고유명 검사를 통과한 것만 fork 로 올린다.
fork 는 공개 저장소다 — 올라가는 것은 kit_usage.py 머리말의 「기록하는 것」뿐이다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[2]
U = KIT / '.kit-usage'
SHARE = U / 'share.json'
HOOK_SCRIPT = HERE / 'kit_usage.py'
MARK = 'kit_usage.py'
EVENTS = ('SessionStart', 'SessionEnd')


def run(args, cwd=KIT):
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding='utf-8', errors='replace')


def hook_command():
    py = Path(sys.executable).as_posix()
    py = py if ' ' not in py else f'"{py}"'      # 공백 없는 경로는 따옴표 없이 — Git Bash·PowerShell 둘 다 실행된다
    return f'{py} -X utf8 "{HOOK_SCRIPT.as_posix()}" hook'


def load_settings(p):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8') or '{}')
    except json.JSONDecodeError as e:
        raise SystemExit(f'설정 파일을 읽지 못했다({e}) — 손으로 고친 뒤 다시: {p}')


def set_hooks(p, on):
    s = load_settings(p)
    hooks = s.setdefault('hooks', {})
    changed = False
    for ev in EVENTS:
        groups = hooks.get(ev, [])
        keep = []
        for g in groups:
            hs = [h for h in g.get('hooks', []) if MARK not in str(h.get('command', ''))]
            if len(hs) != len(g.get('hooks', [])):
                changed = True
            if hs:
                keep.append({**g, 'hooks': hs})
        if on:
            keep.append({'hooks': [{'type': 'command', 'command': hook_command(), 'timeout': 10}]})
            changed = True
        if keep:
            hooks[ev] = keep
        elif ev in hooks:
            del hooks[ev]
    if not hooks:
        s.pop('hooks', None)
    if changed:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            bak = p.with_name(p.name + '.bak_kitshare')
            if not bak.exists():
                shutil.copy2(p, bak)
        p.write_text(json.dumps(s, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return changed


def origin_repo():
    url = run(['git', 'remote', 'get-url', 'origin']).stdout.strip()
    url = url.removesuffix('.git')
    for pre in ('https://github.com/', 'git@github.com:'):
        if url.startswith(pre):
            return url[len(pre):]
    return url


def ensure_fork():
    if 'fork' in run(['git', 'remote']).stdout.split():
        return 'fork 원격 있음: ' + run(['git', 'remote', 'get-url', 'fork']).stdout.strip()
    if not shutil.which('gh'):
        raise SystemExit('gh(GitHub CLI)가 없다 — 설치하고 `gh auth login` 한 뒤 다시. GitHub 를 안 쓰면 --no-fork(기록만, 보내기는 make_feedback 꾸러미로).')
    if run(['gh', 'auth', 'status']).returncode:
        raise SystemExit('gh 로그인이 안 돼 있다 — 사용자가 직접 `gh auth login` 을 한 뒤 다시.')
    p = run(['gh', 'repo', 'fork', origin_repo(), '--remote', '--remote-name', 'fork', '--clone=false'])
    if p.returncode or 'fork' not in run(['git', 'remote']).stdout.split():
        raise SystemExit(f'fork 실패: {(p.stderr or p.stdout).strip()[:300]}')
    run(['gh', 'auth', 'setup-git'])
    return 'fork 만듦: ' + run(['git', 'remote', 'get-url', 'fork']).stdout.strip()


def check(settings):
    s = json.loads(SHARE.read_text(encoding='utf-8')) if SHARE.exists() else None
    print(f'share.json: {s}')
    print(f'브랜치: {run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()}')
    print(f'원격: {run(["git", "remote", "-v"]).stdout.strip()}')
    hs = load_settings(settings).get('hooks', {})
    for ev in EVENTS:
        n = sum(MARK in str(h.get('command', '')) for g in hs.get(ev, []) for h in g.get('hooks', []))
        print(f'훅 {ev}: {"있음" if n else "없음"}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--share', choices=('yes', 'no'))
    ap.add_argument('--team', default='', help='팀 별칭(회사·사람 이름 말고)')
    ap.add_argument('--settings', default=str(Path.home() / '.claude' / 'settings.json'))
    ap.add_argument('--no-fork', action='store_true', help='fork 없이 기록만(보내기는 make_feedback 꾸러미로)')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    settings = Path(a.settings)
    if a.check or not a.share:
        return check(settings)
    U.mkdir(exist_ok=True)
    if a.share == 'no':
        SHARE.write_text(json.dumps({'share': False, 'since': str(dt.date.today())}, ensure_ascii=False), encoding='utf-8')
        print('훅 ' + ('뗌' if set_hooks(settings, False) else '없었음') + ' — 기록·보내기 꺼짐.')
        return
    if not a.team.strip():
        raise SystemExit('--team 별칭이 필요하다(예: 팀A, water-team).')
    br = run(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.strip()
    if br != 'local':
        print(f'⚠ 지금 브랜치가 `{br}` — 킷 수정은 local 브랜치에서 해야 보내진다(INSTALL.md 1절).')
    msg = '' if a.no_fork else ensure_fork()
    SHARE.write_text(json.dumps({'share': True, 'team': a.team.strip(), 'remote': 'fork',
                                 'since': str(dt.date.today())}, ensure_ascii=False, indent=1), encoding='utf-8')
    set_hooks(settings, True)
    print('\n'.join(x for x in (msg, f'share.json: {SHARE}', f'훅: {settings} (SessionStart·SessionEnd)',
                                f'명령: {hook_command()}', 'Claude Code 를 다시 열면 그때부터 기록한다.') if x))


if __name__ == '__main__':
    main()
