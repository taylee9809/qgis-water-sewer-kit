---
name: 12-asbuilt-ingest
description: 준공도서 DXF(종평면도·계획평면도·배수설비 평면도)를 판독해 준공 DB(asbuilt_* 논리 레이어, PostGIS 또는 GPKG)에 적재하고 원본과 정합성을 대조·교정한다. 도면 레이어·색·밴드 판독, 좌표 오프셋 산정, 관로/맨홀 제원표 추출, 이름·지반고·깊이 대조, 지반고 vs DEM 이상치, 체인 무결성 검증. 준공도 QGIS 반영, 준공 DB 정합성 검토, 맨홀 번호/관저고 확인, 준공도 좌표가 안 맞을 때 사용.
---

# 12 준공도 → QGIS 적재·정합성

- 킷 루트: 이 SKILL.md 의 두 단계 위 폴더(`skills\12-asbuilt-ingest\..\..`). 아래 `<킷>` 은 킷 루트.
- 설정: 프로젝트 루트 `sewer.config.json` (없으면 **00-onboard** 먼저). 프로젝트 폴더 안에서 실행하면 자동으로 찾고, 아니면 `--config <경로>`.
- 함정: `<킷>\standard\pitfalls-dxf.md` · `<킷>\standard\pitfalls-qgis-postgis.md` — **판독 전에 읽는다.**
- 산출물: `<outputs.root>\12-asbuilt\` (기본 `_산출\12-asbuilt\`). 원본 DXF·DB 는 스크립트가 읽기만 한다. DB 에 쓰는 것은 6단계 적재뿐이고 사람이 확인한 뒤 한다.

| | |
|---|---|
| 선행 | 10 자료수집 · 00-onboard (설정) |
| 후행 | 20 기준 확정 · **40-review-vendor** (기준 DB) |
| 대상 | 논리 레이어 `asbuilt_manhole` `asbuilt_pipe` `asbuilt_inlet` `asbuilt_service_pipe` `asbuilt_house` `asbuilt_pump` — 실제 이름은 설정 `layers.*` |

## 쓰는 설정 키

| 키 | 쓰는 곳 |
|---|---|
| `layers.asbuilt_manhole` · `fields.asbuilt_manhole.{mh_id,district,ground,depth}` | asb_offset_diff (diff·DEM) |
| `layers.asbuilt_pipe` · `fields.asbuilt_pipe.{pipe_id,district,length_m,dia_mm,status}` | asb_offset_diff (diff) |
| `values.status_abandon` (예 `기존폐쇄`) | 관로 건수 차이 안내 |
| `dem` (앞의 것 우선) | asb_offset_diff (지반고 vs DEM) |
| `asbuilt.offsets.<지구>` = `[dx, dy]` | bs_features (`--district`) · 적재 |
| `asbuilt.dxf_layers.*` (선택) | DXF 레이어명. 없으면 스크립트 내장 예시(한 설계사 규격)를 쓴다 |
| `asbuilt.profile.{bands,band_tol,label_y}` (선택) | asb_bands 종단 밴드 y |

`asbuilt.dxf_layers` 논리 키: `pipe_no`(관번호 TEXT) · `pipe_len`(L=) · `pipe_dia`(D=) · `pipe_name`(계통명) · `mh_text`(맨홀명·H=) · `probe`(색 볼 레이어 목록) · `inlet_points`(오수받이·소형맨홀 폐합선 레이어 목록) · `inlet_blocks`(오수받이 블록명 목록) · `service_lines`(옥내/옥외 연결관 레이어 목록). 모든 값은 인자로도 덮어쓴다.

## ★ 0. 백업 먼저

적재·교정 전에 대상 테이블을 통째로 떠 둔다. **테이블명은 설정 `layers.asbuilt_manhole` / `layers.asbuilt_pipe` 의 실제 이름**을 쓴다.
```sql
CREATE TABLE public.<asbuilt_manhole 실제이름>_bak_YYMMDD AS TABLE public.<asbuilt_manhole 실제이름>;
CREATE TABLE public.<asbuilt_pipe 실제이름>_bak_YYMMDD    AS TABLE public.<asbuilt_pipe 실제이름>;
```
GPKG 면 파일째 `…_bak_YYMMDD.gpkg` 로 복사한다.

## 1. 도면 규격 확정 (B1) — `layer_probe_color.py` · `asb_bands.py` · `layer_probe_len.py`

```
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\layer_probe_color.py" "종평면도=<종평면도.dxf>" "배수설비=<배수설비 평면도.dxf>"
```
- 레이어 목록·색(ACI)·관번호 라벨 색 분포를 찍어 **신설/기존활용/기존폐쇄** 구분 체계를 먼저 파악한다.
  도면마다 색 규약이 다르다 — 종평면도는 라벨이 전부 한 색이라 구분이 안 되고 평면도에서만 보이는 경우가 있다.
- 여기서 확인한 레이어명을 `asbuilt.dxf_layers` 에 적는다(또는 각 스크립트 `--*-layer` 인자).

```
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\asb_bands.py" --dxf "<종평면도.dxf>" ^
       --band 누가=<y> --band 지반고=<y> --band 관저고=<y> --band 깊이=<y> --label-y <lo> <hi> --anchor <시점 지반고>
```
- 종단 밴드 y좌표를 찾는다(거리/누가/지반고/관저고/토피/경사가 등간격으로 고정). CAD 에서 각 밴드 행의 y 를 재서 준다.
- 수평축이 **1 도면단위 = 1 m** 인지 확인(누가거리 차이와 x 차이가 같은지 — 조립표의 `x` 와 `누가` 열 비교).

```
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\layer_probe_len.py" --dxf "<계획평면도.dxf>"
```
- 계획평면도의 어느 레이어가 **관로 선형**인지 — 준공 연장값(3단계 `asb_spec.json`)과 길이가 맞는 세그먼트 수로 판별. 3단계 뒤에 돌린다.

## 2. 좌표 오프셋 (B2) — `asb_offset_diff.py --pair`

```
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\asb_offset_diff.py" ^
       --pair <도면x> <도면y> <실x> <실y>  --pair …  --pair …  [--district <지구>]
```
- 기지 맨홀 **3점 이상**으로 산정, **0.2 m 이내** 수렴 확인(스크립트가 OK / ★재확인 판정).
- 라벨은 심볼에서 떨어져 찍히므로 **관로 선형 끝점**을 쓰는 게 정확하다.
- 결과를 `asbuilt.offsets.<지구>` 에 적는다. 같은 발주처·같은 작도 원점이면 여러 지구가 같은 값일 수 있다(사례 참고) — 그래도 지구마다 3점 검증은 한다.

## 3. 제원표 추출 (B3) — `asb_spec.py` · `asb_bands.py` · `bs_features.py`

```
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\asb_spec.py" --dxf "<종평면도.dxf>"          → _산출\12-asbuilt\asb_spec.json
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\bs_features.py" --dxf "<배수설비 평면도.dxf>" --district <지구>   → bs_cmp.json
```
- 종단: 노드별 `누가 / 지반고 / 관저고 / 토피`, 구간별 `L=` `S=` (asb_bands).
- 평면부: `관번호 ↔ L= ↔ D=`, `맨홀명 ↔ H=` (asb_spec — 인접쌍, 관로 25·맨홀 20 도면단위 이내).
- 겹쳐서 오페어링 나는 구역(스크립트가 「중복 라벨 값상이」로 찍는다)은 **종단 경사밴드로 교차검증**.
- 지구가 여럿이면 `--out <지구>_asb_spec.json` 으로 나눠 저장하고 다음 단계에 `--spec` 으로 준다.

## 4. DB diff (B4) — `asb_offset_diff.py --district`

```
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\asb_offset_diff.py" --district <지구> [--spec <asb_spec.json>] [--dem-tol 1.5] [--save]
```
`--save` 를 붙이면 결과가 `_산출\12-asbuilt\asb_offset_diff_YYMMDD_HHMM.txt` 로도 남는다(정합성 보고 근거).
개수 → 이름 → 값 순으로 본다. 끝에 **지반고 vs DEM 이상치**(|차| > 1.5 m)를 찍는다 — 번호 뒤바뀜 신호.

| 비교 | 합격 |
|---|---|
| 맨홀 이름 집합 | 원본과 동일 |
| 맨홀 지반고·깊이 | 전건 일치 |
| `지반고 − 깊이 = 관저고` | 준공 관로 IL 과 맞물림 |
| 오수받이·배수설비관 | 개수 일치, 위치 이격 0.1 m 이내 (bs_cmp.json ↔ `asbuilt_inlet`·`asbuilt_service_pipe`) |
| 관로 건수 | 차이는 **`기존폐쇄`(values.status_abandon) 제외 여부**로 설명될 것 |

## 5. 체인 무결성 (B5)

- ★ **맨홀 이름은 종단도로 확정한다.** 종단 노드의 `지반고 − 관저고 = 깊이` 가 평면부 `H=` 라벨과 1:1.
- **번호와 도형 방향이 반대**인 체인을 찾는다:
  종단의 지반고 추이 vs 도형 방향의 DEM 추이가 반대면 뒤바뀐 것.
  분할 길이 수열(예 15/43/19)이 어느 끝에서 맞는지로 확정.
- 끝점으로 맨홀을 자동 배정하지 말 것 — **인접 관로 공유점 + DEM** 교차검증 필수.

## 6. 적재 — `asb_load.py`

```
python -X utf8 "<킷>\skills\12-asbuilt-ingest\scripts\asb_load.py" --district <지구> --spec <지구>_asb_spec.json ^
       --profile-dxf "<종평면도.dxf>" --plan-dxf "<평면도.dxf>" --bs bs_cmp.json          → 보고만(DB 미변경)
python -X utf8 ...asb_load.py ...(같은 인자)... --write             # 확인 후 적재. 그 지구 행이 이미 있으면 멈춘다
python -X utf8 ...asb_load.py ...(같은 인자)... --write --replace   # 그 지구 행만 지우고 다시
```
- 기본은 **보고만**: `_산출\12-asbuilt\asb_load_<지구>.txt`(건수·필수 결측·판독메모) +
  `asb_load_<지구>_preview.gpkg`(표준 스키마 + `판독메모` 열). **미리보기를 QGIS 로 보고 5단계(체인·방향)를 확인한 뒤** `--write`.
- 입력은 앞 단계 산출 그대로: 3단계 `asb_spec.json`(이름·L·D·H 기준) · 종평면도(지반고·관저고·상하류) · 평면도(위치) ·
  `bs_cmp.json`(오수받이·연결관, 이미 오프셋 적용). 평면 좌표에는 `asbuilt.offsets.<지구>`(또는 `--offset`)를 더한다.

| 대상 | 위치(도형) | 값 |
|---|---|---|
| `asbuilt_manhole` | 평면 맨홀 심볼(동심원 0.3 m 묶음) · 심볼 없으면 맨홀명 지시선 끝 · 사슬 분할점 | 이름 = 평면 지시선 라벨 → **인접 관로 공유점으로 전파**(종단의 상·하류). 지반고·유출관저고 = 종단, 깊이 = 제원표 H(`--depth-from profile` 이면 종단 H) |
| `asbuilt_pipe` | 구분별 선형 레이어를 맨홀에서 자름(기존폐쇄는 중간에서 안 자름). 방향 = 종단 상류 → 하류 | 관번호 = 평면 라벨(거리 + 연장 차) → 끝점 맨홀 두 개가 종단 구간과 맞으면 그것을 따름. 연장 = 종단 L 라벨 > 제원표 L. 관경 ≤ `design.pressure_max_d_mm` 면 압송 |
| `asbuilt_inlet` · `asbuilt_service_pipe` | `bs_cmp.json` 점·선 | 구분·관경은 `--inlet-group 묶음=구분` · `--service-group 묶음=구분:관경`. 연결관 끝 2 m 안 이름 있는 맨홀 = 접속맨홀 |

- 양 끝 맨홀은 아는데 한 관으로 안 맞는 선형은 **종단 구간 사슬의 연장 합이 도형 길이와 맞으면 그 수열대로 자른다**(5단계 「분할 길이 수열」).
- 맨홀 열 판정: 밴드에 맨홀 자리에만 값이 찍히는 행(`맨홀IL`)이 있으면 **그 값과 관저고가 같은 열**만 맨홀로 본다 —
  근접 측점 열은 글자 겹침 때문에 2 단위쯤 비켜 찍혀 x 만으로는 못 가른다. 비스듬히 내려가는 라벨(회전 315° 등)은 글자 **끝**이 노드 쪽이다.
- 쓰는 설정 키(없으면 스크립트 기본값 · 모두 인자로 덮어씀):
  `asbuilt.dxf_layers.mh_symbols` `{레이어: 구분}` · `asbuilt.dxf_layers.pipe_lines` `{레이어: 구분}` · `asbuilt.dxf_layers.{mh_text,pipe_no,pipe_len,pipe_dia}` ·
  `asbuilt.profile.{bands,band_tol,mh_label_y,pipe_label_y,gl_band,il_band,mhil_band,mh_label_skip}`(라벨 y 를 따로 안 적으면 `label_y`) ·
  `asbuilt.load.{bs_manhole_groups,service_dia}` · `design.pressure_max_d_mm` · `values.*` · `layers.asbuilt_*` · `fields.asbuilt_*`.
- 평면 맨홀 라벨 이름이 제원표와 다르면(예: 평면 `처리장 유입맨홀` ↔ 제원표 `처리장 유입펌프장`) `--alias 평면이름=제원표이름`.

보고서에서 볼 것:
| 줄 | 뜻·조치 |
|---|---|
| 제원표 맨홀·관로 중 위치/선형 못 찾은 것 | 0 이어야 한다. 남으면 평면 레이어(`--mh-layer`·`--pipe-layer`)·라벨 반경 확인 |
| `원본 불일치: 지반고 − 관저고 ≠ H` | 도면 자체가 안 맞는 것 — 값은 원본 그대로 넣고 사람이 판단 |
| `H 라벨 제원표 ↔ 종단` · `연장 종단 … 채택(제원표 …)` | 3단계 제원표의 인접쌍 오페어링 의심 — `asb_spec.py` 의 「중복 라벨 값상이」와 같이 본다 |
| `평면 라벨 관번호 A → 종단 B 로 바꿈` · `종단 사슬 … 로 분할` | 자동 교정한 곳 — 미리보기에서 눈으로 확인 |
| `유향 미확인(도면 방향)` | 종단에 없는 관(기존폐쇄 등). 관저고는 비운다 |
| 필수 필드 결측 | 이름 없는 소형맨홀·기존폐쇄 관로 등. PostGIS 표준 DDL 은 NOT NULL 이라 그 행이 거부된다 → `--skip-incomplete` 로 빼거나 NOT NULL 을 풀고 넣는다 |

쓰기(`--write`)가 하는 일:
- **0단계 백업을 스스로 한다** — GPKG 는 파일째 `<이름>_bak_YYMMDD.gpkg`, PostGIS 는 `<표>_bak_YYMMDD`(같은 날 두 번째면 시각을 덧붙임).
- `기존폐쇄` 관로도 넣는다(`구분` = `values.status_abandon`, 유향 미확인). 신설 노선과 간섭·철거 검토에 쓰인다.
- PostGIS 는 넣기 전에 **fid 시퀀스를 max(fid) 로 맞춘다**(복사·복원한 표의 PK 충돌 함정). 비밀번호는 `db.postgis.password_env` 환경변수로만.
- 필드 이름은 `fields.asbuilt_*` 매핑 → 없으면 표준 스키마 이름. 대상 표에 없는 열은 건너뛰고 보고한다.
- 적재 후 **4단계(`asb_offset_diff.py --district <지구>`)를 다시 돌려** 합격 기준을 확인한다.

## 사례

- **체인 번호 뒤바뀜** — 한 지구 준공 DB 에서 한 체인의 맨홀 번호가 도형과 반대로 들어가 있었다. 처음엔 "한 맨홀 지반고가 DEM 과 2.94 m 틀리다"로 보였는데, 도형 정점은 맞고 **분할점과 번호만** 틀린 것이었다. 종단 `지반고 − 관저고 = 깊이` ↔ 평면 `H=` 대응과 분할 길이 수열(15/43/19)로 확정해 재분할했다.
- **위치 뒤바뀜** — 같은 지구 맨홀 두 개의 레코드 위치가 서로 바뀌어 있었다. 그 지점 DEM 50.00 ↔ 원본 GL 50.12(차 0.12) vs 52.80(차 2.80) 으로 판정. DEM 이상치 검사가 잡는 전형.
- **'누락' 관로의 정체** — 원본 46 ↔ DB 42 의 차이 4건은 전부 `기존폐쇄`(배수설비 평면도 노란색 레이어)였다. 종평면도는 라벨 색으로 구분이 안 돼 평면도에서 확인했다.
- **오프셋 실측값** — 한 발주처 준공도 두 지구가 같은 오프셋 (+71.45, +100305.77) 이었다(관로 선형 끝점 4점, 0.12 m 이내 일치). 다른 프로젝트에 그대로 쓰지 말고 반드시 재산정한다.
