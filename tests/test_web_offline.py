# -*- coding: utf-8 -*-
"""Проверка правил безопасности и разбора выдачи поисковиков (без сети):
python tests/test_web_offline.py"""
import base64
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

from chipfinder.core.config import load_config, setup_logging  # noqa: E402
from chipfinder.core.interfaces import Context  # noqa: E402
from chipfinder.core.models import Candidate  # noqa: E402
from chipfinder.core.netsafe import pdf_danger_scan  # noqa: E402
from chipfinder.core.registry import load_modules  # noqa: E402
from chipfinder.modules.web.linkparse import decode_engine_link, extract_links  # noqa: E402

fails = 0


def check(name, cond):
    global fails
    print(("OK   " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


def main():
    work = tempfile.mkdtemp(prefix="cf_web_")
    cfg = load_config(APP)
    cfg["paths"].update({"db": os.path.join(work, "t.sqlite"), "library_dir": os.path.join(work, "lib"),
                         "quarantine_dir": os.path.join(work, "q"), "log_dir": os.path.join(work, "logs")})
    ctx = Context(cfg, APP, setup_logging(APP, cfg))
    load_modules(ctx)
    ws = ctx.modules["web_search"]
    http = ws.http

    # --- белый список ---
    check("https на разрешённый сайт", http.is_allowed("https://www.alldatasheet.com/view.jsp?x=1"))
    check("поддомен разрешённого сайта", http.is_allowed("https://pdf1.alldatasheet.com/a.pdf"))
    check("неизвестный сайт запрещён", not http.is_allowed("https://evil-datasheets.xyz/a.pdf"))
    check("похожий домен запрещён", not http.is_allowed("https://alldatasheet.com.evil.ru/a.pdf"))
    check("http без разрешения запрещён", not http.is_allowed("http://www.alldatasheet.com/"))
    check("IP-адрес запрещён", not http.is_allowed("https://192.168.1.10/a.pdf"))
    check("file:// запрещён", not http.is_allowed("file:///C:/Windows/win.ini"))
    check("нестандартный порт запрещён", not http.is_allowed("https://www.st.com:8443/a.pdf"))

    # --- PDF с активным содержимым ---
    check("PDF с JavaScript распознан", "JavaScript" in pdf_danger_scan(b"%PDF-1.4 /OpenAction << /S /JavaScript /JS (app.alert(1)) >>"))
    check("обычный PDF чистый", pdf_danger_scan(b"%PDF-1.4 /Type /Page /JSONData") == [])

    # --- раскодирование ссылок поисковиков ---
    real = "https://www.ti.com/lit/ds/symlink/lm358.pdf"
    ddg = "https://duckduckgo.com/l/?uddg=" + real.replace(":", "%3A").replace("/", "%2F") + "&rut=abc"
    check("DuckDuckGo", decode_engine_link(ddg, "ddg") == real)
    b = base64.urlsafe_b64encode(real.encode()).decode().rstrip("=")
    check("Bing", decode_engine_link("https://www.bing.com/ck/a?!&&p=x&u=a1" + b + "&ntb=1", "bing") == real)
    html = '<div class="result c-container" mu="https://www.elecfans.com/soft/24c02.pdf"><h3><a href="http://www.baidu.com/link?url=zz">24C02 数据手册</a></h3></div>'
    links = extract_links(html, "https://www.baidu.com/s?wd=24C02")
    check("Baidu (атрибут mu)", any(u == "https://www.elecfans.com/soft/24c02.pdf" for u, _ in links))

    # --- оценка ссылок ---
    s1 = ws._score("https://www.ti.com/lit/ds/symlink/lm358.pdf", "LM358 datasheet", "LM358", "catalog")
    s2 = ws._score("https://www.eevblog.com/forum/beginners/lm358-question/", "LM358 question", "LM358", "forum")
    s3 = ws._score("https://www.example.com/page", "Cooking recipes", "LM358", "catalog")
    check("PDF выше форума (%.2f > %.2f)" % (s1, s2), s1 > s2 > 0)
    check("нерелевантная ссылка отброшена", s3 == 0)

    # --- автономный режим ---
    http.cfg["offline"] = True
    check("автономный режим: поиск пустой", ws.search([Candidate(part="LM358", score=1.0)]) == [])
    for m in ctx.modules.values():
        m.close()
    print("ИТОГ:", "всё верно" if not fails else "%d ошибок" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
