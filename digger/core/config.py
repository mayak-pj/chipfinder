# -*- coding: utf-8 -*-
"""Загрузка настроек, путей и журналов."""
from __future__ import annotations

import copy
import io
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any, Dict


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def read_json(path: str) -> Dict[str, Any]:
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# Прежнее имя программы (до переименования): данные и настройки под ним не теряются.
_OLD = "chip" + "finder"
_DB_SUFFIXES = ("", "-wal", "-shm", "-journal")


def _move_if_free(src: str, dst: str) -> None:
    if os.path.isfile(src) and not os.path.exists(dst):
        try:
            os.replace(src, dst)
        except OSError:        # занят другим процессом — останется под старым именем, потеряно не будет
            pass


def migrate_legacy(app_dir: str, cfg: Dict[str, Any]) -> None:
    """База `data/<старое имя>.sqlite` переезжает под новое имя, старые пути модулей в настройках — на новый пакет.
    Свои пути (сетевой диск и т. п.) не трогаются; существующий файл с новым именем не перезаписывается."""
    paths = cfg.get("paths", {})
    old_db = "data/%s.sqlite" % _OLD
    if str(paths.get("db", "")).replace("\\", "/") == old_db:
        new_db = "data/digger.sqlite"
        for suffix in _DB_SUFFIXES:
            _move_if_free(resolve_path(app_dir, old_db) + suffix, resolve_path(app_dir, new_db) + suffix)
        paths["db"] = new_db
    modules = cfg.get("modules", {})
    for role, spec in list(modules.items()):
        if isinstance(spec, str) and spec.startswith(_OLD + "."):
            modules[role] = "digger." + spec[len(_OLD) + 1:]


def load_config(app_dir: str) -> Dict[str, Any]:
    """config.default.json (поставляется с программой) + config.json (ваши изменения)."""
    default = read_json(os.path.join(app_dir, "config.default.json"))
    user_path = os.path.join(app_dir, "config.json")
    user = read_json(user_path) if os.path.exists(user_path) else {}
    cfg = deep_merge(default, user)
    migrate_legacy(app_dir, cfg)
    cfg["_user_config_path"] = user_path
    return cfg


def save_user_config(app_dir: str, user_part: Dict[str, Any]) -> None:
    """Сохраняет только пользовательские изменения (config.json)."""
    path = os.path.join(app_dir, "config.json")
    current = read_json(path) if os.path.exists(path) else {}
    write_json(path, deep_merge(current, user_part))


def resolve_path(app_dir: str, p: str) -> str:
    if not p:
        return p
    p = os.path.expandvars(os.path.expanduser(p))
    if os.path.isabs(p) or p.startswith("\\\\"):
        return p
    return os.path.normpath(os.path.join(app_dir, p))


def setup_logging(app_dir: str, cfg: Dict[str, Any]) -> logging.Logger:
    log_dir = resolve_path(app_dir, cfg["paths"]["log_dir"])
    os.makedirs(log_dir, exist_ok=True)
    for suffix in ("", ".1", ".2", ".3"):                      # журнал под прежним именем программы
        _move_if_free(os.path.join(log_dir, _OLD + ".log" + suffix), os.path.join(log_dir, "digger.log" + suffix))
    logger = logging.getLogger("digger")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(os.path.join(log_dir, "digger.log"),
                             maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Отдельный журнал всех сетевых обращений — его можно показать администраторам.
    net = logging.getLogger("digger.net")
    nh = RotatingFileHandler(os.path.join(log_dir, "network_audit.log"),
                             maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    nh.setFormatter(logging.Formatter("%(asctime)s\t%(message)s"))
    net.addHandler(nh)
    return logger
