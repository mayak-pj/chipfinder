# -*- coding: utf-8 -*-
from digger.acquire.models import Lead
from digger.acquire.rank import dedupe, normalize_url, rank, score_lead

LEVELS = ["catalog", "maker", "search", "china", "forum", "github"]

NORMALIZE = [
    ("HTTP://Example.COM/a.pdf", "https://example.com/a.pdf"),
    ("https://example.com/a.pdf#page=3", "https://example.com/a.pdf"),
    ("https://example.com/a.pdf?utm_source=x&utm_medium=y", "https://example.com/a.pdf"),
    ("https://example.com/a?id=5&utm_campaign=z&fbclid=1", "https://example.com/a?id=5"),
    ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
    ("https://www.example.com/a/", "https://example.com/a"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com/a?gclid=1", "https://example.com/a"),
    ("  https://example.com/a  ", "https://example.com/a"),
]


def test_normalize_url():
    for raw, want in NORMALIZE:
        assert normalize_url(raw) == want, raw


def test_normalize_keeps_non_ascii_and_garbage():
    assert normalize_url("") == ""
    assert normalize_url("не url") == "не url"
    assert "datasheet" in normalize_url("https://example.com/даташит/datasheet.pdf")


def L(url, **kw):
    kw.setdefault("kind", "pdf" if url.endswith(".pdf") else "page")
    return Lead(url=url, **kw)


def test_dedupe_merges_and_keeps_best_fields():
    a = L("https://www.x.com/a.pdf?utm_source=1", title="", source_id="ddg", level="search")
    b = L("https://x.com/a.pdf", title="STM32F103 datasheet", snippet="medium density", source_id="alldatasheet",
          level="catalog")
    out = dedupe([a, b], LEVELS)
    assert len(out) == 1
    assert out[0].url == "https://x.com/a.pdf"
    assert out[0].title == "STM32F103 datasheet"
    assert out[0].snippet == "medium density"
    assert out[0].level == "catalog"          # уровень выше (ближе к началу) побеждает


def test_dedupe_keeps_order_of_first_seen():
    leads = [L("https://a.com/1.pdf"), L("https://b.com/2.pdf"), L("https://a.com/1.pdf#x")]
    assert [x.url for x in dedupe(leads)] == ["https://a.com/1.pdf", "https://b.com/2.pdf"]


def test_pdf_beats_page():
    pdf = L("https://x.com/d.pdf", level="search")
    page = L("https://x.com/d", level="search")
    assert score_lead(pdf, "LM358", LEVELS) > score_lead(page, "LM358", LEVELS)


def test_part_in_url_title_raises_score():
    a = L("https://x.com/lm358.pdf", title="LM358 datasheet", level="search")
    b = L("https://x.com/other.pdf", title="catalog", level="search")
    assert score_lead(a, "LM358DR", LEVELS, base_part="LM358") > score_lead(b, "LM358DR", LEVELS, base_part="LM358")


def test_maker_domain_bonus():
    a = L("https://www.st.com/resource/en/datasheet/stm32f103c8.pdf", level="maker")
    b = L("https://random.io/stm32f103c8.pdf", level="maker")
    assert score_lead(a, "STM32F103C8T6", LEVELS, maker_domains=["st.com"]) > \
        score_lead(b, "STM32F103C8T6", LEVELS, maker_domains=["st.com"])


def test_level_order_matters():
    a = L("https://a.com/x.pdf", level="catalog")
    b = L("https://b.com/x.pdf", level="github")
    assert score_lead(a, "X", LEVELS) > score_lead(b, "X", LEVELS)


def test_rank_table():
    leads = [
        L("https://forum.eevblog.com/t/1", level="forum", title="LM358 question"),
        L("https://github.com/u/r/blob/main/lm358.pdf", level="github", title="lm358.pdf"),
        L("https://www.ti.com/lit/ds/symlink/lm358.pdf?utm_source=g", level="maker", title="LM358 datasheet"),
        L("https://random.io/blog/opamps", level="search", title="op amps"),
        L("https://www.alldatasheet.com/datasheet-pdf/lm358.pdf", level="catalog", title="LM358 pdf"),
        L("https://ti.com/lit/ds/symlink/lm358.pdf", level="search", title="LM358"),     # дубль
        L("https://random.io/blog/opamps#top", level="search"),                         # дубль
    ]
    out = rank(leads, "LM358DR", LEVELS, maker_domains=["ti.com"], base_part="LM358")
    urls = [x.url for x in out]
    assert len(out) == 5
    assert urls[0] == "https://ti.com/lit/ds/symlink/lm358.pdf"
    assert urls.index("https://alldatasheet.com/datasheet-pdf/lm358.pdf") == 1
    assert urls[-1] in ("https://forum.eevblog.com/t/1", "https://random.io/blog/opamps")
    scores = [x.rank_score for x in out]
    assert scores == sorted(scores, reverse=True)


def test_rank_is_stable_and_pure():
    leads = [L("https://a.com/1.pdf", level="search"), L("https://b.com/2.pdf", level="search")]
    first = [x.url for x in rank(leads, "X", LEVELS)]
    second = [x.url for x in rank(leads, "X", LEVELS)]
    assert first == second == ["https://a.com/1.pdf", "https://b.com/2.pdf"]
    assert leads[0].rank_score == 0.0       # исходные объекты не изменены


def test_unknown_level_goes_last():
    a = L("https://a.com/x.pdf", level="weird")
    b = L("https://b.com/x.pdf", level="github")
    assert score_lead(b, "X", LEVELS) > score_lead(a, "X", LEVELS)
