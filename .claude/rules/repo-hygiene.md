# Repo 邊界與整潔規則

1. 第三方 repo 不可被本專案 git 追蹤，不使用 git submodule / subtree。
2. `workers/` 與 `tools/` 只追蹤 `.gitignore`、`manifest.json` 等控制檔，其餘下載內容忽略。
3. `projects/`、`.cache/`、模型權重、暫存檔、local overrides 一律放入 `.gitignore`。
4. 個人化 Claude 設定使用 `CLAUDE.local.md`、`.claude/settings.local.json`，且不可提交。
