---
name: 40-review-vendor
description: 외주 설계사가 납품한 하수관로 평면도·종단면도·수리계산(makesw .flo/.OUT)을 A1~A5 로 자동 검토해 지적사항을 뽑는다. 종단↔평면↔flo 연장 전수 대조, 계통·유향 대조, 기존 접속부 관저고 대조, 설계자 비출력 메모 수거. 외주 설계사 납품물 검토, 종단과 평면이 안 맞는지 확인, 기존관 접속 관저고 확인, 납품 도면 검증 요청 시 사용.
---

# 40 외주 납품물 검토

함정: 킷 `standard/pitfalls-dxf.md` 를 먼저 읽을 것 · 업무 흐름: `standard/workflow.md` ·
논리 레이어·필드: `standard/data-standard.md`

| | |
|---|---|
| 선행 | **12-asbuilt-ingest** (기준 DB) · 30-route-design |
| 후행 | 32-route-optimize · 80-review-response |
| 입력 | 납품 폴더 안의 평면도 DXF · 종단면도 DXF · `*.flo` / `*.OUT` |
| 설정 | 프로젝트 루트의 `sewer.config.json` (`review_vendor` 절 + `layers` · `fields` · `design` · `outputs`) |
| 산출 | `<outputs.root>\40-review\<납품폴더명>\검토요약.md` + a1~a5 로그·json |

## ★ 0. 먼저

**기준 DB(준공 맨홀)가 맞는지 확인하고 들어간다.** 틀려 있으면 지적이 서지 않는다.
실제로 한 검토에서 납품물 관저고를 따지려고 준공 DB 를 들췄더니 그쪽 맨홀 번호가 도형과
반대로 들어가 있었다. 12 를 고치고 나서야 40 의 지적이 섰다.

## 1. 실행 — 인자는 납품 폴더뿐

```
cd <프로젝트 폴더>        # sewer.config.json 을 현재 폴더에서 위로 올라가며 찾는다
set K=<킷>\skills\40-review-vendor\scripts

python %K%\run_review.py                       # 최신 `YYMMDD … from …` 폴더 자동 선택
python %K%\run_review.py -d YYMMDD              # 날짜 6자리
python %K%\run_review.py -d "실시설계 2차"        # 폴더명 일부(공백·쉼표·괄호 무시)
python %K%\run_review.py -d "<경로>\그 폴더"      # 경로 직접
python %K%\run_review.py --list                 # 납품 폴더 목록 + 납품물 유무
python %K%\run_review.py --only a2 a4            # 일부만
python %K%\run_review.py --gpkg "<설계 GPKG 백업>.gpkg"   # 그 회차 백업과 대조
python %K%\run_review.py --config "<경로>\sewer.config.json"   # 설정 직접 지정(또는 SEWER_CONFIG)
```

A1~A5 를 차례로 돌리고 `검토요약.md` 를 쓴다. 단계별 단독 실행도 같은 인자다
(`python a2_len.py -d YYMMDD`, 산출 하위폴더 이름만 바꾸려면 `--outdir`).

납품 폴더명 규칙(기본): `YYMMDD(순번) from.설계사 …` 는 받은 것, `to.설계사 …` 는 보낸 것.
`from` 만 검토 대상이다 (`review_vendor.delivery_pattern`).

**회차가 바뀔 때 코드·설정을 고칠 일은 없다.** 폴더만 새로 들어오면 된다.
납품물은 폴더 안을 재귀 탐색해 찾는다. 파일명이 달라도(`평면도.dxf` / `○○.dxf` / `○○종단.dxf`)
`review_vendor.input_patterns` 를 앞에서부터 시도한다. 설정은 **프로젝트가 바뀔 때만** 손댄다.
원본 납품 폴더에는 아무것도 쓰지 않는다.

## 2. 설정 (`sewer.config.json`)

| 키 | 쓰임 |
|---|---|
| `review_vendor.exchange_dirs` | 납품 폴더가 쌓이는 디렉터리들(프로젝트 루트 기준 상대 또는 절대). 설계사가 늘면 추가 |
| `review_vendor.delivery_pattern` | 수신 폴더 판별 정규식. 1그룹 = 날짜 6자리(정렬 키) |
| `review_vendor.input_patterns` | `plan` `profile` `flo` `out` `dwg` 별 파일명 패턴 목록. 새 이름이 오면 패턴만 추가 |
| `review_vendor.profile` | 종단면도 서식 상수(아래 4절). 설계사 서식마다 다르다 → `band_dump.py` 로 재어서 적는다 |
| `review_vendor.memo` | 비출력 메모 `layers` · `min_text_height`(본문 글자높이의 10배 정도) |
| `review_vendor.tolerance` | `length_m`(A2) · `invert_m`(A4) · `node_snap_m`(A4 준공 맨홀 근접 반경) |
| `review_vendor.asbuilt_asof` | A4 기준 DB 선택(아래) |
| `review_vendor.asbuilt_note` (선택) | A4 에서 최근접 준공 맨홀이 100 m 넘게 떨어질 때 덧붙일 안내문 |
| `layers.plan_pipe` | 계획관로 — A1·A2·A3·A4 (필수) |
| `layers.plan_manhole` | 계획맨홀 — A1 현황, `asbuilt_asof` 가 있으면 A4 기준 |
| `layers.plan_pump` | 맨홀펌프장 — A1 현황(없으면 건너뜀) |
| `layers.asbuilt_manhole` | 준공 맨홀 — `asbuilt_asof` 가 비면 A4 기준 |
| `values.kind_pressure` | 계획관로 `kind` 필드의 압송 코드값 |
| `design.pressure_max_d_mm` | 이 관경 이하면 압송(예: 압송 D80 / 자연유하 D200+ 이면 100) |
| `outputs.root` | 산출 루트 |

레이어는 GPKG 든 PostGIS 든 된다(`layers.<키>.src`). GPKG 는 표준 `sqlite3` 로 직접 읽고,
PostGIS 는 `db.postgis` 로 접속한다(비밀번호는 설정 파일이 아니라 환경변수로).

쓰는 논리 필드(`fields.<레이어>.<논리>`):

| 레이어 | 필수 | 선택 (없을 때) |
|---|---|---|
| `plan_pipe` | `pipe_id` | `length_m`(도형 길이로 대신) · `kind`(flo 관경으로만 압송 판정) · `district` · `asof`(A2 회차 경고 생략) · `dia_mm` `us_invert` `ds_invert` `us_ground` |
| `plan_manhole` | `asbuilt_asof` 를 쓸 때 `mh_id` `out_invert` `asof` | `ground` `depth` |
| `plan_pump` | — | `asof`(A1 자료기준일별 집계 생략) |
| `asbuilt_manhole` | `mh_id` | `out_invert` — 없으면 **유출관저고 = `ground` − `depth`**(지반고 − 맨홀깊이) |

**A4 기준 DB**
- `review_vendor.asbuilt_asof` 에 값이 있으면 → 계획맨홀 레이어(`plan_manhole`) 중 `asof` 가 그 값인 것
  (준공도를 계획맨홀 레이어에 「준공도 편입」으로 넣어 둔 경우).
- 비어 있으면 → `layers.asbuilt_manhole` (12-asbuilt-ingest 가 만든 준공 레이어).
- `--gpkg` 는 `plan_*` 세 레이어만 그 GPKG 에서 같은 레이어명으로 읽게 바꾼다. `asbuilt_manhole` 은 그대로.

## 3. 구성

| 파일 | 역할 |
|---|---|
| `scripts\vendorlib.py` | 공통 — 설정(kitconfig) · 납품폴더·입력 탐색 · DXF walk · 종단 막대 추출 · flo/OUT 파서 · 레이어 읽기(GPKG/PostGIS) |
| `scripts\a1_inventory.py` | A1 입력 인벤토리 |
| `scripts\a2_len.py` | A2 연장 전수 대조 |
| `scripts\a3_topology.py` | A3 계통·유향·압송 |
| `scripts\a4_existing.py` | A4 기존 접속부 관저고 |
| `scripts\a5_memo.py` | A5 비출력 메모 수거 |
| `scripts\run_review.py` | 일괄 + `검토요약.md` |
| `scripts\band_dump.py` | **진단용** — 종단 서식이 바뀌어 A2/A5 가 안 먹을 때 밴드 y·레이어를 다시 잰다 |

의존: `ezdxf`, PostGIS 레이어를 쓰면 `psycopg2`. 킷의 `lib\kitconfig.py` 는 스크립트 위치에서 위로 찾아 쓴다.

**처음 쓰는 팀**: ① 00-onboard 로 `sewer.config.json` 을 만든다 → ② `exchange_dirs` · `delivery_pattern` 을
맞추고 `run_review.py --list` 로 납품 폴더가 잡히는지 본다 → ③ `band_dump.py --layers`, `band_dump.py --row 0`
으로 종단 서식(막대 레이어·라벨 국소 y)을 재서 `review_vendor.profile` 에 적는다 → ④ A4 기준 DB 를 정한다.

## 4. 검토 5종과 합격 기준

| | 검토 | 보는 것 | 합격 |
|---|---|---|---|
| A1 | 입력 인벤토리 | 납품물 4종 결손 · flo 계통·말단 · flo↔OUT 구간 집합 · 설계 DB 반영 현황 · 관번호 중복 | 결손 0 · flo↔OUT 일치 |
| A2 | 연장 전수 대조 | ① 막대 실측폭↔도면 기재 `L=` ② 종단↔flo ③ 종단↔QGIS(계획관로) ④ flo↔QGIS | **금회 범위 불일치 0** |
| A3 | 계통·토폴로지 | 합류점 · 순환 · **유향**(종단 작도 순서 vs flo 체인) · 압송 표기 집합 · 계통 집합 | 지적 0 |
| A4 | 기존 접속부 관저고 | flo 말단노드의 기존 GL·IL ↔ 준공 맨홀 유출관저고 · **자기값복사 탐지** | **±0.05 m** |
| A5 | 비출력 메모 | 메모 레이어 전수 → 관번호 구간 매핑 · "확인 필요" 미확정 표시 | 미확정 0 |

## 5. 판독 규약 (상수는 `review_vendor.profile` 에)

- **행(row)** = `floor(y / 1000)`(`row_pitch`). ★ **round 를 쓰면 안 된다** — 메모는 그 행 도면 **위** +600 띠에
  찍히므로 round 면 다음 행으로 밀린다(실제로 `종점 변경` 메모를 다음 행 계통의 것으로 오판한 원인).
  기준선의 `-0.0` 오차는 `V.rowof()` 가 eps 로 흡수한다.
- **관번호 막대** = `j_table3`(`bar_layer`) 의 4점 LWPOLYLINE, 국소 y 100~160. 행 안에서 x 순으로 라벨과 1:1.
- **연장 글자** = 막대 위 국소 y 125.5 의 `L=58.00M`. 막대와 1:1 이라 **작도 오류 교차검증**에 쓴다.
  같은 `L=` 형식이 국소 y 96.5/107/120 에도 있으니(압송 합계 등) y 범위(`len_label_y_lo/hi`)를 반드시 좁힌다.
- **압송 합산**: 종단은 압송을 모관번호 1막대로 그린다 → 세분구간(`-00801`)은 모관번호로 합산.
  단 **자연유하인데 세분번호인 것은 합산하지 않는다**(D ≤ `design.pressure_max_d_mm` 로 판정).
  그건 연장 오차가 아니라 **번호 불일치 지적**이다 → 사례: `XX-A-02501~02503` = 도면 `XX-A-026~028`.
- **flo 접속행 꼬리** = 토큰 8·9 가 기존 GL·기존 IL(소수점 있는 것만). ★ 정규식으로 꼬리 실수를
  긁으면 `n=0.01` 을 집는다 — **반드시 토큰 위치로** 읽을 것.
- **2단 압송**: 압송이 또 다른 맨홀펌프장으로 토출될 수 있다(사례: `XX-A-024`→`XX-A-025`).
  압송 추적 루프는 다음 노드가 맨홀펌프장이면 끊어야 압송연장이 안 부풀어난다.
- **종단 압송 주기**(「신설압송, D=80MM」 등)는 구간마다가 아니라 관종이 바뀌는 지점에 한 번 찍힌다 →
  A3 은 **연속 압송 run** 단위로 센다. 찾는 낱말 = `profile.pressure_word`(기본 `압송`).
- **판정 범위**는 금회 납품분(flo 에 있는 구간)으로 한정한다. 설계 DB 에 섞인 이전 회차·기존 계통의
  종단 막대 불일치는 `참고` 로만 찍는다(압송 막대가 여러 시트에 걸려 순서짝짓기가 밀리는 등).
- **설계 DB 회차 경고**: 옛 납품물을 최신 설계 DB 와 비교하면 전부 불일치로 보인다. A2 가
  `plan_pipe.asof` 로 자동 경고하고 `--gpkg` 백업 대조를 안내한다.
- **지구별 준공도 범위**: 모든 지구의 준공 맨홀이 한 기준 DB 에 다 들어 있지는 않다. A4 가 최근접이
  100 m 넘으면 그렇게 안내한다 → 다른 준공 레이어로 기준을 바꾸거나(`asbuilt_asof` 비우고 `layers.asbuilt_manhole`)
  12-asbuilt-ingest 로 먼저 적재한다.

## 지적사항 서술 틀

> `관번호` 측점 — 납품값 vs 기준값 — **차이** — 영향(구간수·연장·공사비)

예) `XX-K-034` — 기존관저 26.89 vs 준공 28.50 — **−1.61 m** — `XX-K-033` 단차 1.63 m·심굴착 41.5 m 불필요

## 사례

- 1차 검토: 724구간 대조 → 679 일치, 지적 4건. 같은 관로를 종단에 유향 반대로 그린 뒤
  펌프장을 붙여 놓은 계통이 A3 유향 대조로 잡혔다.
- 2차 검토: A2 금회 연장 불일치 0 · A3 PASS. 남은 지적은 번호 불일치 3구간(`XX-A-02501~02503`) ·
  접속부 방류 접속부 1곳 −0.12 m · 자기값복사 5/5(flo 기존관저 = 신설 하류관저 +0.01 → 실측 조회 안 함).

## 함정

킷 `standard/pitfalls-dxf.md` 를 먼저 읽을 것.
특히 **DXF 인코딩(ANSI_949 선언이지만 UTF-8)**, **압송 세분구간 합산**, **행별 1:1 짝짓기**.
`python -I` 로 실행하지 않는다 — user site-packages 의 `ezdxf`·`psycopg2` 를 못 찾을 수 있다.
