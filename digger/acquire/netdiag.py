# -*- coding: utf-8 -*-
"""Классификатор неудач обращения к сайтам (ARCHITECTURE §4.11, шаг 5.5).

Каждая неудача получает класс — от него зависит, что сказать пользователю:
  site_protected   ответил сам сайт: капча, защита от ботов, 429, нужен JavaScript или вход → «скачайте вручную»;
  network_blocked  сайт закрыт сетью производства: DNS, сброс, прокси 403/407, страница блокировки, подмена
                   сертификата → в список для администраторов;
  not_whitelisted  домен не в белом списке программы;
  transient        разовый сбой: 5xx, обрыв посреди загрузки, таймаут ответа → повтор позже;
  unknown          признаки противоречат друг другу → «причина не ясна».
Не найдено (404), «это не PDF», «файл велик» и прочие отказы по правилам программы — не неудача доступа: `None`.

Классификатор — таблицы признаков ниже. `Diagnosis.firm` различает ответ, который сам доказывает причину
(страница прокси, капча), и сетевой признак, который бывает и разовым сбоем (DNS, сброс, таймаут соединения).
Нетвёрдую сетевую блокировку фиксирует `BlockTracker`: 2 неудачи подряд с интервалом ≥ 1 мин; до этого — `transient`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from ..core.netsafe import HttpStatus, NetBlocked

SITE_PROTECTED = "site_protected"
NETWORK_BLOCKED = "network_blocked"
NOT_WHITELISTED = "not_whitelisted"
TRANSIENT = "transient"
UNKNOWN = "unknown"
CLASSES = (SITE_PROTECTED, NETWORK_BLOCKED, NOT_WHITELISTED, TRANSIENT, UNKNOWN)

BODY_BYTES = 64 * 1024       # сколько тела ответа просматриваем
SHORT_PAGE = 30 * 1024       # при коде 2xx признаки в тексте считаются только на короткой странице:
#                              на большой обычной странице слово «captcha» — форма входа или реклама
SCRIPT_STUB = 4 * 1024       # «страница» из одного скрипта, который ставит cookie и перезагружается
MIN_GAP_SEC = 60.0           # интервал между двумя неудачами для фиксации сетевой блокировки
NEIGHBOUR_SEC = 600.0        # сколько успех соседнего домена говорит о том, что сеть жива


@dataclass(frozen=True)
class Diagnosis:
    cls: str                          # один из CLASSES
    reason: str                       # код причины: captcha, antibot, rate_limit, js_required, login, forbidden,
    #                                   proxy, proxy_auth, dns, reset, refused, unreachable, connect_timeout, cert,
    #                                   cert_site, mixed, domain, http, http_5xx, read_timeout, broken, error
    firm: bool = True                 # False — может оказаться разовым сбоем, нужен BlockTracker
    signs: Tuple[str, ...] = ()       # сработавшие признаки — для журнала и отчёта


# ---------- таблицы признаков: ответ сервера ----------
# (причина, выражение) — текст страницы, признаки защиты самого сайта; порядок = приоритет
_SITE_BODY: List[Tuple[str, Any]] = [(reason, re.compile(rx, re.I)) for reason, rx in (
    ("captcha", r"g-recaptcha|recaptcha/api|hcaptcha\.com|h-captcha|challenges\.cloudflare\.com|cf-chl|cf_chl|"
                r"<title>\s*just a moment|checkcaptcha|showcaptcha|smartcaptcha|не робот|anomaly-modal|"
                r"百度安全验证|wappass\.baidu\.com|安全验证|访问异常|antispider|geetest|b_captcha|captcha-delivery"),
    ("js_required", r"enable\s+javascript|turn\s+(on\s+)?javascript|javascript\s+is\s+(required|disabled)|"
                    r"requires?\s+javascript|включите\s+javascript|请(启用|开启)\s*javascript"),
    ("login", r"(log|sign)\s?in\s+to\s+(download|view|continue|access)|login\s+required|"
              r"please\s+(log|sign)\s?in|войдите|авторизуйтесь|требуется\s+авторизац|请(先)?登录|登录后(下载|查看)"),
)]
_COOKIE_RELOAD = re.compile(r"document\.cookie[\s\S]{0,2000}location\.(reload|replace|href)", re.I)
# заголовки защиты от ботов: имя → выражение для значения (None — достаточно наличия)
_SITE_HEADERS: Dict[str, Any] = {
    "cf-ray": None, "cf-mitigated": None, "x-sucuri-id": None, "x-iinfo": None, "x-datadome": None,
    "server": re.compile(r"cloudflare|ddos-guard|akamaighost|sucuri|qrator|datadome|variti", re.I),
}
_PROXY_NAME = (r"squid|usergate|kerio|blue\s?coat|zscaler|forti(gate|guard|proxy|net)|mcafee|ideco|"
               r"traffic\s?inspector|check\s?point|sophos|kaspersky|wingate|3proxy|forefront|isa\s?server|"
               r"websense|forcepoint|palo\s?alto|ironport|squidguard|dansguardian|e2guardian")
# ответ сформирован прокси или межсетевым экраном, а не сайтом
_PROXY_HEADERS: Dict[str, Any] = {
    "x-squid-error": None, "proxy-authenticate": None, "server": re.compile(_PROXY_NAME, re.I),
}
_PROXY_VIA = re.compile(_PROXY_NAME, re.I)       # слабый признак: через прокси идут и ответы самого сайта
_PROXY_BODY = re.compile(
    r"ERR_ACCESS_DENIED|ERR_CANNOT_FORWARD|squid/\d|access control configuration prevents|"
    r"fortiguard|zscaler|usergate|kaspersky web traffic|cisco umbrella|"
    r"web\s?(page|site)\s+(is\s+|has\s+been\s+)?blocked|internet usage policy|url\s+(category|filtering)|"
    r"blocked by (your |the )?(system |network )?(administrator|organi[sz]ation|company|policy|web\s?filter)|"
    r"(доступ|сайт|ресурс)[^<.]{0,60}(запрещ[её]н|заблокирован|ограничен)[^<]{0,200}"
    r"(системн\w+\s+администратор|политик|организац|категори)|заблокирован\w*\s+(администратором|политикой)", re.I)

# ---------- таблица признаков: исключение сети ----------
# (класс, причина, твёрдо, выражение по «имена типов | текст» всей цепочки исключений); порядок = приоритет
_EXC: List[Tuple[str, str, bool, Any]] = [(cls, reason, firm, re.compile(rx, re.I)) for cls, reason, firm, rx in (
    (NETWORK_BLOCKED, "proxy_auth", True, r"tunnel connection failed: 407|proxy authentication required"),
    (NETWORK_BLOCKED, "proxy", True, r"tunnel connection failed: 403"),
    (NETWORK_BLOCKED, "proxy", False, r"ProxyError|cannot connect to proxy|tunnel connection failed"),
    (UNKNOWN, "cert_site", True, r"certificate verify failed[^|]*(has expired|not yet valid|hostname mismatch|"
                                 r"doesn't match)|CertificateError"),
    (NETWORK_BLOCKED, "cert", False, r"certificate[ _]verify[ _]failed"),
    (TRANSIENT, "broken", True, r"ChunkedEncodingError|IncompleteRead|ContentDecodingError|connection broken"),
    (NETWORK_BLOCKED, "dns", False, r"NameResolutionError|gaierror|getaddrinfo failed|name or service not known|"
                                    r"nodename nor servname|failure in name resolution|no address associated|"
                                    r"failed to resolve"),
    (NETWORK_BLOCKED, "connect_timeout", False, r"ConnectTimeout|connect timeout|\b10060\b|"
                                                r"connection attempt failed because the connected party"),
    (NETWORK_BLOCKED, "refused", False, r"ConnectionRefusedError|connection refused|actively refused|\b10061\b"),
    (NETWORK_BLOCKED, "unreachable", False, r"network is unreachable|no route to host|host is unreachable|"
                                            r"unreachable (network|host)|\b1005[01]\b|\b10065\b"),
    (NETWORK_BLOCKED, "reset", False, r"ConnectionResetError|connection reset|forcibly closed|\b1005[34]\b|"
                                      r"ConnectionAbortedError|connection aborted|RemoteDisconnected|"
                                      r"EOF occurred in violation of protocol|UNEXPECTED_EOF|SSLEOFError"),
    (TRANSIENT, "read_timeout", True, r"ReadTimeout|timed out|timeout"),
)]
_HTTP_STATUS = re.compile(r"^HTTP (\d{3})\b")
# причины, после которых про доступность домена ничего не известно: счёт сетевых неудач не трогаем
_SAYS_NOTHING = ("read_timeout", "broken", "error")


def _text(body: Union[bytes, str, None]) -> str:
    if not body:
        return ""
    if isinstance(body, str):
        return body[:BODY_BYTES]
    chunk = bytes(body[:BODY_BYTES])
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "cp1251"):
        text = chunk.decode(enc, errors="replace")
        if text.count("�") * 50 <= len(text):
            return text
    return chunk.decode("utf-8", errors="replace")


def _header_signs(headers: Mapping[str, str], table: Dict[str, Any]) -> List[str]:
    out = []
    for name, rx in table.items():
        value = headers.get(name)
        if value is not None and (rx is None or rx.search(str(value))):
            out.append("%s: %s" % (name, str(value)[:60]))
    return out


def classify_response(status: int, headers: Optional[Mapping[str, str]] = None,
                      body: Union[bytes, str, None] = None) -> Optional[Diagnosis]:
    """Класс неудачи по ответу сервера (код, заголовки, начало тела). `None` — это не неудача доступа.

    С кодом 2xx вызывать, когда страница не дала того, что ждали (нет выдачи, нет ссылок): капча и страница
    блокировки приходят и с кодом 200."""
    hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}
    text = _text(body)
    ok = 200 <= status < 300
    look = bool(text) and (not ok or len(text) <= SHORT_PAGE)       # смотреть ли признаки в тексте

    site_reason = ""
    site: List[str] = []
    if look:
        for reason, rx in _SITE_BODY:
            m = rx.search(text)
            if m:
                site_reason = reason
                site.append("%s: %s" % (reason, m.group(0)[:40]))
                break
        if not site_reason and len(text) <= SCRIPT_STUB and _COOKIE_RELOAD.search(text):
            site_reason = "js_required"
            site.append("js_required: cookie + reload")
    antibot = _header_signs(hdrs, _SITE_HEADERS)

    proxy = _header_signs(hdrs, _PROXY_HEADERS)
    if look:
        m = _PROXY_BODY.search(text)
        if m:
            proxy.append("page: %s" % m.group(0)[:40])
    via = _PROXY_VIA.search(str(hdrs.get("via", "")))

    if status == 407:
        return Diagnosis(NETWORK_BLOCKED, "proxy_auth", True, tuple(proxy) or ("HTTP 407",))
    if proxy and (site_reason or antibot):
        return Diagnosis(UNKNOWN, "mixed", True, tuple(proxy + site + antibot))
    if proxy:
        return Diagnosis(NETWORK_BLOCKED, "proxy", True, tuple(proxy))
    if site_reason:
        return Diagnosis(SITE_PROTECTED, site_reason, True, tuple(site + antibot))
    if status == 429:
        return Diagnosis(SITE_PROTECTED, "rate_limit", True, ("HTTP 429",))
    if status == 401:
        return Diagnosis(SITE_PROTECTED, "login", True, ("HTTP 401",))
    if status == 403:
        if antibot:
            return Diagnosis(SITE_PROTECTED, "antibot", True, tuple(antibot))
        if via:
            return Diagnosis(NETWORK_BLOCKED, "proxy", True, ("via: %s" % str(hdrs["via"])[:60],))
        return Diagnosis(SITE_PROTECTED, "forbidden", True, ("HTTP 403",))
    if status >= 500:
        return Diagnosis(TRANSIENT, "http_5xx", True, ("HTTP %d" % status,))
    return None


def _chain(exc: BaseException) -> str:
    """«Имена типов | тексты» исключения и всех его причин — по ним ищет таблица `_EXC`."""
    names: List[str] = []
    texts: List[str] = []
    seen = set()
    todo: List[Any] = [exc]
    while todo and len(seen) < 12:
        e = todo.pop(0)
        if not isinstance(e, BaseException) or id(e) in seen:
            continue
        seen.add(id(e))
        names.append(type(e).__name__)
        texts.append(str(e))
        todo.extend([e.__cause__, e.__context__, getattr(e, "reason", None)])
        todo.extend(a for a in getattr(e, "args", ()) if isinstance(a, BaseException))
    return " ".join(names) + " | " + " | ".join(texts)


def classify_exception(exc: BaseException) -> Optional[Diagnosis]:
    """Класс неудачи по исключению `SafeHttp` или сетевой библиотеки. `None` — это не неудача доступа."""
    if isinstance(exc, HttpStatus):
        return classify_response(exc.status, getattr(exc, "headers", None), getattr(exc, "body", None))
    if isinstance(exc, NetBlocked):
        text = str(exc)
        m = _HTTP_STATUS.search(text)
        if m:
            return classify_response(int(m.group(1)))
        if "не в белом списке" in text:
            return Diagnosis(NOT_WHITELISTED, "domain", True, (text[:80],))
        if "Только HTTPS" in text:
            return Diagnosis(NOT_WHITELISTED, "http", True, (text[:80],))
        return None           # правила и лимиты программы: чёрный список, размер, «не PDF», автономный режим
    line = _chain(exc)
    for cls, reason, firm, rx in _EXC:
        m = rx.search(line)
        if m:
            return Diagnosis(cls, reason, firm, (m.group(0)[:60],))
    return Diagnosis(TRANSIENT, "error", True, (type(exc).__name__,))


class BlockTracker:
    """Фиксация сетевой блокировки (§4.11): 2 неудачи подряд с интервалом ≥ `min_gap` секунд.

    Память — на время работы программы; постоянный список ведёт `acquire/access.py`. Время — через `clock`."""

    def __init__(self, clock: Callable[[], float] = time.time, min_gap: float = MIN_GAP_SEC,
                 neighbour_sec: float = NEIGHBOUR_SEC) -> None:
        self._clock = clock
        self.min_gap = min_gap
        self.neighbour_sec = neighbour_sec
        self._first_fail: Dict[str, float] = {}
        self._blocked: Dict[str, str] = {}          # домен → причина
        self._last_ok: Dict[str, float] = {}
        self._reason: Dict[str, str] = {}           # домен → причина последней неудачи (для заключения)

    def ok(self, domain: str) -> None:
        """Домен ответил: блокировки нет, счёт неудач сброшен."""
        d = domain.lower()
        self._first_fail.pop(d, None)
        self._blocked.pop(d, None)
        self._last_ok[d] = self._clock()

    def settle(self, domain: str, diag: Optional[Diagnosis]) -> str:
        """Итоговый класс неудачи для домена ("" — не неудача доступа)."""
        if diag is None:
            return ""
        d = domain.lower()
        self._reason[d] = diag.reason
        if diag.cls != NETWORK_BLOCKED:
            if diag.reason not in _SAYS_NOTHING and diag.cls != NOT_WHITELISTED:
                self._first_fail.pop(d, None)       # ответил сам сайт — сеть до него открыта
                self._blocked.pop(d, None)
            return diag.cls
        now = self._clock()
        first = self._first_fail.setdefault(d, now)
        if diag.firm or d in self._blocked or now - first >= self.min_gap:
            self._blocked[d] = self._blocked.get(d) or diag.reason
            return NETWORK_BLOCKED
        return TRANSIENT

    def reason(self, domain: str) -> str:
        """Код причины последней неудачи домена (`Diagnosis.reason`); не было — пусто."""
        return self._reason.get(domain.lower(), "")

    def is_blocked(self, domain: str) -> bool:
        return domain.lower() in self._blocked

    def blocked(self) -> Dict[str, str]:
        """Зафиксированные блокировки: домен → причина."""
        return dict(self._blocked)

    def neighbours_ok(self, domain: str) -> bool:
        """Недавно отвечал другой домен: сеть жива, значит не открывается именно этот — вероятнее блокировка."""
        d = domain.lower()
        now = self._clock()
        return any(k != d and now - t <= self.neighbour_sec for k, t in self._last_ok.items())


def failure_class(exc: BaseException, tracker: Optional[BlockTracker] = None, domain: str = "") -> str:
    """Класс неудачи одной строкой — для `FetchResult.failure_class` и событий `access.*`.

    Без `tracker` нетвёрдая сетевая неудача считается разовым сбоем (`transient`)."""
    diag = classify_exception(exc)
    if tracker is not None and domain:
        return tracker.settle(domain, diag)
    if diag is None:
        return ""
    return diag.cls if diag.firm else TRANSIENT
