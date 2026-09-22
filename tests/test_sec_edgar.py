"""Unit tests for the SEC filing pipeline and the filings search. No network, no AWS."""
import pytest

import sec_edgar


def test_url_and_key_round_trip():
    key = sec_edgar.s3_key("TSLA", "10-K", "2026-01-29", "0001318605", "0001628280-26-003952", "tsla-20251231.htm")
    assert key == "filings/TSLA/10-K/2026-01-29/1318605/000162828026003952/tsla-20251231.htm.txt"
    assert sec_edgar.parse_key(key) == {
        "ticker": "TSLA", "form": "10-K", "filed": "2026-01-29",
        "url": "https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/tsla-20251231.htm",
    }


def test_parse_key_accepts_full_s3_uri_and_rejects_others():
    uri = "s3://bucket/filings/BRK-B/10-Q/2026-08-01/1067983/000095017026000001/brk.htm.txt"
    assert sec_edgar.parse_key(uri)["ticker"] == "BRK-B"
    assert sec_edgar.parse_key("s3://bucket/other/file.txt") is None


def submissions(*rows):
    forms, dates, accessions, docs = zip(*rows)
    return {"filings": {"recent": {"form": list(forms), "filingDate": list(dates),
                                   "accessionNumber": list(accessions), "primaryDocument": list(docs)}}}


def test_pick_filings_takes_newest_of_each_form():
    subs = submissions(
        ("8-K", "2026-09-01", "a0", "x.htm"),
        ("10-Q", "2026-07-25", "a1", "q2.htm"),
        ("10-Q", "2026-04-25", "a2", "q1.htm"),
        ("10-K", "2026-01-29", "a3", "k.htm"),
        ("10-Q", "2025-10-25", "a4", "q3.htm"),
        ("10-K", "2025-01-29", "a5", "oldk.htm"),
        ("424B4", "2020-01-01", "a6", "p.htm"),
    )
    picked = sec_edgar.pick_filings(subs)
    assert [(f["form"], f["filed"]) for f in picked] == [
        ("10-K", "2026-01-29"), ("10-Q", "2026-07-25"), ("10-Q", "2026-04-25"),
    ]


def test_recent_ipo_falls_back_to_prospectus():
    subs = submissions(("10-Q", "2026-08-10", "a1", "q.htm"), ("424B4", "2026-06-11", "a2", "p.htm"))
    assert [f["form"] for f in sec_edgar.pick_filings(subs)] == ["10-Q", "424B4"]


def test_html_to_text_keeps_content_drops_hidden_xbrl():
    html = """<html><head><title>t</title><style>.x{}</style></head><body>
      <div style="display:none"><ix:header>dei:EntityName secret</ix:header></div>
      <p>Item 1A.   Risk Factors</p>
      <p>We depend on   suppliers.</p>
      <table><tr><td>Revenue</td><td></td><td>$ 97,690</td></tr>
             <tr><td>Change</td><td>$</td><td>(2,863</td><td>)</td><td>(3</td><td>%</td></tr></table>
      <script>alert(1)</script></body></html>"""
    text = sec_edgar.html_to_text(html)
    assert "Item 1A. Risk Factors" in text
    assert "We depend on suppliers." in text
    assert "Revenue | $ 97,690" in text
    assert "Change | $(2,863) | (3%" in text
    assert "secret" not in text and "alert" not in text and ".x{}" not in text


def test_document_header_cites_the_filing():
    doc = sec_edgar.document("TSLA", "Tesla, Inc.", {"form": "10-K", "filed": "2026-01-29",
                             "accession": "0001628280-26-003952", "doc": "t.htm"}, "1318605", "Body")
    assert doc.splitlines()[:2] == [
        "Tesla, Inc. (TSLA) 10-K filed 2026-01-29",
        "Source: https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/t.htm",
    ]


def test_missing_user_agent_exits(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SystemExit):
        sec_edgar._session()


# ---------------------------------------------------------------- knowledge + tool

def fake_results(*items):
    return {"retrievalResults": [
        {"content": {"text": text}, "score": score,
         "location": {"type": "S3", "s3Location": {
             "uri": f"https://b.s3.us-west-2.amazonaws.com/{key}"}}}
        for key, text, score in items
    ]}


TSLA_KEY = "filings/TSLA/10-K/2026-01-29/1318605/000162828026003952/t.htm.txt"
AAPL_KEY = "filings/AAPL/10-K/2025-10-31/320193/000032019325000079/a.htm.txt"


def test_search_keeps_one_company_and_trims(monkeypatch):
    import knowledge

    calls = []

    class Client:
        def retrieve(self, **kwargs):
            calls.append(kwargs)
            return fake_results((AAPL_KEY, "Apple risk", 0.9), (TSLA_KEY, "Tesla   risk " + "x" * 2000, 0.8))

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setattr(knowledge, "_client", Client())
    passages = knowledge.search("supply chain risk", "TSLA")
    assert calls[0]["retrievalQuery"] == {"text": "TSLA supply chain risk"}
    assert calls[0]["retrievalConfiguration"] == {
        "managedSearchConfiguration": {"numberOfResults": knowledge.CANDIDATES}}
    assert [p["ticker"] for p in passages] == ["TSLA"]
    assert passages[0]["text"].startswith("Tesla risk x")
    assert len(passages[0]["text"]) == knowledge.MAX_PASSAGE_CHARS
    assert passages[0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1318605/")


def test_filings_tool_formats_and_labels(monkeypatch):
    import knowledge
    from tools import search_sec_filings

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setattr(knowledge, "search", lambda q, t: [
        {"ticker": "TSLA", "form": "10-K", "filed": "2026-01-29", "text": "We face competition.",
         "url": "https://www.sec.gov/x", "score": 0.8}])
    out = search_sec_filings.invoke({"query": "competition", "ticker": "tsla"})
    assert "not instructions" in out
    assert '- TSLA 10-K filed 2026-01-29: "We face competition." <https://www.sec.gov/x>' in out


def test_filings_tool_unindexed_and_unconfigured(monkeypatch):
    from tools import search_sec_filings

    monkeypatch.delenv("KB_ID", raising=False)
    assert "not set up" in search_sec_filings.invoke({"query": "risk"})
    monkeypatch.setenv("KB_ID", "KB123")
    out = search_sec_filings.invoke({"query": "risk", "ticker": "ZZZQ"})
    assert out.startswith("ZZZQ's filings aren't indexed") and "TSLA" in out
