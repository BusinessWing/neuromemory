# NeuroMemory

自架 AI 記憶系統，基於 Honcho，支援多 Agent 共享記憶 + 主動建議推送到 LINE。

## 功能
- 多 AI Agent 共享記憶（Hermes、OpenClaw、Claude Desktop）
- 自動提煉對話重點（Deriver）
- 三語支援：繁體中文（台灣）/ 日語 / 英語
- Proactive 主動建議層，Dream 結束後自動分析並推送 LINE

## 服務架構
- Honcho Core（記憶引擎）→ Zeabur
- PostgreSQL + pgvector（向量資料庫）→ Zeabur
- Redis（佇列 + 快取）→ Zeabur
- Proactive Layer（主動分析 + LINE 推送）→ Zeabur

## 相關文件
- [Honcho 官方文件](https://docs.honcho.dev)
- [LINE Notify](https://notify-bot.line.me)
