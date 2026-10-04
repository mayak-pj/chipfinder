# -*- coding: utf-8 -*-
"""Проверка adapters: тестовый запрос (NE555) к каждому адаптеру из data/sources.json.

Итог по источнику: ok / empty / captcha / no_key / quota / error / parse_error / no_adapter / disabled
(`chipfinder/acquire/diagnose.py`). Для источников с ошибкой дополнительно открывается главная страница домена
(sites.probe_url) — видно, закрыт сайт или сломан разбор. Сырые ответы неудачных источников сохраняются в
adapters/raw/ — из них делаются фикстуры (шаг 3.4). Список закрытых доменов для администраторов — в adapters/
для_администраторов.txt. Обращения — только через SafeHttp.
"""
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BUDGET_SEC = 20 * 60


def md(rows, probes, note):
    lines = ["# Проверка adapters: тестовый запрос NE555 к каждому адаптеру", "", note, "",
             "| Уровень | Источник | Адаптер | Итог | Найдено (PDF) | Время, с | Подробности | Главная страница |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %d (%d) | %s | %s | %s |" % (
            r["level"], r["id"], r["adapter"], r["status"], r["leads"], r["pdfs"], r["seconds"],
            r["detail"].replace("|", "/")[:80], probes.get(r["id"], "")))
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
        from chipfinder.acquire import diagnose
        from chipfinder.acquire.registry import Registry
        from chipfinder.core.config import load_config
        reg = Registry.load(path)
        hosts = reg.allowed_domains()
        http, net = sites.make_http(ctx.app_dir, hosts, ctx.work_dir)
        keys = load_config(ctx.app_dir).get("acquire", {}).get("api_keys", {})
    except BaseException:  # noqa
        return {"status": "fail", "error": "программа не загрузилась", "traceback": traceback.format_exc()}
    if http.offline:
        return {"status": "fail", "error": "автономный режим (offline) включён в config.json"}

    t0 = time.time()
    raw = os.path.join(ctx.work_dir, "raw")
    cancel = type("Stop", (), {"cancelled": property(lambda self: time.time() - t0 > BUDGET_SEC)})()
    try:
        rows = diagnose.diagnose_adapters(path, http, keys, record_dir=raw, cancel=cancel)
    except BaseException:  # noqa
        return {"status": "fail", "error": "диагностика не выполнилась", "traceback": traceback.format_exc()}

    probes = {}          # запасной вариант: главная страница домена для источников с ошибкой
    for r in rows:
        if r["status"] in ("error", "parse_error") and r["domains"]:
            row, _ = sites.probe_url(http, "https://%s/" % r["domains"][0], None)
            probes[r["id"]] = row["verdict"] + (" (%s)" % row["detail"] if row["detail"] else "")
            r["site_verdict"] = row["verdict"]

    st = diagnose.summary(rows)
    note = "Источников %d: %s" % (len(rows), ", ".join("%s — %d" % kv for kv in sorted(st.items())))
    with open(os.path.join(ctx.work_dir, "adapters.md"), "w", encoding="utf-8") as f:
        f.write(md(rows, probes, note))
    with open(os.path.join(ctx.work_dir, "adapters.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "proxy": net.get("proxy", ""), "use_system_proxy": net.get("use_system_proxy")},
                  f, ensure_ascii=False, indent=2)
    blocked = diagnose.admin_domains(rows)
    with open(os.path.join(ctx.work_dir, "для_администраторов.txt"), "w", encoding="utf-8") as f:
        f.write("Запрос на доступ для программы ChipFinder (поиск технической документации на микросхемы)\n"
                "Только чтение страниц поиска и скачивание PDF по HTTPS (порт 443).\n\n"
                "Не отвечают (%d):\n" % len(blocked))
        f.writelines("  %s\n" % d for d in blocked)
    return {"status": "ok", "sources": len(rows), "summary": st, "blocked_domains": blocked, "note": note,
            "seconds_total": round(time.time() - t0, 1)}
