---
name: 10-data-intake
description: 설계 전에 쓸 기초자료를 프로젝트 DB 로 들여온다 — 측량 표고점으로 1 m DEM 만들기(거친 DEM 에 실측 덮어쓴 사본 포함), 지자체 상수관망도 DXF → 상수관로 GPKG, 기본계획 유량표(엑셀) → 유량기준 표, 배수설비 가구 반영/제외를 엑셀로 주고받기. 도로·건물·하천·교량·지적·DEM 같은 공공자료를 어디서 받는지와 받은 뒤 설정(layers·dem)에 적는 법도 안내한다. 기초자료 들이기, DEM 만들기, 상수관망 넣기, 유량기준 넣기, 배수설비 가구 정리, 공공자료 어디서 받나 요청 시 사용.
---

# 10 기초자료 들이기

- 킷 루트: 이 SKILL.md 의 두 단계 위 폴더(`skills\10-data-intake\..\..`). 아래 `<킷>` 은 킷 루트.
- 설정: 프로젝트 루트 `sewer.config.json` (없으면 **00-onboard** 먼저 — DB 가 없으면 `make_empty_db.py` 로 빈 표준 DB 부터).
- 원본(DXF·엑셀·받은 공공자료)은 **읽기만** 한다. 산출은 `<outputs.root>\10-intake\` (기본 `_산출\10-intake\`).
- 모든 스크립트는 **기본이 보고만**이다. 결과를 보고 `--write` 를 붙여야 파일·DB 에 쓴다. 쓰기 전에 대상을 백업한다(`_bak_YYMMDD`).
- 공공자료는 **들여오기만 하고 고치지 않는다**(`standard/db-design.md`). 고칠 일이 생기면 프로젝트 레이어로 복사해서 고친다.

| | |
|---|---|
| 선행 | 00-onboard (설정·빈 DB) |
| 후행 | 11 측량 → QGIS · 12 준공도 → QGIS · 30 관로 설계(DEM·상수관·유량기준) |
| 산출 | DEM(GeoTIFF) · 상수관로 GPKG · 유량기준 표 행 · 가구 반영관리 엑셀 · 맨홀별 자체유량 CSV |

## 1. 측량 표고점 → 1 m DEM — `build_dem.py`

```
python -X utf8 "<킷>\skills\10-data-intake\scripts\build_dem.py" --points <표고점.gpkg> --z-field elev --filter "outlier == 0"
python -X utf8 ...build_dem.py ...(같은 인자)... --write [--merge-into data\DEM_5m.tif]
```
- 점 입력은 셋 중 하나: `--layer-key <설정 layers 키>`(PostGIS·GPKG) · `--points`(GPKG·SHP·CSV) ·
  `--dxf <측량현황도>`(표고점 POINT `--point-layer` + 옆 표고 글자 `--height-layer`, 2 m 안 최근접).
- 계산: 같은 좌표 중복 제거 → Delaunay TIN 선형보간 → **점에서 30 m(`--maxd`) 넘는 셀은 비움** → 1024 블록 단위로 쓴다.
- `--merge-into <거친 DEM>`: 거친 DEM(5 m 등)의 **사본**에 1 m 5×5 평균(유효 13셀 이상)을 덮어쓴 `<이름>_실측반영.tif` 를 낸다. 원본 거친 DEM 은 그대로.
- **이상치는 걸러 주지 않는다.** DXF 표고 글자는 오기·관저고 혼입이 섞인다 — 결과 DEM 을 등고선으로 보고, 튀는 점은 속성(예: `outlier`)으로 표시해 `--filter` 로 뺀다.
- 도면 좌표가 프로젝트 좌표와 다르면 `--offset DX DY`.
- 만든 뒤: `"dem": ["<1 m>", "<실측반영 5 m 또는 원래 5 m>"]` — 앞의 것이 우선이다(1 m 가 비면 5 m 로 내려감).

## 2. 상수관망도 DXF → 상수관로 — `water_dxf_to_gpkg.py`

```
python -X utf8 "<킷>\skills\10-data-intake\scripts\water_dxf_to_gpkg.py" --dxf "<상수도 관망도.dxf>"          → 매칭 통계만
python -X utf8 ...water_dxf_to_gpkg.py --dxf "<상수도 관망도.dxf>" --write                               → _산출\10-intake\상수관망.gpkg
```
- 관로 = LWPOLYLINE·LINE, 레이어 이름 `시설코드(D관경)_사업명`(`--layer-re`)에서 시설코드·관경·사업명을 읽는다.
- 라벨 `연도/관종/관경/L연장/D토피`(`--label-layer`·`--label-re`)를 반경 40 m 안 관로에 붙인다 —
  점수 = 연장차×2 + 거리×0.5 + (관경 다르면 25), 점수순으로 라벨 하나에 관로 하나.
- 라벨에 토피가 없으면 토피 글자 레이어(`--depth-prefix`·`--depth-layer`)의 숫자를 관 중점 15 m 안에서 → `토피_GIS`.
- 상태 = 레이어 이름의 `폐관`·`삭제`, 아니면 `사용`. 구분 = 시설코드 표(`--code-map`, 기본은 배수관·급수관·도수/송수관 …).
- **지자체마다 레이어·라벨 형식이 다르다.** 먼저 보고만 돌려 「라벨 매칭: 배정 / 후보없음 / 경합탈락」과 「연장 오차」를 보고
  정규식을 맞춘다(설정 `intake.water.{layer_re,label_layer,label_re,depth_prefix,depth_layer,code_map,status_words}` 에 적어 두면 인자 없이 돈다).
- 들인 뒤: `layers.water_pipe` 와 `fields.water_pipe = {"dia_mm": "관경", "material": "관종", "status": "상태", "cover": "토피_GIS"}`.
  토피는 라벨(`토피_라벨`)이 GIS 글자보다 우선이다 — 이격 검토에서 둘 다 없으면 "토피 미상"으로 다룬다.

## 3. 기본계획 유량표 → 유량기준 — `flow_basis_load.py`

```
python -X utf8 "<킷>\skills\10-data-intake\scripts\flow_basis_load.py" --xlsx "<유량 엑셀>" --sheet <시트>                    → 미리보기 CSV
python -X utf8 ...flow_basis_load.py --xlsx "<유량 엑셀>" --sheet <시트> --target-year <목표연도> --write                     → layers.flow_basis
```
- **유량기준 표가 단일 원천**이다. 수리계산은 이 표만 읽고, 엑셀을 고쳤으면 이 스크립트를 다시 돌린다(엑셀 사본을 원천으로 쓰지 않는다).
- 읽는 법: 분구 열(병합 셀 → 위 값 잇기) · 구분 열(전체·기존·계획) 행 = 분구 유량. `비고` 행 아래 = 점유량 표.
- 열 → 표준 필드(`--col 필드=열+열`): 기본 `일최대_m3d=C+D+E`(생활+지하수사용+연계) · `시간최대_m3d=J`(수리계산유량) · `지하수_m3d=G`.
  **서식이 다르면 미리보기 CSV(읽은 열 값이 같이 찍힘)를 보고 `--col` 을 맞춘다.**
- 처리분구 이름: 전체 행 = 분구, 기존·계획 행 = `분구(기존)`·`분구(계획)` (표준 표에 구분 열이 없어서). 처리구역 레이어의 처리분구 값과 맞춰 둔다.
- 근거 = `파일명 시트 N행 (md5 앞 8자리)` — 같은 원본이면 다시 넣지 않는다. 같은 (처리분구, 목표연도)가 있으면 멈추고 `--replace` 로 바꾼다.
- 점유량 표(특정 시설의 시간최대)는 표준 표에 자리가 없어 `유량기준_점유량.csv` 로만 낸다 — 배정 맨홀은 사람이 정한다.

## 4. 배수설비 가구 반영/제외 — `house_xlsx_export.py` → 사람 편집 → `house_xlsx_import.py`

```
python -X utf8 "<킷>\skills\10-data-intake\scripts\house_xlsx_export.py"                         → _산출\10-intake\배수설비가구_반영관리_YYMMDD.xlsx
(엑셀 노란 칸 편집: 반영여부 포함/제외 · 제외사유 · 점유량_m3d · 배정맨홀)
python -X utf8 ...house_xlsx_import.py <그 엑셀>            → 바뀔 값·맨홀별 자체유량 CSV (보고만)
python -X utf8 ...house_xlsx_import.py <그 엑셀> --write    → layers.house 갱신
```
- 원본(마스터)은 레이어, 엑셀은 편집 창구. **fid 로 되맞추고 가구 이름이 다르면 그 행은 건너뛴다**(안전장치).
- 관리 필드 `반영여부`·`제외사유`·`검토일`·`점유량_m3d` 가 레이어에 없으면 import `--write` 때 더한다. 바뀐 가구만 fid 로 갱신한다(도형·다른 필드는 그대로).
- 맨홀별 자체유량 = 포함 가구의 (점유량 있으면 그것, 없으면 Q) 를 배정맨홀별로 합친 것 → 30 수리계산 입력.
- 필드: `fields.house.{house_id, district, new_or_old, q_m3d, assigned_mh}`. 엑셀 열 순서는 `--cols`, 요약 묶음은 `--group-by`.

## 5. 공공자료 — 어디서 받나

국내 공공 공간정보는 대부분 아래 세 곳에서 받는다. **데이터 이름·제공 방식(무료 내려받기 / 신청·승인)·갱신 주기는 자주 바뀌니
받기 전에 사이트에서 확인**한다. 좌표계도 자료마다 다르다(EPSG:5174·5179·5186 등) — 들여오기 전에 확인하고 `project.crs` 로 변환한다.
SHP 한글 속성은 대개 cp949 다.

| 자료 | 킷 논리 키 | 받는 곳 | 비고 |
|---|---|---|---|
| 도로 경계·중심선 | `road_boundary` | 국토지리정보원 **국토정보플랫폼**(map.ngii.go.kr)의 **수치지형도**(1:1,000·1:5,000) | 도로 경계는 면 레이어로 만들어 둔다. 도로명주소 전자지도(도로명주소 개발자센터)의 도로구간·실폭도로도 쓸 수 있다 — 레이어 구성은 확인 필요 |
| 건물 | (선택) | 수치지형도 건물 레이어 · **브이월드**(vworld.kr) / **국가공간정보포털**(nsdi.go.kr) 의 건물 자료(GIS건물통합정보) | 배수설비 가구 위치를 잡을 때 |
| 하천 | (선택) | 수치지형도 하천 레이어 | 하천구역·하천기본계획 선형은 하천 관리청 자료 — 출처 확인 필요 |
| 교량 | `bridge` | 수치지형도 교량 레이어 | 관 매달기 구간 판정 |
| 연속지적도 | `parcel` | 브이월드 / 국가공간정보포털 의 **연속지적도** | 지번 필드(끝 글자 = 지목)를 `fields.parcel.jibun` 에. 토지대장 면적과 도형 면적은 다를 수 있다(공유 필지는 대장에 지분 면적이 붙기도 함) — 면적은 출처를 밝혀 쓴다 |
| DEM | `dem` | 국토정보플랫폼 **수치표고모델** — 제공 해상도·신청 절차 확인 필요. 없으면 수치지형도 등고선·표고점을 점으로 뽑아 `build_dem.py --points` 로 TIN | 노선 구간은 **측량 표고점 1 m DEM 을 앞에** 둔다 |
| 상수관망 | `water_pipe` | **지자체 상수도 부서**에 공문으로 도면(DXF·SHP) 요청 | 상수도 지형지물 코드(시설코드) 체계 도면이면 2절 기본값으로 돈다 |
| 처리구역·계획하수량 | `service_area` · `flow_basis` | 그 시·군 **하수도정비기본계획**(보고서·도면) — 발주처에 요청 | 처리분구별 계획하수량 표를 엑셀로 옮겨 3절 |
| 측량 표고점 | (`build_dem.py` 입력) | 측량사 성과(측량현황도 DXF) | 11 스킬과 같은 원도 |

- 브이월드 오픈API 를 쓸 때는 각자 인증키를 받는다(키는 설정 파일에 적지 않는다 — 환경변수).
- 받은 원본은 `기초자료\공공자료\<자료명>_<기준일>\` 처럼 **기준일을 붙여** 보관하고, 프로젝트 레이어의 `출처` 에 같은 문구를 남긴다.

## 6. 들인 뒤 설정에 적기

들인 레이어는 확인한 뒤 `data\` 같은 프로젝트 자료 폴더로 옮겨(산출 폴더는 다시 돌리면 바뀐다) 설정에 적는다.
```json
"layers": {
  "water_pipe":    {"src": "data/상수관망.gpkg", "layer": "상수관로"},
  "road_boundary": {"src": "data/현황.gpkg",     "layer": "도로경계_면"},
  "bridge":        {"src": "data/현황.gpkg",     "layer": "교량"},
  "parcel":        {"src": "data/현황.gpkg",     "layer": "연속지적"},
  "survey_point":  {"src": "data/측량.gpkg",     "layer": "표고점"}
},
"fields": {
  "water_pipe":   {"dia_mm": "관경", "material": "관종", "status": "상태", "cover": "토피_GIS"},
  "parcel":       {"jibun": "JIBUN"},
  "survey_point": {"elev": "elev"}
},
"dem": ["data/DEM_실측_1m.tif", "data/DEM_5m_실측반영.tif"]
```
- `survey_point` 는 `build_dem.py --layer-key survey_point` 용이다(다른 스킬은 안 쓴다).
- 적은 뒤 `<킷>\skills\00-onboard\scripts\check_config.py` 로 경로·필드가 맞는지 본다.

## 결과를 믿기 전에

- **보고만 단계의 숫자를 먼저 본다** — DEM 점 수·범위, 상수관 라벨 매칭률·연장 오차, 유량 미리보기 CSV 의 읽은 열 값, 가구 변경 건수.
- DEM 은 점에서 30 m 밖이 비어 있는 게 정상이다(거기는 5 m 로 내려간다). 비어 있어야 할 곳이 채워졌거나 등고선이 튀면 표고점 이상치다.
- 상수관 `토피_GIS` 는 글자를 가까운 관에 붙인 **추정**이다 — 이격 판정이 아슬아슬하면 원도에서 다시 본다.
- 유량기준 표의 수치는 기본계획 값이다. 과업에서 바꾼 값(협의·재산정)이면 `근거` 에 그 근거를 쓰고 결정기록을 남긴다.
