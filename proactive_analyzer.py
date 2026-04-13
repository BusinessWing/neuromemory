"""
NeuroMemory Proactive Analyzer
三語支援：English / 日本語 / 繁體中文（台灣）
觸發時機：Dream 結束後 / 每天定時 / 對話結束後即時
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional
from langdetect import detect_langs
import httpx
from openai import AsyncOpenAI

# ── 設定 ──────────────────────────────────────────────
HONCHO_BASE_URL = os.getenv("HONCHO_BASE_URL", "http://localhost:8000")
HONCHO_WORKSPACE = os.getenv("HONCHO_WORKSPACE", "default")

LLM_BASE_URL  = os.getenv("LLM_BASE_URL", "https://api.minimax.chat/v1")
LLM_API_KEY   = os.getenv("LLM_API_KEY", "")
LLM_MODEL     = os.getenv("LLM_MODEL", "MiniMax-Text-01")

WEBHOOK_URL   = os.getenv("PROACTIVE_WEBHOOK_URL", "")  # LINE / Slack / 留空=不推送
MIN_PRIORITY  = int(os.getenv("PROACTIVE_MIN_PRIORITY", "6"))  # 0-10，低於此不推送

# ── 語言偵測 ─────────────────────────────────────────
def detect_primary_lang(text: str) -> str:
    """偵測主要語言，回傳 'zh-TW' | 'ja' | 'en'"""
    try:
        langs = detect_langs(text)
        top = langs[0].lang
        # langdetect 把繁簡都當 zh-cn，需進一步判斷
        if top in ("zh-cn", "zh-tw", "zh"):
            # 繁體字碼點範圍判斷（台灣常用字）
            trad_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
            simp_markers = len(re.findall(r'[们说没这么个为国来]', text))
            trad_markers = len(re.findall(r'[們說沒這麼個為國來]', text))
            if trad_markers >= simp_markers:
                return "zh-TW"
            return "zh-CN"
        if top == "ja":
            return "ja"
        return "en"
    except Exception:
        return "zh-TW"  # 預設


# ── 系統提示（三語對應版）─────────────────────────────
SYSTEM_PROMPTS = {
    "zh-TW": """你是一個主動分析 AI，負責審閱用戶的記憶摘要，找出值得主動討論的議題。

輸出格式為 JSON 陣列，每筆包含：
- topic: 議題標題（繁體中文，15字內）
- insight: 洞察說明（50字內）  
- action: 具體建議行動（30字內）
- priority: 優先度 0-10（10最高）
- lang: 建議回應語言 ("zh-TW"|"ja"|"en")

原則：
1. 找出「未完成的事」「重複出現的困擾」「可以改善的模式」
2. 不要重複上次已建議過的議題
3. 台灣用語、台灣視角
4. 只輸出 JSON，不要其他文字""",

    "ja": """あなたはユーザーの記憶サマリーを分析し、積極的に議論すべきトピックを見つけるAIです。

出力形式はJSONの配列で、各項目に含める内容：
- topic: トピックのタイトル（日本語、20文字以内）
- insight: 洞察の説明（80文字以内）
- action: 具体的な提案アクション（50文字以内）
- priority: 優先度 0-10（10が最高）
- lang: 推奨返答言語 ("zh-TW"|"ja"|"en")

原則：
1. 「未完了の事項」「繰り返す悩み」「改善できるパターン」を見つける
2. 前回すでに提案したトピックは繰り返さない
3. JSONのみ出力、他のテキストは不要""",

    "en": """You are a proactive analysis AI that reviews user memory summaries to surface topics worth discussing.

Output format: JSON array, each item containing:
- topic: topic title (English, max 10 words)
- insight: insight explanation (max 60 words)
- action: specific recommended action (max 30 words)
- priority: priority 0-10 (10 = highest)
- lang: recommended response language ("zh-TW"|"ja"|"en")

Principles:
1. Identify "unfinished tasks", "recurring frustrations", "improvable patterns"
2. Don't repeat topics already suggested last time
3. Output JSON only, no other text"""
}


# ── Honcho API クライアント ────────────────────────────
async def fetch_recent_conclusions(peer_id: str, limit: int = 40) -> list[dict]:
    """從 Honcho 取得最近的 conclusions（Dream 提煉結果）"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{HONCHO_BASE_URL}/v1/workspaces/{HONCHO_WORKSPACE}"
            f"/peers/{peer_id}/conclusions",
            params={"limit": limit, "order": "desc"}
        )
        if resp.status_code == 200:
            return resp.json().get("items", [])
        return []


async def fetch_recent_sessions_summary(peer_id: str, limit: int = 5) -> str:
    """取得最近幾個 session 的 summary"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{HONCHO_BASE_URL}/v1/workspaces/{HONCHO_WORKSPACE}"
            f"/peers/{peer_id}/sessions",
            params={"limit": limit, "order": "desc"}
        )
        summaries = []
        if resp.status_code == 200:
            sessions = resp.json().get("items", [])
            for s in sessions:
                if s.get("summary"):
                    summaries.append(s["summary"])
        return "\n---\n".join(summaries)


# ── 語言自動偵測（從 conclusions 文字）────────────────
def infer_user_lang(conclusions: list[dict]) -> str:
    sample = " ".join(c.get("content", "") for c in conclusions[:10])
    return detect_primary_lang(sample) if sample.strip() else "zh-TW"


# ── LLM 分析 ─────────────────────────────────────────
async def analyze_with_llm(
    conclusions_text: str,
    sessions_summary: str,
    past_topics: list[str],
    lang: str
) -> list[dict]:
    client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    system = SYSTEM_PROMPTS.get(lang, SYSTEM_PROMPTS["zh-TW"])

    user_msg = f"""=== 用戶記憶摘要（Conclusions）===
{conclusions_text}

=== 最近對話摘要 ===
{sessions_summary or '（無）'}

=== 上次已建議的議題（請勿重複）===
{chr(10).join(past_topics) if past_topics else '（無）'}

請分析以上內容，找出 3-5 個值得主動討論的議題。"""

    try:
        resp = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.4,
            max_tokens=800
        )
        raw = resp.choices[0].message.content.strip()
        # 清理 markdown code block（如果 LLM 加了的話）
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        suggestions = json.loads(raw)
        return [s for s in suggestions if isinstance(s, dict)]
    except Exception as e:
        print(f"[ProactiveAnalyzer] LLM error: {e}")
        return []


# ── 推送（Webhook 通用版）────────────────────────────
async def push_suggestion(suggestion: dict, lang: str):
    if not WEBHOOK_URL:
        return
    labels = {
        "zh-TW": {"title": "🧠 主動建議", "priority": "優先度"},
        "ja":    {"title": "🧠 プロアクティブ提案", "priority": "優先度"},
        "en":    {"title": "🧠 Proactive Suggestion", "priority": "Priority"},
    }
    l = labels.get(lang, labels["zh-TW"])
    text = (
        f"{l['title']}\n"
        f"📌 {suggestion['topic']}\n"
        f"💡 {suggestion['insight']}\n"
        f"▶️ {suggestion['action']}\n"
        f"{l['priority']}: {suggestion['priority']}/10"
    )
    async with httpx.AsyncClient() as client:
        # LINE Notify 格式（也相容大部分 webhook）
        await client.post(
            WEBHOOK_URL,
            headers={"Content-Type": "application/json"},
            json={"message": text}
        )


# ── 主流程 ────────────────────────────────────────────
async def run_proactive_analysis(
    peer_id: str,
    past_topics: Optional[list[str]] = None,
    force_lang: Optional[str] = None,
    dry_run: bool = False
) -> list[dict]:
    """
    執行主動分析。
    peer_id: Honcho peer ID（每個 Agent 不同）
    past_topics: 上次已建議的議題清單（避免重複）
    force_lang: 強制指定語言；None = 自動偵測
    dry_run: True = 只分析不推送
    """
    print(f"[ProactiveAnalyzer] Starting analysis for peer: {peer_id}")

    conclusions = await fetch_recent_conclusions(peer_id)
    if not conclusions:
        print("[ProactiveAnalyzer] No conclusions found, skipping.")
        return []

    sessions_summary = await fetch_recent_sessions_summary(peer_id)
    conclusions_text = "\n".join(
        f"- [{c.get('created_at', '')[:10]}] {c.get('content', '')}"
        for c in conclusions
    )

    lang = force_lang or infer_user_lang(conclusions)
    print(f"[ProactiveAnalyzer] Detected language: {lang}")

    suggestions = await analyze_with_llm(
        conclusions_text,
        sessions_summary,
        past_topics or [],
        lang
    )

    # 按優先度排序，過濾低優先
    suggestions.sort(key=lambda s: s.get("priority", 0), reverse=True)
    high_priority = [s for s in suggestions if s.get("priority", 0) >= MIN_PRIORITY]

    print(f"[ProactiveAnalyzer] Found {len(suggestions)} suggestions, "
          f"{len(high_priority)} above priority threshold ({MIN_PRIORITY})")

    if not dry_run:
        for s in high_priority:
            await push_suggestion(s, lang)

    return suggestions


# ── CLI 入口 ──────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NeuroMemory Proactive Analyzer")
    parser.add_argument("--peer-id", required=True, help="Honcho peer ID")
    parser.add_argument("--lang", choices=["zh-TW", "ja", "en"],
                        help="Force output language (auto-detect if omitted)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze only, don't push notifications")
    parser.add_argument("--past-topics", nargs="*", default=[],
                        help="Previously suggested topics to avoid repeating")
    args = parser.parse_args()

    results = asyncio.run(run_proactive_analysis(
        peer_id=args.peer_id,
        past_topics=args.past_topics,
        force_lang=args.lang,
        dry_run=args.dry_run
    ))
    print("\n=== Proactive Suggestions ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
