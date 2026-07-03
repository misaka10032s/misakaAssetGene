# misakaAssetGene — Claude collaboration guide

Desktop-first multimodal asset workbench. Consultant-style dialogue integrates image generation,
character lines, voice, songs, and video — plus downstream LoRA/GPT-SoVITS training pipelines.
Stack: Tauri + Vue 3/Vite/UnoCSS (frontend) · Python/FastAPI (core API) · Ollama (local LLM).

> Cluster conventions (git authority, language, i18n, ports, layout) are BINDING and live at
> D:/backup/CSIA/@PM/.claude/context/cluster-conventions.md — Read it before any work here.

## Delegation & verification

- Orchestration, model tiering, and dispatch rules: D:/backup/CSIA/@PM/.claude/context/model-dispatch-doctrine.md
- Decision rubrics (escalate / done / ask / change course): D:/backup/CSIA/@PM/.claude/context/judgment-rubrics.md
- Whoever produced work never certifies it — verification runs in a fresh-context agent.
- Every done/correct/dead/broken claim carries evidence: file:line, test output, or read-back.
- Target missing or contradicting the task → STOP and ask; never scaffold around it.

## Context index

- `context/spec.md` — **SINGLE SOURCE OF TRUTH (spec-first). MUST read before ANY change; update spec here FIRST, then code.**

## Core principles

1. **Spec-first:** When a new requirement arrives, discuss feasibility, architectural impact, risks, and implementation approach with the `architect` role using `.claude/context/spec.md`, then update `.claude/context/spec.md` only after confirmation (spec lives at `.claude/context/spec.md`).
2. **Plan-aware:** `.plan/DEVELOPMENT_PLAN.md` defines development roles and workflows; `.plan/RESEARCH_LOG.md` records research conclusions and spec amendments. Completed items must be marked as **Done** in the research log.
3. **Repo boundary:** Always treat third-party repos as external dependencies — use an independent clone or download artifacts; they must not be tracked by this project's git; no submodule / subtree.
4. **Multimodal by default:** Feature designs must not assume a single asset output type; must be able to handle composite deliverables including images, character lines, character voices, songs, videos, and animated stills.
5. **Open-source friendly:** Any workflow, spec, and documentation should consider readability, executability, and license clarity for external contributors.
6. **Truthful delivery:** Never describe a skeleton, stub, or PoC as a completed milestone; when reporting, clearly distinguish "Done", "Partially done", and "Not done".

## Work entry points

- Spec discussion: use `.claude/commands/spec-discuss.md`
- Spec sync: use `.claude/commands/update-spec.md`
- Plan review: use `.claude/commands/review-plan.md`

## Rule modules

- `.claude/rules/spec-workflow.md`: standard workflow from requirement to spec
- `.claude/rules/multimodal-assets.md`: composite asset and output design constraints
- `.claude/rules/repo-hygiene.md`: repo boundary, gitignore, and external dependency rules
- `.claude/rules/community-workflow.md`: open-source contribution and review workflow
- `.claude/rules/frontend-standards.md`: frontend i18n, types, RWD, styles, and comment standards

## Ports

All local services bind to `127.0.0.1`; ports are defined centrally in `.env`:

- **Frontend** `http://127.0.0.1:8400`, **Core API** `http://127.0.0.1:8401`, **Ollama** `http://127.0.0.1:11434`

> Note: MisakaAssetGene is a desktop Tauri app (not a browser-delivered web service). The canonical
> browser-testing rules (in `cluster-conventions.md`) apply when verifying the embedded WebView or
> the Vite dev-server URL during development.

## Dev mode and diagnostic standards

1. **Diagnostic output during development must be controlled by mode / env.**
   - Python backend uses `MISAKA_ENV=dev`
   - Frontend / Vite uses `VITE_MISAKA_ENV=dev` and `--mode development`
   - Production builds must not output development debug messages by default
2. **Build and dev must be isolated.**
   - dev server, typecheck, build, doctor, and manager must each have a clearly defined command entry point
   - When verifying, state whether it is a dev verification, build verification, or API/behavior verification
3. **Development messages serve only verification purposes and must not pollute the end-user experience.**
4. **When adding diagnostic output, simultaneously document the launch method, expected output, and disable condition.**
5. **Env naming segregation:** backend reads `MISAKA_*` and provider secrets; frontend reads only `VITE_MISAKA_*`.

## Report format (must be included with every development progress report)

1. **Current progress:** corresponding `.claude/context/spec.md` / milestone / item
2. **How to verify:** command, page, API, expected output
3. **Current assessment:** Done / Partially done / Not done
4. **Next step:** the next most reasonable development or acceptance action

For milestone acceptance, additionally list:
- Which items passed
- Which items are still missing
- Which are only scaffold / stub

## Role assignments

| Role | Primary responsibilities |
| --- | --- |
| `architect` | Requirement feasibility, system layering, spec gatekeeping |
| `backend` | FastAPI core, file system, project management, metadata |
| `ai-ml` | RAG, prompt engineering, LLM routing, generation/training workflows |
| `frontend` | Tauri/Vue UI, version tree, asset browsing and interaction |
| `ui-ux` | Dialogue experience, visual hierarchy, onboarding and usability |
| `devops` | Setup, packaging, cross-platform installation, tool and worker management |
| `qa-sdet` | Smoke / integration / E2E test strategy |
| `security` | Permission boundaries, command safety, sensitive data sanitization |

See `.claude/agents/` for detailed personas.
