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


def load_config(app_dir: str) -> Dict[str, Any]:
    """config.default.json (поставляется с программой) + config.json (ваши изменения)."""
    default = read_json(os.path.join(app_dir, "config.default.json"))
    user_path = os.path.join(app_dir, "config.json")
    user = read_json(user_path) if os.path.exists(user_path) else {}
    cfg = deep_merge(default, user)
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
    logger = logging.getLogger("chipfinder")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(os.path.join(log_dir, "chipfinder.log"),
                             maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Отдельный журнал всех сетевых обращений — его можно показать администраторам.
    net = logging.getLogger("chipfinder.net")
    nh = RotatingFileHandler(os.path.join(log_dir, "network_audit.log"),
                             maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    nh.setFormatter(logging.Formatter("%(asctime)s\t%(message)s"))
    net.addHandler(nh)
    return logger
