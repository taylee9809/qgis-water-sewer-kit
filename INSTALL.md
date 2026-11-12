# 설치 — Claude 가 따르는 절차

사람은 Claude Code 에게 "https://github.com/taylee9809/qgis-water-sewer-kit 의 INSTALL.md 대로 설치해줘" 라고만 하면 된다.
아래는 Claude 가 따르는 순서다. 각 단계 끝에 확인 명령이 있다.

## 0. 준비물 확인
| 항목 | 확인 |
|---|---|
| QGIS 3.22 이상(3.40 LTR 권장) | `python-qgis-ltr.bat` 위치 확인(보통 `C:\Program Files\QGIS 3.40.x\bin\`) |
| Python 3.10+ | `python --version` |
| 파이썬 패키지 | `python -X utf8 -c "import geopandas, shapely, ezdxf, openpyxl"` — 없으면 사용자 동의 후 `pip install geopandas shapely ezdxf openpyxl` |
| PostGIS 를 쓰면 | `pip install psycopg2-binary`, 비밀번호는 사용자가 직접 환경변수 `SEWER_PG_PASSWORD` 로 설정(값을 대화에 받지 않는다) |
| DEM 계산(30·32) | `pip install rasterio` — import 전에 `kitconfig.fix_proj()` 필수 |

## 1. 킷 놓기
킷을 프로젝트 **밖** 고정 위치에 받는다(사용자에게 위치를 묻고, 없으면 예시 경로). 프로젝트가 여러 개여도 킷은 하나.

```powershell
git clone https://github.com/taylee9809/qgis-water-sewer-kit "D:\도구\qgis-water-sewer-kit"
git -C "D:\도구\qgis-water-sewer-kit" switch -c local
```
- **`local` 브랜치에서 쓴다.** 이 팀이 킷을 고치면 여기에 커밋으로 남고, 관리자 판(`main`)은 그대로라 업데이트·피드백이 깔끔하다(90-kit-feedback).
- 업데이트: `git -C <킷> fetch origin` 후 `local` 에서 `git -C <킷> rebase origin/main`. 이 팀이 고친 것 중 관리자 판에 들어간 것은 rebase 가 알아서 합친다.
- git 이 없으면 저장소 페이지의 **Code → Download ZIP** 으로 받아 같은 위치에 푼다. 이때는 고친 것을 커밋으로 남길 수 없으니, 고칠 일이 생기면 git 설치를 권한다.

## 2. 스킬 연결 — 정션
각 스킬 폴더를 사용자 스킬 폴더에 **디렉터리 정션**으로 건다(관리자 권한 불필요). 복사하지 않는다 —
스크립트가 킷의 `lib\kitconfig.py` 를 찾아야 하고, 킷을 업데이트하면 바로 반영돼야 한다.

```powershell
$kit = "D:\도구\qgis-water-sewer-kit"          # 실제 경로로
$dst = "$env:USERPROFILE\.claude\skills"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem "$kit\skills" -Directory | ForEach-Object {
  $link = Join-Path $dst $_.Name
  if (Test-Path $link) { Write-Host "이미 있음(건너뜀): $link" }
  else { New-Item -ItemType Junction -Path $link -Target $_.FullName | Out-Null; Write-Host "연결: $link" }
}
```
- 같은 이름의 스킬이 이미 있으면 덮지 말고 사용자에게 알린다.
- 확인: Claude Code 를 다시 열고 `/00-onboard` 가 보이는지.

## 3. QGIS 플러그인
- **Sewer Profile Viewer**(종단도): https://github.com/taylee9809/qgis-sewer-profile 의 README 「AI용 설치 절차」대로.
- **QGIS MCP**(선택 — Claude 가 켜져 있는 QGIS 를 직접 조작): https://github.com/jjsantos01/qgis_mcp — QGIS 플러그인 `qgis_mcp_plugin` 설치 + MCP 서버를 `uv` 로 등록. 플러그인에서 서버를 켜야 연결된다.
  - 주의: 이 연결로 150 dpi 이상 조판 내보내기를 하면 QGIS 가 죽는다 → 고해상 출력은 헤드리스로(51 스킬).
- **korean-engineering-mcp**(선택 — KDS/KCS 건설기준·법제처 법령 검색, 인용 검증, 표준품셈): https://github.com/taylee9809/korean-engineering-mcp
  - 저장소가 안 열리면 킷 관리자에게 접근 권한을 요청한다.
  - 저장소 README 의 「AI 도구에 URL만 주고 설치하기」대로. API 키(`KCSC_API_KEY` 국가건설기준센터, `LAW_API_KEY` 법제처 OPEN API)는 **사용자가 직접 발급받아** MCP 설정의 `env` 에 넣는다(대화에 붙여 넣지 않는다). 표준품셈 검색은 키 없이도 된다.
  - 연결되면 30 스킬이 조문을 원문으로 확인하고 인용을 검증하는 데 쓴다.

## 4. 프로젝트 세팅
프로젝트 폴더에서 Claude Code 를 열고 **00-onboard 스킬**을 실행한다("이 프로젝트에 맞게 킷 세팅해줘").
그 스킬이 데이터 조사 → 대응표 → 질문 → `sewer.config.json` → 설정 검사 → 프로젝트 `CLAUDE.md` 까지 한다.
**DB 가 아직 없으면** 같은 스킬이 표준 스키마(`standard/db-design.md`)로 빈 GPKG 또는 PostGIS DDL 을 만들어 준다.

## 5. 확인
- [ ] `<프로젝트>\sewer.config.json` 이 있고 `check_config.py` 치명 0
- [ ] `<프로젝트>\CLAUDE.md` 의 킷 경로가 실제 경로
- [ ] `%USERPROFILE%\.claude\skills\` 에 8개 정션
- [ ] `git -C <킷> branch --show-current` 가 `local`
- [ ] QGIS 에서 Sewer Profile Viewer 패널이 열리고 계획관로 레이어로 구간 트리가 뜬다
