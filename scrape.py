#!/usr/bin/env python3
"""
松原市議会議事録 スクレイピング実行スクリプト

使い方:
    python scrape.py                  # 全対象年度を収集
    python scrape.py --year 令和7年   # 特定年度のみ
    python scrape.py --council 857    # 特定会議のみ（デバッグ用）
"""

import asyncio
import argparse
import sys

from src.scraper import scrape_all
from src.config import TARGET_YEAR_LABELS


def parse_args():
    parser = argparse.ArgumentParser(
        description="松原市議会議事録スクレイパー"
    )
    parser.add_argument(
        "--year",
        type=str,
        help=f"対象年度を指定（例: 令和7年）。省略すると全対象年度を収集。\n対象年度: {TARGET_YEAR_LABELS}",
    )
    parser.add_argument(
        "--council",
        type=int,
        help="council_id を指定（デバッグ用。単一会議のみ取得）",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    print("=" * 50)
    print("松原市議会議事録 スクレイパー")
    print("=" * 50)
    print(f"対象年度: {args.year or '全対象年度'}")
    print()

    await scrape_all()


if __name__ == "__main__":
    asyncio.run(main())
