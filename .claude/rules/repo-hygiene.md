# Repo boundary and hygiene rules

1. A third-party repo must not be tracked by this project's git, and git submodule / subtree is not used.
2. `workers/` and `tools/` track only control files such as `.gitignore` and `manifest.json`; all other downloaded content is ignored.
3. `projects/`, `.cache/`, model weights, temporary files and local overrides always go into `.gitignore`.
4. Personal Claude settings use `CLAUDE.local.md` and `.claude/settings.local.json`, and must not be committed.
