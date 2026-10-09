# -*- coding: utf-8 -*-
"""Действия над сайтом из заключения (ARCHITECTURE §4.11; шаг 7.7): в запрос администраторам, разрешить домен."""
from __future__ import annotations

from typing import Any

from ..core.config import read_json, save_user_config
from ..core.netsafe import domain_match
from .conclusion import SiteNote
from .netdiag import NETWORK_BLOCKED


def add_to_access_request(access: Any, note: SiteNote, part: str) -> bool:
    """Сайт — в список для администраторов (`network_blocks`), даже если программа сама сочла его защищённым."""
    return bool(access is not None and access.record_failure(note.site, NETWORK_BLOCKED, part, note.url, note.level))


def allow_domain(http: Any, app_dir: str, domain: str) -> bool:
    """Домен — в белый список: сразу для текущего сеанса и в `config.json → network.allowed_domains`.
    Ложь — домен уже разрешён."""
    domain = (domain or "").lower().strip(".")
    if not domain or domain_match(domain, http.allowed):
        return False
    http.add_allowed([domain])
    import os
    path = os.path.join(app_dir, "config.json")
    saved = list(((read_json(path) if os.path.exists(path) else {}).get("network") or {}).get("allowed_domains", []))
    if domain not in saved:
        save_user_config(app_dir, {"network": {"allowed_domains": saved + [domain]}})
    return True
