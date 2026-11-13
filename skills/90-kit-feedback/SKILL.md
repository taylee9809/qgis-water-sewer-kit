---
name: 90-kit-feedback
description: qgis-water-sewer-kit 을 쓰는 팀의 킷 수정·사용 기록이 관리자에게 모이게 한다. 설치 때 한 번 동의하면 훅이 대화 기록에서 킷에 관한 것만 뽑아(스킬·스크립트·성공/실패·고친 파일) 고유명을 지우고 그 팀 fork 로 자동으로 올린다. 관리자는 collect_feedback.py 로 모든 팀 것을 모아 본다. 킷 공유 설정·끄기, 자동 보내기가 막혔다는 알림, "킷 고친 거 보내줘", "킷 스크립트가 안 돼", "팀들 피드백 모아줘", "다른 팀이 킷을 어떻게 쓰는지", 킷 수정·업데이트 후 정리 때 사용.
---

# 90 킷 피드백 — 팀이 고치고 쓰는 것이 관리자에게 모이게

킷은 여러 팀이 같이 쓰고, 각 팀은 자기 Claude 로 킷을 고쳐 가며 쓴다. 사람이 "보내 줘" 라고 하지 않아도
**어떻게 고쳤고 어떻게 쓰는지** 가 관리자에게 모여야 다음 판에 모두가 받는다.

킷 루트 = 이 SKILL.md 의 두 단계 위(`skills\90-kit-feedback\..\..`). 아래 `<킷>` 은 그 경로.

## 자동 수집 — 설치 때 한 번 동의

INSTALL.md 1-2절에서 Claude 가 사용자에게 아래 표를 그대로 설명하고 **예/아니오**를 받는다. 예면 `--share yes`.
```
python -X utf8 "<킷>\skills\90-kit-feedback\scripts\setup_share.py" --share yes --team <팀 별칭>
python -X utf8 "<킷>\skills\90-kit-feedback\scripts\setup_share.py" --share no        # 끄기
python -X utf8 "<킷>\skills\90-kit-feedback\scripts\kit_usage.py" status               # 무엇이 기록·전송됐나
```
| | 내용 |
|---|---|
| 기록 | 대화 기록에서 **킷에 관한 것만**: 어느 킷 스킬을 부르고 읽었나, 어느 킷 스크립트를 어떤 옵션 이름으로 돌렸나, 성공/실패와 실패의 마지막 오류 한 줄, 어느 킷 파일을 고쳤나. 프로젝트 설정의 **모양**(레이어가 gpkg/postgis/shp 중 무엇인지, 매핑한 논리 필드 이름, design 숫자) |
| 지움 | 프로젝트 경로·폴더 이름 낱말·지구/분구 값(설정에서 뽑음), 드라이브 경로·이메일·IP·비밀번호 패턴 → `<name>`·`<path>` |
| 안 보냄 | 대화 내용, 데이터·설정 파일·산출물, 옵션 값, 비밀번호 |
| 보냄 | `local` 브랜치 커밋(고유명 검사 통과분) → 그 사람 GitHub fork 의 `local`, 사용 기록 → fork 의 `usage` |
| 언제 | Claude Code 세션 시작·끝(훅). 백그라운드로 돌아 세션을 늦추지 않는다. 보내기는 10분에 한 번까지 |
| 공개 | fork 는 공개 저장소다. 위 「보냄」만 올라간다 |

- 고유명 검사에 걸린 킷 수정은 **보내지 않고** `<킷>\.kit-usage\blocked.md` 에 적는다. 다음 세션 시작 때 훅이 그 내용을 Claude 에게 넣어 준다 →
  사용자에게 한 줄로 알리고, 원하면 그 커밋을 일반화한다(이름을 설정 값으로 바꾸고, 걸린 커밋은 `git reset --soft origin/main` 후 다시 커밋해 대신한다).
- 사람 이름·업체명처럼 설정에 없는 고유명은 검사가 모른다 — 킷을 고칠 때 애초에 쓰지 않는다(평소 규칙 2).
- GitHub 계정이 없으면 `--no-fork` — 기록만 남고, 보내기는 아래 「손으로 보내기」로.
- 동의하지 않으면(`--share no`) 아무것도 기록하지 않는다. 평소 규칙과 손으로 보내기만 쓴다.

## 관리자 — 모인 것 보기
관리자 킷 작업본(main)에서:
```
python -X utf8 "<킷>\skills\90-kit-feedback\scripts\collect_feedback.py"
```
- `gh` 로 fork 목록을 읽어 각 fork 의 `local`·`usage` 를 `refs/feedback/<계정>/…` 로 받아 온다(main 은 안 건드린다).
- `<킷 옆>\<킷이름>-feedback\<YYMMDD_HHMM>\report.md`: 팀별 요약 · 스킬 사용 순위 · 스크립트 실행 · **같은 오류끼리 묶은 실패 순위** · 팀이 고친 킷 파일 · 팀별 킷 수정 커밋 · 설정 모양. `patches\<계정>.patch`.
- 받아들일 수정은 `git cherry-pick <커밋>` 또는 `git am patches\<계정>.patch` → 일반화가 필요하면 고쳐서 main 에 커밋. 실패 순위 상위는 킷 버그·문서 부족 후보.

## ★ 평소 규칙 (스킬을 부르지 않아도 늘 지킨다)

1. **킷은 `local` 브랜치에서만 고친다.** `main` 은 관리자 판 그대로 둔다. 설치 때 `local` 을 만든다(INSTALL.md).
   `git -C <킷> branch --show-current` 가 `local` 이 아니면 고치기 전에 `git -C <킷> switch local`.
2. **고칠 때마다 커밋 하나.** 메시지 `[스킬번호] 무엇을 — 왜` (예: `[40] A2 연장 허용차를 설정으로 — 서식마다 달라서`).
   커밋을 잊어도 세션 시작·끝에 훅이 `[auto]` 커밋으로 남기지만 **왜** 가 빠진다 — 관리자가 받아들이려면 이유가 필요하니 직접 커밋한다.
   고친 내용은 일반적이어야 한다: 경로·레이어·필드는 `kitconfig` 로, 이 프로젝트 이름·지구·업체를 코드나 주석에 쓰지 않는다.
3. **못 고친 실패는 기록만 한다** — `<outputs.root>\kit-feedback\notes\YYMMDD_<스킬>_<한두낱말>.md`:
   ```
   # [스킬번호] 무엇이 안 됐나
   - 명령: (킷 경로는 <킷>, 프로젝트 경로는 <프로젝트> 로 바꿔 적기)
   - 증상: 오류 메시지 마지막 몇 줄 / 기대한 것과 실제
   - 데이터 상황: 어떤 형식·규약이라 안 됐나 (예: 레이어명이 다른 설계사 규격, 종단 밴드 y 가 다름) — 지구명·좌표값은 빼고
   - 임시 우회: 했다면 무엇을
   ```
4. **두 번 이상 쓴 프로젝트 전용 스크립트**는 킷 후보다. `kitconfig` 로 일반화해 `local` 의 알맞은 스킬 폴더에 넣고 커밋하거나,
   그대로 두고 꾸러미에 `--include` 로 같이 보낸다.
5. 자동 수집을 켜지 않은 팀만: 작업을 마칠 때 `local` 에 `origin/main` 에 없는 커밋이나 실패 기록이 있으면 **"킷에 보낼까요?" 라고 한 번 묻는다**(먼저 보내지 않는다).
   켠 팀은 묻지 않는다 — 훅이 보낸다. 실패 기록·추가 도구처럼 대화 밖의 것은 아래 손으로 보내기로.

## 손으로 보내기 (자동 수집을 안 켰거나, 실패 기록·추가 도구를 보낼 때)

### 1. 꾸러미 만들기 — `scripts\make_feedback.py`
```
python -X utf8 "<킷>\skills\90-kit-feedback\scripts\make_feedback.py" --config sewer.config.json [--include <파일> ...] [--deny <이름> ...]
```
- `<outputs.root>\kit-feedback\<YYMMDD_HHMM>\` 에 `changes.patch` · `summary.md` · `scrub.md` · `extras\` 와 같은 이름 zip.
- **고유명 검사**: 이 프로젝트 설정에서 경로·용역명·지구·처리분구 값을 뽑아, 보낼 내용(추가된 줄·커밋 메시지·기록·추가 도구)에 섞였는지 본다.
  드라이브 경로·이메일·`password=`·IP·전화번호도 본다. 사람 이름·업체명처럼 설정에 없는 건 `--deny` 로 더 준다.
- 종료코드 **2 = 검출**. 그 커밋·기록을 일반화해서 고치고(커밋 수정은 `git rebase -i` 대신 새 커밋으로 덮어도 된다) **0 이 될 때까지** 다시 돌린다.

### 2. 사람에게 보여 주고 승인 받기
`summary.md` 요약(무엇을 왜 고쳤나, 몇 건)과 `scrub.md` 0건을 보여 주고 **어느 길로 보낼지** 묻는다. 승인 없이는 3으로 가지 않는다.

### 3. 보내기 — 셋 중 하나
| 길 | 조건 | 방법 |
|---|---|---|
| **PR** (권장) | 그 사람에게 GitHub 계정, `gh` 로그인 | `gh repo fork taylee9809/qgis-water-sewer-kit --remote --remote-name fork` (한 번) → `git -C <킷> push fork local:feedback/<YYMMDD>-<한두낱말>` → `gh pr create --repo taylee9809/qgis-water-sewer-kit --head <계정>:feedback/<…> --title "[스킬번호] …" --body-file <꾸러미>\summary.md` |
| **이슈** | 코드 수정 없이 실패 기록·제안만 | `gh issue create --repo taylee9809/qgis-water-sewer-kit --title "[스킬번호] …" --body-file <꾸러미>\summary.md` (양식: 안 됨 / 새 함정 / 추가 도구) |
| **파일** | GitHub 를 안 쓸 때 | 꾸러미 zip 을 관리자에게 메일·메신저로. 관리자는 `git am -k changes.patch` 로 적용한다 |

- PR 에는 **고유명 검사를 통과한 커밋만** 들어가야 한다. 검출이 남은 커밋은 빼고 보낸다.
- 보낸 뒤 PR·이슈 주소(또는 보낸 파일명)를 사용자에게 알리고, 실패 기록 파일은 `notes\보냄\` 으로 옮긴다.

## 받아들여진 뒤 맞추기
관리자가 합치면 다음 업데이트 때 같은 수정이 `main` 으로 들어온다.
```
git -C <킷> fetch origin
git -C <킷> rebase origin/main        # local 브랜치에서
```
- 이미 들어간 커밋은 rebase 가 알아서 건너뛴다. 관리자가 다르게 고쳐서 충돌이 나면 **관리자 판을 기본으로** 하고, 이 팀에 꼭 필요한 차이만 다시 커밋한다.
- 받아들여지지 않은 수정은 `local` 에 그대로 남아 이 팀은 계속 쓴다. 이유가 오면 실패 기록에 적어 둔다.

## 하지 말 것
- `main` 에 커밋하거나 `origin` 에 직접 push (권한도 없지만 시도하지 않는다)
- 설정 파일(`sewer.config.json`), 데이터(GPKG·DXF·flo), 산출물, 비밀번호를 꾸러미에 넣기
- 사람 승인 없이 PR·이슈·메일 보내기(자동 수집은 설치 때 받은 동의 범위 — 위 표 — 안에서만)
- 동의 없이 `setup_share.py --share yes` 를 돌리거나, 동의 범위 밖의 것(대화 내용·데이터)을 기록에 더하기
