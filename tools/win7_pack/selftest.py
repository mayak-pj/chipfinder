# -*- coding: utf-8 -*-
"""Проверка selftest: `run.py --selftest` встроенным Python, полный вывод — в файл отчёта."""
import os
import subprocess

TIMEOUT = 900


def run(ctx):
    out_path = os.path.join(ctx.work_dir, "selftest.txt")
    cmd = [ctx.python, os.path.join(ctx.app_dir, "run.py"), "--selftest"]
    try:
        p = subprocess.run(cmd, cwd=ctx.app_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT)
        text = p.stdout.decode("utf-8", "replace")
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        text = (e.stdout or b"").decode("utf-8", "replace")
        rc = None
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    tail = [s for s in text.splitlines() if s.strip()][-3:]
    return {"status": "ok" if rc == 0 else "fail", "returncode": rc, "timeout": rc is None, "tail": tail}
