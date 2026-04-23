# Contributing to MisakaAssetGene

感謝你想參與 MisakaAssetGene。

## 先讀文件

1. `spec.md`：產品與架構規格的單一事實來源
2. `CLAUDE.md`：Claude 協作總則與角色入口
3. `.plan/DEVELOPMENT_PLAN.md`：開發角色與工作流
4. `.plan/RESEARCH_LOG.md`：研究結論與規格修訂背景

## 貢獻原則

1. 新需求或重大變更，先和 `architect` 角色對照 `spec.md` 討論可行性，再提交實作。
2. 如果你的變更會影響功能、流程或 repo 邊界，請同步更新 `spec.md`、`CLAUDE.md` 或 `.claude/` 文件。
3. 第三方 repo、模型權重、下載工具與使用者專案不得直接提交到本 repo。
4. 任何與授權、外部依賴、模型來源相關的內容，請在 PR 說明中寫清楚。

## 建議流程

1. Fork / branch
2. 先更新規格或補研究紀錄（如需要）
3. 實作與驗證
4. 提交 PR，描述問題、方案、影響範圍與驗證方式

## Pull Request 檢查表

- 變更內容與 `spec.md` 一致
- 沒有提交第三方 repo、模型、快取或個人設定
- 文件與 changelog 已同步
- 如果需求已完成，`.plan/RESEARCH_LOG.md` 有標註 **「已完成」**
