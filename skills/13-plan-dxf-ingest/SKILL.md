---
name: 13-plan-dxf-ingest
description: 설계사가 납품한 평면 DXF·종단 DXF·makesw .flo/.OUT 를 읽어 계획 노선(plan_pipe)·계획맨홀(plan_manhole)·맨홀펌프장(plan_pump) 레이어로 만든다. 속성맨홀 블록(ATTRIB)이 있으면 그 대장으로, 없으면 관로 폴리라인+관로명 라벨 배정으로 구축하고 평면↔flo 관번호·관저고·연장을 교차검증한 뒤 이전 회차 행을 교체한다. 설계사 납품물 QGIS 반영, 계획노선 레이어 갱신, 새 회차 평면도·수리계산 반영, flo 와 평면 관번호 대조 요청 시 사용.
---

# 13 설계사 납품물 → 계획 레이어

함정: 킷 `standard/pitfalls-dxf.md` 를 먼저 읽을 것 · 업무 흐름: `standard/workflow.md` ·
논리 레이어·필드: `standard/data-standard.md`

| | |
|---|---|
| 선행 | 00-onboard(설정) · 12-asbuilt-ingest(기존망 접속 확인용) |
| 후행 | 40-review-vendor(납품물 검토) · 30-route-design(수리계산·펌프 용량) · 32-route-optimize |
| 입력 | 납품 폴더의 평면 DXF · 종단 DXF(선택) · `*.flo` · `*.OUT` |
| 설정 | `sewer.config.json` 의 `plan_ingest` 절(작도 규약) + `layers.plan_*` · `fields.plan_*` · `values` · `design` · `review_vendor.exchange_dirs`(납품 폴더 찾기) |
| 산출 | `<outputs.root>\13-plan-ingest\<납품폴더명>\` — `scan.json` `census.txt` `build.json` `build_checks.csv` `변경메모_*.csv` `apply_log_*.json` `backup\` |
| 쓰기 | **`p3_apply.py --write` 일 때만**, GPKG 만, 백업 후. 나머지는 전부 읽기 전용 |

## 0. 먼저 — 어느 경로인가

`p1_scan.py` 의 census 를 보고 정한다(`--mode auto` 가 대신 고르지만 이유는 알고 쓴다).

| 평면 DXF 에 | 경로 | 관로 도형 | 속성 |
|---|---|---|---|
| **속성맨홀 블록**(ATTRIB: 상류·하류맨홀·관저고1/2·지반고·관경·관종·맨홀 호수)이 있다 | **attrib** | 상류노드 → 하류노드 직선(압송 접기 시 체인 노드열) | 평면 ATTRIB, 연장·자체유량 flo, 수리값 OUT |
| 관로 폴리라인 + 관로명 TEXT + 맨홀 심볼만 있다(경량 평면) | **polyline** | 폴리라인(화살촉 정점 제거, 끝점을 맨홀 심볼에 스냅) | 전부 flo/OUT |

- 속성맨홀 경로가 **정답 경로**다. 평면 ATTRIB 와 flo 가 회차 내내 불일치 0 이었고, 라벨 근접추정이 필요 없다.
  같은 설계사라도 회차에 따라 경량 평면(속성 없음)만 오기도 한다 → 그 회차만 polyline.
- 전도면 평면 DXF 에는 **다른 지구·이전 회차·기존망 속성맨홀이 같이 들어 있다.** 금회 관번호 접두를 `--prefix` 로
  고른다(census 의 「속성맨홀 상류 접두」와 flo 접두를 비교). 접두를 안 주면 flo 에 있는 구간 전체가 금회다.
- **이전 회차 자료기준일**(교체 대상)을 레이어에서 확인해 둔다 — p3 의 `--replace-asof` 로 준다.

## 1. 실행 순서

```
cd <프로젝트 폴더>                  # sewer.config.json 을 위로 올라가며 찾는다
set K=<킷>\skills\13-plan-dxf-ingest\scripts

python -X utf8 %K%\p1_scan.py   -d YYMMDD                      # ① 평면 DXF 스트리밍 스캔 → census
python -X utf8 %K%\p2_build.py  -d YYMMDD --prefix XX --district OO --asof 2026-01-31 --source "설계사 N차"
                                                               # ② 레코드 구축 + 교차검증 (쓰지 않음)
python -X utf8 %K%\p3_apply.py  -d YYMMDD --replace-asof "<이전 회차 자료기준일>"          # ③ 보고
python -X utf8 %K%\p3_apply.py  -d YYMMDD --replace-asof "<이전 회차 자료기준일>" --write  # ④ 백업 후 반영
python -X utf8 %K%\p4_verify.py -d YYMMDD                      # ⑤ 반영 검증 (읽기 전용)
```

- `-d` 는 날짜 6자리·폴더명 일부·경로(40 스킬과 같은 규칙, `review_vendor.exchange_dirs`). 파일을 직접 줄 때는
  `--dxf` `--flo`(여러 번) `--out`(여러 번) `--profile`.
- ② 는 산출 폴더의 `scan.json` 을 다시 쓴다(설정 `plan_ingest.dxf` 가 바뀌면 자동으로 다시 스캔, 강제는 `--rescan`).
- **사본에 먼저**: `p3_apply.py ... --gpkg <사본.gpkg> --write` → `p4_verify.py ... --gpkg <사본.gpkg>` 로 확인한 뒤 본 GPKG 에.
- 회귀 대조: `p4_verify.py --build <build.json> --gpkg <그 회차 반영 직후 백업.gpkg> --asof "<그때 자료기준일>"`.

### p2 주요 인자
| 인자 | 뜻 |
|---|---|
| `--prefix XX` | 금회 관번호 접두(여러 번). 생략 = `plan_ingest.prefixes`, 그것도 없으면 flo 전체 |
| `--district` `--area` | 지구·처리구역 기본값. `plan_ingest.district_by_prefix`·`area_by_prefix`, `--district-by-flo A.flo=OO` 가 먼저 |
| `--asof` `--source` | 자료기준일·출처. **자료기준일이 다음 회차 교체의 열쇠**다 — 회차마다 다른 값으로 |
| `--pressure fold\|split` | fold = 맨홀펌프장마다 압송 1행(`pressure_profile` 에 `누가거리:관저고;…`), split = 세분구간 그대로 |
| `--pressure-hyd keep\|null` | 압송 행의 OUT 유량·유속·판정. makesw 는 압송을 자연유하식으로 풀어 값이 믿을 만하지 않다 → 쓰지 않으려면 null |
| `--ext-node polyline\|attrib` | 금회 밖 하류노드(기존망 접속점) 좌표 찾는 순서 — 아래 함정 참고 |
| `--rename flo번호=도면번호` | flo 가 옛 번호를 남긴 구간. ② 가 종단 DXF 로 **별칭 후보와 권고**를 찍어 준다(자동 적용 안 함) |
| `--no-out` | OUT 을 안 쓴다(유량 재조정 후 직접 계산할 때) |

### p3 주요 인자
| 인자 | 뜻 |
|---|---|
| `--replace-asof` | 지울 이전 회차 자료기준일(여러 번). 이번 build 의 자료기준일 행도 같이 지워 **다시 돌려도 중복이 안 생긴다** |
| `--carry-fields "금회_*"` | 관번호가 같은 이전 행에서 사람이 계산한 열을 이어 붙인다(교체로 사라지는 것 방지) |
| `--carry-mh XX-001` | 그 맨홀의 접속맨홀·유출관저고·심도·비고 승계. **설계가 안 바뀐 것을 사람이 확인한 맨홀만** |
| `--gpkg` | plan_* 를 이 GPKG 에서 같은 레이어명으로(사본 시험) |
| `--write` | 실제 쓰기. 없으면 보고만(지울 행·넣을 행·필드 매핑·변경메모 CSV) |

이전 회차 대비 변경은 레이어에 `변경_YYMMDD` 열을 늘리지 않고 **변경메모 CSV**(동일·값변경·번호변경추정·신규·삭제)로 남긴다.
번호변경추정 = 이전 행과 상류절점이 0.5 m(`tolerance.renumber_m`) 안 — 회차 사이 renumber 를 잡는다.

## 2. 설정 `plan_ingest` (없으면 스크립트 내장 예시 = 한 설계사 도면 규약)

```json
"plan_ingest": {
  "dxf": {
    "pipe_layers": ["0계획관로_일반", "0계획관로_압송", "d압송관로"],
    "pressure_layers": ["0계획관로_압송", "d압송관로"],
    "mh_layers": {"0계획맨홀": "plan", "0기존맨홀": "existing", "+0-펌프장": "pump"},
    "label_layers": ["0관로명", "0관로명_압송"],
    "attrib_block": "속성맨홀", "obstacle_block": "기타데이터",
    "ext_node_layers": null
  },
  "attrib": {"up": "관로명1", "down": ["관로명2A", "관로명2"], "ground": "지반고",
             "us_invert": "관저고1", "ds_invert": "관저고2", "dia": "관경", "kind": "관종", "mh_type": "맨홀"},
  "kind_map": {"신설오수": "gravity", "신설압송": "pressure", "기존오수": "existing"},
  "mh_types": {"1호맨홀": {"role": "plan", "type": "1호맨홀", "inner_mm": 900, "outer_mm": 1200},
               "맨홀펌프장": {"role": "pump", "type": "3호맨홀", "inner_mm": 1500, "outer_mm": 1800},
               "1호기존": {"role": "existing", "type": "1호기존", "inner_mm": 900, "outer_mm": 1200}},
  "mh_kind": {"plan": "계획맨홀", "pump": "맨홀펌프장", "existing": "기존맨홀"},
  "write_mh_roles": ["plan", "pump"],
  "obstacle_tags": {"pipe": "관로명", "kind": "관종", "dia": "관경", "box": "BOX", "invert": "관저고"},
  "out": {"name_regex": "[A-Z0-9]+-[A-Z0-9]+-\\d+", "outfall_regex": "방류-?\\d*",
          "judge_words": ["LESS0.6", "NO GOOD", "UPPR3.0", "GOOD", "FULL", "Q-ZERO"]},
  "prefixes": [], "district": null, "area": null, "district_by_prefix": {}, "area_by_prefix": {},
  "pressure": "fold", "pressure_hyd": "keep", "ext_node": "polyline",
  "pump": {"kind": "신설", "spec": "3호", "inner_mm": 1500, "basis_note": "미산정(용량계산서 후속)", "peak_factor": 1.44},
  "tolerance": {"attr": 0.005, "len_m": 0.5, "node_m": 3.0, "snap_m": 3.0, "bend_m": 0.3,
                "trim_m": 3.2, "trim_pressure_m": 0.0, "len_poly_m": 2.5, "renumber_m": 0.5, "alias_il": 0.005}
}
```

- **다른 설계사 도면**이면 `census.txt` 의 레이어·블록·태그 이름으로 `dxf`·`attrib`·`kind_map`·`mh_types` 만 바꾼다.
  `mh_types` 에 없는 호수 값(빈칸 = 압송 중간절점 등)은 맨홀로 안 쓴다.
- `role` = plan / pump / existing. `write_mh_roles` 에 든 역할만 plan_manhole 에 쓴다(기존맨홀까지 넣으려면 existing 추가).
- 관로 `구분` 코드값은 `values.kind_gravity` / `kind_pressure` / `kind_existing`(기본 '기존').
- 계획맨홀 레이어가 **폴리곤**이면 `outer_mm` 지름의 원(32각형)으로, 점이면 점으로 쓴다(레이어 기하형을 따른다).
- 레이어에 쓰는 값은 `fields.<레이어>` 에 매핑된 것뿐이다. 매핑 없는 값은 build.json·CSV 에만 남고, 레이어에 있으나
  매핑 안 된 필드는 NULL 이 된다(보고 모드가 목록을 보여 준다). 이 스킬이 쓰는 논리 필드:
  - plan_pipe: `pipe_id district area kind length_m dia_mm us_invert ds_invert us_ground slope_permil us_cover flow_m3d velocity_mps judge obstacles asof source`
    + `us_mh ds_mh self_flow_m3d full_flow_m3d flow_depth_m depth_ratio_pct pressure_profile obstacle_count geom_length_m length_diff_m`
  - plan_manhole: `mh_id district kind mh_type ground out_invert depth drop connect_to asof` + `area inner_mm outer_mm note source`
  - plan_pump: `pump_id district kind discharge_to flow_m3d force_main_m in_invert asof` + `area mh_id peak_m3min depth_m top_bop mh_spec inner_mm basis source`
- `asof` 와 번호(`pipe_id`·`mh_id`·`pump_id`) 매핑은 필수(교체의 열쇠).

## 3. 합격 기준

| 단계 | 기준 |
|---|---|
| ② attrib | 평면↔flo **불일치 0**(금회 범위: 하류맨홀·관경·관저고1/2·지반고 ±0.005) · flo 에만 있는 금회 구간 0 · 도형↔flo 연장차 ≤ 0.5 m · 압송 체인 밖 구간 0 · 금회 밖 하류노드 전부 심볼 스냅(≤ 3 m) |
| ② polyline | 라벨 배정 전건 · 연장불일치(트림 복원 후 ±2.5 m) 목록을 사람이 확인 · 맨홀 심볼 없는 끝점 목록 확인 · 하류 종점 = 실제 접속점·방류구뿐 |
| ③ | 지울 행 수 = 이전 회차 행 수 · 남는 행과 번호 겹침 0 · 변경메모의 번호변경추정·신규·삭제를 사람이 납득 |
| ⑤ | 속성 역대조 0 · 맨홀 중심↔노선 시점 0 m · 피처수 = rtree = gpkg_ogr_contents · 노선 시점에 맨홀 없음 0(압송 중간절점 제외) |

설계기준 훑기(⑤ 출력: 토피 `design.min_cover_m` 미만, 자연유하 경사 `design.min_slope_permil` 미만, 역경사, 단차·심도 목록)는
판정이 아니라 **40 검토로 넘길 후보**다.

## 4. 함정 (공통은 `standard/pitfalls-dxf.md`)

- **인코딩**: AC1021(2007) 이상 DXF 는 `$DWGCODEPAGE=ANSI_949` 라도 UTF-8 — 스캐너가 버전으로 판단한다(`plan_ingest.encoding` 으로 강제 가능).
- **200 MB 대 전도면**은 ezdxf 로 통째로 열지 않는다 — p1 이 group code 스트리밍으로 필요한 레이어만 뽑는다(수 초).
- **속성맨홀 태그 이름이 회차 중에 바뀐다**(하류맨홀 `관로명2` → `관로명2A`/`2B`). `attrib.down` 은 목록이라 앞에서부터 찾는다.
  census 의 태그 목록을 매 회차 확인.
- **flo 관번호 ≠ 도면 관번호**: 압송을 자연유하로 바꾸면서 flo 에만 옛 세분번호(`XX-A-02501`)가 남은 회차가 있었다.
  ② 가 관저고 일치로 별칭 후보를 찾고 종단 DXF 글자로 어느 쪽이 도면 번호인지 권고한다 → `--rename` 으로 도면 번호 채택.
- **2단 압송**: 압송이 다른 맨홀펌프장으로 토출된다. 체인은 다음 노드가 펌프장이면 끊는다(안 끊으면 압송연장이 2.8배로 부풀었다).
- **기존망 속성맨홀이 다른 원점에 작도**된 도면이 있다 → 금회 밖 하류노드 좌표는 기본(`ext_node: polyline`)으로
  「상류노드에서 폴리라인 방향으로 flo 연장만큼 → 기존맨홀 심볼 스냅」. 속성맨홀 좌표가 믿을 만한 도면이면 `attrib`.
- **같은 이름 속성맨홀이 여럿**(방류구 등): 하류노드로 쓸 때는 상류에 가까운 것, 계획맨홀 속성도 그 사본에서 읽는다.
- **호수 빈칸 절점 = 압송 중간절점** — 맨홀이 아니다. 계획맨홀·유량 배정 대상에서 빼고, ⑤ 도 「정상」으로 센다.
- **OUT 판정 `Q-ZERO`**(유량 0)를 판정어 목록에서 빠뜨려 LESS0.6 으로 들어간 회차가 있었다 → `out.judge_words`.
- **OUT 의 압송 구간 수치**는 등류식으로 역구배를 푼 값 — `--pressure-hyd null` 로 비우거나 30 스킬로 따로 본다.
- **경량 평면(polyline)**: 관로는 3정점 화살표(촉 3.0 m), 자연유하 화살표는 맨홀마다 1.6 m 씩 잘려 있다(`trim_m` 3.2),
  압송 폴리라인은 펌프장별 1개로 맨홀까지 그려져 있다(`trim_pressure_m` 0) → flo 세분구간을 접어 연장을 맞춘다.
  관로명 TEXT 는 맨홀 라벨이 아니라 **관로 중점 옆 라벨**(보통 3~4 m) — 중점과 전역최적배정한다.
  기존맨홀 번호는 금회 접속점에서 flo 기존 체인을 **연장거리로 걸어가며** 붙인다.
- **기타데이터(종단 지장물) 이중 작도**: 같은 속성으로 2건씩 그려진 회차 → 좌표 뺀 속성키로 1건만. flo 지장물 토큰도 중복 기재가 있다.
- **교체 반영은 사람이 계산한 열을 지운다** → `--carry-fields`. 접속 기존맨홀 정보는 설계가 그대로인 것만 `--carry-mh`.
- `python -I` 로 돌리지 않는다(user site-packages 의 fiona·ezdxf 를 못 찾는다). `python -X utf8`.
- GPKG 를 QGIS 에서 편집 중이면 ④ 가 잠금으로 멈춘다 — 편집을 끝내고 다시. 반영 후 QGIS 레이어 새로고침.

## 5. 사례 (교훈만)

- **전도면 + 속성맨홀 회차**(노선 241·계획맨홀 245·펌프장 4): 평면↔flo 불일치 0, 도형↔flo 연장차 최대 0.13 m,
  기존망 접속 2곳 심볼 스냅 0.01·0.04 m. 이전 회차 대비 값변경 8·삭제 3 이 변경메모로 바로 나왔다.
  원본 회차별 스크립트로 반영한 GPKG 와 세 레이어 전 행·전 필드·도형이 일치.
- **경량 평면(속성 없음) 회차**: 라벨 241개를 중점 전역배정(중앙 3.8 m), 맨홀 심볼 없는 끝점 1곳을 노드로 보완,
  기존맨홀 120개를 체인 추적으로 번호 부여. 연장불일치 1건(도면 9.0 vs flo 3.4)은 진짜 작도/입력 차이로 남았다.
- **flo 옛 번호 회차**: 말단 3구간이 flo 와 평면·종단 번호가 달랐고 좌표·관저고는 같았다 → 종단 글자로 도면 번호 확인.
- **번호 대거 변경(renumber) 회차**: 같은 지구를 설계사가 자기 체계로 다시 매겨 공통 관번호가 일부뿐이었다 →
  관번호로 이어 붙인 사람 계산값은 대부분 승계되지 않는다. 변경메모의 번호변경추정(상류절점 동일)으로 대응표를 만든다.
