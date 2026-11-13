"""90-kit-feedback 자동 수집 — 이 팀이 킷을 어떻게 쓰고 고치는지 기록해 관리자 저장소의 이 팀 fork 로 올린다.

설치 때 사람이 한 번 동의해야 돈다(setup_share.py 가 <킷>/.kit-usage/share.json 을 만들고 Claude Code 훅을 건다).
share.json 이 없거나 "share": false 면 아무것도 기록하지도 보내지도 않는다.

    python -X utf8 kit_usage.py hook            # Claude Code 훅(SessionStart·SessionEnd)이 부른다. stdin = 훅 JSON
    python -X utf8 kit_usage.py sync [--transcripts <폴더> ...] [--cwd <프로젝트>] [--no-push]
    python -X utf8 kit_usage.py approve [--head <커밋>] [--no-send]   # 저장에 동의한 지점 표시(90 스킬이 커밋 뒤에 부른다)
    python -X utf8 kit_usage.py status          # 무엇이 기록됐고 무엇이 막혔는지

기록하는 것 (대화 기록에서 킷에 관한 것만 뽑는다)
  - 킷 스킬 호출(Skill) · 킷 문서 읽기 횟수(스킬별) · 킷 파일 수정 횟수(킷 안 상대경로)
  - 킷 스크립트 실행: 스크립트 이름, 옵션 이름(값은 버림), 성공/실패, 실패면 마지막 오류 한 줄(경로·고유명 지움)
  - 프로젝트 설정의 모양: 레이어 종류(gpkg/postgis/shp), 매핑한 논리 필드 이름, design 값 — 경로·이름·코드값은 뺀다
기록하지 않는 것: 대화 내용, 데이터·설정 파일·산출물, 프로젝트 경로·지구·업체 이름, 비밀번호
보내는 것: 사용 기록 → fork 의 usage 브랜치(설치 때 받은 동의 범위),
        킷 수정 커밋 → fork 의 local 브랜치 — **사용자가 저장에 동의한 지점까지만**(.kit-usage/approved.json), 고유명 검사 통과분만
훅은 작업 폴더를 건드리지 않는다: 커밋하지 않은 킷 수정을 대신 커밋하지 않고 blocked.md 로 알리기만 한다
        (저장 여부는 사용자가 90 스킬의 저장 질문으로 정한다).
고유명 검사에 걸리면 코드는 보내지 않고 .kit-usage/blocked.md 에 적는다(다음 세션 시작 때 Claude 에게 알린다).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[2]
U = KIT / '.kit-usage'
SHARE = U / 'share.json'
STATE = U / 'state.json'
USAGE = U / 'usage.jsonl'
TERMS = U / 'terms.json'
APPROVED = U / 'approved.json'
BLOCKED = U / 'blocked.md'
LOG = U / 'sync.log'
LOCK = U / 'sync.lock'
USAGE_REF = 'refs/kit-usage/log'
PUSH_EVERY_S = int(os.environ.get('KIT_USAGE_PUSH_EVERY', 600))   # 보내기 최소 간격(초)
NOWIN = 0x08000000 if os.name == 'nt' else 0            # CREATE_NO_WINDOW — git 창이 깜빡이지 않게

KIT_SKILLS = sorted(p.name for p in (KIT / 'skills').iterdir() if p.is_dir()) if (KIT / 'skills').exists() else []
SK = '|'.join(re.escape(s) for s in KIT_SKILLS) or r'\d\d-[a-z0-9-]+'
RX_SKILLFILE = re.compile(r'(?:^|/)skills/(' + SK + r')/(.+)$', re.I)
RX_SCRIPT = re.compile(r'(?:^|[/\s"\'])(' + SK + r')/((?:scripts|tools)/[\w.-]+\.py)', re.I)
RX_FLAG = re.compile(r'(?<![\w-])(--[A-Za-z][\w-]*)')
RX_PY = re.compile(r'(?:^|[\s/"\'&])(?:python[\w.-]*|py|python-qgis[\w.-]*)(?:\.exe|\.bat)?["\']?\s', re.I)
EDIT_TOOLS = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit'}
SHELL_TOOLS = {'Bash', 'PowerShell'}


# ───────────────────────────────────────── 공통
def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(p, o):
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding='utf-8')


def share_cfg():
    s = load_json(SHARE, None)
    return s if isinstance(s, dict) and s.get('share') else None


def log(msg):
    try:
        U.mkdir(exist_ok=True)
        old = LOG.read_text(encoding='utf-8').splitlines()[-300:] if LOG.exists() else []
        old.append(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}')
        LOG.write_text('\n'.join(old) + '\n', encoding='utf-8')
    except Exception:
        pass


def git(*args, check=False, input_=None, env=None):
    # stdin 은 바이트로 — 윈도 텍스트 파이프는 \n 을 \r\n 으로 바꿔 mktree 파일 이름에 \r 이 붙는다
    p = subprocess.run(['git', '-C', str(KIT), *args], capture_output=True,
                       input=input_.encode('utf-8') if input_ is not None else None, creationflags=NOWIN, timeout=120,
                       env=dict(os.environ, **(env or {})))
    p.stdout = p.stdout.decode('utf-8', 'replace')
    p.stderr = p.stderr.decode('utf-8', 'replace')
    if check and p.returncode:
        raise RuntimeError(f'git {" ".join(args)}: {p.stderr.strip()[:200]}')
    return p


def norm(p):
    return str(p).replace('\\', '/').replace('//', '/')


def ident_env():
    """커밋 작성자 — git 설정이 없으면 익명값(이메일·이름을 지어 넣지 않는다)."""
    e = {}
    if not git('config', 'user.name').stdout.strip():
        e.update(GIT_AUTHOR_NAME='kit-user', GIT_COMMITTER_NAME='kit-user')
    if not git('config', 'user.email').stdout.strip():
        e.update(GIT_AUTHOR_EMAIL='kit-user@users.noreply', GIT_COMMITTER_EMAIL='kit-user@users.noreply')
    return e


# ───────────────────────────────────────── 고유명 지우기
def scrub_patterns():
    sys.path.insert(0, str(HERE))
    import make_feedback as mf  # noqa: E402  — 같은 검사 규칙을 쓴다
    return mf


def project_terms(cwd):
    """cwd 에서 sewer.config.json 을 찾으면 그 프로젝트 고유명을 뽑아 캐시에 더한다(캐시는 이 PC 에만)."""
    cache = set(load_json(TERMS, []))
    try:
        mf = scrub_patterns()
        cfg = mf.kitconfig.load(None, start=cwd)
        cache |= set(mf.config_terms(cfg))
    except BaseException:  # 설정 없음·DB 접속 불가 — 캐시만 쓴다
        pass
    terms = sorted(cache, key=len, reverse=True)
    save_json(TERMS, terms)
    return terms


def sanitize(s, terms):
    if not s:
        return s
    mf = scrub_patterns()
    s = str(s)
    rep = {'드라이브 경로': '<path>', 'UNC 경로': '<path>', '이메일': '<email>', '비밀번호': '<secret>',
           'IP 주소': '<ip>', '전화번호': '<phone>'}
    for t in terms:                      # 고유명(긴 것부터) 먼저 — 경로 패턴이 먼저 먹으면 공백 뒤 나머지가 남는다
        if t and t in s:
            s = s.replace(t, '<name>')
    for label, rx in mf.GENERIC:
        s = rx.sub(rep.get(label, '<x>'), s)
    # 따옴표 안 경로가 공백에서 끊겨 남은 꼬리까지 지운다
    s = re.sub(r"(['\"])<(?:path|name)>[^'\"]*\1", r'\1<path>\1', s)
    return s[:200]


# ───────────────────────────────────────── 대화 기록 → 세션 요약
def kit_rel(path):
    """파일 경로가 킷 안이면 킷 상대경로, 아니면 None. 정션(~/.claude/skills/<킷 스킬>) 경로도 킷으로 본다."""
    p = norm(path)
    k = norm(KIT)
    if p.lower().startswith(k.lower() + '/'):
        return p[len(k) + 1:]
    m = RX_SKILLFILE.search(p)
    if m and m.group(1) in KIT_SKILLS:
        return f'skills/{m.group(1)}/{m.group(2)}'
    return None


def result_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return '\n'.join(b.get('text', '') for b in c if isinstance(b, dict))
    return ''


def last_error_line(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in reversed(lines):
        if re.search(r'(Error|Exception|Traceback|오류|실패|없음|없다|SystemExit|Exit code)', l):
            return l
    return lines[-1] if lines else ''


def scan_transcript(path, terms):
    """→ 세션 요약 dict 또는 None(킷을 안 쓴 세션)."""
    uses, results, meta = {}, {}, {}
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if not meta.get('date') and o.get('timestamp'):
                    meta['date'] = str(o['timestamp'])[:10]
                if o.get('version'):
                    meta['cc'] = o['version']
                if o.get('sessionId'):
                    meta['sid'] = o['sessionId']
                msg = o.get('message')
                c = msg.get('content') if isinstance(msg, dict) else None
                if not isinstance(c, list):
                    continue
                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get('type') == 'tool_use':
                        uses[b.get('id')] = (b.get('name'), b.get('input') or {})
                    elif b.get('type') == 'tool_result':
                        results[b.get('tool_use_id')] = (bool(b.get('is_error')), result_text(b.get('content')))
    except OSError:
        return None
    skills, reads, edits, runs = Counter(), Counter(), Counter(), []
    for uid, (name, inp) in uses.items():
        if name == 'Skill':
            s = str(inp.get('skill', '')).split(':')[-1]
            if s in KIT_SKILLS:
                skills[s] += 1
        elif name == 'Read':
            r = kit_rel(inp.get('file_path', ''))
            if r:
                reads[r.split('/')[1] if r.startswith('skills/') else r.split('/')[0]] += 1
        elif name in EDIT_TOOLS:
            r = kit_rel(inp.get('file_path') or inp.get('notebook_path') or '')
            if r:
                edits[r] += 1
        elif name in SHELL_TOOLS:
            cmd = norm(inp.get('command', ''))
            # 파이썬으로 실제 실행한 것만(같은 명령 토막에 python·py·python-qgis 가 있을 때) — grep·cat 로 본 것은 뺀다
            hits = [(m.group(1), m.group(2)) for seg in re.split(r'&&|\|\||;|\|', cmd) if RX_PY.search(seg)
                    for m in RX_SCRIPT.finditer(seg) if m.group(1) in KIT_SKILLS]
            if not hits:
                continue
            is_err, text = results.get(uid, (False, ''))
            flags = sorted(set(RX_FLAG.findall(cmd)))
            for sk, sc in hits:
                runs.append({'script': f'{sk}/{sc}', 'flags': flags, 'ok': not is_err,
                             'err': sanitize(last_error_line(text), terms) if is_err else ''})
    if not (skills or reads or edits or runs):
        return None
    sid = hashlib.sha1(str(meta.get('sid') or path).encode()).hexdigest()[:12]   # 원 세션 ID 대신 해시
    return {'sid': sid, 'date': meta.get('date', ''), 'cc': meta.get('cc', ''), 'skills': dict(skills),
            'reads': dict(reads), 'edits': dict(edits), 'runs': runs}


def project_shape(cwd):
    """프로젝트 설정의 모양만(경로·이름·코드값 없이)."""
    try:
        sys.path.insert(0, str(KIT / 'lib'))
        import kitconfig
        cfg = kitconfig.load(None, start=cwd)
    except BaseException:
        return None
    layers = {}
    for k, spec in (cfg.get('layers') or {}).items():
        src = str((spec or {}).get('src', '')) if isinstance(spec, dict) else ''
        kind = 'postgis' if src == 'postgis' else (Path(src).suffix.lower().lstrip('.') or '?')
        layers[k] = {'kind': kind, 'fields': sorted((cfg.get(f'fields.{k}') or {}).keys())}
    design = {k: v for k, v in (cfg.get('design') or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {'project': hashlib.sha1(str(cfg.root).encode()).hexdigest()[:10], 'layers': layers,
            'dem': len(cfg.get('dem') or []), 'design': design}


# ───────────────────────────────────────── 동기화
def update_usage(transcript_dirs, cwd, terms):
    st = load_json(STATE, {})
    scanned = st.setdefault('scanned', {})
    recs = {}
    if USAGE.exists():
        for l in USAGE.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(l)
                recs[r['sid']] = r
            except Exception:
                pass
    shape = project_shape(cwd) if cwd else None
    n_new = 0
    for d in transcript_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in d.glob('*.jsonl'):
            key = hashlib.sha1(norm(f).encode()).hexdigest()[:16]
            m = f.stat().st_mtime
            if scanned.get(key, 0) >= m:
                continue
            r = scan_transcript(f, terms)
            scanned[key] = m
            if r:
                if shape and r['sid'] not in recs:
                    r['config'] = shape
                elif r['sid'] in recs and 'config' in recs[r['sid']]:
                    r['config'] = recs[r['sid']]['config']
                recs[r['sid']] = r
                n_new += 1
    USAGE.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n'
                             for r in sorted(recs.values(), key=lambda r: (r.get('date', ''), r['sid']))), encoding='utf-8')
    save_json(STATE, st)
    return n_new, len(recs)


def pending_changes():
    """커밋하지 않은 킷 수정을 알리기만 한다 — 훅은 사용자 동의 없이 커밋하지 않는다.

    반환: (현재 브랜치, 알릴 말 또는 None)
    """
    br = git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    dirty = git('status', '--porcelain').stdout.strip()
    if not dirty:
        return br, None
    files = [l[3:].strip().strip('"') for l in dirty.splitlines()]
    names = ', '.join(Path(f).name for f in files[:6]) + (' 외' if len(files) > 6 else '')
    ask = '사용자에게 「오늘 작업한 스킬과 도구를 Git에 저장하고 GitHub에 올릴까요?」를 묻고, 동의하면'
    if br != 'local':
        return br, (f'킷이 `{br}` 브랜치에서 고쳐졌다({names}) — 저장하려면 local 로 옮겨야 한다. '
                    f'{ask} local 에서 커밋한 뒤 `kit_usage.py approve` 를 돌릴 것.')
    return br, (f'커밋하지 않은 킷 수정이 있다({names}) — 훅은 대신 커밋하지 않는다. '
                f'{ask} 일반화·커밋 후 `kit_usage.py approve` 를 돌릴 것.')


def approved_head():
    """사용자가 저장에 동의하고 **전송까지 동의한** 커밋(local 의 조상)만 반환한다. 없으면 None."""
    a = load_json(APPROVED, {}) or {}
    h = a.get('head', '')
    if not h or not a.get('send', True):
        return None
    sha = git('rev-parse', '-q', '--verify', f'{h}^{{commit}}').stdout.strip()
    if not sha or git('merge-base', '--is-ancestor', sha, 'local').returncode:
        return None
    return sha


def approve(head='', send=True):
    """90 스킬이 사용자 동의를 받아 커밋한 뒤 부른다 — 그 지점까지만 전송 대상이 된다.

    send=False: 「저장은 하되 올리진 마」 — 커밋만 남기고 fork 로 보내지 않는다.
    """
    br = git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    ref = head or 'local'
    sha = git('rev-parse', '-q', '--verify', f'{ref}^{{commit}}').stdout.strip()
    if not sha:
        print(f'그런 커밋이 없다: {ref}')
        return 1
    if git('merge-base', '--is-ancestor', sha, 'local').returncode:
        print(f'{ref} 은 local 브랜치의 커밋이 아니다 — local 에서 커밋한 뒤 다시 돌릴 것(현재 브랜치 {br}).')
        return 1
    U.mkdir(exist_ok=True)
    save_json(APPROVED, {'head': sha, 'send': bool(send),
                         'at': dt.datetime.now().isoformat(timespec='seconds')})
    subj = git('log', '-1', '--format=%s', sha).stdout.strip()[:60]
    print(f'저장 동의 표시: {sha[:9]} ({subj}) · 전송 {"동의" if send else "보류 — 커밋만 남긴다"}')
    dirty = git('status', '--porcelain').stdout.strip()
    if dirty:
        print('커밋하지 않은 변경은 저장·전송 대상이 아니다:\n' + dirty)
    return 0


def code_hits(terms, head='local'):
    """origin/main..head(보통 사용자가 동의한 지점)의 추가 줄·커밋 메시지에서 고유명을 찾는다."""
    mf = scrub_patterns()
    base = 'origin/main'
    if git('rev-parse', '--verify', '-q', base).returncode:
        return [], ''
    log_ = git('log', '--format=%h %s', f'{base}..{head}').stdout.strip()
    if not log_:
        return [], ''
    patch = git('format-patch', '--stdout', f'{base}..{head}').stdout
    added = '\n'.join(l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++'))
    msgs = git('log', '--format=%s%n%b', f'{base}..{head}').stdout
    hits = []
    mf.scan(added, 'patch', terms, hits)
    mf.scan(msgs, '커밋 메시지', terms, hits)
    return hits, log_


def usage_commit(terms, team):
    """usage.jsonl(다시 지운 판) + team.json 을 별도 ref 에 커밋(작업 폴더·브랜치는 건드리지 않는다)."""
    lines = []
    for l in USAGE.read_text(encoding='utf-8').splitlines() if USAGE.exists() else []:
        try:
            r = json.loads(l)
        except Exception:
            continue
        for run in r.get('runs', []):
            run['err'] = sanitize(run.get('err', ''), terms)
        lines.append(json.dumps(r, ensure_ascii=False))
    kitrev = git('rev-parse', '--short', 'origin/main').stdout.strip()
    files = {'usage.jsonl': '\n'.join(lines) + '\n',
             'team.json': json.dumps({'team': team, 'kit_main': kitrev}, ensure_ascii=False) + '\n'}
    tree_in = ''
    for name, text in files.items():
        sha = git('hash-object', '-w', '--stdin', input_=text, check=True).stdout.strip()
        tree_in += f'100644 blob {sha}\t{name}\n'
    tree = git('mktree', input_=tree_in, check=True).stdout.strip()
    parent = git('rev-parse', '-q', '--verify', USAGE_REF).stdout.strip()
    if parent and git('rev-parse', f'{parent}^{{tree}}').stdout.strip() == tree:
        return parent, False
    args = ['commit-tree', tree, '-m', f'usage {dt.date.today()}'] + (['-p', parent] if parent else [])
    c = git(*args, check=True, env=ident_env()).stdout.strip()
    git('update-ref', USAGE_REF, c, check=True)
    return c, True


def sync(transcript_dirs, cwd, push=True):
    s = share_cfg()
    if not s:
        return 0
    U.mkdir(exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 300:
        return 0
    LOCK.write_text(str(os.getpid()))
    try:
        terms = project_terms(cwd) if cwd else load_json(TERMS, [])
        n_new, n_all = update_usage(transcript_dirs, cwd, terms)
        br, note = pending_changes()
        ok_head = approved_head()
        hits, commits = code_hits(terms, ok_head) if ok_head else ([], '')
        problems = [note] if note else []
        appr = load_json(APPROVED, {}) or {}
        if appr.get('head') and not appr.get('send', True):
            problems.append('사용자가 저장에만 동의했다(전송 보류) — 커밋은 local 에 남기고 보내지 않는다. '
                            '올리기로 하면 `kit_usage.py approve` 를 다시 돌릴 것.')
        elif appr.get('head') and not ok_head:
            problems.append('동의 표시(approved.json)의 커밋이 local 에 없다 — 커밋을 고쳤거나 되돌렸다면 '
                            '`kit_usage.py approve` 를 다시 돌릴 것. 그때까지 킷 수정은 보내지 않는다.')
        if hits:
            problems.append(f'킷 수정에 고유명·경로 {len(hits)}건 — 코드는 보내지 않았다. '
                            '`python -X utf8 <킷>/skills/90-kit-feedback/scripts/make_feedback.py --config <프로젝트 설정>` 로 '
                            '위치를 보고 일반화한 커밋으로 덮을 것.')
            problems += [f'  - {w}:{i} {k} | {t[:100]}' for w, i, k, t in hits[:20]]
        _, changed = usage_commit(terms, s.get('team', ''))
        pushed = []
        st = load_json(STATE, {})
        remote = s.get('remote', 'fork')
        has_remote = remote in git('remote').stdout.split()
        if push and has_remote and time.time() - st.get('last_push', 0) >= PUSH_EVERY_S:
            p = git('push', '-q', remote, f'{USAGE_REF}:refs/heads/usage')
            pushed.append('usage' if p.returncode == 0 else f'usage 실패({p.stderr.strip()[:80]})')
            if commits and not hits and ok_head:
                p = git('push', '-q', '--force', remote, f'{ok_head}:refs/heads/local')
                pushed.append('local' if p.returncode == 0 else f'local 실패({p.stderr.strip()[:80]})')
            if all('실패' not in x for x in pushed):
                st['last_push'] = time.time()
                save_json(STATE, st)
        elif push and not has_remote:
            problems.append(f'`{remote}` 원격이 없어 보내지 못했다 — setup_share.py 를 다시 돌릴 것.')
        if problems:
            BLOCKED.write_text('# 킷 자동 보내기 — 확인 필요\n\n' + '\n'.join(f'- {p}' if not p.startswith('  ') else p
                                                                       for p in problems) + '\n', encoding='utf-8')
        elif BLOCKED.exists():
            BLOCKED.unlink()
        log(f'sync 새 세션 {n_new}/{n_all} · 커밋 {len(commits.splitlines()) if commits else 0} · 검출 {len(hits)} · '
            f'usage {"갱신" if changed else "같음"} · push {pushed or "-"}')
        return 0
    except Exception as e:
        log(f'sync 오류 {type(e).__name__}: {e}')
        return 1
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


# ───────────────────────────────────────── 훅
def spawn_sync(tdir, cwd):
    args = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), 'sync']
    if tdir:
        args += ['--transcripts', tdir]
    if cwd:
        args += ['--cwd', cwd]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, cwd=str(KIT))
    if os.name == 'nt':
        base = NOWIN | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(args, creationflags=base | 0x01000000, **kw)   # + CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            subprocess.Popen(args, creationflags=base, **kw)
    else:
        subprocess.Popen(args, start_new_session=True, **kw)


def hook():
    if not share_cfg():
        return 0
    try:
        h = json.loads(sys.stdin.read() or '{}')
    except Exception:
        h = {}
    ev = h.get('hook_event_name', '')
    tp = h.get('transcript_path') or ''
    tdir = str(Path(tp).parent) if tp else ''
    if ev in ('SessionStart', 'SessionEnd'):
        spawn_sync(tdir, h.get('cwd', ''))
    if ev == 'SessionStart' and BLOCKED.exists():
        # 평문 stdout 은 Claude 의 맥락으로 들어간다
        print('[qgis-water-sewer-kit] 킷 자동 보내기에 확인할 것이 있다. 사용자에게 한 줄로 알리고, 원하면 고친다:\n'
              + BLOCKED.read_text(encoding='utf-8')[:3000])
    return 0


def status():
    s = load_json(SHARE, None)
    print(f'공유 설정: {s if s else "없음(기록·보내기 꺼짐)"}')
    st = load_json(STATE, {})
    if st.get('last_push'):
        print(f'마지막 보내기: {dt.datetime.fromtimestamp(st["last_push"]):%Y-%m-%d %H:%M}')
    n = len(USAGE.read_text(encoding='utf-8').splitlines()) if USAGE.exists() else 0
    print(f'기록된 세션: {n}')
    c = git('log', '--oneline', 'origin/main..local').stdout.strip()
    print(f'local 에만 있는 킷 수정 커밋: {len(c.splitlines()) if c else 0}')
    ok = approved_head()
    appr = load_json(APPROVED, {}) or {}
    if ok:
        n_ok = git('log', '--oneline', f'origin/main..{ok}').stdout.strip()
        print(f'저장·전송 동의 지점: {ok[:9]} — 전송 대상 커밋 {len(n_ok.splitlines()) if n_ok else 0}건')
    elif appr.get('head') and not appr.get('send', True):
        print(f'저장만 동의: {appr["head"][:9]} — 커밋은 남기고 전송은 보류')
    else:
        print('저장·전송 동의 지점: 없음 — 킷 수정 커밋은 보내지 않는다(90 스킬의 저장 질문 → approve)')
    dirty = git('status', '--porcelain').stdout.strip()
    if dirty:
        print(f'커밋하지 않은 킷 수정: {len(dirty.splitlines())}개 파일 — 훅은 대신 커밋하지 않는다')
    if BLOCKED.exists():
        print('\n' + BLOCKED.read_text(encoding='utf-8'))
    if LOG.exists():
        print('\n최근 동기화:\n' + '\n'.join(LOG.read_text(encoding='utf-8').splitlines()[-5:]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('hook')
    sp = sub.add_parser('sync')
    sp.add_argument('--transcripts', nargs='*', default=[])
    sp.add_argument('--cwd', default='')
    sp.add_argument('--no-push', action='store_true')
    sp_ok = sub.add_parser('approve')
    sp_ok.add_argument('--head', default='', help='동의 지점 커밋(기본: local 의 현재 끝)')
    sp_ok.add_argument('--no-send', action='store_true', help='저장만 동의 — 커밋은 남기고 fork 로 보내지 않는다')
    sub.add_parser('status')
    a = ap.parse_args()
    if a.cmd == 'hook':
        try:
            return hook()
        except Exception as e:      # 훅은 절대 세션을 막지 않는다
            log(f'hook 오류 {type(e).__name__}: {e}')
            return 0
    if a.cmd == 'sync':
        return sync(a.transcripts, a.cwd, push=not a.no_push)
    if a.cmd == 'approve':
        return approve(a.head, send=not a.no_send)
    return status()


if __name__ == '__main__':
    sys.exit(main())
