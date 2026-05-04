"""
松原市議会議事録スクレイパー

Playwright を使用してJavaScriptレンダリングされたページからデータを収集する。
サーバーへの負荷を減らすため、リクエスト間に待機時間を設けている。
"""

import asyncio
from playwright.async_api import async_playwright, Page, Browser

from .config import (
    TOP_URL, MINUTE_VIEW_URL, TENANT_ID,
    TARGET_YEAR_LABELS, REQUEST_DELAY, HEADLESS
)
from . import db


async def _wait(page: Page, seconds: float = None):
    """リクエスト間の待機（サーバー負荷軽減）"""
    await asyncio.sleep(seconds if seconds is not None else REQUEST_DELAY)


def _normalize(text: str) -> str:
    """全角数字・スペースを半角に正規化して比較しやすくする"""
    text = text.strip()
    table = str.maketrans('０１２３４５６７８９　', '0123456789 ')
    return text.translate(table)


async def _collect_visible_councils(page: Page) -> list[dict]:
    """現在ページに表示されている全 council を収集する"""
    return await page.evaluate("""() => {
        const items = document.querySelectorAll('li[data-council_id]');
        return Array.from(items).map(li => ({
            council_id: parseInt(li.getAttribute('data-council_id')),
            name: li.childNodes[0]
                ? li.childNodes[0].textContent.trim() || li.textContent.trim()
                : li.textContent.trim()
        })).filter(c => c.council_id);
    }""")


async def get_councils_for_year(page: Page, year_label: str) -> list[dict]:
    """
    「開催年から閲覧する」セクションで年をクリックし、その年の全会議を取得する。
    JavaScriptで直接リンクを探してクリックする。
    """
    await page.goto(TOP_URL, wait_until="networkidle")
    await _wait(page, 1.5)

    normalized_target = _normalize(year_label)

    # JavaScript でページ上の全リンクテキストを取得してデバッグ情報を収集
    all_link_texts = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a'))
            .map(a => a.textContent.trim())
            .filter(t => t.length > 0 && t.length < 30);
    }""")

    # 正規化して一致するリンクを探す
    clicked = await page.evaluate("""(normalizedTarget) => {
        function normalize(text) {
            return text.trim()
                .replace(/[０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0))
                .replace(/　/g, ' ');
        }
        // a タグから探す
        const links = Array.from(document.querySelectorAll('a'));
        for (const link of links) {
            if (normalize(link.textContent) === normalizedTarget) {
                link.click();
                return 'a:' + link.textContent.trim();
            }
        }
        // li タグから探す（リンクを含まない場合）
        const lis = Array.from(document.querySelectorAll('li'));
        for (const li of lis) {
            const text = normalize(li.textContent);
            if (text === normalizedTarget) {
                li.click();
                return 'li:' + li.textContent.trim();
            }
        }
        return null;
    }""", normalized_target)

    if not clicked:
        # デバッグ: 実際のリンクテキストを表示
        print(f"  [警告] '{year_label}' が見つかりません")
        normalized_links = [_normalize(t) for t in all_link_texts]
        year_like = [t for t in normalized_links if '年' in t or '令和' in t or '平成' in t]
        print(f"  ページ上の年関連リンク: {year_like[:10]}")
        return []

    print(f"  → クリック成功: {clicked}")
    await _wait(page, 2.0)

    councils = await _collect_visible_councils(page)
    return [{"council_id": c["council_id"], "name": c["name"]} for c in councils]


def _guess_council_type(name: str) -> str:
    """会議名から種別を推測する"""
    if "委員会" in name:
        return "委員会"
    return "本会議"


async def get_schedules_for_council(page: Page, council_id: int) -> list[dict]:
    """
    会議の日程一覧を取得する。
    トップページで対象会議の li をクリックすると schedule_id がDOMに現れる。
    """
    await page.goto(TOP_URL, wait_until="networkidle")
    await _wait(page, 1.5)

    # council_id の li が表示されていない場合は「もっと見る」を試みる
    visible = await page.evaluate(f"""() => {{
        return !!document.querySelector("li[data-council_id='{council_id}']");
    }}""")

    if not visible:
        # 「もっと見る」をクリックして追加表示を試みる
        await page.evaluate("""() => {
            const mores = Array.from(document.querySelectorAll('*'))
                .filter(el => el.textContent.trim() === '»もっと見る');
            mores.forEach(el => el.click());
        }""")
        await _wait(page, 1.5)

    # JavaScriptでクリック
    clicked = await page.evaluate(f"""() => {{
        const li = document.querySelector("li[data-council_id='{council_id}']");
        if (li) {{
            li.click();
            return true;
        }}
        return false;
    }}""")

    if not clicked:
        print(f"  [警告] council_id={council_id} が見つかりません")
        return []

    await _wait(page, 2.0)

    # JavaScriptでスケジュール一覧を取得（これは以前の調査で動作確認済み）
    schedules = await page.evaluate(f"""() => {{
        const li = document.querySelector("li[data-council_id='{council_id}']");
        if (!li) return [];
        const items = li.querySelectorAll('li.schedule-name');
        return Array.from(items).map(s => ({{
            schedule_id: s.getAttribute('schedule_id'),
            name: s.textContent.trim()
        }}));
    }}""")

    return [
        {"schedule_id": int(s["schedule_id"]), "name": s["name"]}
        for s in schedules
        if s.get("schedule_id")
    ]


async def get_minute_text(page: Page, council_id: int, schedule_id: int) -> str:
    """
    議事録の本文テキストを取得する。
    SpMinuteView.html に直接アクセスしてテキストを抽出する。
    """
    url = (
        f"{MINUTE_VIEW_URL}"
        f"?tenant_id={TENANT_ID}&council_id={council_id}&schedule_id={schedule_id}"
    )
    await page.goto(url, wait_until="networkidle")
    await _wait(page, 1.5)

    title = await page.title()
    if "404" in title or "error" in title.lower():
        print(f"  [エラー] ページ取得失敗: {url}")
        return ""

    text = await page.evaluate("""() => {
        const main = document.querySelector('#contents') ||
                     document.querySelector('main') ||
                     document.querySelector('.minute-content') ||
                     document.body;
        return main ? main.innerText : document.body.innerText;
    }""")
    return text.strip()


async def _collect_councils_js(page: Page) -> list[dict]:
    """現在ページに表示されている全 council を JS で収集する"""
    return await page.evaluate("""() => {
        const items = document.querySelectorAll('li[data-council_id]');
        return Array.from(items).map(li => ({
            council_id: parseInt(li.getAttribute('data-council_id')),
            name: li.textContent.trim().split('\\n')[0].trim()
        })).filter(c => c.council_id && !isNaN(c.council_id));
    }""")


async def _load_top_and_wait(page: Page):
    """
    トップページを読み込んで councils が表示されるまで待つ。
    debug_page.py で10秒で動作確認済みのため、12秒待機を基本とする。
    """
    await page.goto(TOP_URL, wait_until="domcontentloaded")
    await asyncio.sleep(12)


async def get_all_councils_from_top(page: Page) -> list[dict]:
    """
    トップページから全年度の会議を収集する。
    ・毎回ページを再読み込みして年リンクをクリック
    ・クリック後は十分に待ってからDOMを収集
    """
    all_councils: dict[int, dict] = {}

    for year_label in TARGET_YEAR_LABELS:
        print(f"  [{year_label}] ページ読み込み中...")

        # 毎回トップに戻る（年リンクは遷移後に消えるため）
        await _load_top_and_wait(page)

        # 念のため現在表示されている件数を確認
        before_count = len(await _collect_councils_js(page))

        normalized_target = _normalize(year_label)

        # 「»もっと見る」をクリックして全年リンクを展開
        await page.evaluate("""() => {
            document.querySelectorAll('*').forEach(el => {
                if (el.children.length === 0 &&
                    (el.textContent.trim() === '»もっと見る' ||
                     el.textContent.trim() === 'もっと見る')) {
                    el.click();
                }
            });
        }""")
        await asyncio.sleep(2)

        # 年リンクをクリック
        clicked = await page.evaluate("""(normalizedTarget) => {
            function normalize(text) {
                return text.trim()
                    .replace(/[０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0))
                    .replace(/　/g, ' ');
            }
            const links = Array.from(document.querySelectorAll('a'));
            for (const link of links) {
                if (normalize(link.textContent) === normalizedTarget) {
                    link.click();
                    return link.textContent.trim();
                }
            }
            return null;
        }""", normalized_target)

        if not clicked:
            # リンクが見つからない場合は現在の内容だけ回収
            print(f"  [{year_label}] 年リンクが見つかりません")
            after = await _collect_councils_js(page)
        else:
            # クリック後は十分に待つ（ページ遷移 or AJAX更新のため）
            await asyncio.sleep(8)
            after = await _collect_councils_js(page)

        new_count = 0
        for c in after:
            if c["council_id"] not in all_councils:
                all_councils[c["council_id"]] = {**c, "year_label": year_label}
                new_count += 1

        print(f"  [{year_label}] 表示: {len(after)} 件 / 新規: {new_count} 件（累計: {len(all_councils)} 件）")

    return list(all_councils.values())


async def scrape_all():
    """
    メインのスクレイピング処理
    1. 対象年の全会議を収集
    2. 各会議の日程を収集
    3. 各日程の議事録テキストを収集
    全てDBに保存。既取得データはスキップする。
    """
    db.init_db()

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        # 本物のブラウザに見せるためUserAgentを設定
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        page.set_default_timeout(30000)

        # --- ステップ1: 会議リストを収集 ---
        print("\n=== ステップ1: 会議リストを収集中 ===")
        all_councils: dict[int, dict] = {}

        raw_councils = await get_all_councils_from_top(page)
        for c in raw_councils:
            cid = c["council_id"]
            if cid not in all_councils:
                all_councils[cid] = c

        print(f"\nWeb取得: {len(all_councils)} 件")

        # Web取得が0件の場合はDBの既存データを使う
        if not all_councils:
            db_councils = db.get_all_councils()
            if db_councils:
                print(f"  → DBの既存データを使用: {len(db_councils)} 件")
                all_councils = {c["council_id"]: c for c in db_councils}
            else:
                print("\n[エラー] Webからの取得もDBにもデータがありません。")
                print("ヒント: debug_page.py を先に実行して動作確認してください。")
                await browser.close()
                return

        print(f"合計 {len(all_councils)} 件の会議を処理します")

        # DBに保存（Web取得できた分のみ）
        for cid, c in all_councils.items():
            if "year_label" in c:  # Webから取得したデータのみ保存
                db.upsert_council(
                    council_id=cid,
                    name=c["name"],
                    year_label=c.get("year_label", ""),
                    council_type=_guess_council_type(c["name"]),
                )

        # --- ステップ2: 各会議の日程を収集 ---
        print("\n=== ステップ2: 日程リストを収集中 ===")
        for cid, c in all_councils.items():
            existing = db.get_schedules_for_council(cid)
            if existing:
                print(f"  [スキップ] council_id={cid} ({c['name']}) は取得済み")
                continue

            print(f"  council_id={cid} ({c['name']}) の日程を取得中...")
            try:
                schedules = await get_schedules_for_council(page, cid)
                for s in schedules:
                    db.upsert_schedule(cid, s["schedule_id"], s["name"])
                print(f"  → {len(schedules)} 件の日程を取得")
            except Exception as e:
                print(f"  [エラー] council_id={cid}: {e}")

            await asyncio.sleep(REQUEST_DELAY)

        # --- ステップ3: 各日程の議事録テキストを収集 ---
        print("\n=== ステップ3: 議事録テキストを収集中 ===")
        councils_in_db = db.get_all_councils()
        total_count = 0
        skip_count = 0

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

                print(
                    f"  取得中: [{council['name']}] {sched['name']}"
                )
                try:
                    text = await get_minute_text(page, cid, sid)
                    if text:
                        db.insert_minute(cid, sid, text)
                        total_count += 1
                        print(f"  → 保存完了 ({len(text):,} 文字)")
                    else:
                        print(f"  → テキスト取得失敗")
                except Exception as e:
                    print(f"  [エラー] {e}")

                await asyncio.sleep(REQUEST_DELAY)

        await browser.close()

    print(f"\n=== 完了 ===")
    print(f"新規取得: {total_count} 件 / スキップ: {skip_count} 件")
