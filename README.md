# 松原市議会 議事録AI要約システム

松原市議会の会議録検索システムから議事録を取得し、AIを使って市民にわかりやすく要約するツールです。

議員が「何のテーマについて、どんな質問をしたか」、そして「担当課がどのように答弁したか」を誰でも検索・閲覧できるようにすることを目指しています。

## 機能

- 松原市議会議事録検索システムから議事録を自動収集（平成30年〜令和7年・8年分）
- 本会議・各委員会の議事録に対応（226会議・470件以上）
- Gemini AI（gemini-2.0-flash）によるオンデマンドQ&A要約（結果はDBにキャッシュ）
- SQLiteによるローカルデータベース保存
- キーワード全文検索（新しい年度から優先表示）

## セットアップ

### 1. 必要なものをインストール

Python 3.11以上が必要です。

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 環境変数を設定

```bash
cp .env.example .env
```

`.env` を編集して `GEMINI_API_KEY` を設定してください。

GeminiのAPIキーは [Google AI Studio](https://aistudio.google.com/app/apikey) で無料取得できます。

### 3. データ収集（スクレイピング）

**全年度まとめて収集する場合（推奨）：**

```bash
python collect_all_years.py
```

平成30年〜令和7年の全会議・全日程・全議事録テキストを収集します。
初回実行時は数時間かかります（サーバー負荷軽減のため待機時間を設けています）。

**個別にステップ実行する場合：**

```bash
# 会議リスト・日程のみ収集
python collect_all_years.py --councils-only

# 議事録テキストを発言単位に解析
python collect_all_years.py --parse

# または旧スクリプト（単年度向け）
python scrape.py
```

### 4. AI要約と検索

要約はオンデマンドで生成されます（検索時に自動実行・DBにキャッシュ）。

```bash
# キーワード検索（自動でAI要約を生成）
python summarize.py --search 子育て支援
python summarize.py --search 道路整備
python summarize.py --search 橋本議員

# まとめてバッチ要約（任意）
python summarize.py
```

## データ構造

```
data/
└── gikai.db            # SQLiteデータベース（.gitignoreで除外）

src/
├── config.py           # 設定（対象年度・URL・APIキーなど）
├── db.py               # データベース操作
├── scraper.py          # スクレイピング（Playwright）
├── parser.py           # 議事録テキストの解析（発言単位への分解）
└── summarizer.py       # Gemini AI要約

collect_all_years.py    # 全年度データ収集（メインスクリプト）
scrape.py               # スクレイピング実行（単年度向け）
summarize.py            # AI要約・検索
```

### データベーステーブル

| テーブル | 内容 |
|---------|------|
| councils | 会議一覧（定例会・委員会など） |
| schedules | 各会議の開催日・号数 |
| minutes | 議事録本文（生テキスト） |
| speeches | 発言単位に分解したデータ |
| summaries | AI要約結果（テーマ・要旨・キーワード） |

## 注意事項

- このツールは松原市議会の公開情報を利用しています
- サーバーへの負荷軽減のため、リクエスト間に待機時間（2秒）を設けています
- `.env` ファイルはGitHubにアップロードしないでください（APIキーが含まれます）
- データベースファイル（`data/gikai.db`）はサイズが大きいためGitHubには含まれません

## 今後の予定

- [ ] Web検索インターフェース（Flask/FastAPIによるAPI）
- [ ] フロントエンド（市民向け検索画面）
- [ ] 議員ごとの発言テーマ分析
- [ ] 委員会別・テーマ別の時系列表示

## ライセンス

MIT License

## データ出典

松原市議会 会議録検索システム  
https://ssp.kaigiroku.net/tenant/matsubara/SpTop.html  
© 2018 Matsubara City
