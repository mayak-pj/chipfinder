# -*- coding: utf-8 -*-
"""Проверка selftest: `run.py --selftest` встроенным Python, полный вывод — в файл отчёта.

Если самопроверка не завершилась, а оборвалась (код не из кодов pytest: аварийный выход модуля Qt, onnxruntime…),
она повторяется подробно (`-vv`, вывод без буфера, faulthandler): последний начатый тест — виновник. Он
исключается, и остальные тесты прогоняются снова — до `MAX_CRASHES` раз. Так за один выезд известны и все
тесты, обрывающие самопроверку, и итог остальных. Подробный вывод — `selftest_verbose.txt`.
"""
import os
import re
import subprocess

TIMEOUT = 900
MAX_CRASHES = 5
_DONE = re.compile(r"\b(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b")


def _run(ctx, extra=()):
    cmd = [ctx.python, os.path.join(ctx.app_dir, "run.py"), "--selftest"] + list(extra)
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1")
    try:
        p = subprocess.run(cmd, cwd=ctx.app_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=TIMEOUT)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        return None, (e.stdout or b"").decode("utf-8", "replace")


def _tail(text):
    return [s for s in text.splitlines() if s.strip()][-3:]


def crashed(rc):
    """Оборвалась, а не завершилась: коды pytest — 0…5."""
    return rc is not None and rc not in range(6)


def last_started(text):
    """Тест, на котором оборвался подробный вывод: последняя строка `файл::тест` без итога."""
    lines = [s.strip() for s in text.splitlines() if "::" in s and not s.startswith(" ")]
    if not lines or _DONE.search(lines[-1]):
        return ""
    return lines[-1].split(" ")[0]


def run(ctx):
    rc, text = _run(ctx)
    with open(os.path.join(ctx.work_dir, "selftest.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    res = {"status": "ok" if rc == 0 else "fail", "returncode": rc, "timeout": rc is None, "tail": _tail(text)}
    if not crashed(rc):
        return res
    culprits = []
    rest_rc, rest_text = rc, text
    with open(os.path.join(ctx.work_dir, "selftest_verbose.txt"), "w", encoding="utf-8") as f:
        for _ in range(MAX_CRASHES + 1):
            extra = ["-vv"] + ["--deselect=" + name for name in culprits]
            rest_rc, rest_text = _run(ctx, extra)
            f.write("=== %s\n%s\n\n" % (" ".join(extra), rest_text))
            name = last_started(rest_text) if crashed(rest_rc) else ""
            if not name or name in culprits:
                break
            culprits.append(name)
    res.update(crashed_tests=culprits, rest_returncode=rest_rc, rest_tail=_tail(rest_text),
               note="самопроверка оборвалась (код %s) на: %s; остальные тесты — код %s"
                    % (rc, ", ".join(culprits) or "не определено", rest_rc))
    return res
