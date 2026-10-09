# -*- coding: utf-8 -*-
"""ПРИМЕР расширения. Скопируйте папку под другим именем, поменяйте id в extension.json — и пишите своё.

Включается в окне «Расширения». Всё, что доступно расширению, описано в digger/extensions/api.py.
"""
from digger.extensions.api import Extension as BaseExtension


class Extension(BaseExtension):
    def setup(self, services):
        self.services = services
        self.label = None
        self.db = services.db()
        self.db.migrate(["CREATE TABLE {prefix}seen (part TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0)"])
        services.subscribe(self.on_event)

    def on_event(self, event):
        # вызывается в потоке, где произошло событие: только быстрые действия, окно не трогаем
        if event.key == "photo.recognized":
            part = str(event.params.get("part", "?"))
            self.db.execute("INSERT OR IGNORE INTO {prefix}seen (part) VALUES (?)", (part,))
            self.db.execute("UPDATE {prefix}seen SET n = n + 1 WHERE part = ?", (part,))

    def text(self):
        rows = self.db.execute("SELECT part, n FROM {prefix}seen ORDER BY n DESC, part LIMIT 20")
        if not rows:
            return "Распознанных фото пока нет. Распознайте фото и нажмите «Расширения → Пример: обновить»."
        return "Распознано с этим расширением:\n" + "\n".join("%s — %d раз(а)" % r for r in rows)

    def contribute(self, ui):
        ui.add_tab("Пример", self.make_tab)
        ui.add_menu_item("Расширения", "Пример: обновить", self.refresh)
        ui.add_csv_column("Пример: сколько раз", self.count)

    def make_tab(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel
        self.label = QLabel(self.text())
        self.label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.label.setMargin(12)
        return self.label

    def refresh(self):
        if self.label is not None:
            self.label.setText(self.text())

    def count(self, report):
        rows = self.db.execute("SELECT n FROM {prefix}seen WHERE part = ?", (report.chosen_part or "?",))
        return rows[0][0] if rows else 0
