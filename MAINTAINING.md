# 유지보수 절차 · Maintaining

관리자가 들어온 이슈·PR·꾸러미를 흡수하는 절차. 관리자 쪽 Claude 도 이 순서로 검토한다.

## 1. 모으기 (주 1회 또는 알림 때)
```
gh issue list --repo taylee9809/qgis-water-sewer-kit --state open
gh pr list    --repo taylee9809/qgis-water-sewer-kit --state open
```
파일로 받은 꾸러미는 `summary.md` · `scrub.md` 부터 읽고, 코드는 검토용 브랜치에서 `git am -k changes.patch` 로 적용한다
(`-k` 가 없으면 커밋 제목 앞 `[스킬번호]` 가 떨어진다).

## 2. 검토 기준
| 항목 | 보는 법 |
|---|---|
| 고유명·비밀번호 | 변경분을 그 팀 지구명·경로·업체명·`password` 로 훑는다. 꾸러미면 `scrub.md` 0건 확인 |
| 설정 배관 | 경로·레이어·필드를 `kitconfig` 로 읽는가. 새 설정 키는 예시 설정·데이터 표준에 적었는가 |
| 판정 변경 | 임계값·판독 규약을 바꿨다면 근거가 있는가. 한 팀 도면 규격에만 맞춘 변경은 **설정 값**으로 돌린다 |
| 회귀 | 기존 결과가 그대로인가 — 아래 3 |
| 문서 | SKILL.md·README(한·영)·함정 노트가 바뀐 동작과 맞는가 |

## 3. 회귀 검증
관리자 PC 에는 **저장소 밖에 비공개 회귀 검증 도구**가 있다(실제 프로젝트 데이터로 도는 명령 묶음 + 기준본).
PR 을 작업본으로 받아 그 도구를 그 작업본에 돌린다.
```
git -C <킷> fetch origin pull/<번호>/head:pr-<번호>
git -C <킷> worktree add <임시폴더> pr-<번호>
python -X utf8 <회귀도구>\regress.py --kit <임시폴더>
```
- **모두 통과** → 기존 동작이 그대로다.
- **DIFF** → 의도한 변화인지 본다. 의도한 개선이면 합친 뒤 `regress.py --save` 로 기준본을 새로 저장하고, 무엇이 왜 바뀌었는지 CHANGELOG 에 적는다.
- 새 스킬·도구는 회귀 명령 묶음에 케이스를 하나 더한다(읽기 전용 명령만).

## 4. 합치기
- 합치기 전에 고유명 검사를 한 번 더(공개 저장소). 커밋 메시지도 본다.
- 합친 뒤 `CHANGELOG.md` 에 한 줄: 날짜 · 스킬 · 무엇이 바뀌었나 · 보낸 팀(팀 이름만, 원하면 생략).
- 이슈는 닫으면서 어느 커밋에 들어갔는지 남긴다. 받아들이지 않은 제안은 이유를 남긴다 — 그 팀은 자기 `local` 브랜치로 계속 쓴다.

---

## English (summary)
Collect open issues/PRs (or bundle zips), review them against the table above (no project names or passwords, config via `kitconfig`, justified threshold changes, docs updated), run the maintainer's **private regression harness** (kept outside this repo, runs on real project data) against the PR worktree, merge, re-save the baseline only for intended changes, and add a line to `CHANGELOG.md`.
