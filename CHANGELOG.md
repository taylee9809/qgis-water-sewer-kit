# 변경 기록 · Changelog

## 2026-11-13
- **쓰는 방식 공유**(90-kit-feedback): 설치 때 한 번 동의하면 SessionStart·SessionEnd 훅(`kit_usage.py`)이 대화 기록에서 킷에 관한 것만 — 스킬 호출·문서 읽기, 킷 스크립트 실행(옵션 이름·성공/실패·오류 마지막 줄), 고친 킷 파일, 설정 모양 — 뽑아 고유명을 지우고 그 팀 fork 의 `usage` 브랜치로, `local` 커밋은 고유명 검사를 통과하면 fork 의 `local` 로 올린다. 걸리면 보내지 않고 다음 세션에 알린다. 설정·끄기 `setup_share.py`, 관리자 모으기 `collect_feedback.py`(팀별 사용량·같은 오류끼리 묶은 실패 순위·팀별 패치).
- 고유명 검사(`make_feedback.py`)가 프로젝트 폴더 이름을 낱말로 쪼개 킷 본판에 없는 낱말도 잡는다(전에는 폴더 이름 통째와 지구 값만 봤다).
- 30 기준값 표(`routelib.DESIGN`)와 30·31 SKILL.md 의 KDS 인용을 원문과 대조했다 — KDS 61 40 00 관로시설 2025판(2026-08-03 수정), 57 65 00 배수시설·57 70 00 급수시설 2017판, 44 90 00 도로암거구조 2021판, 61 45 00 펌프장시설 2025판. 값은 모두 원문과 같고, 절 번호를 항까지 적었다(예: 7.1② → 7.1(1)②, 1.3 → 1.3(1)③).
- `box_wall_m`(기존 BOX 저판 두께 0.30 m) 출처를 「기준값」에서 「자체 가정」으로 — KDS 44 90 00 4.5.2(1)은 신설 암거의 최소두께라 기존 구조물 두께를 정하지 않는다. 계산값은 그대로.
- 상수관 이격 0.3 m 는 상수 쪽 기준을 하수관에 준용한 것이라고 적었다. 하상 피복 1 m 에 KDS 61 40 00 6(5)(역사이펀 흙두께) 참고를 달았다.
- 압송 손실 Hazen-Williams C = 100(원 계산서 값)은 KDS 61 40 00 1.2(2)③ 표준 110 보다 손실을 약 19 % 크게 잡는다고 30·31 SKILL.md 에 적었다. 값은 그대로(`design.pump.hw_c` 로 바꿀 수 있다).

## 2026-11-12 (2)
- 스크립트 묶음 추가 — DXF 만 있는 팀도 처음부터 끝까지 돌 수 있게:
  - **10-data-intake**(새 스킬): DEM 만들기, 상수관 DXF → GPKG, 유량기준 표 적재, 가구 엑셀 내보내기·되읽기
  - **12** `asb_load.py`: 판독 결과를 표준 스키마 이름으로 준공 레이어에 적재(GPKG·PostGIS)
  - **13-plan-dxf-ingest**(새 스킬): 설계사 납품 평면 DXF(+ `.flo`) → 계획 관로·맨홀·펌프장 레이어
  - **30** `scripts/` r1~r9 + `run_all.py`: 망 → 지반고 → 교차 → 유량 배분 → 종단 → 수리 → 압송 → `.flo`·`.inp`·GPKG·DXF → 기존관 통수능, `설계요약.md`
  - **31-pump-capacity**(새 스킬): 맨홀펌프장 전양정·동력·흡수정 저류, 용량계산서 엑셀
  - **50-profile-sheets**(새 스킬): 설계사 종단 DXF → A3 도면집 PDF(리명 제목), 계획 노선에서 종단 새로 그리기
  - **51** `scripts/`: 헤드리스 조판 실행·내보내기, 검토 삽도 빌드 예시
- `lib/flospec.py`(makesw `.flo` 읽기·검증·쓰기) 공용 모듈로.
- 설정 예시: `route_design`·`profile`·`plan_ingest`·`design.pump` 절, 레이어 `survey_point`·`river`·`admin_ri`, 값 `house_existing`·`kind_pump_mh` 등. 표준 스키마: 점유량 표, 유량기준 `구분`, 노선·접속맨홀 필드, 준공 펌프장 제원 24필드.
- 설정 검사기가 SHP·GeoJSON 레이어도 읽는다(SHP 는 `.cpg` 없으면 cp949).
- 모든 새 스크립트는 우리 원 스크립트와 같은 입력으로 결과가 같은지 대조했다(README 「검증 범위」).

## 2026-11-12
- DB 설계 가이드(`standard/db-design.md`)와 표준 스키마(`templates/db_schema.json`) 추가. `make_empty_db.py` 가 빈 GPKG(유일 인덱스)·PostGIS DDL(NOT NULL·CHECK·UNIQUE·GIST)·설정 파일을 만든다. 온보딩에 「DB 가 아직 없으면」 절.
- korean-engineering-mcp(원본 sonmeggy/korean-engineering-mcp, 공개)를 함께 쓰는 도구로 — 30 스킬·프로젝트 CLAUDE.md 에 조문 원문 확인·인용 검증 규칙.
- README 결과물 예시 3장(고유명 가림). 설정 검사기: 위치 없는 표는 좌표계 비교 제외, 빈 레이어는 코드값 검사 생략.
- 피드백 흐름 추가: `90-kit-feedback` 스킬(`make_feedback.py` — 변경분·실패 기록 꾸러미 + 고유명 검사), 이슈 양식 3종·PR 양식, `CONTRIBUTING.md`, `MAINTAINING.md`. 설치 때 `local` 브랜치를 만들고 업데이트는 `rebase origin/main`.
- README 영어판 그림을 한글판 아래 영어판으로 둘씩 넣음.
- 첫 공개판: 스킬 00·11·12·30·32·40·51, `lib/kitconfig.py`, `standard/`, `templates/`. 11·12·32·40 은 원본 도구와 결과 일치를 확인함.
