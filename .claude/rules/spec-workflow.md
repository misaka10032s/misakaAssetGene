# Spec-first workflow

1. A new requirement goes back to `docs/superpowers/specs/spec.md` first, and the `architect` role discusses feasibility and implementation details.
2. After the discussion, write the conclusion back to `docs/superpowers/specs/spec.md`, keeping the spec the single source of truth.
3. If a requirement involves roles, workflow or repo rules, `CLAUDE.md` and the matching `.claude/` documents must be updated in step.
4. `.plan/RESEARCH_LOG.md` must keep the research conclusions and status, and completed items are marked **「已完成」**.
5. Every subagent report file for a development task must state explicitly: current progress, how to verify, the done assessment, and the next step.
6. Verification cannot rest on a file merely existing; it must check against the items of `docs/superpowers/specs/spec.md` with structural verification, behavior verification, or build/dev verification.
