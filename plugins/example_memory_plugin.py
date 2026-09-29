# -*- coding: utf-8 -*-
"""ПРИМЕР своего модуля. Как подключить:
в config.json напишите
    {"modules": {"memory": "example_memory_plugin:MyMemoryAnalyzer"}}
и перезапустите программу. Папка plugins/ уже подключена к поиску модулей.

Любой модуль можно заменить так же: достаточно наследовать нужный интерфейс
из chipfinder/core/interfaces.py и вернуть те же структуры данных (models.py).
"""
from chipfinder.modules.memory_rules import RuleMemoryAnalyzer
from chipfinder.core.models import MemoryItem


class MyMemoryAnalyzer(RuleMemoryAnalyzer):
    """Стандартный анализ + своё правило: чипы с «ABC» в названии считаем с EEPROM."""

    def analyze(self, part, datasheet_text, description=""):
        v = super().analyze(part, datasheet_text, description)
        if "ABC" in (part or "").upper():
            v.items.append(MemoryItem(kind="EEPROM", evidence="правило из плагина"))
            v.has_memory = True
            v.summary = "ЕСТЬ ПАМЯТЬ (правило плагина). " + v.summary
        return v
