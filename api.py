"""
松原市議会議事録AI検索 - FastAPI バックエンド

起動方法:
    uvicorn api:app --host 0.0.0.0 --port 8000

開発中（自動リロード）:
    uvicorn api:app --reload --port 8000
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src import db
from src.summarizer import suggest_keywords, summarize_one

app = FastAPI(title="松原市議会議事録AI検索")
executor = ThreadPoolExecutor(max_workers=2)

STATIC_DIR = Path(__file__).parent / "static"


@app.on_event("startup")
def startup():
    db.init_db()


# ---- フロントエンド配信 ----

@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


# ---- API ----

@app.get("/api/years")
def get_years():
    """DB に存在する年度ラベルを古い順で返す"""
    return {"years": db.get_distinct_years()}


# 年度の並び順（古→新）
YEAR_ORDER = [
    "平成30年",
    "令和元年", "令和1年", "令和元年/平成31年",
    "令和2年", "令和3年", "令和4年",
    "令和5年", "令和6年", "令和7年",
]


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, description="検索キーワード"),
    limit: int = Query(20, ge=0, description="0=上限なし"),
    year_from: str = Query(None, description="開始年度（例：令和5年）"),
    year_to: str = Query(None, description="終了年度（例：令和7年）"),
):
    """キーワードで議員発言を検索する"""
    # 年度フィルター計算
    year_labels = None
    if year_from or year_to:
        from_idx = YEAR_ORDER.index(year_from) if year_from in YEAR_ORDER else 0
        to_idx   = YEAR_ORDER.index(year_to)   if year_to   in YEAR_ORDER else len(YEAR_ORDER) - 1
        # 逆順でも自動修正
        lo, hi = min(from_idx, to_idx), max(from_idx, to_idx)
        year_labels = YEAR_ORDER[lo : hi + 1]

    speeches = db.search_speeches(q, limit=limit, year_labels=year_labels)

    results = []
    for s in speeches:
        cached = db.get_summary_for_speech(s["id"])
        results.append({
            "id": s["id"],
            "speaker_name": s["speaker_name"],
            "speaker_role": s["speaker_role"] or "",
            "council_name": s["council_name"],
            "schedule_name": s["schedule_name"],
            "content_excerpt": s["content"][:200].replace("\n", " "),
            "council_id": s["council_id"],
            "has_summary": cached is not None,
        })

    suggestions = []
    if not results:
        suggestions = suggest_keywords(q)

    return {
        "query": q,
        "total": len(results),
        "results": results,
        "suggestions": suggestions,
    }


@app.get("/api/speech/{speech_id}")
def get_speech(speech_id: int):
    """発言の全文を返す"""
    speech = db.get_speech_by_id(speech_id)
    if not speech:
        raise HTTPException(status_code=404, detail="発言が見つかりません")
    return {
        "id": speech["id"],
        "speaker_name": speech["speaker_name"],
        "speaker_role": speech["speaker_role"] or "",
        "council_name": speech["council_name"],
        "schedule_name": speech["schedule_name"],
        "content": speech["content"],
    }


@app.post("/api/summary/{speech_id}")
async def get_summary(speech_id: int):
    """発言IDのAI要約を取得する（キャッシュがあれば即返す）"""
    # キャッシュ確認（DB にあれば API コールなし）
    cached = db.get_summary_for_speech(speech_id)
    if cached:
        kw = cached["keywords"]
        if isinstance(kw, str):
            try:
                kw = json.loads(kw)
            except Exception:
                kw = []
        return {**dict(cached), "keywords": kw, "_cached": True}

    # 発言データを取得
    speech = db.get_speech_by_id(speech_id)
    if not speech:
        raise HTTPException(status_code=404, detail="発言が見つかりません")

    # Gemini 呼び出し（同期関数をスレッドプールで実行してブロッキングを防ぐ）
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, summarize_one, speech)

    if not result:
        raise HTTPException(status_code=500, detail="AI要約の生成に失敗しました")

    kw = result.get("keywords", [])
    if isinstance(kw, str):
        try:
            kw = json.loads(kw)
        except Exception:
            kw = []

    return {**result, "keywords": kw, "_cached": False}


# 静的ファイル配信（CSS・画像など）
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
