# -*- coding: utf-8 -*-
"""Решение пользователя по документу (ARCHITECTURE §4.5, правило «user»; шаг 7.6a).

«Подтвердить» — `confirm` с `user=True` (жёсткий отказ проверки файла так не обойти), итог пишется в хранилище:
probable переезжает в `confirmed/`, needs_user сохраняется, если файл ещё лежит в карантине.
«Отклонить» — запись получает `rejected` с причиной `user_rejected`; если файл уже был в библиотеке,
он оттуда убирается. Оба действия работают с файлами и базой — вызывать из фона.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from . import confirm as confirm_mod
from .models import AcquisitionRecord
from .store import AcquireStore
from .verify import SourceTrust


def accept(rec: AcquisitionRecord, store: AcquireStore, others: Iterable[AcquisitionRecord] = (),
           trust: Optional[SourceTrust] = None, owners: Iterable[Sequence[str]] = ()) -> AcquisitionRecord:
    """Пользователь подтвердил документ. Без вердикта или после жёсткого отказа запись не меняется."""
    if rec.verdict is None:
        return rec
    rec = confirm_mod.apply(rec, confirm_mod.confirm(rec, others, trust, owners, user=True))
    if rec.verdict.status == "confirmed":
        rec.verdict.reasons = [r for r in rec.verdict.reasons if r not in ("unconfirmed", "user_rejected")]
        store.save(rec)
    return rec


def reject(rec: AcquisitionRecord, store: AcquireStore) -> AcquisitionRecord:
    """Пользователь отклонил документ: запись отклонена, файл убран из библиотеки."""
    if rec.verdict is None:
        return rec
    rec.verdict.status = "rejected"
    rec.verdict.reasons = [r for r in rec.verdict.reasons if r != "user_confirmed"]
    if "user_rejected" not in rec.verdict.reasons:
        rec.verdict.reasons.append("user_rejected")
    if rec.stored_path:
        store.remove(rec.stored_path)
        rec.stored_path = ""
    store.save(rec)
    return rec
