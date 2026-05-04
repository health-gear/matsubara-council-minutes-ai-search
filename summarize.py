#!/usr/bin/env python3
"""
松原市議会議事録 検索・AI要約ツール

【基本的な使い方】
  python summarize.py --search 子育て支援    # キーワードで検索して要約
  python summarize.py --search 橋本          # 議員名でも検索できる
  python summarize.py --search 防災 --limit 20  # 件数を増やす（デフォルト10件）

【準備コマンド（初回のみ）】
  python summarize.py --parse    # 議事録を発言に分解（AI不使用・高速）

【仕組み】
  1. キーワードでDB内の発言テキストを全文検索（FTS5）
  2. ヒットした発言だけ Gemini で要約（必要な分だけ）
  3. 要約結果はキャッシュ保存 → 2回目以降は瞬時に返る
"""

import argparse

from src.summarizer import run_parse, search_and_summarize
from src.config import GEMINI_API_KEY, GEMINI_MODEL


def parse_args():
    parser = argparse.ArgumentParser(
        description="松原市議会議事録 検索・AI要約ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python summarize.py --search 子育て支援
  python summarize.py --search 空き家 --limit 20
  python summarize.py --parse
        """
    )
    parser.add_argument(
        "--search",
        type=str,
        metavar="キーワード",
        help="キーワードで発言を検索してAI要約を表示する",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="検索結果の最大件数（デフォルト: 20、0で全件）",
    )
    parser.add_argument(
        "--parse",
        action="store_true",
        help="議事録テキストを発言単位に分解してDBに保存する（API不使用・高速）",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 50)
    print("松原市議会議事録 AI要約ツール")
    print(f"使用モデル: {GEMINI_MODEL}")
    print("=" * 50)
    print()

    # 発言分解モード（--parse）
    if args.parse:
        run_parse()
        return

    # 検索モード（--search）
    if args.search:
        if not GEMINI_API_KEY:
            print("[エラー] GEMINI_API_KEY が設定されていません。")
            print("  .env ファイルに GEMINI_API_KEY=... を追加してください。")
            return
        search_and_summarize(args.search, limit=args.limit)
        return

    # 引数なし → ヘルプ表示
    print("使い方:")
    print("  python summarize.py --search キーワード")
    print("  python summarize.py --parse")
    print()
    print("  --help で詳細を確認できます。")


if __name__ == "__main__":
    main()
