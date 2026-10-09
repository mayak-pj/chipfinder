# -*- coding: utf-8 -*-
"""Проверка live: 12 эталонных чипов × все источники настоящим поиском программы (tools/live_check.py) + фото.

Каждый чип ищется оркестратором с «искать везде»; библиотека проверки — своя (в папке проверки), рабочая не
меняется. Фото из папки «фото» проходят весь путь: распознавание → партномер → поиск → заключение. В отчёт идут
live.md (чипы, матрица «источник × чип», заключения, ход поиска) и live.json. Обращения — только через SafeHttp.
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
MINUTES = 45


def _tool(app_dir):
    """live_check.py: в сборке лежит рядом (checks/), в исходниках — в tools/."""
    for d in (HERE, os.path.join(os.path.dirname(HERE))):
        if os.path.isfile(os.path.join(d, "live_check.py")):
            return d
    return ""


def run(ctx):
    if ctx.app_dir not in sys.path:
        sys.path.insert(0, ctx.app_dir)
    where = _tool(ctx.app_dir)
    if not where:
        return {"status": "fail", "error": "нет live_check.py"}
    if where not in sys.path:
        sys.path.insert(0, where)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import live_check
        import sites
        from digger.acquire.registry import Registry
        path = os.path.join(ctx.app_dir, "data", "sources.json")
        if not os.path.isfile(path):
            return {"status": "fail", "error": "нет data/sources.json"}
        http, net = sites.make_http(ctx.app_dir, Registry.load(path).allowed_domains(), ctx.work_dir)
    except BaseException:  # noqa
        return {"status": "fail", "error": "программа не загрузилась", "traceback": traceback.format_exc()}
    if http.offline:
        return {"status": "fail", "error": "автономный режим (offline) включён в config.json"}
    photos = ctx.photos_dir if os.path.isdir(ctx.photos_dir) else ""
    try:
        res = live_check.run_live(ctx.app_dir, os.path.join(ctx.work_dir, "lib"), photos_dir=photos, minutes=MINUTES,
                                  http=http)
    except BaseException:  # noqa
        return {"status": "fail", "error": "живая проверка не выполнилась", "traceback": traceback.format_exc()}
    with open(os.path.join(ctx.work_dir, "live.md"), "w", encoding="utf-8") as f:
        f.write(res["text"])
    with open(os.path.join(ctx.work_dir, "live.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": res["rows"], "photos": res["photos"], "proxy": net.get("proxy", "")}, f,
                  ensure_ascii=False, indent=2, default=str)
    ok = res["stat"].get("confirmed", 0) + res["stat"].get("probable", 0)
    return {"status": "ok" if ok else "fail", "chips": len(res["rows"]), "summary": res["stat"],
            "photos": len(res["photos"]), "note": res["note"]}
