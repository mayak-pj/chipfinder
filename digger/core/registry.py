# -*- coding: utf-8 -*-
"""Загрузка модулей по именам из config.json.

Папка plugins/ рядом с программой добавляется в sys.path, поэтому свои
модули можно класть туда и указывать как "my_ocr:MyOCR".
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Dict

from .interfaces import INTERFACES, Context, Module


class ModuleLoadError(Exception):
    pass


def load_class(spec: str):
    if ":" not in spec:
        raise ModuleLoadError("Неверное имя модуля '%s' (нужно 'пакет.модуль:Класс')" % spec)
    mod_name, cls_name = spec.split(":", 1)
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa
        raise ModuleLoadError("Не удалось импортировать %s: %s" % (mod_name, e))
    cls = getattr(mod, cls_name, None)
    if cls is None:
        raise ModuleLoadError("В %s нет класса %s" % (mod_name, cls_name))
    return cls


def load_modules(ctx: Context) -> Dict[str, Module]:
    plugins = os.path.join(ctx.app_dir, "plugins")
    if os.path.isdir(plugins) and plugins not in sys.path:
        sys.path.insert(0, plugins)

    specs = ctx.config.get("modules", {})
    settings = ctx.config.get("module_settings", {})
    errors = []
    for role, iface in INTERFACES.items():
        spec = specs.get(role)
        if not spec:
            errors.append("Не задан модуль для роли '%s'" % role)
            continue
        try:
            cls = load_class(spec)
            if not issubclass(cls, iface):
                raise ModuleLoadError("%s не наследует %s" % (spec, iface.__name__))
            ctx.modules[role] = cls(settings.get(role, {}), ctx)
            ctx.log.info("Модуль %-10s -> %s", role, spec)
        except Exception as e:  # noqa
            errors.append("%s: %s" % (role, e))
            ctx.log.exception("Ошибка загрузки модуля %s", role)
    if errors:
        raise ModuleLoadError("\n".join(errors))
    return ctx.modules
