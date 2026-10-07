# -*- coding: utf-8 -*-
"""Согласие пользователя на отправку фото облачному провайдеру (ARCHITECTURE §6.1).

Согласие даётся на одно фото или до закрытия программы; в `config.json` не записывается.
Спрашивает окно (`ask`); пока спрашивать некому (тесты, командная строка) — ответ «нет».
"""
from __future__ import annotations

import threading
from typing import Callable, Optional, Set

ONCE, SESSION, NO = "once", "session", "no"


class CloudConsent:
    def __init__(self) -> None:
        self.ask: Optional[Callable[[str, str], str]] = None     # (id, название) → once | session | no
        self._session: Set[str] = set()
        self._lock = threading.Lock()

    def allowed(self, provider_id: str, title: str) -> bool:
        """Вызывается из фонового потока перед каждой отправкой; может ждать ответа пользователя."""
        with self._lock:
            if provider_id in self._session:
                return True
        if self.ask is None:
            return False
        answer = self.ask(provider_id, title)
        if answer == SESSION:
            with self._lock:
                self._session.add(provider_id)
        return answer in (ONCE, SESSION)

    def revoke(self) -> None:
        with self._lock:
            self._session.clear()
