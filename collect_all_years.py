#!/usr/bin/env python3
"""
全年度の会議・日程・議事録を収集するスクリプト（API直接呼び出し版）

Playwright不要でAPIを直接叩くため、ブラウザ待機なしで高速に動作する。
議事録テキストの取得のみ Playwright を使用する。

python3 collect_all_years.py
"""

import asyncio
import re
import json
import time

import httpx
from playwright.async_api import async_playwright, Browser

from src.config import MINUTE_VIEW_URL, TENANT_ID, REQUEST_DELAY, HEADLESS
from src import db
from src.scraper import get_minute_text, _guess_council_type

BASE_API = "https://ssp.kaigiroku.net/dnp/search"
BROWSE_URL = "https://ssp.kaigiroku.net/tenant/matsubara/SpMinuteBrowse.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://ssp.kaigiroku.net/tenant/matsubara/SpMinuteBrowse.html",
}

# 対象年度（新しい順）
TARGET_YEARS = [
    (2025, "令和7年"),
    (2024, "令和6年"),
    (2023, "令和5年"),
    (2022, "令和4年"),
    (2021, "令和3年"),
    (2020, "令和2年"),
    (2019, "令和元年/平成31年"),
    (2018, "平成30年"),
]

YEAR_FROM_LABEL = {label: year for year, label in TARGET_YEARS}


def parse_jsonp(text: str) -> dict:
    """JSONP レスポンス cb({...}) から dict を取り出す"""
    match = re.search(r'\((\{.*\})\)', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return {}


def fetch_councils_for_year(client: httpx.Client, year: int) -> list[dict]:
    """指定年の全会議を councils/index API から取得する"""
    resp = client.post(
        f"{BASE_API}/councils/index",
        params={"callback": "cb"},
        data={"tenant_id": TENANT_ID, "view_years": year},
        headers=HEADERS,
        timeout=30,
    )
    data = parse_jsonp(resp.text)
    councils = []
    for entry in data.get("councils", []):
        for vy in entry.get("view_years", []):
            for ct in vy.get("council_type", []):
                for c in ct.get("councils", []):
                    cid = c.get("council_id")
                    name = c.get("name", "")
                    if cid:
                        councils.append({"council_id": int(cid), "name": name})
    return councils


def fetch_schedules_for_council(client: httpx.Client, council_id: int) -> list[dict]:
    """
    会議の日程一覧を minutes/get_schedule_all API から取得する。
    （debug_schedules_api.py でブラウザの実際のリクエストを確認して判明した正しいエンドポイント）
    """
    try:
        resp = client.post(
            f"{BASE_API}/minutes/get_schedule_all",
            params={"callback": "cb"},
            data={
                "tenant_id": TENANT_ID,
                "council_id": council_id,
                "power_user": "false",
            },
            headers=HEADERS,
            timeout=15,
        )
        data = parse_jsonp(resp.text)
        schedules = []
        # レスポンスのキー名を複数候補で試す
        raw = (
            data.get("schedules_and_materials")
            or data.get("schedules")
            or data.get("get_schedule_all")
            or data.get("schedule_list")
            or []
        )
        for s in raw:
            sid = s.get("schedule_id") or s.get("id")
            name = s.get("schedule_name") or s.get("name", "")
            if sid:
                schedules.append({"schedule_id": int(sid), "name": name})
        return schedules
    except Exception as e:
        print(f"  [日程API エラー] council_id={council_id}: {e}")
        return []


async def collect_schedules_playwright(page, council_id: int) -> list[dict]:
    """
    対象年のページ（SpMinuteBrowse.html）がロード済みの状態で、
    council_id の li をクリックして日程リスト（li.schedule-name）を取得する。
    scraper.py の get_schedules_for_council と同じ DOM 構造を想定。
    """
    # 対象 council li をクリック
    clicked = await page.evaluate(f"""() => {{
        const li = document.querySelector("li[data-council_id='{council_id}']");
        if (li) {{
            li.click();
            return true;
        }}
        return false;
    }}""")
    if not clicked:
        return []

    # クリック後に schedule-name リストが描画されるまで待機
    await asyncio.sleep(2.5)

    # li.schedule-name 要素から schedule_id と名前を取得
    # （属性名は 'schedule_id'、元コード scraper.py#L152 と同じ）
    raw = await page.evaluate(f"""() => {{
        const li = document.querySelector("li[data-council_id='{council_id}']");
        if (!li) return [];
        const items = li.querySelectorAll('li.schedule-name');
        return Array.from(items).map(s => ({{
            schedule_id: s.getAttribute('schedule_id'),
            name: s.textContent.trim()
        }}));
    }}""")

    schedules = []
    for s in raw:
        sid = s.get("schedule_id")
        name = s.get("name", "")
        if sid:
            try:
                schedules.append({"schedule_id": int(sid), "name": name})
            except (ValueError, TypeError):
                pass
    return schedules


TOP_URL = f"https://ssp.kaigiroku.net/tenant/matsubara/SpTop.html?tenant_id={TENANT_ID}"

# 令和元年/平成31年 は複数の表記があるため、複数パターンで試す
YEAR_LINK_CANDIDATES = {
    2019: ["令和元年/平成31年", "令和元年", "平成31年"],
}


async def load_year_page(page, year: int, year_label: str) -> bool:
    """
    SpTop.html から年リンクをクリックして SpMinuteBrowse.html（対象年）に遷移する。
    scraper.py の get_all_councils_from_top と同じ方式：
      - domcontentloaded + sleep(12) でトップを待つ
      - 年リンククリック後は wait_for_selector で council 出現まで待つ
    """
    # scraper.py の _load_top_and_wait と同じ: domcontentloaded + 12秒
    await page.goto(TOP_URL, wait_until="domcontentloaded")
    await asyncio.sleep(12)

    # 「»もっと見る」を展開して古い年のリンクも表示させる
    await page.evaluate("""() => {
        document.querySelectorAll('*').forEach(el => {
            if (el.children.length === 0 && el.textContent.trim().includes('もっと見る')) {
                el.click();
            }
        });
    }""")
    await asyncio.sleep(2)

    # 試みるラベル一覧（令和元年は複数候補）
    candidates = YEAR_LINK_CANDIDATES.get(year, [year_label])

    clicked = None
    for candidate in candidates:
        clicked = await page.evaluate("""(normalizedTarget) => {
            function normalize(text) {
                return text.trim()
                    .replace(/[０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0))
                    .replace(/　/g, ' ');
            }
            const nt = normalize(normalizedTarget);
            const links = Array.from(document.querySelectorAll('a'));
            for (const link of links) {
                if (normalize(link.textContent) === nt) {
                    link.click();
                    return link.textContent.trim();
                }
            }
            return null;
        }""", candidate)
        if clicked:
            break

    if not clicked:
        # デバッグ: ページ上の年関連リンクを表示
        year_links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a'))
                .map(a => a.textContent.trim())
                .filter(t => t.includes('年') || t.includes('令和') || t.includes('平成'));
        }""")
        print(f"    ⚠️ 年リンク '{year_label}' が見つかりません。ページ上の年リンク: {year_links[:10]}")
        return False

    print(f"    → 年リンククリック成功: {clicked}")

    # クリック後: ページ遷移 + JS による council 描画を待つ
    # wait_for_selector で実際に要素が出現するまで最大30秒待機
    try:
        await page.wait_for_selector("li[data-council_id]", timeout=30000)
        await asyncio.sleep(1)  # 追加バッファ
    except Exception:
        # タイムアウトした場合も一応続行（visible_ids が 0 になる）
        print(f"    ⚠️ li[data-council_id] が30秒以内に出現しませんでした")
    return True


async def main():
    db.init_db()

    # ==========================================================
    # ステップ1: 全年度の会議リストを API で取得
    # ==========================================================
    print("\n" + "=" * 55)
    print("ステップ1: 会議リストを収集中（API直接呼び出し）")
    print("=" * 55)

    with httpx.Client() as client:
        for year, label in TARGET_YEARS:
            print(f"\n  [{label}] 取得中...")
            councils = fetch_councils_for_year(client, year)
            for c in councils:
                db.upsert_council(
                    council_id=c["council_id"],
                    name=c["name"],
                    year_label=label,
                    council_type=_guess_council_type(c["name"]),
                )
            print(f"  [{label}] {len(councils)} 件取得・保存")
            time.sleep(0.5)

    all_councils = db.get_all_councils()
    print(f"\n合計 {len(all_councils)} 件の会議をDBに登録")

    # ==========================================================
    # ステップ2: 各会議の日程を取得（API優先、Playwright フォールバック）
    # ==========================================================
    print("\n" + "=" * 55)
    print("ステップ2: 日程リストを収集中")
    print("=" * 55)

    # まず API で試す
    api_ok = None
    need_playwright_for_schedules = []

    with httpx.Client() as client:
        for council in all_councils:
            cid = council["council_id"]
            existing = db.get_schedules_for_council(cid)
            if existing:
                print(f"  [スキップ] {council['name']}")
                continue

            print(f"  {council['name']} の日程を取得中...")
            schedules = fetch_schedules_for_council(client, cid)

            if schedules:
                if api_ok is None:
                    api_ok = True
                    print(f"  ✅ 日程APIが使えます")
                for s in schedules:
                    db.upsert_schedule(cid, s["schedule_id"], s["name"])
                print(f"  → {len(schedules)} 件")
            else:
                if api_ok is None:
                    api_ok = False
                    print(f"  ⚠️  日程APIが使えません → Playwright にフォールバック")
                need_playwright_for_schedules.append(council)

            time.sleep(0.5)

    # Playwright フォールバック（日程APIが使えない場合）
    # DOM ナビゲーションではなく、ブラウザのセッション(Cookie)を使った
    # context.request.post で schedules/index API を直接叩く。
    if need_playwright_for_schedules:
        print(f"\n  Playwright (context.request) で {len(need_playwright_for_schedules)} 件の日程を取得します...")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(user_agent=HEADERS["User-Agent"])
            page = await context.new_page()

            # SpTop.html を開いてサイトのセッション/Cookie を確立する
            print("  SpTop.html でセッション確立中...")
            await page.goto(TOP_URL, wait_until="domcontentloaded")
            await asyncio.sleep(5)

            # ブラウザのJSからfetchを呼ぶためのテンプレート
            # page.evaluate で実行するため、真の same-origin リクエストになる
            # （Sec-Fetch-* ヘッダーも自動付与される）
            API_URL = f"{BASE_API}/minutes/get_schedule_all?callback=cb"

            debug_done = False  # 最初の1件だけ raw レスポンスを表示

            for council in need_playwright_for_schedules:
                cid = council["council_id"]
                existing = db.get_schedules_for_council(cid)
                if existing:
                    print(f"  [スキップ] {council['name']}")
                    continue

                try:
                    # ブラウザ内 fetch で API を呼ぶ（same-origin + 全Cookie付き）
                    # page.evaluate の引数は1つだけなので配列でまとめる
                    text = await page.evaluate("""async ([apiUrl, body]) => {
                        try {
                            const resp = await fetch(apiUrl, {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/x-www-form-urlencoded',
                                },
                                body: body
                            });
                            return await resp.text();
                        } catch(e) {
                            return 'ERROR: ' + e.message;
                        }
                    }""",
                    [API_URL, f"tenant_id={TENANT_ID}&council_id={cid}&power_user=false"],
                    )

                    # 最初の1件は生レスポンスをデバッグ表示
                    if not debug_done:
                        print(f"  [DEBUG] API raw (page.evaluate): {text[:400]}")
                        debug_done = True

                    if text.startswith("ERROR:") or text.startswith("<!"):
                        print(f"  {council['name']} → API エラー: {text[:80]}")
                        await asyncio.sleep(REQUEST_DELAY)
                        continue

                    data = parse_jsonp(text)
                    raw = (
                        data.get("schedules_and_materials")
                        or data.get("schedules")
                        or data.get("get_schedule_all")
                        or data.get("schedule_list")
                        or []
                    )
                    schedules = []
                    for s in raw:
                        sid = s.get("schedule_id") or s.get("id")
                        name = s.get("schedule_name") or s.get("name", "")
                        if sid:
                            schedules.append({"schedule_id": int(sid), "name": name})

                    if schedules:
                        for s in schedules:
                            db.upsert_schedule(cid, s["schedule_id"], s["name"])
                        print(f"  {council['name']} → {len(schedules)} 件保存")
                    else:
                        if data:
                            print(f"  {council['name']} → 0 件 (keys: {list(data.keys())})")
                        else:
                            print(f"  {council['name']} → 0 件（パース失敗）")

                except Exception as e:
                    print(f"  [エラー] {council['name']}: {e}")

                await asyncio.sleep(REQUEST_DELAY)

            await browser.close()

    # ==========================================================
    # ステップ3: 議事録テキストを Playwright で取得
    # ==========================================================
    print("\n" + "=" * 55)
    print("ステップ3: 議事録テキストを収集中（Playwright）")
    print("=" * 55)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(user_agent=HEADERS["User-Agent"])
        page = await context.new_page()
        page.set_default_timeout(30000)

        total_count = 0
        skip_count = 0
        councils_in_db = db.get_all_councils()

        for council in councils_in_db:
            cid = council["council_id"]
            schedules = db.get_schedules_for_council(cid)

            for sched in schedules:
                sid = sched["schedule_id"]
                if "目次" in sched["name"] or "資料" in sched["name"]:
                    continue
                if db.has_minute(cid, sid):
                    skip_count += 1
                    continue

                print(f"  取得中: [{council['name']}] {sched['name']}")
                try:
                    text = await get_minute_text(page, cid, sid)
                    if text:
                        db.insert_minute(cid, sid, text)
                        total_count += 1
                        print(f"  → 保存完了 ({len(text):,} 文字)")
                    else:
                        print(f"  → テキスト取得失敗（空）")
                except Exception as e:
                    print(f"  [エラー] {e}")

                await asyncio.sleep(REQUEST_DELAY)

        await browser.close()

    print(f"\n{'='*55}")
    print(f"完了")
    print(f"新規取得: {total_count} 件 / スキップ: {skip_count} 件")


if __name__ == "__main__":
    asyncio.run(main())
