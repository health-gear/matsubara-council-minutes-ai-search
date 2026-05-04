"""
オンデマンド検索 + AI要約モジュール

検索されたときだけ Gemini を呼ぶ「必要な分だけ要約」方式。
一度要約した内容は DB にキャッシュし、2回目以降は即返す。
"""

import json
import time
import re

from google import genai

from .config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MAX_CHARS
from . import db
from .parser import parse_transcript


client = genai.Client(api_key=GEMINI_API_KEY)


SUMMARY_PROMPT = """あなたは松原市議会の議事録を市民にわかりやすく要約するアシスタントです。

以下の議会での発言を読んで、市民が「この会議で何が議論されたか」をすぐに理解できるよう要約してください。

【会議名】{council_name}（{schedule_name}）
【議員名】{speaker_name}（{speaker_role}）
【議員の発言】
{question_text}

【答弁者】{official_name}（{official_role}）
【答弁内容】
{answer_text}

---

以下のJSON形式で出力してください（他のテキストは不要）：
{{
  "topic": "議題・テーマ（20文字以内、例：「文化施設の指定管理者選定について」）",
  "question_summary": "議員の質問の要旨（100文字以内、です・ます調）",
  "answer_summary": "市の答弁の要旨（150文字以内、です・ます調）",
  "keywords": ["キーワード1", "キーワード2", "キーワード3"]
}}
"""

KEYWORD_SUGGEST_PROMPT = """あなたは松原市議会の議事録検索を助けるアシスタントです。

ユーザーが「{query}」というキーワードで検索しましたが、該当する発言が見つかりませんでした。

議会の議事録では、日常的な言葉と異なる公式な表現が使われることがよくあります。
「{query}」に関連するテーマを議会で検索するときに試せるキーワードを5つ提案してください。

議事録でよく使われる表現（例：「市道」「道路整備」「幹線道路」など）を優先してください。

以下のJSON形式で出力してください（他のテキストは不要）：
{{"keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5"]}}
"""

QUESTION_ONLY_PROMPT = """あなたは松原市議会の議事録を市民にわかりやすく要約するアシスタントです。

以下の議員発言を読んで要約してください。

【会議名】{council_name}（{schedule_name}）
【議員名】{speaker_name}（{speaker_role}）
【発言内容】
{question_text}

---

以下のJSON形式で出力してください（他のテキストは不要）：
{{
  "topic": "議題・テーマ（20文字以内）",
  "question_summary": "発言の要旨（100文字以内、です・ます調）",
  "answer_summary": "",
  "keywords": ["キーワード1", "キーワード2", "キーワード3"]
}}
"""


def _truncate(text: str, max_chars: int = GEMINI_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…（省略）"


def _parse_json_response(text: str) -> dict | None:
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def _get_answer_speech(speech: dict) -> dict | None:
    """議員発言の直後にある市側答弁を取得する"""
    all_speeches = db.get_speeches_for_minute(speech["minute_id"])
    for s in all_speeches:
        if s["order_num"] > speech["order_num"]:
            if s["speaker_type"] == "official":
                return s
            elif s["speaker_type"] == "member":
                # 次の議員が発言し始めたら答弁なし
                break
    return None


def suggest_keywords(query: str) -> list[str]:
    """検索で0件だったとき、関連する議会用語をGeminiに提案させる"""
    prompt = KEYWORD_SUGGEST_PROMPT.format(query=query)
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        result = _parse_json_response(response.text)
        if result and isinstance(result.get("keywords"), list):
            return result["keywords"]
    except Exception as e:
        print(f"  [提案生成エラー] {e}")
    return []


def _call_gemini(speech: dict, answer: dict | None) -> dict | None:
    """Gemini API を呼んで要約を得る"""
    if answer:
        prompt = SUMMARY_PROMPT.format(
            council_name=speech["council_name"],
            schedule_name=speech["schedule_name"],
            speaker_name=speech["speaker_name"],
            speaker_role=speech["speaker_role"],
            question_text=_truncate(speech["content"]),
            official_name=answer["speaker_name"],
            official_role=answer["speaker_role"],
            answer_text=_truncate(answer["content"]),
        )
    else:
        prompt = QUESTION_ONLY_PROMPT.format(
            council_name=speech["council_name"],
            schedule_name=speech["schedule_name"],
            speaker_name=speech["speaker_name"],
            speaker_role=speech["speaker_role"],
            question_text=_truncate(speech["content"]),
        )

    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return _parse_json_response(response.text)
    except Exception as e:
        print(f"  [Gemini エラー] {e}")
        return None


def summarize_one(speech: dict) -> dict | None:
    """
    1件の発言をオンデマンドで要約する。
    DB にキャッシュがあれば即返す。なければ Gemini で要約してキャッシュ保存。
    """
    # キャッシュ確認
    cached = db.get_summary_for_speech(speech["id"])
    if cached:
        keywords = cached["keywords"]
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except Exception:
                keywords = []
        return {**speech, **cached, "keywords": keywords, "_cached": True}

    # 答弁を探す
    answer = _get_answer_speech(speech)

    # AI 要約
    result = _call_gemini(speech, answer)
    if result:
        db.insert_summary(
            speech_id=speech["id"],
            answer_speech_id=answer["id"] if answer else None,
            topic=result.get("topic", ""),
            question_summary=result.get("question_summary", ""),
            answer_summary=result.get("answer_summary", ""),
            keywords=result.get("keywords", []),
            model=GEMINI_MODEL,
        )
        return {**speech, **result, "_cached": False}
    return None


def run_parse():
    """
    全議事録を発言単位に分解してDBに保存する（API コールなし・高速）。
    検索前の準備ステップ。
    """
    db.init_db()

    unprocessed = db.get_unparsed_minutes()
    if not unprocessed:
        print("未解析の議事録はありません（全件処理済み）")
        print("FTSインデックスを同期中...")
        db.rebuild_speeches_fts()
        print("完了")
        return

    print(f"未解析の議事録: {len(unprocessed)} 件")
    total = 0
    for minute in unprocessed:
        speeches = parse_transcript(minute["raw_text"])
        if speeches:
            db.insert_speeches(minute["id"], speeches)
            total += len(speeches)
            print(f"  {minute['council_name']} - {minute['schedule_name']}: {len(speeches)} 件")
        else:
            print(f"  {minute['council_name']} - {minute['schedule_name']}: 発言なし")

    # FTS インデックスを最新状態に同期
    print(f"\n合計 {total} 件の発言を保存。FTS インデックスを更新中...")
    db.rebuild_speeches_fts()
    print("完了")


def search_and_summarize(query: str, limit: int = 10):
    """
    キーワードで発言を検索し、ヒットした分だけ AI で要約する。

    - DB にキャッシュがあれば即返す（API コールなし）
    - 新規のものだけ Gemini を呼ぶ
    - 結果はキャッシュ保存するので、2回目以降は瞬時に返る
    """
    db.init_db()

    # 未解析の議事録を自動で分解（FTS 検索できるようにするため）
    unprocessed = db.get_unparsed_minutes()
    if unprocessed:
        print(f"[自動準備] {len(unprocessed)} 件の議事録を発言に分解中...")
        for minute in unprocessed:
            speeches = parse_transcript(minute["raw_text"])
            if speeches:
                db.insert_speeches(minute["id"], speeches)
        print("  完了\n")

    # FTSインデックスを常に最新状態に同期（既存データも含む）
    db.rebuild_speeches_fts()

    # 検索
    speeches = db.search_speeches(query, limit=limit)
    if not speeches:
        print(f"「{query}」に該当する発言は見つかりませんでした。\n")
        print("関連キーワードを調べています...")
        suggestions = suggest_keywords(query)
        if suggestions:
            print(f"\n💡 こんなキーワードで試してみてはどうですか？\n")
            for kw in suggestions:
                print(f"   python3 summarize.py --search {kw}")
        else:
            print("ヒント: 別のキーワードや議員名で試してみてください。")
        return

    print(f"「{query}」: {len(speeches)} 件ヒット\n")

    results = []
    for speech in speeches:
        cached = db.get_summary_for_speech(speech["id"])
        if cached:
            print(f"  [キャッシュ] {speech['speaker_name']} / {speech['council_name']}")
            keywords = cached["keywords"]
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except Exception:
                    keywords = []
            results.append({**speech, **cached, "keywords": keywords, "_cached": True})
        else:
            print(f"  [AI要約中] {speech['speaker_name']}（{speech['speaker_role']}）/ {speech['council_name']}")
            result = summarize_one(speech)
            if result:
                results.append(result)
                time.sleep(1.2)  # API レート制限対策

    # 結果表示
    print()
    for i, r in enumerate(results, 1):
        label = "キャッシュ" if r.get("_cached") else "AI要約済み"
        kw = r.get("keywords", [])
        if isinstance(kw, str):
            try:
                kw = json.loads(kw)
            except Exception:
                kw = []

        print(f"{'='*60}")
        print(f"[{i}] {r.get('topic', '（タイトルなし）')}  [{label}]")
        print(f"会議: {r['council_name']}  {r['schedule_name']}")
        print(f"議員: {r['speaker_name']}（{r['speaker_role']}）")
        print(f"質問: {r.get('question_summary', '')}")
        ans = r.get("answer_summary", "")
        if ans:
            print(f"答弁: {ans}")
        if kw:
            print(f"キーワード: {', '.join(kw)}")
        print()
