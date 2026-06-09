"""
設定ファイル
.env に GEMINI_API_KEY を書いて使用してください
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- 対象サイト ---
BASE_URL = "https://ssp.kaigiroku.net"
TENANT = "matsubara"
TENANT_ID = 438
TOP_URL = f"{BASE_URL}/tenant/{TENANT}/SpTop.html"
MINUTE_VIEW_URL = f"{BASE_URL}/tenant/{TENANT}/SpMinuteView.html"

# --- 収集対象年度（直近8年分）---
# 画面上の表示ラベルと一致させる
TARGET_YEAR_LABELS = [
    "令和7年",
    "令和6年",
    "令和5年",
    "令和4年",
    "令和3年",
    "令和2年",
    "令和元年/平成31年",
    "平成30年",
]

# --- データベース ---
DB_PATH = os.getenv("DB_PATH", "data/gikai.db")

# --- スクレイピング設定 ---
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))  # サーバー負荷軽減のため
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# --- Gemini AI ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash")

# 1リクエストあたりの最大文字数（コスト削減）
GEMINI_MAX_CHARS = int(os.getenv("GEMINI_MAX_CHARS", "3000"))
