# 데이터 표준 — 논리 레이어·논리 필드

킷의 스크립트와 스킬은 실제 레이어 이름·필드 이름을 모른다. **논리 이름**만 알고, 실제 이름은
프로젝트의 `sewer.config.json` 의 `layers` / `fields` / `values` 에서 찾는다.
그래서 그 팀 데이터가 우리와 이름이 달라도 설정만 맞추면 그대로 돈다.

이 문서는 논리 이름의 정의서다. 00-onboard 스킬은 그 팀 데이터를 조사해 이 표와 대응시킨다.

## 공통 규칙

| 항목 | 규칙 |
|---|---|
| 좌표계 | 프로젝트 전체 하나(`project.crs`). 국내는 보통 EPSG:5186. 다른 좌표계 레이어는 변환해서 넣는다 |
| **관로 방향** | **상류 → 하류**. 역방향 도형은 판정·종단이 전부 틀어진다 — 온보딩 때 표본 검사 |
| 관번호 | **관번호 = 상류 맨홀 번호**(우리 규약). 다른 규약이면 온보딩 때 기록하고 스크립트가 그 규약을 따르게 한다 |
| 단위 | 길이·표고 m, 관경 mm, 경사 ‰, 유량 ㎥/일(`_m3d`) 또는 ㎥/분(`_m3min`), 유속 m/s |
| 표고 기준 | 지반고·관저고는 같은 표고 기준(EL.). 준공도 표고와 DEM 기준이 다르면 기록 |
| 추적성 | 계산·적재한 레이어에는 `자료기준일`·`출처` 를 둔다. 값이 언제 어디서 왔는지 물을 때 답이 된다 |
| 회차 | 회차별 재계산 결과는 `금회_` 접두 필드, 변경 이력은 `변경_YYMMDD` 필드로 남기는 것을 권장(덮어쓰지 않기) |

## 계획(설계) 레이어

| 논리 키 | 기하 | 필수 논리 필드 | 선택 논리 필드 | 쓰는 스킬 |
|---|---|---|---|---|
| `plan_pipe` 계획관로 | Line | `pipe_id` `dia_mm` `us_invert` `ds_invert` `kind` | `district` `area` `length_m` `us_ground` `slope_permil` `us_cover` `flow_m3d` `velocity_mps` `judge` `obstacles` `asof` `source` | 30 · 32 · 40 · 종단도 플러그인 |
| `plan_manhole` 계획맨홀 | Point 또는 Polygon | `mh_id` `ground` `out_invert` | `district` `kind` `mh_type` `depth` `drop` `asof` `connect_to` | 30 · 40 |
| `plan_pump` 맨홀펌프장 | Point | `pump_id` `in_invert` | `district` `kind` `discharge_to` `flow_m3d` `q_m3min` `head_m` `power_kw` `force_main_m` | 30 · 31 |
| `house` 배수설비 가구 | Point | `house_id` `q_m3d` | `district` `new_or_old` `assigned_mh` | 30(유량 배분) |

- `kind` 의 값은 `values.kind_gravity`(자연유하) / `values.kind_pressure`(압송).
- `obstacles`(종단 지장물) 문자열 형식: `종류 D규격@측점(저면EL)` 을 ` / ` 로 이음. 예: `횡배수관 D600@13.3(74.70) / BOX D2.00X1.00@3.3(25.50)` — 측점은 관 시점부터 m. 원형관은 저면EL+0.30 을 관저로 본다(makesw 규약).

## 준공(기존) 레이어 — 12 스킬이 만든다

| 논리 키 | 기하 | 필수 논리 필드 | 선택 논리 필드 |
|---|---|---|---|
| `asbuilt_pipe` 준공 관로 | Line | `pipe_id` `dia_mm` `us_invert` `ds_invert` `status` | `district` `flow_kind` `length_m` `us_mh` `ds_mh` `us_ground` `ds_ground` `slope` `material` `source` |
| `asbuilt_manhole` 준공 맨홀 | Point | `mh_id` `ground` `depth` | `district` `status` `source` |
| `asbuilt_pump` 준공 펌프장 | Point | 펌프장명 · 유입관저고 · 펌프 제원 | (스킬 문서 참고) |
| `asbuilt_service_pipe` 배수설비관 | Line | 관경 · 접속맨홀 | |
| `asbuilt_inlet` 오수받이 | Point | 구분 | |
| `asbuilt_house` 준공 배수설비 가옥 | Point | 가옥번호 · 접속맨홀 | |
| `asbuilt_house_pump` 자가펌프 | Point | 구분 · 가옥번호 | |

- `status` 값: `values.status_new`(신설) / `status_reuse`(기존활용) / `status_abandon`(기존폐쇄). **기존폐쇄도 넣어 둔다** — 신설 노선과의 간섭·철거 검토에 쓴다.
- 관저고 관계: `지반고 − 맨홀깊이 = 관저고` 가 준공 관로 IL 과 맞물려야 한다(12 스킬 합격 기준).

## 현황·제약 레이어

| 논리 키 | 기하 | 쓰임 |
|---|---|---|
| `obstacle_poly` / `obstacle_line` 지장물 | Polygon / Line | 신설관 교차·하월 검토. **11 스킬 출력 스키마** 사용(아래) |
| `obstacle_review` 지장물 확인필요 | Polygon | 근거가 약한 지장물(단선버퍼·추정폭) — 사람이 확인할 목록 |
| `water_pipe` 상수관 | Line | 이격(0.3 m) 검토. 필드 `dia_mm` `material` `status` `cover` |
| `service_area` 처리구역 | Polygon | 대상 범위·분구별 유량. `subarea` `in_scope` `existing_or_plan` |
| `road_boundary` 도로경계면 | Polygon | 부설 통로·터파기 폭 검토 |
| `bridge` 교량 | Polygon | 관매달기(교량 부착) 구간 |
| `parcel` 지적 | Polygon | 사유지 통과·구거 필지(지목 구/천/유) 검증 |
| DEM (`dem` 목록) | Raster | 지반고. 1 m 실측 우선, 없으면 5 m |

## 11 스킬 출력 스키마 (지장물)

11 스킬이 측량현황도에서 **새로 만드는** 레이어는 아래 필드를 쓴다. 이 이름은 킷 표준이라 바꾸지 않는다.

| 필드 | 뜻 |
|---|---|
| `지장물번호` | 킷이 매기는 고유번호 |
| `분류` / `종류` | 기타지장물 … / 원형관·박스·측구·미상·면형(확인필요) … |
| `표기` | 도면 원문 규격 글자 |
| `규격_폭m` / `규격_깊이m` / `연수` / `관경mm` | 표기에서 해석한 제원(연수 = `2@` 같은 다련) |
| `관경산출` | 제원을 어디서 얻었나 — `도면표기` / `도면표기(단선버퍼)` / `추정(단선 기본폭 …)` / `…(인접승계)`. **근거의 세기**: 단선버퍼·추정이면 약함 → 확인필요 레이어로 |
| `생성방식` | 폴리곤을 만든 경로 — 폐합 · 체인쌍(평행 2선) · 단선버퍼 · 심볼 |
| `실측폭m` / `실측장변m` / `면적m2` | 도형에서 잰 값(표기 폭과 20% 넘게 다르면 04 검사가 잡는다) |
| `구분` | 현행 / 매립 |
| `출처` | 원도 이름(02 `--label`) |
| `관저_시점` / `관저_종점` / `관저_출처` | 종단(.flo)에서 붙인 관저고와 출처(06·11 단계 후) |
| 보정 기록 | `fields.obstacle_poly.fix_note` 가 가리키는 필드 — 수작업·규칙 보정 내역 |

통합 '정본' 레이어로 옮길 때 `관경산출` 이 빠지면 근거의 세기를 잃는다 — 11 스킬의 `method_map` 참조표가 그 경우를 메운다.

## 코드값 (`values`)

| 논리 값 | 우리 값 | 뜻 |
|---|---|---|
| `kind_gravity` | 자연유하 | |
| `kind_pressure` | 압송 | `design.pressure_max_d_mm` 이하 관경도 압송으로 본다 |
| `status_new` | 신설 | |
| `status_reuse` | 기존활용 | |
| `status_abandon` | 기존폐쇄 | |
