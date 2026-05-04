"""
議事録テキストの解析モジュール

テキスト形式：
  P.5 議長（河内徹君）
  ○議長（河内徹君） おはようございます...
  ～～～～ (区切り線)
  P.9 11番（河本晋一君）
  ◆11番（河本晋一君） 質問内容...
  P.9 総務部長（鶴山隆二君）
  ◎総務部長（鶴山隆二君） 答弁内容...

発言者タイプ：
  ○ = 議長 (chair)
  ◆ = 議員・委員 (member) ← 質問する側
  ◎ = 市長・副市長・部長等 (official) ← 答弁する側
"""

import re
from typing import Optional


# 発言開始のパターン
SPEECH_PATTERN = re.compile(
    r'^([○◆◎])\s*(.+?)\s*[（(](.+?)[）)]\s+(.*)',
    re.DOTALL
)

# ページ番号のパターン（例: "P.5"）
PAGE_PATTERN = re.compile(r'^P\.(\d+)')

# 区切り線パターン
SEPARATOR_PATTERN = re.compile(r'^～{5,}')

# 発言タイプのマッピング
TYPE_MAP = {
    "○": "chair",     # 議長
    "◆": "member",    # 議員
    "◎": "official",  # 市側
}


def _clean_name(raw: str) -> tuple[str, str]:
    """
    発言者名と役職を分離する
    例: '11番（河本晋一君）' → name='河本晋一', role='11番議員'
    例: '総務部長（鶴山隆二君）' → name='鶴山隆二', role='総務部長'
    例: '議長（河内徹君）' → name='河内徹', role='議長'
    """
    # 番号付き議員: "11番（河本晋一君）" のような形式が入力されることも
    # ここでは speaker_marker 部分のみ入力される
    # role=raw の中の（）の外、name=（）の中
    m = re.match(r'^(.+?)[（(](.+?)[）)]', raw.strip())
    if m:
        role = m.group(1).strip()
        name = m.group(2).strip().rstrip('君')
        return name, role
    return raw.strip(), ""


def parse_transcript(raw_text: str) -> list[dict]:
    """
    議事録の生テキストを発言リストに変換する

    Returns:
        list of dict with keys:
            order_num, page_num, speaker_name, speaker_role,
            speaker_type, content
    """
    speeches = []
    current_page = None
    current_type = None
    current_raw_speaker = None
    current_lines = []
    order = 0

    def flush():
        nonlocal order
        if current_type and current_raw_speaker and current_lines:
            content = "\n".join(current_lines).strip()
            # 最初の行に発言者名が含まれていることがあるので除去
            if content:
                name, role = _clean_name(current_raw_speaker)
                speeches.append({
                    "order_num": order,
                    "page_num": current_page,
                    "speaker_name": name,
                    "speaker_role": role,
                    "speaker_type": TYPE_MAP.get(current_type, "other"),
                    "content": content,
                })
                order += 1

    lines = raw_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 区切り線はスキップ
        if SEPARATOR_PATTERN.match(line) or line == "以上" or not line:
            i += 1
            continue

        # ページ番号行（"P.5 議長（河内徹君）" のパターン）
        page_match = PAGE_PATTERN.match(line)
        if page_match:
            current_page = int(page_match.group(1))
            i += 1
            continue

        # 発言開始行（○、◆、◎ で始まる）
        if line and line[0] in ("○", "◆", "◎"):
            marker = line[0]
            rest = line[1:].strip()

            # 発言者名部分（「議長（河内徹君）」など）
            speaker_match = re.match(r'^(.+?[）)])\s*(.*)', rest)
            if speaker_match:
                flush()
                current_type = marker
                current_raw_speaker = speaker_match.group(1).strip()
                current_lines = []
                body = speaker_match.group(2).strip()
                if body:
                    current_lines.append(body)
                i += 1
                continue

        # 継続行（現在の発言の続き）
        if current_type and line:
            current_lines.append(line)

        i += 1

    flush()
    return speeches


def pair_qa(speeches: list[dict]) -> list[tuple[dict, Optional[dict]]]:
    """
    議員の質問と直後の市側答弁をペアリングする

    Returns:
        list of (question_speech, answer_speech_or_None)
    """
    pairs = []
    i = 0
    while i < len(speeches):
        sp = speeches[i]
        if sp["speaker_type"] == "member":
            # 直後の official 発言を答弁として取得
            answer = None
            if i + 1 < len(speeches) and speeches[i + 1]["speaker_type"] == "official":
                answer = speeches[i + 1]
                i += 2
            else:
                i += 1
            pairs.append((sp, answer))
        else:
            i += 1
    return pairs
