# -*- coding: utf-8 -*-
"""Ранжирование Lead: нормализация URL, слияние дублей, оценка (ARCHITECTURE §4.1, этап Ranking).

Оценка — сумма понятных слагаемых (вес в `W`): уровень источника (по порядку `levels` из sources.json),
PDF вместо страницы, партномер в адресе/заголовке, домен производителя. Функции не меняют переданные объекты.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Dict, Iterable, List, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset(("fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "ref", "ref_src",
                             "igshid", "_hsenc", "_hsmi", "spm"))
W = {"level": 10.0, "pdf": 25.0, "part_url": 15.0, "part_title": 10.0, "part_snippet": 4.0, "maker": 20.0}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_tracking(name: str) -> bool:
    n = name.lower()
    return n.startswith("utm_") or n in TRACKING_PARAMS


def normalize_url(url: str) -> str:
    """Единый вид адреса: https, нижний регистр хоста, без www/порта по умолчанию/якоря/меток/хвостового `/`."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return url
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    if port and port != _DEFAULT_PORTS[parts.scheme]:
        host = "%s:%d" % (host, port)
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not _is_tracking(k)))
    return urlunsplit(("https", host, path, query, ""))


def _level_index(level: str, levels: Sequence[str]) -> int:
    return list(levels).index(level) if level in levels else len(levels)


def _host_matches(url: str, domains: Iterable[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains if d)


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", (text or "").lower())


def score_lead(lead, part: str, levels: Sequence[str], maker_domains: Sequence[str] = (),
               base_part: str = "") -> float:
    score = W["level"] * (len(levels) - _level_index(lead.level, levels))
    if lead.kind == "pdf" or urlsplit(lead.url).path.lower().endswith(".pdf"):
        score += W["pdf"]
    names = [n for n in (_norm(base_part), _norm(part)) if len(n) >= 3]
    url_n, title_n, snip_n = _norm(lead.url), _norm(lead.title), _norm(lead.snippet)
    if any(n in url_n for n in names):
        score += W["part_url"]
    if any(n in title_n for n in names):
        score += W["part_title"]
    if any(n in snip_n for n in names):
        score += W["part_snippet"]
    if maker_domains and _host_matches(lead.url, maker_domains):
        score += W["maker"]
    return score


def dedupe(leads: Iterable, levels: Sequence[str] = ()) -> List:
    """Слияние Lead с одинаковым нормализованным адресом. Порядок — по первому появлению;
    берётся заголовок/сниппет, где они есть, и источник с более ранним уровнем."""
    merged: Dict[str, object] = {}
    for lead in leads:
        key = normalize_url(lead.url)
        cur = merged.get(key)
        if cur is None:
            merged[key] = replace(lead, url=key)
            continue
        if _level_index(lead.level, levels) < _level_index(cur.level, levels):
            cur.level, cur.source_id, cur.query, cur.language = lead.level, lead.source_id, lead.query, lead.language
        if lead.kind == "pdf":
            cur.kind = "pdf"
        if len(lead.title) > len(cur.title):
            cur.title = lead.title
        if len(lead.snippet) > len(cur.snippet):
            cur.snippet = lead.snippet
    return list(merged.values())


def rank(leads: Iterable, part: str, levels: Sequence[str], maker_domains: Sequence[str] = (),
         base_part: str = "") -> List:
    """Дубли слиты, `rank_score` выставлен, порядок — по убыванию (при равенстве — как пришли)."""
    out = dedupe(leads, levels)
    for lead in out:
        lead.rank_score = score_lead(lead, part, levels, maker_domains, base_part)
    return sorted(out, key=lambda x: -x.rank_score)
