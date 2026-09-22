"""Download recent SEC filings for a watchlist and load them into the knowledge base.

Run from your machine (not the agent), after infra/deploy.sh:
    SEC_USER_AGENT="Your Name you@example.com" python sec_edgar.py            # watchlist
    SEC_USER_AGENT="..." python sec_edgar.py AAPL SPCX                          # just these

The SEC requires a User-Agent naming a real contact and allows 10 requests a
second: https://www.sec.gov/os/accessing-edgar-data

Each filing becomes one plain-text file in S3 at
    filings/<TICKER>/<FORM>/<FILED>/<CIK>/<ACCESSION>/<PRIMARY_DOC>.txt
The key alone rebuilds the sec.gov URL, so a search result can always cite
the exact filing without depending on knowledge-base metadata support.
"""
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

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


# ---------------------------------------------------------------- pure helpers

def filing_url(cik: str, accession: str, primary_doc: str) -> str:
    return f"{SEC}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"


def s3_key(ticker: str, form: str, filed: str, cik: str, accession: str, primary_doc: str) -> str:
    return f"filings/{ticker}/{form}/{filed}/{int(cik)}/{accession.replace('-', '')}/{primary_doc}.txt"


def parse_key(key: str) -> dict | None:
    """S3 key -> ticker, form, filing date, and the sec.gov URL; None if not a filing key."""
    m = KEY_RE.search(key)
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
    taken = {form: [] for form in FORMS}
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
        row.replace_with(" | ".join(c for c in cells if c) + "\n")
    text = soup.get_text("\n")
    lines = (" ".join(line.split()) for line in text.splitlines())
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text)


def document(ticker: str, company: str, filing: dict, cik: str, text: str) -> str:
    """The file stored in S3: a short header (for the first chunk) and the text."""
    url = filing_url(cik, filing["accession"], filing["doc"])
    header = (
        f"{company} ({ticker}) {filing['form']} filed {filing['filed']}\n"
        f"Source: {url}\n\n"
    )
    return header + text


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
            body = document(ticker, company["name"], f, cik, html_to_text(html))
            key = s3_key(ticker, f["form"], f["filed"], cik, f["accession"], f["doc"])
            s3.put_object(Bucket=bucket, Key=key, Body=body.encode(),
                          ContentType="text/plain; charset=utf-8")
            print(f"{ticker}: {f['form']} {f['filed']} ({len(body) // 1024} KB)")


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
