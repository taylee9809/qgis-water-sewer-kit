"""90-kit-feedback 묶음 만들기 — 이 팀이 킷에 한 수정·실패 기록·새 도구를 '보낼 수 있는 꾸러미'로 만든다.

읽기 전용이다(킷·프로젝트 원본을 고치지 않는다). 아무것도 보내지 않는다 — 보내기는 SKILL.md 절차로 사람 승인 후에.

    python -X utf8 make_feedback.py [--config sewer.config.json] [--base origin/main]
                                    [--include <프로젝트 스크립트·문서> ...] [--deny 단어 ...] [--no-fetch]

하는 일
  1. 킷 git: <base>..HEAD 커밋과 변경분(patch). 커밋 안 한 변경이 있으면 경고하고 멈춘다.
  2. 실패 기록: <outputs.root>/kit-feedback/notes/*.md
  3. --include 로 준 파일(킷으로 올리고 싶은 프로젝트 도구)
  4. 고유명 검사 — 아래가 보낼 내용에 섞였는지 본다
       · 이 프로젝트 설정에서 뽑은 것: 프로젝트 루트·이름, 설정 안 모든 경로, 지구·처리분구 값(레이어에서 읽음)
       · 일반 패턴: 드라이브 경로, 이메일, password=..., IP, 전화번호
       · --deny 로 더 준 단어
  5. <outputs.root>/kit-feedback/<YYMMDD_HHMM>/ 에 changes.patch · summary.md · extras/ · scrub.md + 같은 이름 zip
종료코드: 0 깨끗함 / 2 고유명 검출(고친 뒤 다시) / 1 오류
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(KIT / 'lib'))
import kitconfig  # noqa: E402

GENERIC = [
    ('드라이브 경로', re.compile(r'\b[A-Za-z]:[\\/][^\s"\'`<>|]*')),
    ('UNC 경로', re.compile(r'\\\\[A-Za-z0-9._-]+\\[^\s"\'`]+')),
    ('이메일', re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')),
    ('비밀번호', re.compile(r'(?i)(password|passwd|pwd)\s*[=:]\s*["\']?[^\s"\'`,)]{3,}')),
    ('IP 주소', re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')),
    ('전화번호', re.compile(r'\b0\d{1,2}-\d{3,4}-\d{4}\b')),
]
# 킷 자신이 쓰는 안전한 표기(검출 제외)
ALLOW = [re.compile(p) for p in (r'password_env', r'SEWER_PG_PASSWORD', r"password='\*\*\*'", r'password=\*\*\*',
                                  r'noreply@anthropic\.com', r'127\.0\.0\.1', r'0\.0\.0\.0')]


def git(*args, check=True):
    p = subprocess.run(['git', '-C', str(KIT), *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and p.returncode != 0:
        raise SystemExit(f'git {" ".join(args)} 실패: {p.stderr.strip()}')
    return p.stdout


def config_terms(cfg):
    """설정에서 이 프로젝트 고유명 후보를 모은다: 경로, 이름, 지구·분구 값."""
    terms = set()
    root = str(cfg.root)
    terms |= {root, Path(root).name}
    name = cfg.get('project.name')
    if name:
        terms.add(str(name))

    # 경로가 들어가는 설정 항목만 본다(필드명 '자연유하/압송'·정규식 같은 건 경로가 아니다)
    paths = [spec.get('src') for spec in (cfg.get('layers') or {}).values() if isinstance(spec, dict)]
    paths += list(cfg.get('dem') or [])
    paths += [v for v in (cfg.get('survey') or {}).values() if isinstance(v, str)]
    paths += [x for x in (cfg.get('survey.flo') or []) if isinstance(x, str)]
    paths += list(cfg.get('review_vendor.exchange_dirs') or [])
    paths += [cfg.get('asbuilt.src_dir'), cfg.get('outputs.root')]
    for o in paths:
        if not o or o == 'postgis':
            continue
        terms.add(str(o))
        for part in Path(str(o)).parts:          # 폴더·파일 이름 조각도(용역명·업체명·날짜 붙은 파일명)
            if len(part) >= 4 and not re.match(r'^[A-Za-z]:\\?$', part):
                terms.add(part)
    for name in (cfg.get('asbuilt.offsets') or {}):   # 오프셋 키 = 지구명
        if not str(name).startswith('_'):
            terms.add(str(name))
    # 지구·분구 값 — 레이어에서 실제 값을 읽는다(GPKG 는 sqlite, PostGIS 는 연결되면)
    for key, logical in (('plan_pipe', 'district'), ('plan_manhole', 'district'), ('asbuilt_pipe', 'district'),
                         ('asbuilt_manhole', 'district'), ('house', 'district'), ('service_area', 'subarea')):
        col = cfg.get(f'fields.{key}.{logical}')
        if not col or not cfg.has_layer(key):
            continue
        try:
            L = cfg.layer(key)
            if L['kind'] == 'gpkg' and L['path'] and L['path'].exists():
                cn = sqlite3.connect(f'file:{L["path"].as_posix()}?mode=ro', uri=True)
                rows = cn.execute(f'select distinct "{col}" from "{L["layer"]}" where "{col}" is not null limit 500').fetchall()
                cn.close()
            elif L['kind'] == 'postgis':
                with cfg.pg_connect() as cn:
                    cur = cn.cursor()
                    cur.execute(f'select distinct "{col}"::text from "{L["schema"]}"."{L["layer"]}" where "{col}" is not null limit 500')
                    rows = cur.fetchall()
            else:
                continue
            for (v,) in rows:
                v = str(v).strip()
                if len(v) >= 2 and not v.isdigit():
                    terms.add(v)
                    # 'A지구(추가)' → 'A지구' 만 더한다. 괄호 안은 수식어(추가·처리장위 …)라 일반 낱말이 많다
                    base = re.split(r'[(（\[]', v)[0].strip()
                    terms |= {t for t in re.split(r'[\s,/·]+', base) if len(t) >= 2}
        except (Exception, SystemExit) as e:  # 접속 불가 등 — 검사는 계속
            print(f'  (지구값 읽기 건너뜀: {key} — {str(e)[:60]})')
    # 너무 일반적인 낱말은 뺀다(오탐 방지)
    generic = {'data', 'gpkg', 'postgis', '기존', '계획', '신설', '기타', '자연유하', '압송', '_산출', 'tools', 'scripts',
               '추가', '금회', '준공', '연계', '변경', '검토', '확인', '전체', '본선', '지선', '처리장', '처리구역', '.', '..'}
    return sorted((t for t in terms if t and t.lower() not in generic and len(t) >= 2), key=len, reverse=True)


def scan(text, where, terms, hits):
    for i, line in enumerate(text.splitlines(), 1):
        clean = line
        for a in ALLOW:
            clean = a.sub('', clean)
        for t in terms:
            if t in clean:
                hits.append((where, i, f'프로젝트 고유명 「{t}」', line.strip()[:160]))
        for label, rx in GENERIC:
            m = rx.search(clean)
            if m:
                hits.append((where, i, label, line.strip()[:160]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    kitconfig.add_config_arg(ap)
    ap.add_argument('--base', default='origin/main', help='비교 기준 (기본 origin/main)')
    ap.add_argument('--include', nargs='*', default=[], help='킷으로 올리고 싶은 프로젝트 파일')
    ap.add_argument('--deny', nargs='*', default=[], help='더 검사할 고유명(사람 이름·업체명 등)')
    ap.add_argument('--no-fetch', action='store_true', help='origin 을 새로 받지 않는다(오프라인)')
    a = ap.parse_args()
    cfg = kitconfig.load(a.config)

    if not a.no_fetch:
        p = subprocess.run(['git', '-C', str(KIT), 'fetch', '-q', 'origin'], capture_output=True, text=True)
        if p.returncode:
            print('  (origin 받기 실패 — 마지막으로 받은 origin/main 과 비교한다)')
    branch = git('rev-parse', '--abbrev-ref', 'HEAD').strip()
    dirty = git('status', '--porcelain').strip()
    if dirty:
        raise SystemExit('킷 폴더에 커밋하지 않은 변경이 있다 — 먼저 local 브랜치에 커밋할 것:\n' + dirty)
    commits = git('log', '--reverse', '--format=%h %s', f'{a.base}..HEAD').strip()
    patch = git('format-patch', '--stdout', f'{a.base}..HEAD') if commits else ''
    stat = git('diff', '--stat', f'{a.base}...HEAD') if commits else ''

    notes_dir = cfg.path_of(cfg.get('outputs.root', '_산출')) / 'kit-feedback' / 'notes'
    notes = sorted(notes_dir.glob('*.md')) if notes_dir.exists() else []
    extras = [Path(x) for x in a.include]
    for x in extras:
        if not x.exists():
            raise SystemExit(f'--include 파일이 없다: {x}')
    if not (commits or notes or extras):
        print('보낼 것이 없다 — 킷 수정 커밋·실패 기록·추가 도구가 모두 비어 있다.')
        return

    stamp = dt.datetime.now().strftime('%y%m%d_%H%M')
    out = cfg.out_dir('kit-feedback', stamp)

    terms = config_terms(cfg) + [d for d in a.deny if d]
    hits = []
    added = '\n'.join(l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++'))
    # 커밋 메시지는 git log 에서 직접(패치의 Subject: 는 한글이면 =?UTF-8?…?= 로 인코딩돼 검사에 안 걸린다)
    msgs = git('log', '--format=%s%n%b', f'{a.base}..HEAD') if commits else ''
    scan(added, 'patch(추가된 줄)', terms, hits)
    scan(msgs, '커밋 메시지', terms, hits)
    for n in notes:
        scan(n.read_text(encoding='utf-8', errors='replace'), f'notes/{n.name}', terms, hits)
    for x in extras:
        if x.suffix.lower() in ('.py', '.md', '.txt', '.json', '.bat', '.ps1', '.csv', '.sql'):
            scan(x.read_text(encoding='utf-8', errors='replace'), f'extras/{x.name}', terms, hits)

    if patch:
        (out / 'changes.patch').write_text(patch, encoding='utf-8')
    if extras:
        (out / 'extras').mkdir(exist_ok=True)
        for x in extras:
            shutil.copy2(x, out / 'extras' / x.name)
    note_text = '\n\n---\n\n'.join(f'### {n.name}\n\n' + n.read_text(encoding='utf-8', errors='replace') for n in notes)

    S = [f'# 킷 피드백 — {stamp}', '',
         f'- 킷 브랜치: `{branch}` (기준 `{a.base}`)',
         f'- 킷 수정 커밋: {len(commits.splitlines()) if commits else 0}건 · 실패 기록: {len(notes)}건 · 추가 도구: {len(extras)}건', '']
    if commits:
        S += ['## 킷 수정', '', '```', commits, '```', '', '```', stat.strip(), '```', '']
    if notes:
        S += ['## 실패·개선 기록', '', note_text, '']
    if extras:
        S += ['## 킷으로 올리고 싶은 도구', ''] + [f'- `extras/{x.name}`' for x in extras] + ['']
    S += ['## 보내는 쪽 확인', '',
          '- [ ] 고유명 검사 0건(scrub.md)',
          '- [ ] 경로·레이어·필드는 kitconfig 로 읽는다(하드코딩 없음)',
          '- [ ] 원본 데이터·설정 파일·비밀번호가 들어 있지 않다',
          '- [ ] 바꾼 판정·임계값이 있으면 근거를 적었다']
    (out / 'summary.md').write_text('\n'.join(S), encoding='utf-8')

    R = [f'# 고유명 검사 — {len(hits)}건', '', f'검사 낱말 {len(terms)}개(설정에서 뽑음 + --deny) + 일반 패턴 {len(GENERIC)}종', '']
    if hits:
        R += ['| 위치 | 줄 | 걸린 것 | 내용 |', '|---|---|---|---|']
        R += [f'| {w} | {i} | {k} | `{t.replace("|", "/")}` |' for w, i, k, t in hits]
    (out / 'scrub.md').write_text('\n'.join(R), encoding='utf-8')
    zip_path = shutil.make_archive(str(out), 'zip', root_dir=out)

    print(f'꾸러미: {out}\n  zip: {zip_path}')
    print(f'  커밋 {len(commits.splitlines()) if commits else 0} · 기록 {len(notes)} · 도구 {len(extras)}')
    if hits:
        print(f'\n고유명 검출 {len(hits)}건 — 보내기 전에 일반화할 것 (scrub.md):')
        for w, i, k, t in hits[:15]:
            print(f'  {w}:{i}  {k}  | {t[:90]}')
        sys.exit(2)
    print('\n고유명 검사 통과.')


if __name__ == '__main__':
    main()
