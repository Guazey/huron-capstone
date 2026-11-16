"""Download recent SEC filings for a watchlist and load them into the knowledge base.

Run from your machine (not the agent), after infra/deploy.sh:
    SEC_USER_AGENT="Your Name you@example.com" python sec_edgar.py            # watchlist
    SEC_USER_AGENT="..." python sec_edgar.py AAPL SPCX                          # just these

The SEC requires a User-Agent naming a real contact and allows 10 requests a
second: https://www.sec.gov/os/accessing-edgar-data

Each filing is cut into section chunks (`chunks`), and each chunk is one
plain-text file in S3 at
    chunks/<TICKER>/<FORM>/<FILED>/<CIK>/<ACCESSION>/<PRIMARY_DOC>/<NNNN>.txt
with a .metadata.json sidecar (ticker, form, filed, section) beside it. The
key alone rebuilds the sec.gov URL, so a search result can always cite the
exact filing. Keys from before chunking (filings/...<PRIMARY_DOC>.txt) still
parse.
"""
import json
import os
import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Filings are inline-XBRL XHTML; the HTML parser handles them fine.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Companies whose filings are searchable. Add a ticker and rerun to include it.
WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "RKLB", "SPCX"]

# Latest annual report, the two latest quarterlies, and for recent IPOs the
# prospectus, since they have no 10-K yet.
FORMS = {"10-K": 1, "10-Q": 2, "424B4": 1}
PROSPECTUS_ONLY_IF_NO = "10-K"

SEC = "https://www.sec.gov"
DATA = "https://data.sec.gov"
REQUEST_GAP_SECONDS = 0.15  # stay well under the SEC's 10 requests/second
KEY_RE = re.compile(
    r"filings/(?P<ticker>[^/]+)/(?P<form>[^/]+)/(?P<filed>\d{4}-\d{2}-\d{2})/"
    r"(?P<cik>\d+)/(?P<accession>\d+)/(?P<doc>.+)\.txt$"
)
CHUNK_KEY_RE = re.compile(
    r"chunks/(?P<ticker>[^/]+)/(?P<form>[^/]+)/(?P<filed>\d{4}-\d{2}-\d{2})/"
    r"(?P<cik>\d+)/(?P<accession>\d+)/(?P<doc>[^/]+)/(?P<n>\d+)\.txt$"
)


# ---------------------------------------------------------------- pure helpers

def filing_url(cik: str, accession: str, primary_doc: str) -> str:
    return f"{SEC}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"


def s3_key(ticker: str, form: str, filed: str, cik: str, accession: str, primary_doc: str) -> str:
    return f"filings/{ticker}/{form}/{filed}/{int(cik)}/{accession.replace('-', '')}/{primary_doc}.txt"


def parse_key(key: str) -> dict | None:
    """S3 key of a filing or a chunk -> ticker, form, filing date, and the sec.gov URL; None if neither."""
    m = CHUNK_KEY_RE.search(key) or KEY_RE.search(key)
    if not m:
        return None
    return {
        "ticker": m["ticker"], "form": m["form"], "filed": m["filed"],
        "url": filing_url(m["cik"], m["accession"], m["doc"]),
    }


def pick_filings(submissions: dict) -> list[dict]:
    """Newest filings of each wanted form from an EDGAR submissions document."""
    recent = submissions.get("filings", {}).get("recent", {})
    rows = zip(recent.get("form", []), recent.get("filingDate", []),
               recent.get("accessionNumber", []), recent.get("primaryDocument", []))
    taken: dict[str, list[dict]] = {form: [] for form in FORMS}
    for form, filed, accession, doc in rows:  # EDGAR lists newest first
        if form in FORMS and len(taken[form]) < FORMS[form] and doc:
            taken[form].append({"form": form, "filed": filed, "accession": accession, "doc": doc})
    if taken[PROSPECTUS_ONLY_IF_NO]:
        taken["424B4"] = []
    return [f for form in FORMS for f in taken[form]]


def html_to_text(html: str) -> str:
    """Readable text from a filing's HTML: tables as ' | ' rows, hidden XBRL dropped."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    for tag in soup.find_all(["ix:header"]):  # inline-XBRL block, invisible in the filing
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        tag.decompose()
    for row in soup.find_all("tr"):
        cells = [" ".join(c.get_text(" ").split()) for c in row.find_all(["td", "th"])]
        line = " | ".join(c for c in cells if c)
        # Filings put "$", "%", and ")" in their own cells: "$ | 94,827 | (3) | %"
        line = line.replace("$ | ", "$").replace(" | %", "%").replace(" | )", ")")
        row.replace_with(line + "\n")
    text = soup.get_text("\n")
    lines = (" ".join(line.split()) for line in text.splitlines())
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text)


# Section names come from the SEC's form instructions, not the filing's own
# heading text: filings style headings every which way (MSFT splits them
# mid-word: "ITEM 1A. RIS" / "K FACTORS"), but the Item numbers are fixed.
ITEMS_10K = {
    "1": "Business", "1A": "Risk Factors", "1B": "Unresolved Staff Comments", "1C": "Cybersecurity",
    "2": "Properties", "3": "Legal Proceedings", "4": "Mine Safety Disclosures",
    "5": "Market for Common Equity and Stock Repurchases", "6": "Reserved",
    "7": "Management's Discussion and Analysis", "7A": "Market Risk Disclosures",
    "8": "Financial Statements", "9": "Changes in Accountants", "9A": "Controls and Procedures",
    "9B": "Other Information", "9C": "Foreign Jurisdictions", "10": "Directors and Officers",
    "11": "Executive Compensation", "12": "Security Ownership", "13": "Related Transactions",
    "14": "Accountant Fees", "15": "Exhibits", "16": "Form 10-K Summary",
}
ITEMS_10Q = {
    "I": {"1": "Financial Statements", "2": "Management's Discussion and Analysis",
          "3": "Market Risk Disclosures", "4": "Controls and Procedures"},
    "II": {"1": "Legal Proceedings", "1A": "Risk Factors", "2": "Stock Repurchases",
           "3": "Defaults", "4": "Mine Safety Disclosures", "5": "Other Information", "6": "Exhibits"},
}
# "ITEM 1A. RISK FACTORS", "Item 7. Management's...", "ITEM 1." (title on the
# next line), "Item 1. | Business" (a two-cell heading table).
_ITEM = re.compile(r"^(?:PART\s+[IV]+\W*)?Item\s*(\d{1,2})\s*([A-C])?\b\s*[.:\-\u2013\u2014]?\s*(.*)$", re.I)
# A table-of-contents row ends with its page number: "Item 1A. | Risk Factors | 12".
_TOC_PAGE = re.compile(r"\|\s*[\d\-\u2013 ]+$")
# A prospectus has no Items; its sections are ALL-CAPS lines ("USE OF PROCEEDS").
_CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,'\u2019&\-]{5,79}$")
CHUNK_CHARS = 1000


def _item(line: str) -> str | None:
    """The Item number ("1A") if `line` is an Item heading, else None."""
    if len(line) > 120 or _TOC_PAGE.search(line):
        return None
    m = _ITEM.match(line.replace(" | ", " "))
    if not m:
        return None
    rest = m[3].strip()
    # A heading has no title, an ALL-CAPS one, or a short capitalized one.
    # "Item 1A of Part I describes..." is a sentence, not a heading.
    if rest and not (rest.isupper() or (rest[0].isupper() and len(rest.split()) <= 12)):
        return None
    return f"{int(m[1])}{(m[2] or '').upper()}"


def _caps_title(line: str) -> str:
    small = {"and", "of", "the", "for", "in", "to", "a", "on", "or"}
    words = line.lower().split()
    return " ".join(w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words))


_PART = re.compile(r"^PART\s+(II|I)\b", re.I)


def sections(text: str, form: str) -> list[tuple[str, list[str]]]:
    """Split filing text into (section name, lines). Text before the first section is "Cover".

    10-K and 10-Q sections are their Items. A 10-Q numbers Items again in
    Part II, so its "PART I"/"PART II" lines say which one an Item is in.
    A heading for the section already open (a running page header like
    MSFT's "PART I / Item 1" on every page) doesn't start a new one.
    """
    lines = text.splitlines()
    out: list[tuple[str, list[str]]] = [("Cover", [])]
    if form in ("10-K", "10-Q"):
        part, current = "I", None
        for line in lines:
            m = _PART.match(line)
            if m:
                part = m[1].upper()
            item = _item(line)
            if form == "10-K":
                name = f"Item {item}. {ITEMS_10K[item]}" if item in ITEMS_10K else None
            else:
                titles = ITEMS_10Q[part]
                name = f"Part {part}, Item {item}. {titles[item]}" if item in titles else None
            if name and name != current:
                current = name
                out.append((name, []))
            elif not m or name:
                out[-1][1].append(line)
    else:
        for line in lines:
            if " | " not in line and _CAPS_HEADING.match(line) and len(line.split()) >= 2:
                out.append((_caps_title(line), []))
            else:
                out[-1][1].append(line)
    return [(name, body) for name, body in out if body]


def _pieces(line: str) -> list[str]:
    """A line short enough for one chunk: long paragraphs split at sentence ends."""
    if len(line) <= CHUNK_CHARS:
        return [line]
    out, current = [], ""
    for sentence in re.split(r"(?<=[.;])\s+", line):
        if current and len(current) + 1 + len(sentence) > CHUNK_CHARS:
            out.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    return out + [current] if current else out


def chunks(text: str, form: str) -> list[tuple[str, str]]:
    """(section, chunk text) pairs of about CHUNK_CHARS, never crossing a section or splitting a sentence."""
    out = []
    for name, body in sections(text, form):
        current = ""
        for line in body:
            for piece in _pieces(line):
                if current and len(current) + 1 + len(piece) > CHUNK_CHARS:
                    out.append((name, current))
                    current = ""
                current = f"{current}\n{piece}".strip()
        if current:
            out.append((name, current))
    # Page numbers, "Table of Contents" links, and other scraps.
    return [(name, c) for name, c in out if len(c) >= 80]


def passage_link(url: str, text: str) -> str:
    """The filing URL, scrolled to the passage: a text fragment of its first words.

    Browsers that support text fragments (Chrome, Edge, Safari 16.1+, Firefox
    131+) highlight the passage; others just open the filing. Table text
    doesn't match the rendered page, so a passage starting with a table
    links to the filing alone.
    """
    first = text.split("\n", 1)[0]
    words = first.split()[:8]
    if " | " in first or len(words) < 4:
        return url
    return f"{url}#:~:text={quote(' '.join(words), safe='')}"


def chunk_key(ticker: str, filing: dict, cik: str, n: int) -> str:
    """chunks/<TICKER>/<FORM>/<FILED>/<CIK>/<ACCESSION>/<DOC>/<NNNN>.txt: a filing key plus a chunk number."""
    base = s3_key(ticker, filing["form"], filing["filed"], cik, filing["accession"], filing["doc"])
    return f"chunks/{base.removeprefix('filings/').removesuffix('.txt')}/{n:04d}.txt"


def chunk_files(ticker: str, company: str, filing: dict, cik: str, text: str) -> list[tuple[str, str, dict]]:
    """(S3 key, body, metadata) for each chunk of one filing.

    Every chunk starts with the company, form, date, and section, so it still
    says what it's about once it's cut out of the filing: "the Company's
    manufacturing" alone doesn't say which company, or that it's a risk.
    The metadata lets the knowledge base filter by company and form itself.
    """
    out = []
    for n, (section, body) in enumerate(chunks(text, filing["form"])):
        header = f"{company} ({ticker}) {filing['form']} filed {filing['filed']}. {section}."
        meta = {"ticker": ticker, "form": filing["form"], "filed": filing["filed"], "section": section}
        out.append((chunk_key(ticker, filing, cik, n), f"{header}\n{body}", meta))
    return out


# ---------------------------------------------------------------- network + AWS

def _session() -> requests.Session:
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in agent:
        sys.exit('Set SEC_USER_AGENT="Your Name you@example.com" (the SEC requires a contact).')
    s = requests.Session()
    s.headers.update({"User-Agent": agent, "Accept-Encoding": "gzip, deflate"})
    return s


def _get(session, url, **kwargs):
    time.sleep(REQUEST_GAP_SECONDS)
    response = session.get(url, timeout=30, **kwargs)
    response.raise_for_status()
    return response


def ticker_to_cik(session) -> dict[str, dict]:
    rows = _get(session, f"{SEC}/files/company_tickers.json").json().values()
    return {r["ticker"].upper(): {"cik": str(r["cik_str"]), "name": r["title"]} for r in rows}


def load(tickers: list[str]) -> None:
    import boto3

    bucket = os.environ["FILINGS_BUCKET"]
    s3 = boto3.client("s3")
    session = _session()
    ciks = ticker_to_cik(session)
    for ticker in tickers:
        company = ciks.get(ticker.upper())
        if not company:
            print(f"{ticker}: not in the SEC ticker list, skipped")
            continue
        cik = company["cik"]
        subs = _get(session, f"{DATA}/submissions/CIK{int(cik):010d}.json").json()
        filings = pick_filings(subs)
        if not filings:
            print(f"{ticker}: no 10-K, 10-Q, or prospectus found")
        for f in filings:
            html = _get(session, filing_url(cik, f["accession"], f["doc"])).text
            files = chunk_files(ticker, company["name"], f, cik, html_to_text(html))
            _replace_prefix(s3, bucket, chunk_key(ticker, f, cik, 0).rsplit("/", 1)[0] + "/", files)
            print(f"{ticker}: {f['form']} {f['filed']} ({len(files)} chunks)")


def _replace_prefix(s3, bucket: str, prefix: str, files: list[tuple[str, str, dict]]) -> None:
    """Write one filing's chunks and their metadata, removing chunks left from an earlier run."""
    def put(item):
        key, body, meta = item
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode(), ContentType="text/plain; charset=utf-8")
        # The knowledge base reads <key>.metadata.json as the chunk's filterable attributes.
        s3.put_object(Bucket=bucket, Key=f"{key}.metadata.json",
                      Body=json.dumps({"metadataAttributes": meta}).encode(), ContentType="application/json")

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(put, files))
    keep = set()
    for key, _, _ in files:
        keep.update({key, f"{key}.metadata.json"})
    stale = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
             for o in page.get("Contents", []) if o["Key"] not in keep]
    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale[i:i + 1000]]})


def sync() -> None:
    """Tell the knowledge base to (re)ingest the bucket, and wait for it."""
    import boto3

    kb_id, ds_id = os.environ["KB_ID"], os.environ["KB_DATA_SOURCE_ID"]
    agent = boto3.client("bedrock-agent")
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]
    while job["status"] in ("STARTING", "IN_PROGRESS"):
        time.sleep(15)
        job = agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job["ingestionJobId"],
        )["ingestionJob"]
        print(f"  ingestion: {job['status']} {job.get('statistics', {})}")
    if job["status"] != "COMPLETE":
        sys.exit(f"ingestion {job['status']}: {job.get('failureReasons')}")


if __name__ == "__main__":
    load([t.upper() for t in sys.argv[1:]] or WATCHLIST)
    sync()
