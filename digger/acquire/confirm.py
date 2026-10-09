# -*- coding: utf-8 -*-
"""Подтверждение документа (ARCHITECTURE §4.5; шаг 5.1).

Документ «подтверждён», если выполнено одно из:
(а) `maker` — вердикт confirmed и файл скачан с домена производителя;
(б) `independent` — два confirmed-документа с независимых сайтов совпадают по sha256 или отпечатку текста
    (схожесть ≥ 0.9);
(в) `user` — пользователь нажал «Подтвердить» (кроме жёсткого отказа проверки файла).
Иначе максимум — «вероятно»: `apply` понижает confirmed до probable с причиной `unconfirmed`.

Независимость считается по сайту (`site_of`: `pdf1.alldatasheet.com` и `www.alldatasheet.com` — один сайт),
а сайты одного владельца (`owners`: домены одного источника в sources.json, например lcsc.com и szlcsc.com)
считаются одним. Один сайт дважды — не подтверждение.

Функции чистые: сеть, события (`confirm.search`, `confirm.identical`, `confirm.none`) и повторный поиск
копии — дело оркестратора. `agreeing_sites` — те же сайты для улики E10 (`verify.collect(same_doc_domains=…)`),
там вердикты копий не важны.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from .fingerprint import DUP_THRESHOLD, is_duplicate
from .models import AcquisitionRecord, Model
from .rank import _host_matches
from .verify import SourceTrust

RULE_MAKER, RULE_INDEPENDENT, RULE_USER = "maker", "independent", "user"
INDEPENDENT_SITES = 2
# Вторые уровни, под которыми регистрируют сайты в национальных зонах: example.com.tw, example.co.uk.
_SECOND_LEVEL = frozenset("com co net org gov edu ac or ne".split())
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


@dataclass
class Confirmation(Model):
    confirmed: bool = False
    rule: str = ""            # maker | independent | user, пусто — не подтверждён
    sites: List[str] = field(default_factory=list)   # независимые сайты с этим документом, первый — свой


def site_of(url: str) -> str:
    """Сайт адреса: `https://pdf1.alldatasheet.com/x.pdf` → `alldatasheet.com`. Не адрес — пустая строка."""
    try:
        host = (urlsplit(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    if not host or _IP_RE.match(host) or ":" in host:
        return host
    labels = host.split(".")
    keep = 3 if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def owners_from_sources(data: Mapping[str, Any]) -> List[List[str]]:
    """Группы сайтов одного владельца из sources.json: источник с несколькими сайтами."""
    out: List[List[str]] = []
    for src in data.get("sources") or []:
        sites = list(dict.fromkeys(site_of("//" + d) for d in src.get("domains") or [] if d))
        if len(sites) > 1:
            out.append(sites)
    return out


def _url(rec: AcquisitionRecord) -> str:
    """Откуда файл на самом деле скачан: адрес после переадресаций, иначе адрес ссылки."""
    if rec.fetch is not None and rec.fetch.final_url:
        return rec.fetch.final_url
    return rec.lead.url if rec.lead is not None else ""


def _status(rec: AcquisitionRecord) -> str:
    return rec.verdict.status if rec.verdict is not None else ""


def _owner(site: str, owners: Iterable[Sequence[str]]) -> str:
    for group in owners:
        names = [site_of("//" + d) for d in group if d]
        if site in names:
            return names[0]
    return site


def same_document(a: AcquisitionRecord, b: AcquisitionRecord, threshold: float = DUP_THRESHOLD) -> bool:
    """Тот же файл (sha256) или тот же текст (отпечаток). Без sha256 и без отпечатка совпадения нет."""
    sha_a = a.fetch.sha256 if a.fetch is not None else ""
    sha_b = b.fetch.sha256 if b.fetch is not None else ""
    if sha_a and sha_a.lower() == sha_b.lower():
        return True
    fp_a = a.facts.text_fingerprint if a.facts is not None else ""
    fp_b = b.facts.text_fingerprint if b.facts is not None else ""
    return is_duplicate(fp_a, fp_b, threshold)


def agreeing_sites(doc: AcquisitionRecord, others: Iterable[AcquisitionRecord],
                   owners: Iterable[Sequence[str]] = (), threshold: float = DUP_THRESHOLD,
                   statuses: Optional[Sequence[str]] = None) -> List[str]:
    """Независимые сайты, с которых получен этот документ; первый — сайт самого документа.
    `statuses` — учитывать только копии с таким вердиктом (для правила (б) — confirmed)."""
    owners = [list(g) for g in owners]
    seen: Dict[str, str] = {}
    own = site_of(_url(doc))
    if own:
        seen[_owner(own, owners)] = own
    for other in others:
        if other is doc or (statuses is not None and _status(other) not in statuses):
            continue
        site = site_of(_url(other))
        if site and same_document(doc, other, threshold):
            seen.setdefault(_owner(site, owners), site)
    return list(seen.values())


def confirm(doc: AcquisitionRecord, others: Iterable[AcquisitionRecord] = (),
            trust: Optional[SourceTrust] = None, owners: Iterable[Sequence[str]] = (),
            user: bool = False, threshold: float = DUP_THRESHOLD) -> Confirmation:
    """Подтверждён ли документ по §4.5. `others` — остальные документы этого поиска (и библиотеки)."""
    hard = doc.verdict is not None and any(r.startswith("hard:") for r in doc.verdict.reasons)
    if _status(doc) != "confirmed":
        sites = agreeing_sites(doc, (), owners)
        if user and not hard:
            return Confirmation(confirmed=True, rule=RULE_USER, sites=sites)
        return Confirmation(sites=sites)
    sites = agreeing_sites(doc, others, owners, threshold, statuses=("confirmed",))
    if trust is not None and _host_matches(_url(doc), trust.makers):
        rule = RULE_MAKER
    elif len(sites) >= INDEPENDENT_SITES:
        rule = RULE_INDEPENDENT
    elif user:
        rule = RULE_USER
    else:
        rule = ""
    return Confirmation(confirmed=bool(rule), rule=rule, sites=sites)


def apply(doc: AcquisitionRecord, confirmation: Confirmation) -> AcquisitionRecord:
    """Переносит итог в запись: `sources_agreeing` и статус вердикта. Без подтверждения confirmed → probable."""
    doc.sources_agreeing = list(confirmation.sites)
    verdict = doc.verdict
    if verdict is None:
        return doc
    if confirmation.confirmed:
        if confirmation.rule == RULE_USER and "user_confirmed" not in verdict.reasons:
            verdict.reasons.append("user_confirmed")
        verdict.status = "confirmed"
    elif verdict.status == "confirmed":
        verdict.status = "probable"
        verdict.reasons.append("unconfirmed")
    return doc
