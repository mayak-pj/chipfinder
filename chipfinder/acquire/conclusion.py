# -*- coding: utf-8 -*-
"""Заключение при неудаче поиска (ARCHITECTURE §4.11; шаг 6.4).

Оркестратор отдаёт неудачи доступа (`SearchResult.failures`), здесь они раскладываются по разделам:
  protected        сайт защищён от программ — человек в браузере скачать может: найденная страница или PDF,
                   а если страницы нет — ссылка на поиск этого партномера на сайте;
  blocked          нет доступа из сети производства — в запрос администраторам (список ведёт `access.py`);
  not_whitelisted  ссылки на домены вне белого списка программы;
  unclear          причина не ясна — показывается как есть.
Разовые сбои (`transient`) в заключение не выносятся. Один сайт — одна строка: первая найденная страница.
Заключение строится, когда документ не подтверждён; если что-то найдено (`found`), разделы показываются как
«где ещё посмотреть». Текст — всегда по-русски (`Conclusion.text()`); окно берёт те же данные из полей.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Tuple
from urllib.parse import quote, quote_plus

from .events import ru_plural
from .netdiag import NETWORK_BLOCKED, NOT_WHITELISTED, SITE_PROTECTED, UNKNOWN
from .query import base_part

HINT_MANUAL = "Скачали вручную? Нажмите «Проверить свой PDF»."
# код причины из `netdiag.Diagnosis.reason` → что сказать пользователю
REASONS_RU = {
    "captcha": "капча", "antibot": "защита от программ", "rate_limit": "слишком много запросов",
    "js_required": "нужен JavaScript", "login": "нужен вход", "forbidden": "сайт отказал программе",
    "proxy": "закрыто прокси-сервером", "proxy_auth": "прокси требует пароль", "dns": "имя сайта не находится",
    "reset": "соединение сброшено", "refused": "соединение отклонено", "unreachable": "сайт недостижим",
    "connect_timeout": "нет соединения", "cert": "подменён сертификат", "cert_site": "ошибка сертификата сайта",
    "mixed": "признаки противоречат друг другу", "http": "сайт без HTTPS",
}
_HEAD = {"not_found": "Не найдено автоматически.", "probable": "Найден вероятный документ, подтверждения нет.",
         "needs_user": "Найдены кандидаты, нужно ваше решение.", "rejected": "Найденные документы не подходят."}
_SECTIONS = (("protected", "Можно скачать вручную (сайт защищён от программ)"),
             ("blocked", "Нет доступа из сети производства"),
             ("not_whitelisted", "Не проверено: вне белого списка программы"),
             ("unclear", "Причина не ясна"))
_BY_CLASS = {SITE_PROTECTED: "protected", NETWORK_BLOCKED: "blocked", NOT_WHITELISTED: "not_whitelisted",
             UNKNOWN: "unclear"}


@dataclass
class SiteNote:
    """Строка заключения: сайт, причина и ссылка, которую человек может открыть в браузере."""
    site: str
    reason: str = ""          # код причины (`REASONS_RU`); пусто — не известна
    url: str = ""
    link: str = ""            # page — найденная страница или PDF; search — поиск партномера на сайте; "" — нет
    source: str = ""
    level: str = ""

    @property
    def reason_text(self) -> str:
        return REASONS_RU.get(self.reason, "")


@dataclass
class Conclusion:
    part: str
    status: str = "not_found"             # итог поиска: not_found | probable | needs_user | rejected
    sources: int = 0
    languages: int = 0
    seconds: float = 0.0
    protected: List[SiteNote] = field(default_factory=list)
    blocked: List[SiteNote] = field(default_factory=list)
    not_whitelisted: List[SiteNote] = field(default_factory=list)
    unclear: List[SiteNote] = field(default_factory=list)
    hint: str = HINT_MANUAL

    @property
    def found(self) -> bool:
        """Что-то найдено, но не подтверждено: разделы — «где ещё посмотреть»."""
        return self.status != "not_found"

    def rows(self) -> List[Tuple[str, str, SiteNote]]:
        """Все строки разделов подряд: (код раздела, заголовок, сайт) — для окна, HTML-отчёта и CSV."""
        return [(name, title, n) for name, title in _SECTIONS for n in getattr(self, name)]

    def csv_cell(self) -> str:
        """Одной ячейкой для сводки CSV: «вручную: a.com; нет доступа: b.com; вне списка: c.com»."""
        short = {"protected": "вручную", "blocked": "нет доступа", "not_whitelisted": "вне списка", "unclear": "не ясно"}
        return "; ".join("%s: %s" % (short[name], n.site) for name, _t, n in self.rows())

    def text(self) -> str:
        lines = ["%s Проверено %d %s на %d %s за %d с." % (
            _HEAD.get(self.status, _HEAD["not_found"]),
            self.sources, ru_plural(self.sources, "источник", "источника", "источников"),
            self.languages, ru_plural(self.languages, "языке", "языках", "языках"), int(round(self.seconds)))]
        for name, title in _SECTIONS:
            notes = getattr(self, name)
            if not notes:
                continue
            lines.append(("Где ещё посмотреть — %s%s:" % (title[0].lower(), title[1:])) if self.found else title + ":")
            for n in notes:
                reason = " — " + n.reason_text if n.reason_text and name != "not_whitelisted" else ""
                url = ": " + n.url if n.url and name != "blocked" else ""       # закрытый сайт не открыть
                lines.append("  %s%s%s" % (n.site, reason, url))
        lines.append(self.hint)
        return "\n".join(lines)


def _site(host: str) -> str:
    host = (host or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def search_link(registry: Any, site: str, part: str) -> str:
    """Ссылка на поиск партномера на этом сайте — по шаблону `url` источника из `sources.json`; нет — пусто.

    Шаблон с ключом API человеку не годится. Запрос — как у адаптера: `{part}` — партномер без суффиксов
    корпуса, `{q}` — запрос поисковику."""
    host = _site(site)
    for entry in registry.entries(include_disabled=True):
        if not any(host == _site(d) or host.endswith("." + _site(d)) for d in entry.domains):
            continue
        template = str(entry.options.get("url") or "")
        if not template.startswith("https://") or "{key}" in template:
            continue
        try:
            if "{part}" in template:
                return template.format(part=quote(base_part(part) or part, safe=""))
            if "{q}" in template:
                return template.format(q=quote_plus(part + " datasheet"))
        except (KeyError, IndexError, ValueError):
            continue
    return ""


def build_conclusion(part: str, status: str, failures: Iterable[Mapping[str, str]], registry: Any,
                     sources: int = 0, languages: int = 0, seconds: float = 0.0) -> Conclusion:
    """Заключение по неудачам доступа одного поиска (записи `SearchResult.failures` в порядке появления)."""
    out = Conclusion(part=part, status=status, sources=sources, languages=languages, seconds=seconds)
    notes: Dict[str, SiteNote] = {}
    kinds: Dict[str, str] = {}
    for f in failures:
        section = _BY_CLASS.get(f.get("cls", ""))
        site = _site(f.get("site", ""))
        if not section or not site:
            continue
        note = notes.get(site)
        if note is None or kinds[site] != section:        # класс уточнился (сбой → блокировка): берём последний
            note = notes[site] = SiteNote(site=site, source=f.get("source", ""), level=f.get("level", ""))
            kinds[site] = section
        note.reason = note.reason or f.get("reason", "")
        if not note.url and f.get("url"):
            note.url, note.link = f["url"], "page"
    for site, note in notes.items():
        if not note.url:
            note.url = search_link(registry, site, part)
            note.link = "search" if note.url else ""
        getattr(out, kinds[site]).append(note)
    return out
