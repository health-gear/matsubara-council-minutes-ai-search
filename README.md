# 松原市議会 議事録AI検索くん

> **市民が議会をもっと身近に感じられるように。**  
> 松原市議会（大阪府）の全議事録をAIで検索・要約できる、オープンソースの市民向け無料Webサービスです。

🌐 **公開URL**: [https://matsubara.council-minutes-ai-search.jp](https://matsubara.council-minutes-ai-search.jp)

---

## 🗂️ プロジェクト概要

本プロジェクトは、松原市議会の公開議事録（平成30年〜令和7年、約8年分）をスクレイピングで収集し、キーワード検索とAI要約によって市民が簡単に調べられるようにしたシビックテックアプリケーションです。

議員が「何のテーマについて、どんな質問をしたか」、そして「行政側がどのように答弁したか」を、誰でも・無料で・スマートフォンからも検索できます。

### なぜ作ったか

日本の地方議会の議事録は公開されていますが、専用システムへの直接アクセスが必要で、市民が日常的に参照しやすい形にはなっていません。本サービスはその課題を解決し、**行政の透明性向上と市民の政治参加促進**を目的としています。

---

## ✨ 主な機能

| 機能 | 詳細 |
|------|------|
| 🔍 全文キーワード検索 | 発言内容・発言者名で横断検索（新しい年度から優先表示） |
| 📅 年度フィルター | 平成30年〜令和7年の期間を自由に絞り込み |
| 🤖 AIオンデマンド要約 | Gemini AI（gemini-3.1-flash-lite-preview）による質問・答弁の要旨生成 |
| 💾 要約キャッシュ | 一度生成した要約はSQLiteにキャッシュ（APIコスト削減） |
| 📖 原文全文表示 | 要約と原文を並べて確認可能 |
| 📱 スマートフォン対応 | レスポンシブデザインでモバイルからも快適に利用可能 |

---

## 🛠️ 技術スタック

| レイヤー | 技術 |
|---------|------|
| バックエンド | Python 3.11 / FastAPI / uvicorn |
| フロントエンド | Vanilla HTML / CSS / JavaScript（依存ゼロ） |
| データベース | SQLite（FTS5全文検索インデックス付き） |
| スクレイピング | Playwright（ヘッドレスChromium）/ httpx |
| AI要約 | Google Gemini API（gemini-3.1-flash-lite-preview） |
| インフラ | Ubuntu 24.04 VPS / Nginx / Let's Encrypt（HTTPS） |
| プロセス管理 | systemd（自動再起動） |

---

## 📊 データ規模

- **対象年度**: 平成30年〜令和7年（8年分）
- **収録会議数**: 226会議以上
- **収録発言件数**: 26,162件（議員発言のみ）
- **対象**: 本会議・各委員会の全議事録

---

## 📦 データ出典

松原市議会 会議録検索システム  
https://ssp.kaigiroku.net/tenant/matsubara/SpTop.html  
© Matsubara City

---

## 👥 Community & Maintainer

本プロジェクトは、テクノロジーによる社会課題解決（**Tech for Good**）の理念に賛同する有志の市民エンジニアによって開発・維持されています。

- **Lead Maintainer**: Takafumi Maruyoshi
- **GitHub Profile**: [@health-gear](https://github.com/health-gear)

---

## 📄 License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.
