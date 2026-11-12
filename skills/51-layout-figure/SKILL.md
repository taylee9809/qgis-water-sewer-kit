---
name: 51-layout-figure
description: 하수도 보고서·제안서용 QGIS 삽도(위치도·검토도)와 조판(A1 계획평면도 등)을 만들고 고칠 때의 시각 문법·배치 규칙·PyQGIS 파이프라인. 관로 흰 테두리·검토구간 색띠·키표·범례·라벨 엔진 우선순위, 조판 한 장에만 스타일 적용, 헤드리스 고해상 출력, 출력 검수. 삽도, 위치도, 조판, 계획평면도 출력, 범례·라벨 겹침 수정 요청 시 사용.
---

# 51 조판·삽도 작성 원칙

## 0. 먼저 볼 것
- 그 팀에 **기존 삽도·제안서 그림**이 있으면 그것이 정답이다. 먼저 몇 장을 보고 아래 1절 표를 그 팀 문법으로 고쳐 프로젝트 `CLAUDE.md` 에 적는다. 없으면 아래 문법을 기본으로 쓴다.
- QGIS 조작은 QGIS MCP(라이브 QGIS) 또는 `python-qgis-ltr.bat` 헤드리스 스크립트로. 레이어는 `sewer.config.json` 의 논리 레이어로 찾는다.

## 1. 시각 문법 (우리 팀 기본)
| 요소 | 규칙 |
|---|---|
| 관로 | **굵은 선 + 흰 테두리**. 반드시 **심볼 레벨**을 켠다(안 켜면 접합부에서 흰 테두리가 검정선을 끊는다). 기존 = 검정(실선 자연유하 / 점선 압송), 계획 = 원색 파랑 실선 / 빨강 점선. 톤다운 색은 힘이 없다 |
| 검토구간 | 검정 관로 **뒤에 굵은 반투명 색띠**(9 mm, α≈50%) + 띠 색 원형 번호 ①~⑤. 색은 구간 구분에만, 선 자체 색은 유지 |
| 시설 | 큰 아이콘(펌프 12 mm, 처리장 15 mm) + 강조 시 점선 위치원. 기존 = 검정, 계획 = 빨강 |
| 라벨 | 대상 **바로 옆**, 인출선 없음. 지명·시설명 = 흰 바탕 알약/상자(얇은 검정 테두리). 강조 = 진한 바탕 + 흰 글자. 관로명은 선을 따라 흰 테두리 글자 |
| 제원 | 지도에 쓰지 말고 **키표**(구분/번호/관로·시설/제원/연장)로 지도 밖에. 지도에는 번호·이름만 |
| 범례 | 표형(기존/계획/검토) 또는 네이티브 범례. 항목 최소 |
| 흑백 안전 | 색 없이도 읽히게 굵기·점선·빗금·번호로 구분(제안서는 흑백 인쇄되기도 한다) |

## 2. 배치 규칙
- 라벨은 **QGIS 라벨 엔진(PAL)** 에 맡긴다: 룰 기반 라벨 + 우선순위(구간·펌프 10 > 압송관 8 > 보조 3). 좌표를 손으로 계산해 딱지를 놓지 않는다.
- 라벨 배경(알약/원) = `QgsTextBackgroundSettings`(ShapeRectangle/ShapeCircle, SizeBuffer).
- 짧은 선의 라벨은 `Line` 배치 + `setOverrunDistance`, 긴 선은 `Horizontal`(구간번호) 또는 `Line`(관로명).
- 조판 마커·라벨 아이템은 PAL 장애물이 아니다 → 겹치면 레이어 라벨로 바꾼다.
- 시각 위계: 위성(100%) < 기타 기존관(회색 1.0 mm) < 계획노선(원색 1.4 mm) < 검토대상(검정 2.4 mm + 색띠) < 아이콘·번호.

## 3. PyQGIS 파이프라인
1. **조판 한 장에만 스타일**: `map.setKeepLayerSet(True)`, **`setKeepLayerStyles(True)`**, `setLayerStyleOverrides({id: QgsMapLayerStyle.xmlData()})`. keepLayerStyles 를 안 켜면 재시작 때 사라진다.
2. **범례** `QgsLayoutItemLegend`: `addLayoutItem` **후** `setAutoUpdateModel(False)` → `model().rootGroup()` 을 **제자리에서 비우고** addLayer(`setRootGroup(새 트리)` 는 저장 안 되고, 파이썬이 만든 트리가 GC 되면 QGIS 가 죽는다) → **`setLegendFilterByMapEnabled(True)`**(안 켜면 다시 열 때 원본 스타일로 그려진다) → `model().setLayerStyleOverrides(map.layerStyleOverrides())` → `refreshLayerLegend` → 노드 제목 Hidden, `setLegendNodeOrder` 로 항목 고르기. 크기는 1차 렌더 후 확정되니 렌더 뒤 재배치.
3. **키표**: `QgsLayoutItemManualTable.create(layout)` + `QgsLayoutFrame`.
4. 사람이 직접 옮겨야 하는 라벨(구간 번호원·시설명·관로명)은 PAL 대신 **조판 아이템**(Shape 타원 + Label 그룹, Label 회전). 위치는 피처 지오메트리(중점·방향각)로 계산하고, 헤드리스 폰트 메트릭 오차 때문에 라벨 폭 ×1.3 + 3 mm.
5. **고해상 출력은 헤드리스로**: `python-qgis-ltr.bat <run_headless.py> <qgz> <build.py> <out> 300`. MCP(라이브 QGIS)에서 150 dpi 이상으로 내보내면 QGIS 가 죽는다. `QT_QPA_PLATFORM=offscreen` 은 한글 폰트를 깨뜨리니 쓰지 않는다.
6. **검수**: 70 dpi 전체 + 150 dpi 크롭(밀집 구역·범례·제목)을 **눈으로** 보고 겹침·잘림·빈 범례를 고친 뒤 넘긴다.

## 4. 하지 말 것
- 좌표 계산으로 SVG 딱지 + 인출선 깔기(실패한 방식)
- 기존/계획을 같은 원색으로(혼동)
- 맨홀 번호 같은 잔 라벨 남발(요청 없으면 숨김)
- 조판을 하나로 합치거나 Atlas 로 바꾸자는 제안을 먼저 꺼내기 — 사용자가 조판을 손으로 먼저 만들고 나중에 정리하는 흐름일 수 있다. 물어볼 것

## 5. 외부 전달
QGIS 프로젝트를 다른 사람에게 넘길 때는 킷 `standard\pitfalls-qgis-postgis.md` 「프로젝트 배포」 절대로. **커스텀 SVG 심볼 폴더를 같이 묶는다.**
