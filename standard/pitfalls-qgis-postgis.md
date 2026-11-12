# 함정 — QGIS · PostGIS · GPKG · 파이썬 환경

모든 스킬이 공통으로 참조한다. **새 함정을 만나면 여기에 한 줄 추가한다**(원리만, 회차 수치는 빼고).

## ★ 관로 도형 방향
- 관로 도형은 **상류 → 하류** 가 표준이다. 그런데 기존 자료에는 **반대로 들어간 것이 곳곳에** 있다.
- 끝점으로 맨홀을 자동 배정하면 오배치된다 → **인접 관로와의 공유 끝점 + DEM/지반고 추이**로 교차검증.
- **체인 번호 뒤바뀜**: 정점은 맞는데 번호·분할점만 반대인 경우. 종단 지반고 추이와 도형 방향의 DEM 추이가 반대면 뒤바뀐 것. 분할 길이 수열(예 15/43/19)이 어느 끝에서 맞는지로 확정.

## PostGIS
- 접속 후 `set_client_encoding('UTF8')` 필수 — 안 하면 한글 컬럼명 쿼리가 깨진다(kitconfig 가 해 준다).
- 한글·특수문자 컬럼은 큰따옴표: `"자연유하/압송"`, `"관번호"`.
- **fid 시퀀스가 max(fid) 보다 뒤처져** INSERT 가 PK 충돌로 실패하는 테이블이 있다 → `select setval(pg_get_serial_sequence('<t>','fid'), (select max(fid) from <t>));` 선행.
- 고치기 전 백업: `CREATE TABLE <t>_bak_YYMMDD AS TABLE <t>;`
- 대용량 유니온·`ST_DWithin` 은 `SET statement_timeout` 을 걸고 구역 단위로 나눠 돌린다(전체 유니온이 QGIS 연결을 죽인 적 있음).
- PostGIS 레이어에 없는 컬럼을 QGIS 에서 편집하면 커밋이 실패한다 → 먼저 `ALTER TABLE … ADD COLUMN`.
- **비밀번호는 코드·설정 파일에 적지 않는다.** 환경변수(`db.postgis.password_env`)로.

## GPKG
- GPKG 기본키 `fid` 와 같은 이름의 일반 컬럼이 있으면 UNIQUE 오류 → 다른 이름(`노선fid` 등)으로 바꿔 저장.
- QGIS 기본 저장이 fid 충돌로 실패하면: 피처를 읽어 geopandas 로 새 GPKG 를 쓰고 레이어를 다시 붙인다(같은 GPKG 를 참조하는 레이어는 전부 먼저 제거).
- GPKG 를 **덮어쓰기 전에 QGIS 에서 그 레이어를 `removeMapLayer`** — 파일 잠금. 사용자가 만든 심볼·라벨은 `saveNamedStyle` 로 QML 을 저장해 두었다가 다시 붙인 뒤 `loadNamedStyle`.
- rtree 트리거 때문에 순수 sqlite3 `UPDATE` 가 막힌다 → SQL 에 `AFTER UPDATE ON` 이 들어간 트리거를 찾아 잠시 DROP 후 복원(이름으로 찾지 말 것).
- **같은 커서로 select 돌면서 update 하면 첫 행만 반영**된다 → 먼저 `list()` 로 받는다.
- fiona append 는 스키마의 전 필드를 요구한다(부분 dict 거부).
- 속성만 바꾸는 수정은 PyQGIS `changeAttributeValue` + `commitChanges()` 가 가장 안전하다.

## PyQGIS · QGIS MCP
- 레이어 지오메트리가 Point 가 아닐 수 있다(맨홀이 폴리곤인 레이어 있음) → `asPoint()` 대신 `centroid()`.
- PostGIS 레이어 수정 후 `dataProvider().reloadData()` + `triggerRepaint()`.
- **QGIS MCP 로 고해상(150 dpi 이상) 조판 내보내기를 하면 QGIS 가 죽는다** → `python-qgis-ltr.bat` 헤드리스 스크립트로 출력. 헤드리스에서 `QT_QPA_PLATFORM=offscreen` 을 쓰면 한글 폰트가 깨지니 쓰지 않는다.
- **VWorld 등 XYZ 타일이 켜진 상태로 MCP `render_map` 을 부르면 응답이 멈춘다** → 위성 확인은 타일을 직접 받아 그린다.
- MCP 연결이 끊기면 사용자에게 플러그인 재시작을 요청하고, 그동안은 GPKG/PostGIS 를 직접 읽어 진행.
- 조판 한 장에만 스타일을 바꿀 때는 `setKeepLayerSet(True)` + **`setKeepLayerStyles(True)`** (안 켜면 재시작 시 사라진다).

## 프로젝트 배포(외부 전달)
- 원본이 상대경로 모드면 **원본 qgz 를 제자리에서 read → 소스 교체 → `write(배포경로)`** 순서. 배포 폴더로 먼저 복사해서 열면 경로가 전부 깨진다.
- `setDataSource()` 는 상대경로를 프로세스 CWD 기준으로 푼다 → 절대경로로 바꾸고 저장 전 `writeEntry('Paths','/Absolute', False)`.
- 커스텀 SVG 심볼·그림 폴더를 같이 묶는다(빠지면 받는 쪽에서 물음표 심볼).
- 검증: 절대경로 0건 · `dbname=` 0건 · `password` 0건 · 재로드 시 전 레이어 유효.

## 파이썬 환경
- **rasterio/pyproj 가 QGIS·PostGIS 가 깐 `PROJ_DATA` 와 충돌**한다 → import 전에 `kitconfig.fix_proj()`. DEM 샘플링은 QGIS `provider.sample()` 을 쓰면 충돌이 없다.
- `python -I` (격리 모드)는 user site-packages 를 안 봐서 fiona 등이 import 안 될 수 있고, 환경변수도 무시한다 → 킷 스크립트는 `python -X utf8` 로.
- 파라미터 스윕은 **환경변수/인자 + subprocess** 로. 파일을 치환해 가며 돌리면 크래시 때 중간값이 남아 결과가 계속 같게 나온다.
- 셸 heredoc 안에 파이썬을 넣으면 백슬래시 이스케이프가 접힌다 → 스크립트 파일로 쓰고 실행.

## 수치 판정
- 관저고를 mm(소수 3자리)로 반올림하면 구배가 4.98~4.99‰ 로 찍혀 "5‰ 미만" 오판정이 난다 → 경사 판정에 **±0.02‰ 허용**.
- 맨홀 노드에서만 토피를 보면 맨홀 사이 지반 요철을 놓친다 → **구간 중간 절점까지** 토피를 검사한다(30 스킬 참고).
