# 하수관로 설계 프로젝트 — Claude 작업 지침

이 폴더는 qgis-water-sewer-kit 로 일하는 하수관로 설계 프로젝트다. 답변은 한국어, 설명은 짧게 핵심만.

## 킷
- 킷 위치: `<킷 경로>` ← 00-onboard 가 실제 경로로 바꾼다
- 설정: 이 폴더의 `sewer.config.json` — 레이어·필드·DB·기준값은 **전부 여기서** 읽는다
- 표준: `<킷>\standard\data-standard.md`(논리 레이어·필드) · `workflow.md`(단계 00~80) · `pitfalls-dxf.md` · `pitfalls-qgis-postgis.md`
- 스킬: 00-onboard · 10-data-intake · 11-survey-to-qgis · 12-asbuilt-ingest · 13-plan-dxf-ingest · 30-route-design · 31-pump-capacity · 32-route-optimize · 40-review-vendor · 50-profile-sheets · 51-layout-figure · 90-kit-feedback

## 일하는 방식
1. 요청이 오면 **어느 단계(00~80)인지** 먼저 정하고, 스킬이 있으면 그 스킬대로 한다. 없으면 `workflow.md` 의 「스킬 없는 단계」대로.
2. **원본 자료는 읽기 전용.** 산출물은 `sewer.config.json` 의 `outputs.root` 아래에만 쓴다.
3. DB·GPKG 를 고치기 전에 **백업**(`<테이블>_bak_YYMMDD`, GPKG 사본)부터. 고친 뒤 무엇을 몇 건 바꿨는지 보고.
4. 이 프로젝트용 스크립트는 `scripts\` 에 둔다. 경로·레이어명·필드명을 하드코딩하지 말고 킷 `lib\kitconfig.py` 로 읽는다.
5. **비밀번호·접속키는 코드·설정·대화에 적지 않는다.** 환경변수로.
6. 수치를 보고할 때 **기준값(KDS·조례·과업 기준)과 자체 가정을 구분**한다. 임계값을 바꿨으면 숨기지 않는다.
   조문은 기억으로 인용하지 않는다 — korean-engineering-mcp 가 있으면 원문(`get_standard_detail`)을 확인하고 `verify_citations` 를 거친다.
7. 사람이 판단할 애매한 건은 "오류 0" 으로 덮지 말고 **확인 목록**(3~5건)으로 올린다. 자료가 없으면 "0건" 이 아니라 "미조사".
8. 기준값을 바꾸거나 노선·대안을 정하면 `결정기록.md` 에 날짜·결정·근거를 한 줄로 남긴다.
9. 셸 heredoc 안에 파이썬을 길게 넣지 말고 스크립트 파일로 쓴다. 실행은 `python -X utf8`.

## 킷을 고칠 때 (90-kit-feedback)
- 킷은 **`local` 브랜치에서만** 고친다. `main` 은 관리자 판 그대로 둔다. 고칠 때마다 커밋 하나: `[스킬번호] 무엇을 — 왜`.
- 고친 내용은 일반적이어야 한다 — 이 프로젝트의 지구·경로·업체 이름을 킷 코드·주석·커밋 메시지에 쓰지 않는다.
- 못 고친 실패는 `<outputs.root>\kit-feedback\notes\` 에 기록만 남긴다(양식은 90-kit-feedback 스킬).
- 새 함정은 킷 함정 노트에, 두 번 쓴 프로젝트 스크립트는 킷 스킬로 올릴 후보다 — `local` 에 넣거나 꾸러미에 같이 보낸다.
- 작업을 마칠 때 `local` 에 `origin/main` 에 없는 커밋이나 실패 기록이 있으면 **"킷에 보낼까요?" 라고 한 번 묻는다.** 승인 없이 보내지 않는다.
- 킷 업데이트: `git -C <킷> fetch origin` → `local` 에서 `git -C <킷> rebase origin/main`. 충돌하면 관리자 판을 기본으로.

## 이 프로젝트
<!-- 00-onboard 가 채운다 -->
- 용역명:
- 범위·지구:
- 좌표계 / 표고 기준:
- 관번호 규약:
- 설계기준(KDS 기본과 다른 점):
- 외주 설계사 납품 폴더 규칙:
- 데이터 품질 메모(온보딩 검사 ⚠):
- 담당자에게 확인할 것:
