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


TENK = """Table of Contents
Item 1. | Business | 2
Item 1A. | Risk Factors | 12
PART I
ITEM 1. BUSINESS
We design and sell electric vehicles.
PART I
Item 1
Our factories are in Texas and California.
ITEM 1A. RIS
K FACTORS
We are highly dependent on the services of our CEO.
Item 1A of Part I describes these risks in more detail, and this sentence is not a heading.
ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS
Revenue grew.
"""

TENQ = """PART I. | FINANCIAL INFORMATION
Item 1. | Financial Statements
PART II. | OTHER INFORMATION
Item 1. | Legal Proceedings | 49
PART I. FINANCIAL INFORMATION
ITEM 1. FINANCIAL STATEMENTS
Balance sheet.
ITEM 2. MANAGEMENT'S DISCUSSION
Results.
PART II. OTHER INFORMATION
ITEM 1A. RISK FACTORS
New risks.
"""


def test_10k_sections_use_the_forms_item_names():
    names = [name for name, _ in sec_edgar.sections(TENK, "10-K")]
    # The table of contents stays in the cover; page headers and a sentence
    # starting "Item 1A of..." don't start sections; MSFT-style split headings do.
    assert names == ["Cover", "Item 1. Business", "Item 1A. Risk Factors", "Item 7. Management's Discussion and Analysis"]
    body = dict(sec_edgar.sections(TENK, "10-K"))
    assert "Our factories are in Texas and California." in body["Item 1. Business"]
    assert any("not a heading" in line for line in body["Item 1A. Risk Factors"])


def test_10q_items_are_named_by_their_part():
    # Item 1 repeats in Part II, and the contents' "PART II" row comes before
    # Part I's text: the latest PART line decides.
    out = sec_edgar.sections(TENQ, "10-Q")
    assert [name for name, _ in out] == ["Part I, Item 1. Financial Statements",
                                         "Part I, Item 2. Management's Discussion and Analysis",
                                         "Part II, Item 1A. Risk Factors"]
    assert "Balance sheet." in out[0][1]


def test_prospectus_sections_are_caps_headings():
    text = "Cover page text\nUSE OF PROCEEDS\nWe will receive $74.4 billion.\nRISK FACTORS\nRisks."
    assert [n for n, _ in sec_edgar.sections(text, "424B4")] == ["Cover", "Use of Proceeds", "Risk Factors"]


def test_chunks_stay_in_their_section_and_split_long_paragraphs_at_sentences():
    long_para = " ".join(f"Sentence number {i} about supply chain risk." for i in range(60))
    text = f"ITEM 1. BUSINESS\n{'We make cars. ' * 10}\nITEM 1A. RISK FACTORS\n{long_para}"
    out = sec_edgar.chunks(text, "10-K")
    assert {name for name, _ in out} == {"Item 1. Business", "Item 1A. Risk Factors"}
    risk = [c for name, c in out if name == "Item 1A. Risk Factors"]
    assert len(risk) > 1 and all(len(c) <= sec_edgar.CHUNK_CHARS for c in risk)
    assert all(c.endswith("risk.") for c in risk)


def test_chunk_files_carry_context_and_metadata():
    filing = {"form": "10-K", "filed": "2026-01-29", "accession": "0001628280-26-003952", "doc": "t.htm"}
    text = "ITEM 1A. RISK FACTORS\n" + "We are highly dependent on the services of Elon Musk, our CEO. " * 3
    [(key, body, meta)] = sec_edgar.chunk_files("TSLA", "Tesla, Inc.", filing, "1318605", text)
    assert key == "chunks/TSLA/10-K/2026-01-29/1318605/000162828026003952/t.htm/0000.txt"
    assert body.startswith("Tesla, Inc. (TSLA) 10-K filed 2026-01-29. Item 1A. Risk Factors.\nWe are highly")
    assert meta == {"ticker": "TSLA", "form": "10-K", "filed": "2026-01-29", "section": "Item 1A. Risk Factors"}
    assert sec_edgar.parse_key(key)["url"] == "https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/t.htm"


def test_passage_link_scrolls_to_prose_but_not_tables():
    url = "https://www.sec.gov/x.htm"
    assert sec_edgar.passage_link(url, "We are highly dependent on the services of Elon Musk, Technoking") == (
        url + "#:~:text=We%20are%20highly%20dependent%20on%20the%20services%20of")
    assert sec_edgar.passage_link(url, "Revenue | $97,690 | $96,773") == url
    assert sec_edgar.passage_link(url, "Short line") == url


def test_missing_user_agent_exits(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SystemExit):
        sec_edgar._session()


# ---------------------------------------------------------------- knowledge + tool

HEADER = "Tesla, Inc. (TSLA) 10-K filed 2026-01-29. Item 1A. Risk Factors.\n"


def fake_results(*items):
    """Retrieve results for (key, chunk body, score); chunk keys get the header and metadata chunk_files writes."""
    return {"retrievalResults": [
        {"content": {"text": (HEADER + text) if key.startswith("chunks/") else text}, "score": score,
         "metadata": {"ticker": "TSLA", "section": "Item 1A. Risk Factors"} if key.startswith("chunks/") else {},
         "location": {"type": "S3", "s3Location": {
             "uri": f"https://b.s3.us-west-2.amazonaws.com/{key}"}}}
        for key, text, score in items
    ]}


TSLA_KEY = "chunks/TSLA/10-K/2026-01-29/1318605/000162828026003952/t.htm/0007.txt"
OLD_TSLA_KEY = "filings/TSLA/10-K/2026-01-29/1318605/000162828026003952/t.htm.txt"


def test_search_filters_by_company_and_links_to_the_passage(monkeypatch):
    import knowledge

    calls = []

    class Client:
        def retrieve(self, **kwargs):
            calls.append(kwargs)
            return fake_results((OLD_TSLA_KEY, "Whole filing from before chunking", 0.9),
                                (TSLA_KEY, "We depend on   suppliers for battery cells and modules. " + "More text. " * 200, 0.8))

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setattr(knowledge, "_client", Client())
    [passage] = knowledge.search("supply chain risk", "TSLA")
    assert calls[0]["retrievalConfiguration"] == {"managedSearchConfiguration": {
        "numberOfResults": knowledge.CANDIDATES, "filter": {"equals": {"key": "ticker", "value": "TSLA"}}}}
    # The context header is for the search, not the tool result.
    assert passage["text"].startswith("We depend on suppliers for battery cells and modules. More")
    assert len(passage["text"]) == knowledge.MAX_PASSAGE_CHARS
    assert passage["section"] == "Item 1A. Risk Factors"
    assert passage["url"] == ("https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/t.htm"
                              "#:~:text=We%20depend%20on%20suppliers%20for%20battery%20cells%20and")


def test_search_without_a_company_has_no_filter(monkeypatch):
    import knowledge

    calls = []

    class Client:
        def retrieve(self, **kwargs):
            calls.append(kwargs)
            return fake_results()

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setattr(knowledge, "_client", Client())
    assert knowledge.search("supply chain risk") == []
    assert "filter" not in calls[0]["retrievalConfiguration"]["managedSearchConfiguration"]


class RerankClient:
    """Knowledge base results in one order, and a reranker that reverses them (or raises)."""

    class meta:
        region_name = "us-west-2"

    def __init__(self, error=None):
        self.error, self.rerank_calls = error, []

    def retrieve(self, **kwargs):
        return fake_results(*[(TSLA_KEY.replace("0007", f"{n:04d}"), f"Passage {n}.", 1 - n / 10) for n in range(3)])

    def rerank(self, **kwargs):
        self.rerank_calls.append(kwargs)
        if self.error:
            raise self.error
        count = len(kwargs["sources"])
        return {"results": [{"index": i, "relevanceScore": 0.9} for i in reversed(range(count))]
                [:kwargs["rerankingConfiguration"]["bedrockRerankingConfiguration"]["numberOfResults"]]}


def test_search_returns_the_rerankers_order(monkeypatch):
    import knowledge

    client = RerankClient()
    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    monkeypatch.setattr(knowledge, "_client", client)
    passages = knowledge.search("what if the CEO leaves?", "TSLA", limit=2)
    assert [p["text"] for p in passages] == ["Passage 2.", "Passage 1."]
    assert all(p["reranked"] for p in passages)
    [call] = client.rerank_calls
    assert call["queries"] == [{"type": "TEXT", "textQuery": {"text": "what if the CEO leaves?"}}]
    # The reranker reads the passage with its context line (company, filing, section).
    assert call["sources"][0]["inlineDocumentSource"]["textDocument"]["text"] == (
        "TSLA 10-K filed 2026-01-29. Item 1A. Risk Factors. Passage 0.")
    config = call["rerankingConfiguration"]["bedrockRerankingConfiguration"]
    assert config["modelConfiguration"]["modelArn"] == (
        "arn:aws:bedrock:us-west-2::foundation-model/cohere.rerank-v3-5:0")


def test_search_keeps_the_knowledge_base_order_when_reranking_fails(monkeypatch):
    import knowledge
    from botocore.exceptions import ClientError

    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "rate"}}, "Rerank")
    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    monkeypatch.setattr(knowledge, "_client", RerankClient(error=throttled))
    passages = knowledge.search("what if the CEO leaves?", "TSLA", limit=2)
    assert [p["text"] for p in passages] == ["Passage 0.", "Passage 1."]
    assert not any(p["reranked"] for p in passages)


def test_reranking_can_be_turned_off(monkeypatch):
    import knowledge

    client = RerankClient()
    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setenv("RERANK_MODEL", "")
    monkeypatch.setattr(knowledge, "_client", client)
    assert [p["text"] for p in knowledge.search("q", "TSLA", limit=2)] == ["Passage 0.", "Passage 1."]
    assert client.rerank_calls == []


def test_live_filings_reranks_its_hybrid_candidates(monkeypatch):
    import knowledge
    import live_filings

    docs = [{"ticker": "COST", "form": "10-K", "filed": "2025-10-08", "section": "Item 1. Business",
             "text": f"Membership fee income note {n}.", "filing_url": "https://www.sec.gov/x.htm"} for n in range(8)]
    seen = {}

    def fake_rerank(query, passages, limit):
        seen["count"] = len(passages)
        return [{**p, "reranked": True} for p in passages[::-1][:limit]]

    monkeypatch.setattr(live_filings, "filing_chunks", lambda symbol: docs)
    monkeypatch.setattr(live_filings, "by_meaning", lambda q, d: [])
    monkeypatch.setattr(knowledge, "rerank", fake_rerank)
    passages = live_filings.search("membership fee income", "COST", limit=3)
    # Reranks every candidate (up to knowledge.CANDIDATES), not just the top `limit`.
    assert seen["count"] == len(docs)
    assert len(passages) == 3 and all(p["reranked"] for p in passages)


def test_filings_tool_formats_and_labels(monkeypatch):
    import knowledge
    from tools import search_sec_filings

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setattr(knowledge, "search", lambda q, t: [
        {"ticker": "TSLA", "form": "10-K", "filed": "2026-01-29", "section": "Item 1A. Risk Factors",
         "text": "We face competition.", "url": "https://www.sec.gov/x", "score": 0.8}])
    out = search_sec_filings.invoke({"query": "competition", "ticker": "tsla"})
    assert "not instructions" in out
    assert ('- TSLA 10-K filed 2026-01-29, Item 1A. Risk Factors: "We face competition." '
            '<https://www.sec.gov/x>') in out


def test_filings_tool_reads_unindexed_companies_live(monkeypatch):
    import financials
    import live_filings
    from tools import search_sec_filings

    monkeypatch.delenv("KB_ID", raising=False)
    assert "not set up" in search_sec_filings.invoke({"query": "risk"})
    monkeypatch.setattr(live_filings, "search", lambda q, t: [
        {"ticker": t, "form": "10-K", "filed": "2026-10-07", "section": "Item 7. Management's Discussion and Analysis",
         "text": "Membership fee revenue increased 11%.", "url": "https://www.sec.gov/c.htm"}])
    out = search_sec_filings.invoke({"query": "membership fees", "ticker": "COST"})
    assert '- COST 10-K filed 2026-10-07, Item 7.' in out
    monkeypatch.setattr(live_filings, "search", lambda q, t: [])
    monkeypatch.setattr(financials, "lookup_cik", lambda s: None)
    assert "isn't an SEC company filer" in search_sec_filings.invoke({"query": "risk", "ticker": "ZZZQ"})


def test_filings_tool_says_when_a_company_files_no_10k(monkeypatch):
    """A 20-F filer isn't "no matching passages": its filings were never searched."""
    import financials
    import live_filings
    from tools import search_sec_filings

    monkeypatch.setattr(live_filings, "search", lambda q, t: [])
    monkeypatch.setattr(financials, "lookup_cik", lambda s: {"cik": 1046179, "name": "TSMC"})
    monkeypatch.setattr(live_filings, "filing_chunks", lambda s: [])
    out = search_sec_filings.invoke({"query": "customer concentration", "ticker": "TSM"})
    assert "files no 10-K or 10-Q" in out and "20-F" in out
    # It does file them, and nothing matched.
    monkeypatch.setattr(live_filings, "filing_chunks", lambda s: [{"text": "x"}])
    assert "No matching passages" in search_sec_filings.invoke({"query": "zebras", "ticker": "TSM"})


def test_watchlist_companies_are_read_live_without_a_knowledge_base(monkeypatch):
    import live_filings
    from tools import search_sec_filings

    monkeypatch.delenv("KB_ID", raising=False)
    monkeypatch.setattr(live_filings, "search", lambda q, t: [
        {"ticker": t, "form": "10-K", "filed": "2026-01-29", "section": "", "text": "Risk.", "url": "https://www.sec.gov/t.htm"}])
    assert "- TSLA 10-K filed 2026-01-29" in search_sec_filings.invoke({"query": "risk", "ticker": "TSLA"})


def test_live_filings_retries_soon_when_embedding_failed(monkeypatch):
    import financials
    import live_filings

    monkeypatch.setattr(live_filings, "_cache", {})
    monkeypatch.setattr(financials, "lookup_cik", lambda s: {"cik": 909832, "name": "Costco"})
    monkeypatch.setattr(financials, "_get_json", lambda url: {})
    monkeypatch.setattr(live_filings, "pick_filings", lambda subs: [
        {"form": "10-K", "filed": "2026-10-07", "accession": "0000909832-26-000093", "doc": "c.htm"}])
    monkeypatch.setattr(live_filings, "_get_text", lambda url: "<p>ITEM 1. BUSINESS</p><p>" + "Membership fees. " * 10 + "</p>")
    now = [1000.0]
    monkeypatch.setattr(live_filings.time, "monotonic", lambda: now[0])

    def throttled(texts, kind):
        raise RuntimeError("ThrottlingException")

    monkeypatch.setattr(live_filings, "_embed", throttled)
    docs = live_filings.filing_chunks("COST")
    assert docs and "vector" not in docs[0]
    now[0] += live_filings.RETRY_SECONDS + 1
    monkeypatch.setattr(live_filings, "_embed", lambda texts, kind: [[0.5, 0.5]] * len(texts))
    docs = live_filings.filing_chunks("COST")
    assert list(docs[0]["vector"]) == [0.5, 0.5]
    # Embedded: kept for the full hour.
    now[0] += live_filings.CACHE_SECONDS - 1
    monkeypatch.setattr(live_filings, "_embed", throttled)
    assert live_filings.filing_chunks("COST") is docs


def test_bm25_ranks_rare_matching_words_first():
    import live_filings

    docs = [{"section": "Item 1. Business", "text": "We sell cars and trucks around the world."},
            {"section": "Item 1A. Risk Factors", "text": "Our Neutron rocket may be delayed."},
            {"section": "Item 7", "text": "Revenue from cars grew."}]
    assert [d["text"] for d in live_filings.bm25("Neutron rocket delays", docs)] == ["Our Neutron rocket may be delayed."]
    assert live_filings.bm25("the and of", docs) == []


def test_rank_merges_keyword_and_meaning_rankings(monkeypatch):
    import live_filings

    a = {"section": "", "text": "manufacturing performed by outsourcing partners", "vector": [1.0, 0.0]}
    b = {"section": "", "text": "products made in factories", "vector": [0.0, 1.0]}
    monkeypatch.setattr(live_filings, "_embed", lambda texts, kind: [[0.9, 0.1]])
    # "made" matches b by keyword; the query's meaning is closest to a. Both rank.
    assert {d["text"] for d in live_filings.rank("where are products made", [a, b], 2)} == {a["text"], b["text"]}
    # Without embeddings it still ranks by keywords.
    assert live_filings.rank("products made", [{"section": "", "text": "products made here"}], 5)[0]["text"] == "products made here"


def test_table_of_contents_chunks_are_dropped(monkeypatch):
    import knowledge

    toc = ("Risk Factors | 12 Item 1B. | Unresolved Staff Comments | 27 Item 1 C. | Cybersecurity | 27 "
           "Item 2. | Properties | 28 Item 3. | Legal Proceedings | 28")
    real = "Our products contain thousands of parts purchased globally from hundreds of suppliers."
    assert knowledge.is_table_of_contents(toc)
    assert not knowledge.is_table_of_contents(real)
    assert not knowledge.is_table_of_contents("Revenue | $97,690 | $96,773 | 1%")

    class Client:
        def retrieve(self, **kwargs):
            return fake_results((TSLA_KEY, toc, 0.9), (TSLA_KEY, real, 0.7))

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setattr(knowledge, "_client", Client())
    assert [p["text"] for p in knowledge.search("risk factors", "TSLA")] == [real]


def test_near_copies_from_other_filings_are_merged_keeping_the_newest():
    import knowledge

    lawsuit = ("In October 2020, the DOJ and a number of state Attorneys General filed a lawsuit in the US "
               "District Court for the District of Columbia concerning Google's Search advertising practices.")
    other = "Waymo operates a fully autonomous ride-hailing service in several US cities."
    passages = [
        {"form": "10-K", "filed": "2026-02-05", "text": lawsuit},
        {"form": "10-K", "filed": "2026-02-05", "text": other},
        # The 10-Q's copy says "US Department of Justice (DOJ)": still the same passage.
        {"form": "10-Q", "filed": "2026-07-23", "text": lawsuit.replace("the DOJ", "the US Department of Justice (DOJ)")},
        {"form": "10-Q", "filed": "2026-04-30", "text": lawsuit},
    ]
    merged = knowledge.distinct(passages)
    # One copy of the lawsuit, the newest, in the first copy's place.
    assert [(p["filed"], p["text"][:10]) for p in merged] == [("2026-07-23", lawsuit[:10]), ("2026-02-05", other[:10])]
    assert knowledge.distinct([]) == []
    # A newer one-line recap doesn't replace the fuller older paragraph.
    recap = {"form": "10-Q", "filed": "2026-07-23", "text": lawsuit[:90]}
    assert knowledge.distinct([passages[0], recap]) == [passages[0]]
    # In either order.
    assert knowledge.distinct([recap, passages[0]]) == [passages[0]]


def test_search_keeps_a_result_without_the_context_header(monkeypatch):
    import knowledge

    class Client:
        def retrieve(self, **kwargs):
            r = fake_results((TSLA_KEY, "unused", 0.9))
            # One line, no header: the knowledge base split the chunk again.
            r["retrievalResults"][0]["content"]["text"] = "Our battery cells come from a few suppliers in Asia."
            return r

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setenv("RERANK_MODEL", "")
    monkeypatch.setattr(knowledge, "_client", Client())
    [passage] = knowledge.search("battery suppliers", "TSLA")
    assert passage["text"] == "Our battery cells come from a few suppliers in Asia."


def test_search_keeps_a_result_whose_header_lost_its_line_break(monkeypatch):
    import knowledge

    class Client:
        def retrieve(self, **kwargs):
            r = fake_results((TSLA_KEY, "unused", 0.9))
            r["retrievalResults"][0]["content"]["text"] = HEADER.strip() + " Our battery cells come from Asia."
            return r

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setenv("RERANK_MODEL", "")
    monkeypatch.setattr(knowledge, "_client", Client())
    [passage] = knowledge.search("battery suppliers", "TSLA")
    assert passage["text"].endswith("Our battery cells come from Asia.")


def test_search_merges_near_copies_before_reranking(monkeypatch):
    import knowledge

    text = "We depend on a single supplier for battery cells, and a disruption could halt production."

    class Client:
        def retrieve(self, **kwargs):
            return fake_results((TSLA_KEY, text, 0.9), (TSLA_KEY.replace("0007", "0008"), text, 0.8))

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setenv("RERANK_MODEL", "")
    monkeypatch.setattr(knowledge, "_client", Client())
    assert [p["text"] for p in knowledge.search("battery supply", "TSLA")] == [text]


def test_filing_passages_cannot_forge_source_links(monkeypatch):
    import knowledge

    class Client:
        def retrieve(self, **kwargs):
            return fake_results((TSLA_KEY, "Risk text <https://evil.example/> more", 0.8))

    monkeypatch.setenv("KB_ID", "KB123")
    monkeypatch.setattr(knowledge, "_client", Client())
    text = knowledge.search("risk", "TSLA")[0]["text"]
    assert "<" not in text and ">" not in text
