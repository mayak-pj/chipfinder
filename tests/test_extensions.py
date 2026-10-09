# -*- coding: utf-8 -*-
"""Шаг 7.1: API расширений, загрузчик, изоляция ошибок, окно «Расширения»."""
import io
import json
import os
import shutil
import sys

import pytest

from digger.core.config import load_config, setup_logging
from digger.core.interfaces import Context
from digger.extensions.api import API_VERSION_STR, api_compatible
from digger.extensions.loader import ExtensionManager

from conftest import APP

GOOD = u'''# -*- coding: utf-8 -*-
from digger.extensions.api import Extension as Base

class Extension(Base):
    def setup(self, services):
        self.s, self.seen, self.clicks = services, [], 0
        services.subscribe(lambda e: self.seen.append(e.key))
    def contribute(self, ui):
        ui.add_tab(u"Проба", self.tab)
        ui.add_menu_item(u"Файл", u"Пункт пробы", self.click)
        ui.add_menu_item(u"Своё меню", u"Ещё пункт", self.click)
        ui.add_toolbar_button(u"Кнопка пробы", self.click)
        ui.add_csv_column(u"Колонка", lambda report: "x" + str(report))
    def tab(self):
        from PyQt5.QtWidgets import QLabel
        return QLabel(u"вкладка пробы")
    def click(self):
        self.clicks += 1
'''
BAD_SETUP = u'''from digger.extensions.api import Extension as Base

class Extension(Base):
    def setup(self, services):
        services.subscribe(lambda e: None)
        raise RuntimeError(u"сломалось при запуске")
'''
BAD_LATER = u'''from digger.extensions.api import Extension as Base

class Extension(Base):
    def setup(self, services):
        services.subscribe(self.on_event)
    def contribute(self, ui):
        ui.add_tab("Later", lambda: None)
        ui.add_menu_item("", u"Упасть", self.boom)
        ui.add_csv_column("C", self.boom)
    def on_event(self, event):
        raise ValueError("event")
    def boom(self, *a):
        raise ValueError("boom")
'''


def write_ext(root, folder, code, **manifest):
    d = os.path.join(str(root), folder)
    os.makedirs(d)
    data = dict({"id": folder, "name": u"Расширение " + folder, "version": "0.1", "api": API_VERSION_STR}, **manifest)
    with io.open(os.path.join(d, "extension.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False))
    with io.open(os.path.join(d, "extension.py"), "w", encoding="utf-8") as f:
        f.write(code)
    return d


@pytest.fixture()
def bare_ctx(tmp_path):
    """Контекст без модулей: расширениям нужны конфигурация, журнал и база."""
    cfg = load_config(APP)
    cfg["paths"].update({"db": str(tmp_path / "t.sqlite"), "log_dir": str(tmp_path / "logs")})
    cfg["extensions"] = {"enabled": [], "disabled": [], "settings": {"good": {"n": 5}}}
    return Context(cfg, str(tmp_path / "app"), setup_logging(APP, cfg))


def manager(ctx, tmp_path, **kw):
    kw.setdefault("runner", lambda work, finish: finish(work()))
    return ExtensionManager(ctx, dirs=[str(tmp_path / u"мои плагины")], **kw)


def test_api_version_check():
    assert api_compatible(API_VERSION_STR) and api_compatible("1")
    assert not api_compatible("2.0") and not api_compatible("1.99") and not api_compatible("") and not api_compatible("x")


def test_loads_extension_and_collects_contributions(bare_ctx, tmp_path):
    write_ext(tmp_path / u"мои плагины", "good", GOOD)
    m = manager(bare_ctx, tmp_path)
    info, = m.load()
    assert (info.state, info.error, info.name) == ("active", "", u"Расширение good")
    assert info.instance.s.settings == {"n": 5} and info.instance.s.bus is bare_ctx.bus
    assert [c.title for c in m.ui.of("tab")] == [u"Проба"]
    assert [(c.menu, c.title) for c in m.ui.of("menu_item")][0] == (u"Файл", u"Пункт пробы")
    assert m.ui.of("csv_column")[0].target(7) == "x7"
    bare_ctx.bus.emit("photo.recognized", lang="ru", part="NE555")
    assert info.instance.seen == ["photo.recognized"]
    m.close()
    bare_ctx.bus.emit("photo.recognized", lang="ru", part="NE555")
    assert info.instance.seen == ["photo.recognized"] and m.ui.items == []


def test_failing_extension_is_disabled_and_others_work(bare_ctx, tmp_path):
    root = tmp_path / u"мои плагины"
    write_ext(root, "a_bad", BAD_SETUP)
    write_ext(root, "good", GOOD)
    write_ext(root, "later", BAD_LATER)
    write_ext(root, "old", GOOD, api="2.0")
    write_ext(root, "syntax", u"class Extension(:\n")
    write_ext(root, "noclass", u"x = 1\n")
    write_ext(root, "twin", GOOD, id="good")
    write_ext(root, "Bad-Id", GOOD)
    failed = []
    m = manager(bare_ctx, tmp_path, on_failure=lambda info: failed.append(info.id))
    state = {os.path.basename(x.path): x.state for x in m.load()}
    assert state == {"a_bad": "failed", "good": "active", "later": "active", "old": "incompatible",
                     "syntax": "failed", "noclass": "failed", "twin": "failed", "Bad-Id": "failed"}
    assert u"сломалось при запуске" in m.get("a_bad").error and "2.0" in m.get("old").error
    assert len(bare_ctx.bus._subscribers) == 2          # подписка упавшего в setup снята

    # ошибка в пункте меню: расширение отключается, его вклады и подписки пропадают, остальные живы
    later = m.get("later")
    boom = m.ui.of("menu_item")[-1].target
    assert boom() is None and later.state == "failed" and "boom" in later.error
    assert [c.ext_id for c in m.ui.items if c.ext_id == "later"] == [] and len(m.ui.of("tab")) == 1
    assert failed.count("later") == 1 and boom() is None and failed.count("later") == 1
    bare_ctx.bus.emit("photo.recognized", lang="ru", part="X")
    assert m.get("good").instance.seen == ["photo.recognized"] and m.get("good").state == "active"


def test_failing_event_handler_disables_extension(bare_ctx, tmp_path):
    write_ext(tmp_path / u"мои плагины", "later", BAD_LATER)
    m = manager(bare_ctx, tmp_path)
    m.load()
    bare_ctx.bus.emit("photo.recognized", lang="ru", part="X")
    assert m.get("later").state == "failed" and bare_ctx.bus._subscribers == []


def test_background_task_and_its_failure(bare_ctx, tmp_path):
    write_ext(tmp_path / u"мои плагины", "good", GOOD)
    m = manager(bare_ctx, tmp_path)
    info, = m.load()
    got = []
    info.instance.s.run_in_background(lambda: 41 + 1, got.append)
    assert got == [42]
    info.instance.s.run_in_background(lambda: 1 / 0, got.append)
    assert got == [42] and info.state == "failed" and "ZeroDivisionError" in info.error


def test_own_tables_prefix_and_migrations(bare_ctx, tmp_path):
    write_ext(tmp_path / u"мои плагины", "good", GOOD)
    m = manager(bare_ctx, tmp_path)
    info, = m.load()
    db = info.instance.s.db()
    first = ["CREATE TABLE {prefix}seen (part TEXT PRIMARY KEY)"]
    assert db.migrate(first) == 1 and db.table("seen") == "ext_good_seen"
    db.execute("INSERT INTO {prefix}seen VALUES (?)", ("NE555",))
    assert db.migrate(first + ["ALTER TABLE {prefix}seen ADD COLUMN n INTEGER DEFAULT 3"]) == 2
    assert db.migrate(first + ["ALTER TABLE {prefix}seen ADD COLUMN n INTEGER DEFAULT 3"]) == 2     # повторно — ничего
    assert db.execute("SELECT part, n FROM ext_good_seen") == [("NE555", 3)]
    with pytest.raises(ValueError):
        db.migrate(first * 2 + ["DROP TABLE files"])
    m.close()


def test_enable_disable_is_saved(bare_ctx, tmp_path):
    root = tmp_path / u"мои плагины"
    write_ext(root, "good", GOOD)
    write_ext(root, "opt", GOOD, enabled_by_default=False)
    os.makedirs(bare_ctx.app_dir)
    m = manager(bare_ctx, tmp_path)
    assert {x.id: x.state for x in m.load()} == {"good": "active", "opt": "disabled"}
    m.set_enabled("good", False)
    m.set_enabled("opt", True)
    with io.open(os.path.join(bare_ctx.app_dir, "config.json"), encoding="utf-8") as f:
        assert json.load(f) == {"extensions": {"enabled": ["opt"], "disabled": ["good"]}}
    m2 = manager(bare_ctx, tmp_path)
    assert {x.id: x.state for x in m2.load()} == {"good": "disabled", "opt": "active"}


def test_example_extension_from_plugins(bare_ctx, tmp_path):
    """Пример из поставки: выключен по умолчанию, после включения работает со своей таблицей."""
    bare_ctx.app_dir = APP
    m = ExtensionManager(bare_ctx)
    info = {x.id: x for x in m.load()}["example"]
    assert info.state == "disabled" and not info.builtin
    bare_ctx.config["extensions"]["enabled"] = ["example"]
    m = ExtensionManager(bare_ctx)
    info = {x.id: x for x in m.load()}["example"]
    assert info.state == "active", info.error
    bare_ctx.bus.emit("photo.recognized", lang="ru", part="CH340G")
    assert "CH340G — 1" in info.instance.text()
    m.close()


# ---------- окно ----------

@pytest.fixture()
def window(tmp_path, monkeypatch):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    app_dir = tmp_path / u"программа"
    os.makedirs(str(app_dir / "plugins"))
    shutil.copy(os.path.join(APP, "config.default.json"), str(app_dir))
    shutil.copytree(os.path.join(APP, "data", "i18n"), str(app_dir / "data" / "i18n"))
    shutil.copy(os.path.join(APP, "data", "sources.json"), str(app_dir / "data"))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    write_ext(app_dir / "plugins", "good", GOOD)
    write_ext(app_dir / "plugins", "later", BAD_LATER)
    from digger.gui.main_window import MainWindow
    w = MainWindow(str(app_dir))
    yield w, app
    w.close()


def tab_titles(w):
    return [w.tabs.tabText(i) for i in range(w.tabs.count())]


def test_window_extension_adds_tab_and_menu_item(window):
    w, app = window
    assert w.ctx is not None and u"Проба" in tab_titles(w)
    item = [a for a in w.menus[u"Файл"].actions() if a.text() == u"Пункт пробы"]
    assert len(item) == 1 and u"Кнопка пробы" in [a.text() for a in w.toolbar.actions()]
    item[0].trigger()
    assert w.ext.get("good").instance.clicks == 1

    # падающее расширение: пункт в меню «Расширения» → нажали → отключилось, пункт убран
    names = lambda: [a.text() for a in w.menus[u"Расширения"].actions()]  # noqa: E731
    assert w.ext.get("later").state == "active" and names()[0] == u"Управление расширениями…"
    w.menus[u"Расширения"].actions()[-1].trigger()
    app.processEvents()
    assert w.ext.get("later").state == "failed" and u"Упасть" not in names()
    assert u"Проба" in tab_titles(w) and u"отключено из-за ошибки" in w.log_view.toPlainText()
    item = [a for a in w.menus[u"Файл"].actions() if a.text() == u"Пункт пробы"]
    assert len(item) == 1


def test_extensions_dialog_lists_and_switches(window):
    from PyQt5.QtCore import Qt
    from digger.gui.dialogs import ExtensionsDialog
    w, app = window
    dlg = ExtensionsDialog(w.ext, w)
    ids = [x.id for x in w.ext.extensions]
    assert dlg.table.rowCount() == len(ids) and u"работает" in dlg.table.item(ids.index("good"), 4).text()
    dlg.table.item(ids.index("good"), 0).setCheckState(Qt.Unchecked)
    dlg._save()
    w._load_extensions()
    assert w.ext.get("good").state == "disabled" and u"Проба" not in tab_titles(w)
    assert [a for a in w.menus[u"Файл"].actions() if a.text() == u"Пункт пробы"] == [] and u"Своё меню" not in w.menus
