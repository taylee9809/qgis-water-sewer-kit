---
name: 50-profile-sheets
description: 하수관로 종단면도를 만들고 A3 도면집으로 출력한다. 계획관로 레이어에서 노선별 종단면도(설계사 서식 스트립 DXF + 검토용 PDF)를 그리고, 긴 스트립 종단면도 DXF를 A3 시트로 자동 분할(통일 축척·가로 꽉 채움·장별 기준고·리 이름 제목)해 PDF로 뽑는다. 사람이 그린 A3 프레임대로 출력, PDF 위 펌프장 아이콘·딱지 얹기도 한다. 종단면도 출력, A3 분할, 종단면도 도면집, 종단면도 제목(리 구분), 종단 PDF, 설계사 종단면도 다시 뽑기 요청 시 사용.
---

# 50 종단면도·A3 도면집

함정: 킷 `standard/pitfalls-dxf.md` 를 먼저 읽을 것 · 업무 흐름: `standard/workflow.md` ·
논리 레이어·필드: `standard/data-standard.md`

| | |
|---|---|
| 선행 | 30-route-design(관저고·관경) · 40-review-vendor(설계사 종단 서식 판독) |
| 후행 | 51-layout-figure · 70 보고서 |
| 입력 | ① 설계사 종단면도 DXF(긴 스트립) 또는 ② 계획관로 레이어(`layers.plan_pipe`) |
| 설정 | 프로젝트 루트 `sewer.config.json` 의 `profile` 절(없으면 내장 예시값) + 리 경계 `layers.admin_ri` |
| 산출 | `<outputs.root>\50-profile\` — `<이름>_A3시트.dxf/.json`, `<이름>_A3.pdf`, `ri_map.json`, `<이름>_종단스트립.dxf`, `<이름>_종단검토.pdf` |

## 0. 먼저 — 서식 진단

설계사마다 종단면도 서식(표제 블록 이름·밴드 y·레이어)이 다르다. 처음 받은 도면은 진단부터 한다.

```
python -X utf8 "<킷>\skills\50-profile-sheets\scripts\a3_split.py" "<종단면도.dxf>" --probe
```
찍히는 것: 블록 INSERT 목록(표제 블록 이름), 행(지구) y0·계통 수, 관번호 글자 수, TEXT 회전각 분포,
측점 세로선 수, 첫 계통 원점 왼쪽 글자(표척 눈금값 dx). 내장값과 다르면 `profile.strip` 을 고친다.
40-review-vendor 의 `band_dump.py` 로 밴드 y 를 더 자세히 잴 수 있다.

## 1. 파이프라인

```
set K=<킷>\skills\50-profile-sheets\scripts
cd <프로젝트 폴더>            REM sewer.config.json 을 위로 올라가며 찾는다(A3 분할·PDF 는 설정 없이도 돈다)

REM (선택) 관번호 → 리 매핑 — A3 장 제목 「리명 번호 | 지구 | 관번호 범위」
python -X utf8 %K%\ri_map.py --district OO --district "OO(준공)" --name ri_OO.json

REM ① 설계사 스트립 DXF → A3 시트 DXF + JSON (1단계, 수 초)
python -X utf8 %K%\a3_split.py "<종단면도.dxf>" --ri-map <out>\ri_OO.json --scale 1200 [--rows OO] [--vk 0.6667]

REM ② A3 시트 DXF → A3 PDF (2단계, 장당 1~2초)
python -X utf8 %K%\a3_pdf.py "<out>\<이름>_A3시트.dxf" --png 70      REM --png = 눈 검수용 장별 PNG

REM (선택) 펌프장 아이콘·딱지 — 다시 출력하지 않고 얹는다
python -X utf8 %K%\pdf_overlay.py "<out>\<이름>_A3.pdf" --sheets "<out>\<이름>_A3시트.dxf" ^
       --icon "%K%\..\assets\pump_icon.svg" [--tags tags.json]
```

| 경우 | 쓰는 것 |
|---|---|
| 설계사가 준 종단면도 DXF 를 A3 도면집으로 | `a3_split.py` → `a3_pdf.py` (표준 경로) |
| 계획관로(우리 설계안)로 종단면도를 새로 그리기 | `profile_sheets.py` → 나온 `_종단스트립.dxf` 를 위 표준 경로로 |
| 사람이 DXF 에 A3 프레임 블록(예: `EEE`)을 직접 그려 페이지를 정한 경우 | `a3_frames_pdf.py` (자동 분할 대신 프레임대로, 장별 축척) |
| 계통마다 새 장(한 장에 계통을 이어 붙이지 않음) | `a3_split.py --no-pack` |

### profile_sheets.py — 계획관로 → 종단면도

```
python -X utf8 %K%\profile_sheets.py --district OO [--route XX-A] [--title-from "<설계사 종단면도.dxf>"] [--name OO]
```
- 노선 = 관번호에서 끝 번호를 뗀 앞부분(`XX-A-001` → `XX-A`, `--route-re` 로 바꿈). 같은 노선 관을 도형 끝점
  연결(`--snap` 0.5 m)로 이어 체인을 만든다. **관로 도형 방향이 상류 → 하류여야 한다.**
- DXF 는 설계사 서식 모사(1 unit = 1 m, `Y = 117 + (표고 − DATUM) × 5`, DATUM = floor(최저관저 − 관경) − 12,
  7단 밴드, 맨홀 2중 사각, 45° 인출, 관번호 막대, 구배 원). 시트(계통)는 `profile.draw.sheet_max_m`(500 m)
  넘으면 관 경계에서 나눈다. `--title-from` 을 주면 설계사 표제 블록·스타일을 가져다 쓰고, 없으면 같은 치수로 그린다.
- 지반고: 상류 끝 = `plan_pipe.us_ground` → 맨홀 `ground`, 하류 끝 = 맨홀 → 다음 관 `us_ground`.
  **없으면 비운다**(값을 지어내지 않는다). 관저고가 빈 압송관만 `지반고 − pressure_cover_m − 관경`.
- 지장물은 `plan_pipe.obstacles` 문자열(`종류 D규격@측점(저면EL)` · `지장물@측점(EL…)`), 상수관은 `layers.water_pipe` 교차.
- 검토용 PDF 는 노선마다 한 쪽(550 m 넘으면 나눔): 지반선·관저/관정선·단차·L/S·지장물·상수관·표.

## 2. 설정 (`sewer.config.json`)

**새 절 `profile`** — 전부 선택. 안 적으면 `scripts\profcommon.py` 의 `DEFAULTS`(한 설계사 서식 예시)를 쓴다. 부분만 적어도 된다.

| 키 | 기본(예시) | 쓰임 |
|---|---|---|
| `profile.font` · `font_bold` | `malgun.ttf` · `malgunbd.ttf` | 회전 글자·표제·딱지 글꼴 |
| `profile.strip.title_block` | `title` | 계통(시트) 원점마다 INSERT 되는 표제 블록 이름 |
| `profile.strip.unit_h` · `unit_v` | 1000 · 200 | 원도 축척 분모(축척 글자·표척 눈금값 계산) |
| `profile.strip.band_rows` · `band_top` · `frame_top` · `floor` | `[0,15,…,105]` · 105 · 148 · 152 | 밴드 7단 경계 / 표 윗변 / 고정틀 상단 / 지형부 하한 |
| `profile.strip.station_y` · `pole_top` · `pole_label_dx` · `title_w` · `band_left` | 117 · 267 · −25 · 46 · 10 | 측점선 시작 y / 표척 상단 / 눈금값 x / 표제란 폭 / 밴드 좌변 |
| `profile.strip.layers.*` | `j_gh` `j_pipe1` … | 지형·관로(`profile`), 인출·글자(`annot`), 밴드(`band`), 측점선(`station`), 메모(`memo`), 빨강(`red`) 등 |
| `profile.strip.pipe_pattern` | `^[A-Z]{1,5}-[A-Z0-9]{1,4}-\d{3,5}$` | 관번호 글자 정규식(제목·리 매칭) |
| `profile.strip.row_label_min_h` · `row_split` | 20 · 0.5 | 여러 지구를 행으로 쌓은 도면: 행 이름표 글자 높이 / 행 경계 비율 |
| `profile.a3.scale_denoms` | 1000~2500 | 축척 사다리(H 분모). 모든 계통이 성립하는 가장 큰 축척 하나로 통일 |
| `profile.a3.vk` | 1.0 | 지형부 세로 압축(2/3 이면 V=1:360 — 급경사 계통이 가로를 못 채울 때) |
| `profile.a3.page` · `margin` · `sheet_gap` · `seg_gap` | 420×297 · [7,7,7,30] · 40 · 12 | 종이·여백(위 30 = 표제)·시트 간격·한 장 안 계통 간격 |
| `profile.a3.pump_key` · `pump_mag` · `pump_rename` | `맨홀펌프장` · 1.7 · `{}` | 펌프장 라벨 확대·노란 바탕, 펌프명 바꾸기(`{"XX#1":"OO#1"}`) |
| `profile.a3.join_key` | `유입` | 다른 노선 접속 라벨 → 분홍 바탕 |
| `profile.a3.district_suffix` · `row_labels` | `지구` · `{}` | 부제 지구 표기(`{"OO":"OO처리구역"}` 로 바꿈) |
| `profile.a3.ri_colors` · `colors` | `{}` · 빨강/노랑/분홍/회색 | 리별 표제 색(없으면 팔레트 순), 색군 색 |
| `profile.draw.*` | 500 m · 200 · 12 m · 20 m · 900 mm … | `profile_sheets.py` 시트 최대 연장·간격·DATUM 여유·측점 간격·맨홀 내경·관종 문구 |

**새 논리 레이어 `admin_ri`** (리 경계 폴리곤, `ri_map.py`): `layers.admin_ri` + `fields.admin_ri.name`(리 이름).
행정경계 한 레이어에 시·읍면·리가 섞여 있으면 `fields.admin_ri.kind` + `values.admin_ri_kind`(예: `리경계`).
SHP 는 `.cpg` 가 없으면 cp949 로 읽는다(`--encoding`).
**선택 논리 필드 `fields.plan_manhole.inner_mm`**(맨홀 내경) — 없으면 `mh_type`(1호·2호 …) → `profile.draw.manhole_type_mm`.

## 3. 구성

| 파일 | 역할 |
|---|---|
| `scripts\profcommon.py` | 공통 — 설정 기본값·병합, DXF 읽기(UTF-8 먼저), 글자 상자, **회전 TEXT 안전 렌더링** |
| `scripts\a3_split.py` | [1단계] 스트립 DXF → A3 시트 DXF + JSON(장별 리·번호·부제·관번호·채움률·자체 검증). `--probe` 진단 |
| `scripts\a3_pdf.py` | [2단계] A3 시트 DXF → PDF(색군·형광 바탕·회전 글자) |
| `scripts\a3_frames_pdf.py` | 사람이 그린 A3 프레임 블록대로 → PDF(장별 축척, `-u` 통일, `--ri-map` 제목 자동) |
| `scripts\ri_map.py` | 계획관로 중점 ↔ 리 경계 → `{관번호: [리, 지구]}` (압송 중간노드 축약키 포함) |
| `scripts\profile_sheets.py` | 계획관로 → 노선별 스트립 DXF(설계사 서식) + 검토용 PDF + 시트 목록 JSON |
| `scripts\pdf_overlay.py` | A3 PDF 위 SVG 아이콘(펌프장 라벨 자리)·딱지 — SVG 를 MuPDF 안전하게 정리 |
| `assets\pump_icon.svg` | 예시 펌프장 아이콘(그라디언트·그림자 없음) |

의존: `ezdxf`(1.4 확인), `matplotlib`, `pymupdf`(overlay), `geopandas`·`shapely`(ri_map·profile_sheets).
`python -I` 로 돌리지 않는다(user site-packages 를 못 찾을 수 있다).

## 4. 판독·분할 규약과 함정

- ★ **ezdxf matplotlib 출력기는 기울어진 TEXT 의 글자 구멍을 칠한다**(45° 인출 라벨의 `D`·`0`·`부` 가 검은 덩어리).
  2단계·프레임 출력은 회전 TEXT(블록 안 글자 포함)를 ezdxf 가 그리지 않게 가로채 matplotlib 글자로 그린다.
  자리·정렬은 ezdxf 가 계산한 변환행렬을 그대로 쓰므로 원래와 같다. 덤으로 회전 글자는 PDF 에서 검색되고 출력이 2~3배 빠르다.
- 계통 경계 = 표제 블록 INSERT. 계통마다 표척 기준값이 달라 **계통을 넘겨 한 축척 띠로 잇지 않고**, 조각마다 Δ·표척·축척 글자를 따로 만든다.
- 엔티티 3분류: 고정틀(y ≤ frame_top: 밴드·관번호 막대) / 측점선(아래끝 고정, 위끝만 Δ) / 지형부(Δ 만큼 내림, 5 unit = 1 m 단위).
- ★ 밴드 표 가로선·테두리는 계통 원점보다 `band_left`(10) 왼쪽에서 시작한다 — 여유를 6 으로 두면 표 선이 통째로 사라진다(여유 12).
- ★ 선 자르기는 파라메트릭으로 — 오른쪽 경계에서도 끝점을 경계로 당기면 긴 밴드 가로선이 점이 된다. 1단계가
  `자르다 점으로 뭉갠 선` 을 세어 0 인지 찍는다(원도에 원래 있는 길이 0 선은 세지 않는다).
- ★ 밴드 우측 세로 테두리는 `rmax` 가 아니라 **실제로 그려진 밴드 가로선 끝**에 세운다(rmax 는 인출 글자까지 포함).
- ★ 오른쪽 끝(rmax)은 계통을 가로지르는 긴 선(> 100 unit)을 빼고 잡는다 — 섞이면 페이지 폭이 폭주한다.
- ★ 2단계 페이지 배정은 `//` — `round` 면 페이지 오른쪽 절반이 다음 장으로 가서 사라진다.
- ★ 세로 압축(VK)은 정렬 TEXT 의 `align_point` 도 같이 옮기고, 45° 글자는 인출선과 같이 눕힌다(atan2(sin·VK, cos)).
- ★ 계통 첫 구간 관번호 라벨은 원점보다 최대 7 unit 왼쪽에 찍힌다 → 제목 관번호 범위 여유를 계통 시작에서만 15 로.
- 리 번호는 **페이지당 하나**(한 장에 계통이 셋이어도 「OO리 2」 하나). 페이지 대표 리 = 관번호 다수결.
- 행이 바뀌면(지구) 새 장. 빈 장에 통째로 들어가는 짧은 계통은 자르지 않고 다음 장으로(채움률은 조금 내려간다).
- 메모 레이어(`strip.layers.memo`)는 분할에서 뺀다 — 행 이름표·수정 구름·지시선이 범위를 수백 unit 늘린다.
- 높이 300 넘는 세로 2점 폴리라인 = 옛 서식의 펌프장 노란 강조선 → 페이지 높이로 다시 그린다. 세로 `pump` 글자는 표척을 비켜 내린다.
- 정점 폭 있는 폴리라인(치수선 화살촉)은 폭을 버린다 — ezdxf 는 폭 있는 폴리라인의 폭 0 구간을 그리지 않아 관번호 막대 선이 사라진다.
- 표척 눈금값 TEXT(원점 + `pole_label_dx`)는 Δ 만큼 다시 쓴다. 원도 축척 글자(`H=1:` `V=1:`)도 출력 축척으로 다시 쓴다.
- 성능: 엔티티 수만 개에서 `delete_entity`/`unlink_entity` 는 호출당 수십 초 → 목록으로 다루고 마지막에 `delete_all_entities`.
  `transform(Matrix44)` 대신 균등 확대 + 이동만 하는 `xform`. 측점 셀 단위로 미리 집계해 그리디를 증분으로(엔티티 재순회 금지).
- `MatplotlibBackend(ax, adjust_figure=False)` — 기본값이면 finalize 가 figure 크기를 바꾼다. CUSTOM 색은 `'#RRGGBBAA'` 문자열.
- 프레임 출력: 계통 이름표가 없는 계통에 **순번을 채우지 않는다**(번호가 밀린다). HATCH 는 bbox 가 안 잡혀 통째로 사라지니
  `--hatch-layer` 로 path 별 사각형을 직접 그린다. 도면 밖 잔여물 레이어는 `--skip-layers`.
- PDF 덧씌우기: 자리는 시트 DXF 에서(종이 mm). MuPDF 의 SVG 변환은 CSS 클래스·radialGradient·base64 그림자를 못 그려
  검은 얼룩이 된다 → `pdf_overlay.py` 가 클래스를 인라인 style 로, 그라디언트를 흰색으로, `<image>` 를 지운 사본을 쓴다.

## 5. 합격 기준 (결과를 믿기 전에)

- 1단계 `검증:` 줄 — **관번호 제목 누락 0, 자르다 점으로 뭉갠 선 0**.
- 장 수·통일 축척·채움률(평균 90% 안팎이 정상, 꼬리 장·짧은 계통 장은 낮다)을 보고에 적는다.
- **눈 검수**: `a3_pdf.py --png 70` 으로 전 장을 훑고, 밀집 구역은 PDF 를 300 dpi 로 확대해 글자 구멍·잘림·표 선을 본다.
- `profile_sheets.py` 산출은 `_종단목록.json` 의 노선·시트·구간 수가 계획관로 본 수와 맞는지(빠진 관 0) 본다.

## 사례

- 1행 21계통 241관 스트립: H=1:1200, VK=2/3(V=1:360) → **28장**, 채움률 평균 ≈ 0.90, 제목 누락 0.
  원 도구 결과와 장 수·리·번호·부제·관번호·채움률·조각 범위가 전 장 같다. VK 1.0 이면 29장(첫 장 42%).
- 6행(지구별) 85,000 엔티티 도면에서 한 행만(`--rows OO`): H=1:1200 → 9장, 원 도구와 같음.
- 계획관로 102본(7노선) → `profile_sheets.py` 11시트 스트립 → `a3_split.py` 11장(1:1200). 압송·펌프장이 있는
  지구 241본 → 34시트 → 33장.
