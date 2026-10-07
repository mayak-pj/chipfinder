# -*- coding: utf-8 -*-
"""Проверка screens: снимки всех экранов программы в светлой и тёмной теме (tools/screenshots.py) в отчёт.

Снимки делает отдельный процесс (падение Qt не роняет набор). Сначала родной режим Windows (настоящие шрифты и
рамки Win7), при неудаче — запасной `offscreen`. Фото — свои, из папки «фото» (первые 4), иначе образцы.
Файлы: screens/*.png, screens_log.txt. Что смотреть глазами — в «ЧТО СДЕЛАТЬ.txt».
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 600     # две темы, ~14 окон; при ошибке Qt показывает окно и ждёт «OK» — процесс убиваем по таймауту
THEMES = "light,dark"


def _tool():
    """screenshots.py: в сборке лежит рядом (checks/), в исходниках — в tools/."""
    for d in (HERE, os.path.dirname(HERE)):
        if os.path.isfile(os.path.join(d, "screenshots.py")):
            return os.path.join(d, "screenshots.py")
    return ""


def _shoot(ctx, tool, platform, out):
    env = dict(os.environ)
    if platform:
        env["QT_QPA_PLATFORM"] = platform
    cmd = [ctx.python, tool, "--out", out, "--prefix", "screen", "--themes", THEMES]
    if os.path.isdir(ctx.photos_dir):
        cmd += ["--photos", ctx.photos_dir]
    try:
        p = subprocess.run(cmd, cwd=ctx.app_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=TIMEOUT)
        rc, text = p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rc, text = None, (e.stdout or b"").decode("utf-8", "replace") + "\n[таймаут %d с]" % TIMEOUT
    pngs = sorted(f for f in os.listdir(out) if f.endswith(".png")) if os.path.isdir(out) else []
    return rc, text, pngs


def run(ctx):
    tool = _tool()
    if not tool:
        return {"status": "fail", "error": "нет screenshots.py"}
    out = os.path.join(ctx.work_dir, "screens")
    tries = ["windows", "offscreen"] if sys.platform == "win32" else ["offscreen"]
    log, rc, pngs, used = [], None, [], ""
    for platform in tries:
        os.makedirs(out, exist_ok=True)
        rc, text, pngs = _shoot(ctx, tool, platform, out)
        log.append("--- QT_QPA_PLATFORM=%s: код %s, снимков %d\n%s" % (platform, rc, len(pngs), text))
        used = platform
        if rc == 0 and pngs:
            break
    with open(os.path.join(ctx.work_dir, "screens_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    themes = {t: sum(1 for n in pngs if "_%s_" % t in n) for t in THEMES.split(",")}
    ok = rc == 0 and all(themes.values())
    return {"status": "ok" if ok else "fail", "returncode": rc, "platform": used, "shots": len(pngs),
            "themes": themes, "note": "" if ok else "снимки не получены полностью, см. screens_log.txt"}
