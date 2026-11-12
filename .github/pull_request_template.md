## 무엇을 왜 · What and why
<!-- [스킬번호] 무엇을 — 왜. 90-kit-feedback 의 summary.md 를 붙여도 됩니다. -->

## 확인 · Checklist
- [ ] 90-kit-feedback `make_feedback.py` 고유명 검사 0건 · scrub check passed (0 hits)
- [ ] 경로·레이어·필드는 `kitconfig` 로 읽는다 — 하드코딩 없음 · no hard-coded paths, layers or fields
- [ ] 원본 데이터는 읽기만 하고 산출은 `outputs.root` 아래 · sources read-only, outputs under `outputs.root`
- [ ] 설정 파일·데이터·비밀번호가 들어 있지 않다 · no config, data or passwords
- [ ] 판정·임계값을 바꿨다면 근거를 적었다 · changed thresholds are justified
- [ ] 새 설정 키가 있으면 `templates/sewer.config.example.json` 과 `standard/data-standard.md` 에 반영 · new config keys documented
