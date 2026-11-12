English | [한국어](README.md)

# qgis-water-sewer-kit — water & sewer design with Claude

A skill kit for doing water-supply and sewer design and review on top of QGIS data while handing the repetitive
work to [Claude Code](https://claude.com/claude-code) — packaged so **each team can adapt it to its own project**.
People don't memorize procedures; Claude reads the kit's skills and runs them.

> Right now the kit contains **sewer-network skills only**. Water-supply skills are planned.
> The skills and docs are written in Korean, following Korean design standards (KDS) and Korean drawing conventions.

![One config file adapts the kit to your project — Korean](docs/how-it-works.svg)

![One config file adapts the kit to your project — English](docs/how-it-works.en.svg)

The skills and scripts never hard-code layer names. They read layers, fields, the database connection and design
criteria from a single `sewer.config.json` at the project root. If your layer names differ, you change the config —
not your data. The onboarding skill writes that config by surveying your data.

## What people do — three things
1. **Install** — tell Claude Code: "Install https://github.com/taylee9809/qgis-water-sewer-kit following its INSTALL.md."
2. **Set up** — in your project folder: "Set up the kit for this project." Claude surveys your data and asks only about what it cannot settle (about five questions).
3. **Ask and check** — pick a task from the table below and check the result against "Before you trust a result".

Requirements: Claude Code · QGIS 3.22+ (3.40 LTR recommended) · Python 3.10+. PostGIS is optional.

## What you can ask for
| Say something like | Skill | Result | What a person checks |
|---|---|---|---|
| "The survey came in — build the obstacle layer" | 11 | Obstacle polygons and centerlines + a **needs-review** layer | The needs-review list (usually 3–5 items), against drawings and street view |
| "Load the as-built DXF into the DB" / "Is the as-built DB right?" | 12 | As-built pipes and manholes loaded + a comparison against the source | Coordinate offset converged (within 0.2 m); zero mismatches in names, ground levels, depths |
| "Run inverts and hydraulics for this route" / "Size the pump station" | 30 | Inverts, diameters, flows, pass/fail, makesw `.flo`, pump head and power | Which values are standards and which are assumptions; sections short of cover or velocity |
| "Can we drop this force main?" / "Compare connection manholes" | 32 | Feasible manholes + an alternatives table | Cover and deep-excavation stretches; operating cost |
| "Review the consultant's delivery" (folder name only) | 40 | `검토요약.md` — findings on lengths, network, connection inverts, designer notes | Each finding as "delivered vs reference — difference — impact" |
| "Make a location figure for this section" | 51 | Print layout + 300 dpi image | Overlaps, clipping, empty legends (by eye) |
| Other stages (cost, report, review responses) | — | Claude writes project scripts under `scripts\` | Sources of numbers; standards vs assumptions |
| "Send my kit changes back" | 90 | A bundle of fixes, failure notes and new tools → PR, issue or file | Zero hits in the name scan; what is being sent |

## Workflow
![Workflow stages 00–80 — Korean](docs/workflow.svg)

![Workflow stages 00–80 — English](docs/workflow.en.svg)

For each request Claude first decides which stage it belongs to. Stages with a skill follow the skill; the rest get
project-specific scripts. **Vendor review (40) only holds if the as-built DB (12) is right** — check stage 12 before
stage 40.

## Before you trust a result
- **Are the sources untouched?** The kit only reads source data and writes outputs under `_산출\`. Any DB change must come with the backup table name and the number of rows changed.
- **Is "zero" different from "not surveyed"?** Where there is no survey, obstacles are "not surveyed", not "zero crossings".
- **Standard or assumption?** Numbers headed for a report must say whether they come from KDS / the project brief or from an assumption Claude made.
- **Is judgment left to people?** Cases the drawings cannot settle must come back as a review list, not be decided by Claude.

## Layout
| Folder | Contents |
|---|---|
| `skills\` | Eight skills — 00-onboard · 11 · 12 · 30 · 32 · 40 · 51 · 90-kit-feedback |
| `lib\kitconfig.py` | Config loader used by every script (paths, layers, fields, DB; passwords only via environment variables) |
| `standard\` | Data standard (logical layers and fields) · workflow · two pitfall notes (reading DXF / QGIS & PostGIS) |
| `templates\` | `sewer.config.example.json` (a real project's layout as the example) · a `CLAUDE.md` for project folders |
| `docs\` | Images for this README |

Skills 30 and 51 are method documents only (no scripts). Real implementations are tied too tightly to project data, so
each team's Claude writes them from the method.

## What was verified, and known limits
- Scripts for 11, 12, 32 and 40 were run against real project data and **match the original tools** (11: CSVs byte-identical; 12 and 32: identical output; 40: identical A1–A5 results).
- PostGIS reading was checked against a real database for 12 and 32 only; 11 and 40 were checked with GPKG. Teams on PostGIS should eyeball the first run.
- Skill 11 **writes obstacle layers to GPKG only**.
- Skill 11's tools read the config first, so run them **inside a project folder** (outside one, even `--help` only prints the setup hint).
- DXF layer names and label radii are **one surveyor's / designer's drawing conventions**. For other firms' drawings, change them in the config (`asbuilt.dxf_layers`, `review_vendor.profile`) or via arguments; for 11, edit the layer list in the tool README.

## Companion tools
- [Sewer Profile Viewer](https://github.com/taylee9809/qgis-sewer-profile) — QGIS plugin: route section tree + profile view. Claude installs it during setup.
- [QGIS MCP](https://github.com/jjsantos01/qgis_mcp) (optional) — lets Claude drive a running QGIS.

## Sending changes back, and updating
- Use the kit on a **`local`** branch (created at install). Each team's fixes stay there as commits and survive updates.
- To send fixes, failures or new tools back, tell Claude **"send my kit changes back"** — 90-kit-feedback bundles them, scans for your project's place names, paths and passwords, and **asks you before** sending a PR, an issue or a file.
- The maintainer reviews (including a regression run) and merges; everyone gets it on the next update. Anything not merged stays on your `local` branch.
- Update: `git -C <kit> fetch origin` → `git -C <kit> rebase origin/main`. Skills are linked by junctions, so nothing needs re-linking.
- Details: [CONTRIBUTING.md](CONTRIBUTING.md) · maintainer process: [MAINTAINING.md](MAINTAINING.md) · [CHANGELOG.md](CHANGELOG.md)
