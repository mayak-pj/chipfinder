# -*- coding: utf-8 -*-
"""Переименование программы: данные и настройки под старым именем не теряются (миграция при загрузке)."""
import json
import logging
import os

from digger.core import config as cfgmod


def _app(tmp_path, user=None):
    app = tmp_path / u"прог"
    (app / "data").mkdir(parents=True)
    (app / "config.default.json").write_text(json.dumps({
        "paths": {"db": "data/digger.sqlite", "log_dir": "logs", "library_dir": "data/library"},
        "modules": {"ocr": "digger.recognition.manager:RecognitionManager"}}), encoding="utf-8")
    if user is not None:
        (app / "config.json").write_text(json.dumps(user), encoding="utf-8")
    return str(app)


def test_old_db_file_is_renamed_with_companions(tmp_path):
    app = _app(tmp_path, {"paths": {"db": "data/chipfinder.sqlite"}})
    for suffix in ("", "-wal", "-shm"):
        with open(os.path.join(app, "data", "chipfinder.sqlite" + suffix), "w") as f:
            f.write("данные" + suffix)
    cfg = cfgmod.load_config(app)
    assert cfg["paths"]["db"] == "data/digger.sqlite"
    for suffix in ("", "-wal", "-shm"):
        assert open(os.path.join(app, "data", "digger.sqlite" + suffix)).read() == "данные" + suffix
        assert not os.path.exists(os.path.join(app, "data", "chipfinder.sqlite" + suffix))
    assert cfgmod.load_config(app)["paths"]["db"] == "data/digger.sqlite"      # повторная загрузка безопасна


def test_new_db_is_never_overwritten_by_old(tmp_path):
    app = _app(tmp_path, {"paths": {"db": "data/chipfinder.sqlite"}})
    open(os.path.join(app, "data", "chipfinder.sqlite"), "w").write("старая")
    open(os.path.join(app, "data", "digger.sqlite"), "w").write("новая")
    cfgmod.load_config(app)
    assert open(os.path.join(app, "data", "digger.sqlite")).read() == "новая"
    assert open(os.path.join(app, "data", "chipfinder.sqlite")).read() == "старая"


def test_custom_db_path_is_left_alone(tmp_path):
    app = _app(tmp_path, {"paths": {"db": "\\\\server\\share\\my.sqlite"}})
    assert cfgmod.load_config(app)["paths"]["db"] == "\\\\server\\share\\my.sqlite"


def test_old_module_paths_in_user_config_still_work(tmp_path):
    app = _app(tmp_path, {"modules": {"ocr": "chipfinder.recognition.manager:RecognitionManager",
                                      "report": "my_plugin.report:Mine"}})
    cfg = cfgmod.load_config(app)
    assert cfg["modules"]["ocr"] == "digger.recognition.manager:RecognitionManager"
    assert cfg["modules"]["report"] == "my_plugin.report:Mine"


def test_old_log_is_renamed(tmp_path):
    app = _app(tmp_path)
    logs = os.path.join(app, "logs")
    os.makedirs(logs)
    open(os.path.join(logs, "chipfinder.log"), "w").write("старый журнал")
    open(os.path.join(logs, "chipfinder.log.1"), "w").write("ещё старее")
    logging.getLogger("digger").handlers[:] = []
    try:
        cfgmod.setup_logging(app, cfgmod.load_config(app))
        assert os.path.isfile(os.path.join(logs, "digger.log"))
        assert open(os.path.join(logs, "digger.log.1")).read() == "ещё старее"
        assert not os.path.exists(os.path.join(logs, "chipfinder.log"))
    finally:
        for h in logging.getLogger("digger").handlers[:]:
            h.close()
            logging.getLogger("digger").removeHandler(h)
        for h in logging.getLogger("digger.net").handlers[:]:
            h.close()
            logging.getLogger("digger.net").removeHandler(h)
