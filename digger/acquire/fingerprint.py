# -*- coding: utf-8 -*-
"""Отпечаток текста документа и поиск дублей (ARCHITECTURE §4.3 text_fingerprint, §4.5; шаг 4.4).

Simhash 64 бита по тройкам слов: один и тот же текст с другими метаданными, номером ревизии или
переставленным колонтитулом даёт почти тот же отпечаток. Схожесть = 1 − расстояние Хэмминга / 64;
дубль — схожесть ≥ `DUP_THRESHOLD` (0.9, как в §4.5). Отпечаток — 16 шестнадцатеричных знаков, пустая
строка — текста нет (скан), такой документ ни с чем не совпадает.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

BITS = 64
SHINGLE = 3
DUP_THRESHOLD = 0.9
MIN_TOKENS = 20  # короче — отпечаток ненадёжен

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CJK_RE = re.compile(r"[㐀-鿿]")


def _tokens(text: str) -> List[str]:
    out: List[str] = []
    for word in _TOKEN_RE.findall(unicodedata.normalize("NFKC", text).lower()):
        if _CJK_RE.search(word):  # китайский без пробелов: по знакам
            out.extend(word)
        else:
            out.append(word)
    return out


def simhash(text: str) -> str:
    tokens = _tokens(text)
    if len(tokens) < MIN_TOKENS:
        return ""
    votes = [0] * BITS
    for i in range(len(tokens) - SHINGLE + 1):
        h = int.from_bytes(hashlib.md5(" ".join(tokens[i:i + SHINGLE]).encode("utf-8")).digest()[:8], "big")
        for bit in range(BITS):
            votes[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit in range(BITS):
        if votes[bit] > 0:
            value |= 1 << bit
    return "%016x" % value


def similarity(a: str, b: str) -> float:
    """0..1; пустой или неразборчивый отпечаток — 0."""
    if not a or not b:
        return 0.0
    try:
        distance = bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 0.0
    return 1.0 - distance / BITS


def is_duplicate(a: str, b: str, threshold: float = DUP_THRESHOLD) -> bool:
    return similarity(a, b) >= threshold


def find_similar(fingerprint: str, known: Mapping[str, str],
                 threshold: float = DUP_THRESHOLD) -> Optional[Tuple[str, float]]:
    """Самый похожий из `known` (ключ → отпечаток): (ключ, схожесть) или None."""
    best: Optional[Tuple[str, float]] = None
    for key, other in known.items():
        score = similarity(fingerprint, other)
        if score >= threshold and (best is None or score > best[1]):
            best = (key, score)
    return best


def group_duplicates(items: Mapping[str, str], threshold: float = DUP_THRESHOLD) -> List[List[str]]:
    """Группы из ≥ 2 ключей, чьи отпечатки связаны цепочкой дублей. Порядок — как во входе."""
    keys = [k for k, v in items.items() if v]
    parent: Dict[str, str] = {k: k for k in keys}

    def root(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if is_duplicate(items[a], items[b], threshold):
                parent[root(b)] = root(a)
    groups: Dict[str, List[str]] = {}
    for k in keys:
        groups.setdefault(root(k), []).append(k)
    return [g for g in groups.values() if len(g) > 1]


def fingerprint_pages(pages: Iterable[str]) -> str:
    return simhash("\n".join(pages))
