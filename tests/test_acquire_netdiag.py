# -*- coding: utf-8 -*-
"""Классификатор неудач обращения к сайтам (шаг 5.5, ARCHITECTURE §4.11)."""
import json
import logging
import os
import socket

import pytest
import requests

from chipfinder.acquire import netdiag
from chipfinder.acquire.netdiag import BlockTracker, classify_exception, classify_response, failure_class
from chipfinder.core.netsafe import HttpStatus, NetBlocked, NotPdf, SafeHttp, TooBig
from tests.fakes.fake_http import FIXTURES, FakeHttp

DIR = os.path.join(FIXTURES, "netdiag")
with open(os.path.join(DIR, "cases.json"), encoding="utf-8") as _f:
    CASES = json.load(_f)


def _body(case):
    with open(os.path.join(DIR, case["file"]), "rb") as f:
        data = f.read()
    if case.get("encoding"):
        data = data.decode("utf-8").encode(case["encoding"])
    return data


@pytest.mark.parametrize("case", CASES, ids=lambda c: "%s_%s" % (os.path.basename(c["file"]), c["status"]))
def test_response_fixture(case):
    d = classify_response(case["status"], case["headers"], _body(case))
    if not case["cls"]:
        assert d is None
    else:
        assert d is not None and (d.cls, d.reason) == (case["cls"], case["reason"]), d


def test_classes_are_known():
    assert set(c["cls"] for c in CASES if c["cls"]) <= set(netdiag.CLASSES)
    assert set(netdiag.CLASSES) == {"site_protected", "network_blocked", "not_whitelisted", "transient", "unknown"}


def test_big_normal_page_with_captcha_word_is_not_a_failure():
    page = '<html><body><div class="g-recaptcha"></div>' + "<p>NE555 timer datasheet</p>" * 3000 + "</body></html>"
    assert classify_response(200, {}, page) is None
    assert classify_response(403, {}, page).reason == "captcha"      # при коде ошибки размер не важен


def test_proxy_answer_is_firm_and_site_answer_too():
    with open(os.path.join(DIR, "proxy_squid_403.html"), "rb") as f:
        d = classify_response(403, {"server": "squid/4.10"}, f.read())
    assert d.cls == "network_blocked" and d.firm and d.signs
    assert classify_response(429).firm


def _pool(text):
    return "HTTPSConnectionPool(host='www.ti.com', port=443): Max retries exceeded with url: /x (Caused by %s)" % text


EXC = [
    # DNS
    (requests.exceptions.ConnectionError(_pool(
        "NameResolutionError(\"<urllib3.connection.HTTPSConnection object at 0x1>: Failed to resolve 'www.ti.com' "
        "([Errno 8] nodename nor servname provided, or not known)\")")), "network_blocked", "dns"),
    (requests.exceptions.ConnectionError(_pool(
        "NewConnectionError('<urllib3.connection.HTTPSConnection object at 0x1>: Failed to establish a new "
        "connection: [Errno 11001] getaddrinfo failed')")), "network_blocked", "dns"),
    (socket.gaierror(-2, "Name or service not known"), "network_blocked", "dns"),
    # сброс
    (requests.exceptions.ConnectionError(
        "('Connection aborted.', ConnectionResetError(10054, 'An existing connection was forcibly closed by the "
        "remote host', None, 10054, None))"), "network_blocked", "reset"),
    (ConnectionResetError(104, "Connection reset by peer"), "network_blocked", "reset"),
    (requests.exceptions.ConnectionError(
        "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"),
     "network_blocked", "reset"),
    (requests.exceptions.SSLError(_pool(
        "SSLError(SSLEOFError(8, 'EOF occurred in violation of protocol (_ssl.c:1131)'))")), "network_blocked", "reset"),
    # соединение не устанавливается
    (requests.exceptions.ConnectionError(_pool(
        "NewConnectionError('<urllib3.connection.HTTPSConnection object at 0x1>: Failed to establish a new "
        "connection: [WinError 10061] No connection could be made because the target machine actively refused "
        "it')")), "network_blocked", "refused"),
    (ConnectionRefusedError(61, "Connection refused"), "network_blocked", "refused"),
    (OSError(101, "Network is unreachable"), "network_blocked", "unreachable"),
    # таймаут до установления соединения
    (requests.exceptions.ConnectTimeout(_pool(
        "ConnectTimeoutError(<urllib3.connection.HTTPSConnection object at 0x1>, 'Connection to www.ti.com timed "
        "out. (connect timeout=15)')")), "network_blocked", "connect_timeout"),
    (requests.exceptions.ConnectionError(_pool(
        "NewConnectionError('<urllib3.connection.HTTPSConnection object at 0x1>: Failed to establish a new "
        "connection: [WinError 10060] A connection attempt failed because the connected party did not properly "
        "respond after a period of time')")), "network_blocked", "connect_timeout"),
    # прокси
    (requests.exceptions.ProxyError(_pool(
        "ProxyError('Cannot connect to proxy.', OSError('Tunnel connection failed: 403 Forbidden'))")),
     "network_blocked", "proxy"),
    (requests.exceptions.ProxyError(_pool(
        "ProxyError('Cannot connect to proxy.', OSError('Tunnel connection failed: 407 Proxy Authentication "
        "Required'))")), "network_blocked", "proxy_auth"),
    (requests.exceptions.ProxyError(_pool(
        "ProxyError('Cannot connect to proxy.', OSError('Tunnel connection failed: 503 Service Unavailable'))")),
     "network_blocked", "proxy"),
    # подмена сертификата
    (requests.exceptions.SSLError(_pool(
        "SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self "
        "signed certificate in certificate chain (_ssl.c:1131)'))")), "network_blocked", "cert"),
    (requests.exceptions.SSLError(_pool(
        "SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable "
        "to get local issuer certificate (_ssl.c:1131)'))")), "network_blocked", "cert"),
    # сертификат самого сайта не в порядке — причина не ясна
    (requests.exceptions.SSLError(_pool(
        "SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "certificate has expired (_ssl.c:1131)'))")), "unknown", "cert_site"),
    # сбой
    (requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='www.ti.com', port=443): Read timed out. (read timeout=15)"),
     "transient", "read_timeout"),
    (socket.timeout("timed out"), "transient", "read_timeout"),
    (requests.exceptions.ChunkedEncodingError(
        "('Connection broken: IncompleteRead(1024 bytes read, 4096 more expected)', IncompleteRead(1024 bytes "
        "read, 4096 more expected))"), "transient", "broken"),
    (requests.exceptions.ChunkedEncodingError(
        "(\"Connection broken: ConnectionResetError(104, 'Connection reset by peer')\", ConnectionResetError(104, "
        "'Connection reset by peer'))"), "transient", "broken"),
    (ValueError("что-то непонятное"), "transient", "error"),
    # правила программы
    (NetBlocked("Домен не в белом списке: evil.example"), "not_whitelisted", "domain"),
    (NetBlocked("Только HTTPS (домен old.example не разрешён для http)"), "not_whitelisted", "http"),
    # код ответа
    (HttpStatus(429), "site_protected", "rate_limit"),
    (HttpStatus(403), "site_protected", "forbidden"),
    (HttpStatus(403, {"server": "squid/4.10"}, b"<html>ERR_ACCESS_DENIED</html>"), "network_blocked", "proxy"),
    (HttpStatus(502), "transient", "http_5xx"),
    (NetBlocked("HTTP 503"), "transient", "http_5xx"),
]


@pytest.mark.parametrize("exc,cls,reason", EXC, ids=lambda v: v if isinstance(v, str) else type(v).__name__)
def test_exception(exc, cls, reason):
    d = classify_exception(exc)
    assert d is not None and (d.cls, d.reason) == (cls, reason), d


@pytest.mark.parametrize("exc", [HttpStatus(404), NotPdf("Это не PDF"), TooBig("Файл слишком большой"),
                                 NetBlocked("Домен в чёрном списке: bad.example"),
                                 NetBlocked("Автономный режим: интернет выключен в настройках")])
def test_not_an_access_failure(exc):
    assert classify_exception(exc) is None and failure_class(exc) == ""


def test_cause_chain_is_read():
    try:
        try:
            raise socket.gaierror(11001, "getaddrinfo failed")
        except OSError as inner:
            raise RuntimeError("не удалось открыть") from inner
    except RuntimeError as e:
        assert classify_exception(e).reason == "dns"


def test_firmness():
    assert not classify_exception(ConnectionResetError(104, "Connection reset by peer")).firm
    assert not classify_exception(socket.gaierror(-2, "Name or service not known")).firm
    assert classify_exception(requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden")).firm
    assert classify_exception(NetBlocked("Домен не в белом списке: a.b")).firm


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


DNS = socket.gaierror(-2, "Name or service not known")


def test_single_network_failure_is_transient_without_tracker():
    assert failure_class(DNS) == "transient"
    assert failure_class(HttpStatus(403)) == "site_protected"
    assert failure_class(NetBlocked("Домен не в белом списке: a.b")) == "not_whitelisted"


def test_two_failures_in_a_row_with_gap():
    clock = Clock()
    tr = BlockTracker(clock=clock)
    assert failure_class(DNS, tr, "ti.com") == "transient"          # первая — может быть разовый сбой
    clock.t += 20
    assert failure_class(DNS, tr, "ti.com") == "transient"          # вторая слишком скоро
    assert not tr.is_blocked("ti.com")
    clock.t += 45                                                   # 65 с после первой
    assert failure_class(ConnectionResetError(104, "Connection reset by peer"), tr, "ti.com") == "network_blocked"
    assert tr.is_blocked("ti.com")
    clock.t += 1
    assert failure_class(DNS, tr, "ti.com") == "network_blocked"    # дальше уже без ожидания
    assert failure_class(DNS, tr, "st.com") == "transient"          # у каждого домена свой счёт


def test_success_between_failures_resets():
    clock = Clock()
    tr = BlockTracker(clock=clock)
    tr.settle("ti.com", classify_exception(DNS))
    clock.t += 90
    tr.ok("ti.com")
    assert failure_class(DNS, tr, "ti.com") == "transient"
    clock.t += 90
    assert failure_class(DNS, tr, "ti.com") == "network_blocked"
    tr.ok("TI.com")
    assert not tr.is_blocked("ti.com")


def test_site_answer_resets_and_glitch_does_not():
    clock = Clock()
    tr = BlockTracker(clock=clock)
    failure_class(DNS, tr, "ti.com")
    clock.t += 90
    assert failure_class(requests.exceptions.ReadTimeout("Read timed out."), tr, "ti.com") == "transient"
    assert failure_class(DNS, tr, "ti.com") == "network_blocked"    # разовый таймаут счёт не сбросил
    tr = BlockTracker(clock=clock)
    failure_class(DNS, tr, "ti.com")
    clock.t += 90
    assert failure_class(HttpStatus(403), tr, "ti.com") == "site_protected"     # сайт ответил — сеть открыта
    assert failure_class(DNS, tr, "ti.com") == "transient"


def test_proxy_block_is_fixed_at_once():
    tr = BlockTracker(clock=Clock())
    exc = HttpStatus(407, {"proxy-authenticate": "NTLM"}, b"")
    assert failure_class(exc, tr, "ti.com") == "network_blocked" and tr.is_blocked("ti.com")


def test_neighbours():
    clock = Clock()
    tr = BlockTracker(clock=clock)
    failure_class(DNS, tr, "ti.com")
    assert not tr.neighbours_ok("ti.com")
    tr.ok("st.com")
    assert tr.neighbours_ok("ti.com") and not tr.neighbours_ok("st.com")
    clock.t += 3600
    assert not tr.neighbours_ok("ti.com")                           # давний успех соседа ничего не говорит


def test_netsafe_keeps_headers_and_body_of_error_answer(tmp_path):
    with open(os.path.join(DIR, "proxy_squid_403.html"), "rb") as f:
        page = f.read()
    fake = FakeHttp().add("https://ti.com/p", page, status=403)
    fake.routes["https://ti.com/p"].headers["Server"] = "squid/4.10"
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["ti.com"])
    for call in (http.get_html, http.get_json, http.download_pdf):
        with pytest.raises(HttpStatus) as err:
            call("https://ti.com/p")
        assert str(err.value) == "HTTP 403" and err.value.status == 403
        d = classify_exception(err.value)
        assert (d.cls, d.reason) == ("network_blocked", "proxy")


# ---------- подключение к скачиванию и обходу ----------
def _http(tmp_path, fake):
    return SafeHttp({"allowed_domains": ["ti.com"], "min_interval_sec": 0}, str(tmp_path / "q"),
                    logging.getLogger("t"), transport=fake)


def test_fetch_and_crawl_use_tracker(tmp_path):
    from chipfinder.acquire.crawl import crawl
    from chipfinder.acquire.events import EventBus
    from chipfinder.acquire.fetch import fetch_to_quarantine
    from chipfinder.acquire.models import Lead

    clock = Clock()
    tr = BlockTracker(clock=clock)
    pdf, page = "https://www.ti.com/a.pdf", "https://www.ti.com/page"
    fake = FakeHttp().add_error(pdf, ConnectionResetError(104, "Connection reset by peer")).add_error(page, DNS)
    http = _http(tmp_path, fake)

    res = fetch_to_quarantine(http, Lead(url=pdf), attempts=2, sleep=lambda s: None, tracker=tr)
    assert res.failure_class == "transient"
    clock.t += 61
    bus = EventBus()
    assert crawl(http, Lead(url=page), bus=bus, tracker=tr) == []
    assert [e.key for e in bus.history()] == ["access.network_blocked"]
    res = fetch_to_quarantine(http, Lead(url=pdf), attempts=1, tracker=tr)
    assert res.failure_class == "network_blocked"

    fake.add(pdf, b"%PDF-1.4\n%%EOF\n", content_type="application/pdf")      # доступ открыли
    assert fetch_to_quarantine(http, Lead(url=pdf), tracker=tr).ok and not tr.is_blocked("www.ti.com")


def test_crawl_reports_proxy_page_at_once(tmp_path):
    from chipfinder.acquire.crawl import crawl
    from chipfinder.acquire.events import EventBus
    from chipfinder.acquire.models import Lead

    page = "https://www.ti.com/page"
    fake = FakeHttp().add_fixture(page, "netdiag/proxy_squid_403.html", status=403)
    bus = EventBus()
    crawl(_http(tmp_path, fake), Lead(url=page), bus=bus)
    assert [e.key for e in bus.history()] == ["access.network_blocked"]
