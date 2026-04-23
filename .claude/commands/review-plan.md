# /project:review-plan

檢查目前專案文件是否仍符合 `.plan/DEVELOPMENT_PLAN.md` 的角色分工、工作流與品質要求。

## 檢查重點
- `CLAUDE.md` 與 `.claude/agents/` 是否涵蓋 Dream Team 全部角色
- `spec.md` 是否反映最新需求與工作流
- `.gitignore` 是否確保第三方 repo / 下載產物不被追蹤
- `CONTRIBUTING.md` 與 `LICENSE` 是否齊備

## 輸出
- 缺口清單
- 建議修補順序
- 可標註為 **「已完成」** 的項目
