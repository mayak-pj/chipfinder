# -*- coding: utf-8 -*-
"""Проверка downloads: скачивание и проверка PDF для 12 эталонных чипов из сети работы.

Для каждого чипа (`chipfinder/acquire/trial.py`): источники по уровням → лучшие ссылки → PDF скачивается в карантин
и проверяется (шифрование, страницы, активное содержимое), страница обходится в поисках PDF. Запасные варианты
перебираются сами: другой домен, обход страницы, пришедшей вместо PDF. В отчёт идут: downloads.md (чипы, домены,
классы неудач), downloads.json (все попытки и ход поиска), raw/ — страницы, на которых PDF не нашёлся (фикстуры
для шага 3.4). Сами PDF в отчёт не попадают. Обращения — только через SafeHttp.
"""
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BUDGET_SEC = 30 * 60


def _attempt(a):
    if a["step"] == "crawl":
        return "обход %s: %s" % (a["host"], "PDF %d" % a["found"] if a["found"] else a["detail"] or "нет PDF")
    if not a["ok"]:
        return "%s: ✘ %s" % (a["host"], a["failure_class"] or a["error"][:40])
    if not a["valid"]:
        return "%s: скачан, отказ — %s %s" % (a["host"], a["reason"], ", ".join(a["active"]))
    return "%s: ✔ %d стр., %d КБ%s" % (a["host"], a["pages"], a["size"] // 1024, "" if a["has_text"] else ", скан")


def md(rows, hosts, note):
    lines = ["# Проверка downloads: скачивание и проверка PDF для эталонных чипов", "", note, "",
             "| Чип | Итог | Годных PDF | Ссылок | Время, с | Попытки |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s%s | %d | %d | %s | %s |" % (
            r["part"], r["status"], " (время вышло)" if r["budget"] else "", r["valid"], r["leads"], r["seconds"],
            "; ".join(_attempt(a) for a in r["attempts"]).replace("|", "/") or "—"))
    lines += ["", "## Источники", "", "| Чип | Источник: итог (ссылок) |", "|---|---|"]
    for r in rows:
        lines.append("| %s | %s |" % (r["part"], ", ".join("%s: %s (%d)" % (s["id"], s["status"], s["leads"])
                                                            for s in r["sources"]) or "—"))
    lines += ["", "## Домены", "", "| Домен | Попыток | Скачано | Годных | Неудачи |", "|---|---|---|---|---|"]
    for h in hosts:
        lines.append("| %s | %d | %d | %d | %s |" % (h["host"], h["tried"], h["downloaded"], h["valid"], ", ".join(
            "%s — %d" % kv for kv in sorted(h["failures"].items())) or "—"))
    foreign = sorted(set(d for r in rows for d in r["not_whitelisted"]))
    if foreign:
        lines += ["", "## Не в белом списке программы (ссылки были, не открывались)", ""] + ["- " + d for d in foreign]
        urls = sorted(set((u["url"], u["source"], u["reason"]) for r in rows for u in r.get("not_whitelisted_urls", [])))
        if urls:
            lines += ["", "Сами адреса (причина: `domain` — домена нет в списке, `http` — только http):", ""]
            lines += ["- %s — источник %s, причина %s" % u for u in urls]
    return "\n".join(lines) + "\n"


def run(ctx):
    if ctx.app_dir not in sys.path:
        sys.path.insert(0, ctx.app_dir)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    path = os.path.join(ctx.app_dir, "data", "sources.json")
    if not os.path.isfile(path):
        return {"status": "fail", "error": "нет data/sources.json"}
    try:
        import sites
        from chipfinder.acquire import trial
        from chipfinder.acquire.registry import Registry
        from chipfinder.core.config import load_config
        http, net = sites.make_http(ctx.app_dir, Registry.load(path).allowed_domains(), ctx.work_dir)
        keys = load_config(ctx.app_dir).get("acquire", {}).get("api_keys", {})
        import pypdf
        versions = {"pypdf": pypdf.__version__}
    except BaseException:  # noqa
        return {"status": "fail", "error": "программа не загрузилась", "traceback": traceback.format_exc()}
    if http.offline:
        return {"status": "fail", "error": "автономный режим (offline) включён в config.json"}

    t0 = time.time()
    cancel = type("Stop", (), {"cancelled": property(lambda self: time.time() - t0 > BUDGET_SEC)})()
    try:
        rows = trial.trial_downloads(path, http, keys, record_dir=os.path.join(ctx.work_dir, "raw"), cancel=cancel)
    except BaseException:  # noqa
        return {"status": "fail", "error": "проба не выполнилась", "traceback": traceback.format_exc()}

    st = trial.summary(rows)
    hosts = trial.host_table(rows)
    left = [p for p in trial.CHIPS if p not in [r["part"] for r in rows]]
    note = "Чипов %d из %d: %s" % (len(rows), len(trial.CHIPS), ", ".join("%s — %d" % kv for kv in sorted(st.items())))
    if left:
        note += ". Не хватило времени: " + ", ".join(left)
    with open(os.path.join(ctx.work_dir, "downloads.md"), "w", encoding="utf-8") as f:
        f.write(md(rows, hosts, note))
    with open(os.path.join(ctx.work_dir, "downloads.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "hosts": hosts, "versions": versions, "proxy": net.get("proxy", ""),
                   "use_system_proxy": net.get("use_system_proxy")}, f, ensure_ascii=False, indent=2)
    return {"status": "ok" if st.get("ok") else "fail", "chips": len(rows), "summary": st, "note": note,
            "valid_pdfs": sum(r["valid"] for r in rows), "seconds_total": round(time.time() - t0, 1)}
