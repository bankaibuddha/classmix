# ClassMix — ROADMAP

Unit charter: local-only team randomizer that beats Team Shake on class
mixing, with student privacy as the constitution — names never leave the
device, ever. Owner: the Commander. Drafted by Control 2026-08-10.

## NOW — in flight

- [x] v0.1 PWA shipped — classes, multi-class pool, even-spread, absent
  toggle, crypto shuffle, shake — gate: live click-through on helios
  (passed 2026-08-10: 12 students, 3 teams, 2-per-class spread verified)
- [x] Public hosting — gate: Pages returns 200 on all 6 assets
  (passed 2026-08-10: https://bankaibuddha.github.io/classmix/)
- [ ] Mac dev handoff — gate: clone builds and serves on the Mac,
  first Mac-side commit pushed
- [ ] Real-device proof — gate: Add-to-Home-Screen on the iPad, offline
  relaunch works, shake permission flow grants and reshuffles

## NEXT — scoped, not cut

- Constraint rules — "keep apart" / "keep together" pairs surviving any
  shuffle — gate: 100-shuffle property test, zero violations — unblocks
  the teacher use case Team Shake handles badly
- Team history — last N generations stored locally, "avoid repeat
  teammates" toggle — gate: repeat-pair rate measurably below random
  baseline across 3 consecutive generations
- Native iPad decision — STUDY: WKWebView wrapper vs SwiftUI rewrite
  (budget: one Mac session; kill: if PWA covers every NEXT feature,
  native waits) — unblocks App Store path
- App Store prep — icon set, screenshots, privacy nutrition label
  (trivially clean: no data collected) — gate: passes App Store review
  checklist dry-run

## LATER — direction

- CSV / paste-from-spreadsheet class import
  — photo path shipped 2026-08-11: scan a paper roster with the camera,
  on-device OCR (vendored tesseract.js, offline-capable, nothing leaves
  the device), teacher reviews/fixes names before import
- Skill/level tiers with balanced draft (Team Shake parity, done better)
  — first tier shipped 2026-08-11: per-student jump ability, 1–3 scale,
  local-only like everything else; balanced draft still pending
- Score keeping per generated team (session-only, same privacy rule)
- Tournaments & PE game modes — brackets, rotations, court assignments
- House PE standards in-app (reference benchmarks, e.g. jumping)
- Ship on the App Store under the ClassMix name

## Graduation log

- 2026-08-10 — unit chartered; v0.1 + hosting entered NOW already done
  (built the same day the unit stood up).
