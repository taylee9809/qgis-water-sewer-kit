# 변경 기록 · Changelog

## 2026-11-12
- DB 설계 가이드(`standard/db-design.md`)와 표준 스키마(`templates/db_schema.json`) 추가. `make_empty_db.py` 가 빈 GPKG(유일 인덱스)·PostGIS DDL(NOT NULL·CHECK·UNIQUE·GIST)·설정 파일을 만든다. 온보딩에 「DB 가 아직 없으면」 절.
- korean-engineering-mcp 를 함께 쓰는 도구로 — 30 스킬·프로젝트 CLAUDE.md 에 조문 원문 확인·인용 검증 규칙.
- README 결과물 예시 3장(고유명 가림). 설정 검사기: 위치 없는 표는 좌표계 비교 제외, 빈 레이어는 코드값 검사 생략.
- 피드백 흐름 추가: `90-kit-feedback` 스킬(`make_feedback.py` — 변경분·실패 기록 꾸러미 + 고유명 검사), 이슈 양식 3종·PR 양식, `CONTRIBUTING.md`, `MAINTAINING.md`. 설치 때 `local` 브랜치를 만들고 업데이트는 `rebase origin/main`.
- README 영어판 그림을 한글판 아래 영어판으로 둘씩 넣음.
- 첫 공개판: 스킬 00·11·12·30·32·40·51, `lib/kitconfig.py`, `standard/`, `templates/`. 11·12·32·40 은 원본 도구와 결과 일치를 확인함.
