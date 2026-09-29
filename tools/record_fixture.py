# -*- coding: utf-8 -*-
"""Записывает фикстуру страницы для тестов адаптеров.

  python tools/record_fixture.py <adapter> <name> <url>          # скачать через SafeHttp
  python tools/record_fixture.py <adapter> <name> <url> --from-file page.html

Скрипты и стили вырезаются; результат: tests/fixtures/sources/<adapter>/<name>.html + <name>.meta.json.
Настоящие datasheet в фикстуры не класть — только урезанные HTML-страницы.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP not in sys.path:
    sys.path.insert(0, APP)


def strip_html(html: str) -> str:
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"<(script|style|noscript|svg)\b.*?</\1\s*>", "", html, flags=re.S | re.I)
    html = re.sub(r'\s+(style|on\w+)="[^"]*"', "", html, flags=re.I)
    return re.sub(r"\n\s*\n+", "\n", html).strip() + "\n"


def record(adapter: str, name: str, url: str, html: str, out_root: str) -> str:
    d = os.path.join(out_root, adapter)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(strip_html(html))
    with open(os.path.join(d, name + ".meta.json"), "w", encoding="utf-8") as f:
        json.dump({"url": url, "recorded": time.strftime("%Y-%m-%d"), "adapter": adapter},
                  f, ensure_ascii=False, indent=2)
    return path


def fetch(url: str) -> str:
    from chipfinder.core.config import load_config, setup_logging
    from chipfinder.core.netsafe import SafeHttp
    cfg = load_config(APP)
    http = SafeHttp(cfg["network"], os.path.join(APP, "data", "quarantine"), setup_logging(APP, cfg))
    return http.get_html(url)[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("adapter")
    ap.add_argument("name")
    ap.add_argument("url")
    ap.add_argument("--from-file")
    ap.add_argument("--out", default=os.path.join(APP, "tests", "fixtures", "sources"))
    a = ap.parse_args(argv)
    if a.from_file:
        with open(a.from_file, encoding="utf-8", errors="replace") as f:
            html = f.read()
    else:
        html = fetch(a.url)
    print(record(a.adapter, a.name, a.url, html, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
