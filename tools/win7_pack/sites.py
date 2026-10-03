# -*- coding: utf-8 -*-
"""Проверка sites: что реально отдают сайты из data/sources.json в сети работы.

1) Главная страница каждого домена (источники, каталоги, сайты производителей, хосты PDF): код, время, вердикт.
2) Для 3 чипов (NE555, STM32F103C8T6, CH340G) — страницы выдачи поисковиков и каталогов, урезанные копии
   сохраняются в sites/pages/ отчёта (только в my_reports/, в git не попадают).
Все обращения — через SafeHttp (белый список, вежливая частота, без обхода капчи).
Вердикт: ok / captcha / protection / network_block / http_error / unreachable:<причина>.
"""
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import traceback
from urllib.parse import quote, quote_plus, urlsplit

CHIPS = ["NE555", "STM32F103C8T6", "CH340G"]
MAX_BODY = 512 * 1024
SAVE_BODY = 150 * 1024
WORKERS = 6
BUDGET_SEC = 15 * 60

CAPTCHA_MARKERS = ["captcha", "antispider", "recaptcha", "hcaptcha", "verify you are human", "are you a robot",
                   "unusual traffic", "验证码", "安全验证", "百度安全验证", "异常流量", "подтвердите, что вы не робот",
                   "введите символы", "smartcaptcha"]
PROTECTION_MARKERS = ["just a moment", "cf-chl", "challenge-platform", "attention required", "ddos-guard",
                      "access denied", "akamai", "incapsula", "_incap_", "perimeterx", "request blocked",
                      "enable javascript and cookies"]
NETWORK_BLOCK_MARKERS = ["web filter", "webfilter", "forcepoint", "fortiguard", "fortinet", "zscaler", "blue coat",
                         "bluecoat", "websense", "kaspersky", "категория сайта", "доступ запрещ", "доступ к ресурсу",
                         "заблокирован", "blocked by", "url filtering", "proxy authentication"]


def _v1(sources):
    """sources.json схемы 2 → прежний вид (engines + levels); программа уже в sys.path."""
    from chipfinder.acquire.registry import legacy_sources
    return legacy_sources(sources)


def collect_domains(sources):
    """Отсортированный список (хост, где_встречается) — главные страницы для проверки."""
    hosts = {}
    extra = [(d, s["id"]) for s in sources.get("sources", []) for d in s.get("domains", [])]
    sources = _v1(sources)

    def add(host, where):
        host = (host or "").lower().strip(".")
        if host:
            hosts.setdefault(host, where)

    def add_url(u, where):
        add(urlsplit(u.replace("{q}", "x").replace("{part}", "x")).hostname, where)

    for key, e in sorted(sources.get("engines", {}).items()):
        add_url(e.get("url", ""), "поисковик " + key)
    for lv in sources.get("levels", []):
        for d in lv.get("direct", []) or []:
            add_url(d.get("url", ""), "каталог " + d.get("name", ""))
    for maker, dom in sorted(sources.get("maker_sites", {}).items()):
        add("www." + dom, "производитель " + maker)
    for dom in sources.get("pdf_hosts", []):
        add("www." + dom if dom.count(".") < 2 else dom, "хост PDF")
    for dom, where in extra:
        add("www." + dom if dom.count(".") < 2 else dom, "источник " + where)
    return sorted(hosts.items())


def collect_searches(sources, chips=CHIPS):
    """[(имя, чип, url)] — выдача поисковиков и прямых адресов каталогов."""
    out = []
    sources = _v1(sources)
    for chip in chips:
        for key, e in sorted(sources.get("engines", {}).items()):
            q = chip + " datasheet pdf"
            out.append(("engine_" + key, chip, e["url"].replace("{q}", quote_plus(q))))
        seen = set()
        for lv in sources.get("levels", []):
            for d in lv.get("direct", []) or []:
                if d["url"] in seen:
                    continue
                seen.add(d["url"])
                out.append(("direct_" + d.get("name", "site"), chip, d["url"].replace("{part}", quote(chip))))
    return out


def classify_exception(exc):
    """Причина недоступности по тексту ошибки (без привязки к конкретной библиотеке)."""
    name = type(exc).__name__
    text = ("%s %s" % (name, exc)).lower()
    if "netblocked" in text or name == "NetBlocked":
        return "program_rule"
    if "proxyerror" in text or "proxy" in text and "tunnel" in text:
        return "proxy"
    if "ssl" in text or "certificate" in text:
        return "tls"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(m in text for m in ("name or service", "getaddrinfo", "11001", "nodename", "name resolution",
                               "no address associated")):
        return "dns"
    if any(m in text for m in ("10054", "connection reset", "remotedisconnected", "connection aborted",
                               "reset by peer")):
        return "reset"
    if any(m in text for m in ("10061", "refused", "10060")):
        return "refused"
    return "other"


def classify_response(status, headers, body):
    """(вердикт, признак) по ответу сайта."""
    text = body[:200000].decode("utf-8", errors="ignore").lower()
    server = (headers.get("server") or "").lower()
    if status == 407:
        return "network_block", "407 прокси требует вход"
    for m in NETWORK_BLOCK_MARKERS:
        if m in text and (status >= 300 or len(body) < 20000):
            return "network_block", m
    for m in CAPTCHA_MARKERS:
        if m in text:
            return "captcha", m
    if status in (403, 429, 503):
        for m in PROTECTION_MARKERS:
            if m in text:
                return "protection", m
        if "cloudflare" in server or "akamai" in server or "cf-ray" in headers:
            return "protection", "server=%s" % (server or "cloudflare")
        return "http_error", "HTTP %d" % status
    if status >= 400:
        return "http_error", "HTTP %d" % status
    for m in PROTECTION_MARKERS[:4]:
        if m in text:
            return "protection", m
    if status >= 300:
        return "http_error", "HTTP %d без перехода" % status
    return "ok", ""


def title_of(body):
    m = re.search(rb"<title[^>]*>(.*?)</title>", body[:100000], re.I | re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1).decode("utf-8", errors="ignore")).strip()[:100]


def probe_url(http, url, deadline=None):
    """Одно обращение -> словарь результата (никогда не бросает)."""
    row = {"url": url, "status": 0, "verdict": "", "detail": "", "seconds": 0.0, "bytes": 0, "final": "",
           "title": "", "truncated": False}
    t0 = time.time()
    if deadline is not None and t0 > deadline:
        row.update(verdict="skipped", detail="вышло общее время проверки")
        return row, b""
    body = b""
    try:
        r = http.fetch(url, MAX_BODY)
        body = r["body"]
        row.update(status=r["status"], final=r["url"], bytes=len(body), truncated=r["truncated"],
                   title=title_of(body))
        row["verdict"], row["detail"] = classify_response(r["status"], r["headers"], body)
    except Exception as e:  # noqa — сетевые ошибки бывают любыми
        kind = classify_exception(e)
        row["verdict"] = "unreachable:" + kind
        row["detail"] = ("%s: %s" % (type(e).__name__, e))[:160]
    row["seconds"] = round(time.time() - t0, 2)
    return row, body


def make_http(app_dir, hosts, work_dir):
    from chipfinder.core.config import load_config, setup_logging
    from chipfinder.core.netsafe import SafeHttp
    cfg = load_config(app_dir)
    net = dict(cfg.get("network", {}))
    net["allowed_domains"] = sorted(set(list(net.get("allowed_domains", [])) + list(hosts)))
    net["min_interval_sec"] = max(1.5, float(net.get("min_interval_sec", 1.5)))
    quarantine = os.path.join(work_dir, "quarantine")
    return SafeHttp(net, quarantine, setup_logging(app_dir, cfg)), net


def safe_name(text):
    return re.sub(r"[^\w.-]+", "_", text, flags=re.U)[:80]


def report_md(domains, searches, note):
    lines = ["# Проверка sites: что отдают сайты из сети работы", "", note, ""]
    by = {}
    for r in domains:
        by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    lines += ["Главные страницы: " + ", ".join("%s — %d" % kv for kv in sorted(by.items())), "",
              "| Сайт | Откуда | Вердикт | Код | Время, с | Признак |", "|---|---|---|---|---|---|"]
    for r in domains:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (r["host"], r["where"], r["verdict"], r["status"] or "—",
                                                          r["seconds"], r["detail"].replace("|", "/")[:70]))
    lines += ["", "## Выдача для 3 чипов", "", "| Сайт | Чип | Вердикт | Код | Байт | Время, с | Заголовок страницы |",
              "|---|---|---|---|---|---|---|"]
    for r in searches:
        lines.append("| %s | %s | %s | %s | %d | %s | %s |" % (r["name"], r["chip"], r["verdict"], r["status"] or "—",
                                                               r["bytes"], r["seconds"],
                                                               (r["title"] or r["detail"]).replace("|", "/")[:60]))
    return "\n".join(lines) + "\n"


def run(ctx):
    path = os.path.join(ctx.app_dir, "data", "sources.json")
    if not os.path.isfile(path):
        return {"status": "fail", "error": "нет data/sources.json"}
    with open(path, encoding="utf-8") as f:
        sources = json.load(f)
    if ctx.app_dir not in sys.path:
        sys.path.insert(0, ctx.app_dir)
    try:
        domains = collect_domains(sources)
        searches = collect_searches(sources)
        http, net = make_http(ctx.app_dir, [h for h, _ in domains] + [urlsplit(u).hostname for _, _, u in searches],
                              ctx.work_dir)
    except BaseException:  # noqa
        return {"status": "fail", "error": "программа не загрузилась", "traceback": traceback.format_exc()}
    if http.offline:
        return {"status": "fail", "error": "автономный режим (offline) включён в config.json"}

    deadline = time.time() + BUDGET_SEC
    pages_dir = os.path.join(ctx.work_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    lock = threading.Lock()

    def do_domain(item):
        host, where = item
        row, _ = probe_url(http, "https://%s/" % host, deadline)
        if row["verdict"].startswith("unreachable") and host.startswith("www."):
            alt, _ = probe_url(http, "https://%s/" % host[4:], deadline)      # запасной вариант без www
            if not alt["verdict"].startswith("unreachable"):
                alt["detail"] = "без www; " + alt["detail"]
                row = alt
        row.update(host=host, where=where)
        return row

    def do_search(item):
        name, chip, url = item
        row, body = probe_url(http, url, deadline)
        row.update(name=name, chip=chip)
        if body:
            fn = "%s__%s.html" % (safe_name(name), safe_name(chip))
            with lock, open(os.path.join(pages_dir, fn), "wb") as f:
                f.write(body[:SAVE_BODY])
            row["saved"] = fn
        return row

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        dom_rows = list(pool.map(do_domain, domains))
        search_rows = list(pool.map(do_search, searches))

    ok = sum(1 for r in dom_rows if r["verdict"] == "ok")
    note = "Главных страниц доступно %d из %d; выдач получено %d из %d" % (
        ok, len(dom_rows), sum(1 for r in search_rows if r["verdict"] == "ok"), len(search_rows))
    with open(os.path.join(ctx.work_dir, "sites.md"), "w", encoding="utf-8") as f:
        f.write(report_md(dom_rows, search_rows, note))
    with open(os.path.join(ctx.work_dir, "sites.json"), "w", encoding="utf-8") as f:
        json.dump({"domains": dom_rows, "searches": search_rows, "proxy": net.get("proxy", ""),
                   "use_system_proxy": net.get("use_system_proxy")}, f, ensure_ascii=False, indent=2)
    verdicts = {}
    for r in dom_rows + search_rows:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    return {"status": "ok", "domains": len(dom_rows), "domains_ok": ok, "verdicts": verdicts, "note": note}
