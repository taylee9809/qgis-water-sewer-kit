# 기여 안내 · Contributing

킷을 쓰다가 고친 것·안 된 것·새로 만든 도구는 관리자에게 돌려보내 주세요. 다음 판에서 모두가 받습니다.
대부분은 **Claude 에게 "킷 고친 거 보내줘" 라고 하면** 90-kit-feedback 스킬이 정리해서 보냅니다(보내기 전에 꼭 확인을 받습니다).

## 흐름
1. 킷은 **`local` 브랜치**에서만 고칩니다(설치 때 만듦). 고칠 때마다 커밋 하나 — `[스킬번호] 무엇을 — 왜`.
2. 못 고친 실패는 프로젝트의 `_산출\kit-feedback\notes\` 에 기록합니다.
3. 보낼 때 `make_feedback.py` 가 프로젝트 고유명(지구·경로·업체)·비밀번호를 검사합니다. **0건이어야** 보낼 수 있습니다.
4. 보내는 길: **PR**(GitHub 계정 있으면, 권장) · **이슈**(코드 없이 기록·제안만) · **파일**(꾸러미 zip 을 관리자에게).
5. 관리자가 합치면 다음 업데이트(`fetch` + `rebase origin/main`) 때 그 수정이 본판으로 들어옵니다. 안 합쳐진 수정은 `local` 에 남아 계속 쓸 수 있습니다.

## 받아들여지려면
- 경로·레이어·필드는 `lib\kitconfig.py` 로 읽는다 — 하드코딩 없음
- 원본 데이터는 읽기만 하고, 산출은 `outputs.root` 아래
- 설정 파일·데이터·비밀번호·프로젝트 고유명이 없다
- 판정·임계값을 바꿨다면 근거(KDS 조문, 과업 기준, 사례)를 적었다
- 새 설정 키는 `templates/sewer.config.example.json` 과 `standard/data-standard.md` 에 같이 적었다

---

## English
Send back what you fixed, what failed and what you built — ask Claude "send my kit changes" and the **90-kit-feedback** skill packages it (and asks you before sending anything).

1. Change the kit only on the **`local`** branch, one commit per fix: `[skill] what — why`.
2. Log unfixed failures under `_산출\kit-feedback\notes\` in your project.
3. `make_feedback.py` scans for project-specific names (places, paths, companies) and passwords; it must report **zero hits**.
4. Send as a **PR** (preferred), an **issue** (notes only), or a **bundle zip** to the maintainer.
5. Once merged, `git fetch` + `git rebase origin/main` brings it into the main line; anything not merged stays on your `local` branch.

To be accepted: read paths/layers/fields through `kitconfig`, keep sources read-only, include no config/data/passwords/project names, justify changed thresholds, and document new config keys.
