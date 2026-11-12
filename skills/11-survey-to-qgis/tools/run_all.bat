@echo off
chcp 65001 > nul
rem ── 측량현황도 → QGIS 지장물 점검 일괄 실행 (보고 모드 — GPKG 를 고치지 않는다)
rem    사용: run_all.bat [sewer.config.json] [원본 측량현황도.dxf]
rem      · sewer.config.json 을 이 파일 위로 끌어다 놓아도 된다.
rem      · 설정을 안 주면 환경변수 SEWER_CONFIG, 그다음 현재 폴더에서 위로 찾는다.
rem      · 원본 DXF 를 주면 01 로 경량본 [산출]\11-survey\survey_light.dxf 부터 만든다.
rem      · 06 은 설정 survey.flo 의 종단 .flo 를 쓴다 - 없으면 사용법만 찍고 넘어간다.
rem    실제 반영(--write)은 README 의 명령을 하나씩 직접 실행할 것.
setlocal
set "T=%~dp0"
set "PY=python -X utf8"
if not "%~1"=="" set "SEWER_CONFIG=%~f1"

if not "%~2"=="" (
  echo ================ 01 원본 DXF 경량화 ================
  %PY% "%T%01_filter_dxf.py" "%~2" || goto :err
)

echo.
echo ================ 03 ㄷ자 두께2배 검출 ================
%PY% "%T%03_fix_digeut.py" || goto :err
echo ---- 확인필요 레이어 ----
%PY% "%T%03_fix_digeut.py" --review || goto :err

echo.
echo ================ 04 라벨 오귀속·미상 규격 ================
%PY% "%T%04_check_labels.py" || goto :err

echo.
echo ================ 05 품질 플래그 - 합격 기준 ================
%PY% "%T%05_qc_flags.py" || goto :err

echo.
echo ================ 06 설계사 종단 교차 검증 ================
%PY% "%T%06_check_profile.py" || goto :err
echo ---- 확인필요 레이어 - 11 --promote 입력 ----
%PY% "%T%06_check_profile.py" --review || goto :err

echo.
echo 완료. [산출]\11-survey 의 03_digeut / 04_labels / 05_qc_flags / 06_profile_check CSV 를 확인하십시오.
pause
exit /b 0

:err
echo.
echo *** 오류로 중단됨 ***
pause
exit /b 1
