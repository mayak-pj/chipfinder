# -*- coding: utf-8 -*-
"""Шаг 7.2: дизайн-система — токены, QSS, шрифты, иконки, снимки окна."""
import io
import os
import re
import shutil
import subprocess
import sys

import pytest

from digger.ui.theme import fonts, icons, tokens
from digger.ui.theme.qss import TEMPLATE, build_qss

APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGES = {"chevron_down": "/tmp/d.svg", "chevron_up": "/tmp/u.svg"}


# -------------------- без Qt --------------------

def test_both_themes_have_the_same_tokens():
    assert set(tokens.LIGHT) == set(tokens.DARK)
    t = tokens.tokens("light")
    assert (t["space_xs"], t["space_sm"], t["space_md"], t["space_lg"], t["space_xl"]) == (4, 8, 12, 16, 24)
    assert (t["radius_sm"], t["radius_md"]) == (6, 10)
    assert tokens.tokens(u"нет такой")["name"] == "light"
    assert tokens.tokens("dark")["bg"] != t["bg"]


def test_qss_is_built_from_tokens_only():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", TEMPLATE), "цвета в QSS — только из токенов"
    for name in tokens.THEMES:
        t = tokens.tokens(name)
        qss = build_qss(t, IMAGES)
        assert "$" not in qss
        assert t["accent"] in qss and t["surface"] in qss and 'url("/tmp/d.svg")' in qss
        assert qss.count("{") == qss.count("}")


def test_font_chain_has_chinese_fallback():
    win7 = ["Arial", "Segoe UI", "SimSun", "Microsoft YaHei", "Tahoma"]
    assert fonts.families(win7) == ["Segoe UI", "Microsoft YaHei"]
    mac = ["Helvetica Neue", "PingFang SC", "Arial Unicode MS"]
    assert fonts.families(mac, ".AppleSystemUIFont") == ["Helvetica Neue", "PingFang SC"]
    assert fonts.families(["Courier"], "Sys") == ["Sys"]
    assert fonts.pick(["segoe ui"], fonts.UI_FONTS) == "segoe ui"


def test_icon_set_and_license():
    names = icons.names()
    assert len(names) >= 30 and "search" in names and "lightbulb" in names and "lightbulb-off" in names
    for n in names:
        raw = io.open(os.path.join(icons.ICON_DIR, n + ".svg"), encoding="utf-8").read()
        assert "<svg" in raw and "currentColor" in raw, n
        assert b"currentColor" not in icons.svg_bytes(n, "#123456") and b"#123456" in icons.svg_bytes(n, "#123456")
    lic = io.open(os.path.join(icons.ICON_DIR, icons.LICENSE_FILE), encoding="utf-8").read()
    assert "ISC License" in lic and "Lucide" in lic
    with pytest.raises(KeyError):
        icons.svg_bytes("no-such-icon", "#000000")


def test_icons_used_in_code_exist():
    """Все иконки, которые называет код программы и инструменты, есть в наборе."""
    used = set()
    for root in ("digger", "plugins", "tools"):
        for d, _dirs, files in os.walk(os.path.join(APP, root)):
            for fn in files:
                if fn.endswith(".py"):
                    src = io.open(os.path.join(d, fn), encoding="utf-8").read()
                    used.update(re.findall(r"""\.(?:icon|pixmap)\(\s*["']([a-z0-9-]+)["']""", src))
                    used.update(re.findall(r"""\bico\(\s*["']([a-z0-9-]+)["']""", src))
    assert used, "окно должно использовать иконки"
    assert used <= set(icons.names()), sorted(used - set(icons.names()))


# -------------------- с Qt --------------------

@pytest.fixture()
def app():
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _colors(pm):
    img = pm.toImage()
    return {img.pixelColor(x, y).name() for x in range(img.width()) for y in range(img.height())
            if img.pixelColor(x, y).alpha() > 200}


def test_every_icon_renders_in_theme_color(app):
    from digger.ui.theme import Theme
    theme = Theme("light")
    for n in icons.names():
        pm = icons.pixmap(n, "#ff0000", 20, scale=1.0)
        assert (pm.width(), pm.height()) == (20, 20)
        assert _colors(pm) == {"#ff0000"}, n
    ic = theme.icon("search")
    assert not ic.isNull() and theme.icon("search") is ic
    assert theme.color("icon") in _colors(theme.pixmap("search"))


def test_theme_is_applied(app, tmp_path):
    from PyQt5.QtGui import QPalette
    from PyQt5.QtWidgets import QComboBox, QPushButton
    from digger.ui import theme as th
    cache = tmp_path / u"папка темы"
    theme = th.apply_theme(app, "light", str(cache))
    assert th.current() is theme and theme.name == "light"
    assert theme.info["style"].lower() == "fusion"
    assert app.styleSheet() == theme.qss and theme.color("accent") in theme.qss
    assert app.palette().color(QPalette.Window).name() == theme.color("bg")
    assert app.font().pixelSize() == tokens.FONT_SIZE["body"]
    assert theme.info["fonts"]["ui"] and theme.info["icons"] == len(icons.names())
    assert theme.info["qss_images"] == {"chevron_down": True, "chevron_up": True}, theme.info
    assert sorted(os.listdir(str(cache))) and all(f.endswith(".svg") for f in os.listdir(str(cache)))
    b, c = QPushButton(u"Кнопка"), QComboBox()
    for w in (b, c):
        w.ensurePolished()
    assert b.minimumSizeHint().height() >= tokens.CONTROL_HEIGHT - 2
    dark = th.apply_theme(app, "dark", str(cache))
    assert app.palette().color(QPalette.Window).name() == dark.color("bg") != theme.color("bg")
    th.apply_theme(app, "light", str(cache))


def test_window_uses_theme(app, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    app_dir = tmp_path / u"программа"
    os.makedirs(str(app_dir / "data"))
    shutil.copy(os.path.join(APP, "config.default.json"), str(app_dir))
    shutil.copytree(os.path.join(APP, "data", "i18n"), str(app_dir / "data" / "i18n"))
    shutil.copy(os.path.join(APP, "data", "sources.json"), str(app_dir / "data"))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    from digger.gui.main_window import MainWindow
    from digger.ui import theme as th
    w = MainWindow(str(app_dir))
    try:
        assert w.theme is th.current() and w.theme.name == "light"
        assert app.styleSheet() == w.theme.qss
        assert os.path.isdir(str(app_dir / "tmp" / "theme"))        # стрелки QSS — в папке программы
        actions = [a for a in w.toolbar.actions() if not a.isSeparator()]
        assert actions and all(not a.icon().isNull() for a in actions), [a.text() for a in actions if a.icon().isNull()]
        assert w.img_label.objectName() == "imagePreview" and not w.img_label.styleSheet()
    finally:
        w.close()


def test_screenshots_tool(tmp_path):
    pytest.importorskip("PyQt5")
    out = tmp_path / u"снимки"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    tool = os.path.join(APP, "tools", "screenshots.py")
    if not os.path.isfile(tool):                                 # в сборке для Win7 он лежит в checks/
        tool = os.path.join(APP, "checks", "screenshots.py")
    p = subprocess.run([sys.executable, tool, "--out", str(out),
                        "--prefix", "t", "--themes", "light,dark", "--no-ocr"], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace")
    names = sorted(os.listdir(str(out)))
    assert "t_light_window.png" in names and "t_dark_window.png" in names and "t_light_settings.png" in names
    for n in names:
        assert (out / n).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (out / "t_light_window.png").read_bytes() != (out / "t_dark_window.png").read_bytes()
