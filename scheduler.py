"""
NeuroMemory Scheduler
支援三種觸發模式：
  1. dream_hook   — Honcho Dream 結束後 webhook 觸發
  2. daily        — 每天固定時間（Asia/Taipei）
  3. session_end  — 對話結束後即時分析（輕量版）
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import zoneinfo

from proactive_analyzer import run_proactive_analysis

# ── 設定 ───────────────────────────────────────────────
SCHEDULER_PORT   = int(os.getenv("SCHEDULER_PORT", "8100"))
DAILY_HOUR_JST   = int(os.getenv("PROACTIVE_DAILY_HOUR", "9"))   # 台灣/日本早上 9 點
DAILY_MINUTE     = int(os.getenv("PROACTIVE_DAILY_MIN", "0"))
STATE_DB_PATH    = Path(os.getenv("STATE_DB_PATH", "/data/proactive_state.db"))

TZ = zoneinfo.ZoneInfo("Asia/Taipei")

app = FastAPI(title="NeuroMemory Proactive Scheduler", version="1.0.0")

# ── 狀態管理（SQLite 輕量持久化）─────────────────────
def init_db():
    STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS past_topics (
            peer_id TEXT,
            topic   TEXT,
            created_at TEXT,
            PRIMARY KEY (peer_id, topic)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_id TEXT,
            trigger TEXT,
            lang TEXT,
            suggestions_count INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def load_past_topics(peer_id: str, limit: int = 20) -> list[str]:
    conn = sqlite3.connect(STATE_DB_PATH)
    rows = conn.execute(
        "SELECT topic FROM past_topics WHERE peer_id=? ORDER BY created_at DESC LIMIT ?",
        (peer_id, limit)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def save_topics(peer_id: str, topics: list[str]):
    now = datetime.now(TZ).isoformat()
    conn = sqlite3.connect(STATE_DB_PATH)
    for t in topics:
        conn.execute(
            "INSERT OR REPLACE INTO past_topics VALUES (?,?,?)",
            (peer_id, t, now)
        )
    conn.commit()
    conn.close()


def log_analysis(peer_id: str, trigger: str, lang: str, count: int):
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.execute(
        "INSERT INTO analysis_log (peer_id, trigger, lang, suggestions_count, created_at) "
        "VALUES (?,?,?,?,?)",
        (peer_id, trigger, lang, count, datetime.now(TZ).isoformat())
    )
    conn.commit()
    conn.close()


# ── 核心分析包裝 ──────────────────────────────────────
async def do_analysis(peer_id: str, trigger: str, force_lang: Optional[str] = None):
    past = load_past_topics(peer_id)
    suggestions = await run_proactive_analysis(
        peer_id=peer_id,
        past_topics=past,
        force_lang=force_lang
    )
    if suggestions:
        new_topics = [s["topic"] for s in suggestions]
        save_topics(peer_id, new_topics)
        detected_lang = suggestions[0].get("lang", "zh-TW")
        log_analysis(peer_id, trigger, detected_lang, len(suggestions))
    return suggestions


# ── Webhook 端點（Dream 結束後由 Honcho 呼叫）────────
@app.post("/hooks/dream-complete")
async def dream_hook(request: Request, background_tasks: BackgroundTasks):
    """Honcho Dream 完成後觸發。設定 HONCHO_DREAM_WEBHOOK 指向此端點。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    peer_id = body.get("peer_id", os.getenv("DEFAULT_PEER_ID", ""))
    if not peer_id:
        raise HTTPException(400, "peer_id required")
    background_tasks.add_task(do_analysis, peer_id, "dream_hook")
    return {"status": "analysis_queued", "peer_id": peer_id}


# ── 即時端點（對話結束後輕量觸發）───────────────────
@app.post("/hooks/session-end")
async def session_end_hook(request: Request, background_tasks: BackgroundTasks):
    """
    對話結束後觸發（輕量版：只在超過 N 條新訊息時才執行完整分析）。
    Agent 可在 session close 時 POST 此端點。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    peer_id    = body.get("peer_id", os.getenv("DEFAULT_PEER_ID", ""))
    msg_count  = body.get("new_message_count", 0)
    lang_hint  = body.get("lang_hint")  # Agent 可傳入偵測到的語言

    if not peer_id:
        raise HTTPException(400, "peer_id required")

    # 訊息數太少不值得分析
    THRESHOLD = int(os.getenv("SESSION_END_MSG_THRESHOLD", "6"))
    if msg_count < THRESHOLD:
        return {"status": "skipped", "reason": f"msg_count {msg_count} < threshold {THRESHOLD}"}

    background_tasks.add_task(do_analysis, peer_id, "session_end", lang_hint)
    return {"status": "analysis_queued", "peer_id": peer_id}


# ── 手動觸發端點 ──────────────────────────────────────
@app.post("/analyze")
async def manual_analyze(request: Request):
    body = await request.json()
    peer_id   = body.get("peer_id", os.getenv("DEFAULT_PEER_ID", ""))
    lang      = body.get("lang")
    dry_run   = body.get("dry_run", False)
    if not peer_id:
        raise HTTPException(400, "peer_id required")
    past = load_past_topics(peer_id)
    suggestions = await run_proactive_analysis(
        peer_id=peer_id, past_topics=past,
        force_lang=lang, dry_run=dry_run
    )
    return {"suggestions": suggestions, "count": len(suggestions)}


# ── 查詢端點 ──────────────────────────────────────────
@app.get("/history/{peer_id}")
async def get_history(peer_id: str, limit: int = 10):
    conn = sqlite3.connect(STATE_DB_PATH)
    rows = conn.execute(
        "SELECT trigger, lang, suggestions_count, created_at "
        "FROM analysis_log WHERE peer_id=? ORDER BY id DESC LIMIT ?",
        (peer_id, limit)
    ).fetchall()
    conn.close()
    return {"logs": [
        {"trigger": r[0], "lang": r[1], "count": r[2], "at": r[3]}
        for r in rows
    ]}


@app.get("/past-topics/{peer_id}")
async def get_past_topics(peer_id: str):
    return {"topics": load_past_topics(peer_id)}


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(TZ).isoformat()}


# ── 每日定時任務 ──────────────────────────────────────
async def daily_scheduler():
    """每天 Asia/Taipei 09:00 自動分析所有已知 peer"""
    peer_ids_env = os.getenv("PROACTIVE_PEER_IDS", "")
    while True:
        now = datetime.now(TZ)
        target_hour   = DAILY_HOUR_JST
        target_minute = DAILY_MINUTE
        if now.hour == target_hour and now.minute == target_minute:
            peer_ids = [p.strip() for p in peer_ids_env.split(",") if p.strip()]
            for pid in peer_ids:
                print(f"[Scheduler] Daily trigger for peer: {pid}")
                asyncio.create_task(do_analysis(pid, "daily"))
            await asyncio.sleep(61)  # 避免同分鐘重複
        await asyncio.sleep(30)


@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(daily_scheduler())
    print(f"[NeuroMemory Scheduler] Started on port {SCHEDULER_PORT}")
    print(f"[NeuroMemory Scheduler] Daily trigger at {DAILY_HOUR_JST:02d}:{DAILY_MINUTE:02d} Asia/Taipei")


if __name__ == "__main__":
    uvicorn.run("scheduler:app", host="0.0.0.0", port=SCHEDULER_PORT, reload=False)
