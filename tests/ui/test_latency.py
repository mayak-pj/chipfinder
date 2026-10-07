# -*- coding: utf-8 -*-
"""Шаг 7.3a: отзывчивость — пул фоновых задач, объединение событий, задержка потока интерфейса (§5)."""
import threading
import time

import pytest

pytest.importorskip("PyQt5")

EVENTS = 1000
MAX_LATE_MS = 50
TICK_MS = 10


def _pump(app, done, timeout=20.0):
    """Крутит очередь окна, пока `done()` не станет истиной."""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if done():
            break
        time.sleep(0.001)
    app.processEvents()
    assert done(), "не дождались за %s с" % timeout


class _Lateness(object):
    """Таймер в потоке интерфейса: на сколько миллисекунд опоздал самый поздний тик."""

    def __init__(self):
        from PyQt5.QtCore import Qt, QTimer
        self.worst = 0.0
        self.ticks = 0
        self._last = None
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        now = time.perf_counter()
        if self._last is not None:
            self.worst = max(self.worst, (now - self._last) * 1000.0 - TICK_MS)
        self._last = now
        self.ticks += 1

    def start(self):
        self._last = None
        self.timer.start()

    def stop(self):
        self.timer.stop()
        return self.worst


# -------------------- объединение событий --------------------

def test_coalescer_batches_posts_from_a_thread(qapp):
    from chipfinder.gui.worker import Coalescer
    c = Coalescer(interval_ms=33)
    batches = []
    c.flushed.connect(batches.append)

    def work():
        for i in range(EVENTS):
            c.post(i)
            if i % 20 == 19:
                time.sleep(0.004)
    started = time.time()
    t = threading.Thread(target=work)
    t.start()
    _pump(qapp, lambda: sum(len(b) for b in batches) == EVENTS)
    t.join()
    elapsed = time.time() - started
    assert [x for b in batches for x in b] == list(range(EVENTS))          # ничего не потеряно, порядок сохранён
    assert len(batches) <= elapsed * 30 + 2, "обновлений %d за %.2f с" % (len(batches), elapsed)
    assert len(batches) < EVENTS / 10


def test_coalescer_flush_now_and_idle(qapp):
    from chipfinder.gui.worker import Coalescer
    c = Coalescer(interval_ms=33)
    batches = []
    c.flushed.connect(batches.append)
    c.flush()
    assert batches == []                                                    # пусто — обновления нет
    c.post("a")
    c.post("b")
    c.flush()                                                               # из потока окна — сразу
    assert batches == [["a", "b"]]
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.01)
    assert batches == [["a", "b"]]


# -------------------- пул задач --------------------

def test_pool_runs_jobs_in_parallel_and_reports(qapp):
    from chipfinder.gui.worker import Job, TaskPool
    pool = TaskPool()
    gate = threading.Event()
    got, lines, finished = [], [], []

    def slow(progress, cancel):
        progress("жду")
        gate.wait(5)
        return "slow"

    def quick(progress, cancel):
        return threading.current_thread() is not threading.main_thread()

    a, b, c = Job(slow), Job(quick), Job(lambda progress, cancel: 1 / 0)
    a.progress.connect(lines.append)
    for j in (a, b, c):
        j.done.connect(got.append)
        j.finished.connect(lambda j=j: finished.append(j))
    c.failed.connect(lambda msg: got.append(msg.splitlines()[0]))
    for j in (a, b, c):
        pool.start(j)
    assert a.isRunning()
    _pump(qapp, lambda: len(got) == 2)                  # быстрые задачи не ждут медленную
    assert got[0] is True or got[1] is True
    assert "division by zero" in "".join(str(x) for x in got)
    assert a.isRunning() and not b.isRunning() and not a.wait(10)
    gate.set()
    _pump(qapp, lambda: len(finished) == 3)
    assert got[-1] == "slow" and lines == ["жду"] and a.wait(1000) and not a.isRunning()
    assert pool.wait(1000)


def test_job_is_free_inside_its_done_handler(qapp):
    """Обработчик результата может сразу запустить следующую задачу: первая уже не считается идущей."""
    from chipfinder.gui.worker import Job, TaskPool
    pool = TaskPool()
    seen = []
    j = Job(lambda progress, cancel: 7)
    j.done.connect(lambda res: seen.append((res, j.isRunning())))
    pool.start(j)
    _pump(qapp, lambda: seen)
    assert seen == [(7, False)]


# -------------------- окно --------------------

def test_window_stays_responsive_during_1000_events(window):
    """Имитация поиска: 1000 событий шины и 1000 строк хода — таймер окна не опаздывает больше чем на 50 мс."""
    w, app = window
    batches, events = [], []
    w.feed.flushed.connect(lambda items: batches.append(len(items)))
    w.search_events.connect(events.extend)

    def search(progress, cancel):
        for i in range(EVENTS):
            w.ctx.bus.emit("engine.query", lang=("en", "zh", "ru")[i % 3], engine="Bing", query="NE555 %d" % i)
            progress(u"запрос %d: 数据手册 NE555" % i)
            if i % 10 == 9:
                time.sleep(0.003)                        # сеть отвечает не мгновенно
        return "ok"

    done = []
    late = _Lateness()
    late.start()
    started = time.time()
    assert w.start_job(search, done.append)
    assert w.a_stop.isEnabled() and not w.a_run.isEnabled()
    _pump(app, lambda: done and not w.job.isRunning() and len(events) == EVENTS)
    elapsed = time.time() - started
    for _ in range(5):
        app.processEvents()
        time.sleep(TICK_MS / 1000.0)
    worst = late.stop()
    assert late.ticks > 10
    assert worst <= MAX_LATE_MS, "поток интерфейса опоздал на %.1f мс" % worst
    text = w.log_view.toPlainText()
    assert text.count(u"数据手册 NE555") == EVENTS                          # все строки в журнале
    assert text.index(u"запрос 0:") < text.index(u"запрос 999:")
    assert [e.params["query"] for e in events] == ["NE555 %d" % i for i in range(EVENTS)]
    assert len(batches) <= elapsed * 30 + 2, "обновлений окна %d за %.2f с" % (len(batches), elapsed)
    assert u"запрос 999" in w.status.text()
    assert not w.a_stop.isEnabled() and w.a_run.isEnabled()


def test_window_log_keeps_order_and_busy_state(window):
    """Строка окна после строк фона не обгоняет их; новая задача из обработчика результата не гасит «занято»."""
    w, app = window
    gate = threading.Event()
    done = []

    def second(progress, cancel):
        gate.wait(5)
        return 2

    def first_done(_res):
        w.log("первая готова")
        assert w.start_job(second, done.append)          # первая задача уже свободна

    assert w.start_job(lambda progress, cancel: progress("шаг фона"), first_done)
    _pump(app, lambda: u"первая готова" in w.log_view.toPlainText())
    for _ in range(10):                                  # сигнал «закончена» первой задачи уже пришёл
        app.processEvents()
        time.sleep(0.005)
    text = w.log_view.toPlainText()
    assert text.index(u"шаг фона") < text.index(u"первая готова")
    assert w.a_stop.isEnabled() and not w.a_run.isEnabled()
    gate.set()
    _pump(app, lambda: done == [2] and not w.job.isRunning())
    for _ in range(10):
        app.processEvents()
    assert not w.a_stop.isEnabled() and w.a_run.isEnabled()


def test_extension_jobs_use_the_window_pool(window):
    w, app = window
    got = []
    w._ext_run(lambda: threading.current_thread().name, got.append)
    _pump(app, lambda: got)
    assert got[0] != threading.main_thread().name and not w._ext_jobs
    assert w.pool.wait(1000)
