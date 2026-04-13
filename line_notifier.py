import os
import httpx
from typing import Optional

LINE_NOTIFY_TOKEN     = os.getenv("LINE_NOTIFY_TOKEN", "")
LINE_CHANNEL_TOKEN    = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID          = os.getenv("LINE_USER_ID", "")

LINE_NOTIFY_URL       = "https://notify-api.line.me/api/notify"
LINE_PUSH_URL         = "https://api.line.me/v2/bot/message/push"

LABELS = {
    "zh-TW": {
        "header":    "🧠 NeuroMemory 主動建議",
        "insight":   "💡 洞察",
        "action":    "▶️ 建議行動",
        "priority":  "優先度",
        "footer":    "點選回覆或忽略此建議",
        "reply_yes": "✅ 繼續討論這個",
        "reply_no":  "❌ 暫時忽略",
    },
    "ja": {
        "header":    "🧠 NeuroMemory プロアクティブ提案",
        "insight":   "💡 インサイト",
        "action":    "▶️ 推奨アクション",
        "priority":  "優先度",
        "footer":    "返信して続けるか、無視してください",
        "reply_yes": "✅ これについて話す",
        "reply_no":  "❌ 今回はスキップ",
    },
    "en": {
        "header":    "🧠 NeuroMemory Proactive Suggestion",
        "insight":   "💡 Insight",
        "action":    "▶️ Suggested Action",
        "priority":  "Priority",
        "footer":    "Reply to continue or ignore",
        "reply_yes": "✅ Let's discuss this",
        "reply_no":  "❌ Skip for now",
    },
}

def _label(lang: str) -> dict:
    return LABELS.get(lang, LABELS["zh-TW"])

async def send_line_notify(suggestion: dict, lang: str = "zh-TW") -> bool:
    if not LINE_NOTIFY_TOKEN:
        print("[LINE] LINE_NOTIFY_TOKEN not set, skipping")
        return False
    l = _label(lang)
    priority_bar = "█" * suggestion.get("priority", 5) + "░" * (10 - suggestion.get("priority", 5))
    text = (
        f"\n{l['header']}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 {suggestion['topic']}\n\n"
        f"{l['insight']}：\n{suggestion['insight']}\n\n"
        f"{l['action']}：\n{suggestion['action']}\n\n"
        f"{l['priority']}：{priority_bar} {suggestion.get('priority', 0)}/10\n"
        f"━━━━━━━━━━━━━━\n"
        f"{l['footer']}"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            LINE_NOTIFY_URL,
            headers={"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"},
            data={"message": text},
            timeout=10
        )
    success = resp.status_code == 200
    if not success:
        print(f"[LINE Notify] Failed: {resp.status_code} {resp.text}")
    return success

def _build_flex_message(suggestion: dict, lang: str) -> dict:
    l = _label(lang)
    priority = suggestion.get("priority", 0)
    if priority >= 8:
        badge_color = "#E8593C"
        badge_text  = "HIGH"
    elif priority >= 5:
        badge_color = "#EF9F27"
        badge_text  = "MED"
    else:
        badge_color = "#888780"
        badge_text  = "LOW"
    return {
        "type": "flex",
        "altText": f"🧠 {suggestion['topic']}",
        "contents": {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {"type": "text", "text": "🧠 NeuroMemory", "size": "xs", "color": "#888780", "flex": 1},
                    {"type": "box", "layout": "vertical",
                     "contents": [{"type": "text", "text": badge_text, "size": "xxs", "color": "#FFFFFF", "align": "center"}],
                     "backgroundColor": badge_color, "paddingAll": "4px", "cornerRadius": "4px"}
                ], "paddingBottom": "4px"
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "contents": [
                    {"type": "text", "text": suggestion["topic"], "weight": "bold", "size": "md", "wrap": True, "color": "#2C2C2A"},
                    {"type": "separator", "margin": "sm"},
                    {"type": "box", "layout": "vertical", "margin": "sm", "spacing": "xs",
                     "contents": [
                         {"type": "text", "text": l["insight"], "size": "xs", "color": "#888780"},
                         {"type": "text", "text": suggestion["insight"], "size": "sm", "wrap": True, "color": "#444441"}
                     ]},
                    {"type": "box", "layout": "vertical", "margin": "sm", "spacing": "xs",
                     "backgroundColor": "#F1EFE8", "paddingAll": "10px", "cornerRadius": "6px",
                     "contents": [
                         {"type": "text", "text": l["action"], "size": "xs", "color": "#888780"},
                         {"type": "text", "text": suggestion["action"], "size": "sm", "wrap": True, "weight": "bold", "color": "#3d3d3a"}
                     ]}
                ]
            },
            "footer": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "contents": [
                    {"type": "button", "action": {"type": "message", "label": l["reply_yes"], "text": f"繼續討論：{suggestion['topic']}"}, "style": "primary", "height": "sm", "color": "#1D9E75"},
                    {"type": "button", "action": {"type": "message", "label": l["reply_no"], "text": f"忽略：{suggestion['topic']}"}, "style": "secondary", "height": "sm"}
                ]
            }
        }
    }

async def send_line_flex(suggestion: dict, lang: str = "zh-TW") -> bool:
    if not LINE_CHANNEL_TOKEN or not LINE_USER_ID:
        print("[LINE Bot] Channel token or user ID not set, skipping Flex")
        return False
    flex = _build_flex_message(suggestion, lang)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {LINE_CHANNEL_TOKEN}", "Content-Type": "application/json"},
            json={"to": LINE_USER_ID, "messages": [flex]},
            timeout=10
        )
    success = resp.status_code == 200
    if not success:
        print(f"[LINE Bot] Failed: {resp.status_code} {resp.text}")
    return success

async def push_to_line(suggestion: dict, lang: str = "zh-TW") -> bool:
    if LINE_CHANNEL_TOKEN and LINE_USER_ID:
        success = await send_line_flex(suggestion, lang)
        if not success and LINE_NOTIFY_TOKEN:
            return await send_line_notify(suggestion, lang)
        return success
    elif LINE_NOTIFY_TOKEN:
        return await send_line_notify(suggestion, lang)
    else:
        print("[LINE] No LINE credentials configured")
        return False

async def push_suggestions_to_line(
    suggestions: list,
    lang: str = "zh-TW",
    min_priority: int = 6,
    max_push: int = 3
) -> int:
    filtered = sorted(
        [s for s in suggestions if s.get("priority", 0) >= min_priority],
        key=lambda s: s.get("priority", 0),
        reverse=True
    )[:max_push]
    count = 0
    for s in filtered:
        ok = await push_to_line(s, lang)
        if ok:
            count += 1
    return count
