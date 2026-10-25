// The ticker on the chart or quote page the user has open beside the sidebar,
// read from that page's URL (and, where the URL doesn't carry it, its title).
// Only the ticker and the site's name leave this file: page titles are written
// by whoever made the page, so they never reach the agent.

/** What the host could read about the page in front: any field may be missing. */
export type OpenPage = { url?: string; title?: string };

/** The ticker the page is about, and the site it's on. */
export type PageTicker = { ticker: string; source: string };

type Site = {
  source: string;
  /** Matches the hostname or a parent domain of it. */
  host: string;
  /** Pulls the ticker out of the URL's path; the first group is the ticker. */
  path?: RegExp;
  /** Query parameters that carry the ticker (TradingView's ?symbol=NASDAQ:AAPL). */
  params?: string[];
  /** Pulls the ticker out of the page title when the URL doesn't have it. */
  title?: RegExp;
};

// "Apple Inc. (AAPL) Stock Price..." on most quote pages.
const IN_PARENS = /\(([A-Z][A-Z0-9.-]{0,9})\)/;

const SITES: Site[] = [
  {
    source: "TradingView",
    host: "tradingview.com",
    path: /^\/symbols\/(?:[A-Z]+-)?([A-Z0-9.]+)/i,
    params: ["symbol"],
    // Chart URLs are layout IDs (/chart/AbCd12/); the title starts with the
    // symbol and price: "AAPL 347.73 ▲ +0.14% Unnamed".
    title: /^(?:[A-Z]+:)?([A-Z][A-Z0-9.]{0,9})\s+[\d,]+\.?\d*\s/,
  },
  { source: "Yahoo Finance", host: "finance.yahoo.com", path: /^\/quote\/([A-Z0-9.-]+)/i, title: IN_PARENS },
  { source: "Google Finance", host: "google.com", path: /^\/finance\/quote\/([A-Z0-9.-]+):/i },
  { source: "Robinhood", host: "robinhood.com", path: /^\/(?:us\/en\/)?stocks\/([A-Z0-9.-]+)/i },
  { source: "Webull", host: "webull.com", path: /^\/quote\/[a-z]+-([a-z0-9.-]+)/i },
  { source: "Seeking Alpha", host: "seekingalpha.com", path: /^\/symbol\/([A-Z0-9.-]+)/i },
  { source: "MarketWatch", host: "marketwatch.com", path: /^\/investing\/stock\/([a-z0-9.-]+)/i },
  { source: "CNBC", host: "cnbc.com", path: /^\/quotes\/([A-Z0-9.-]+)/i },
  { source: "Nasdaq", host: "nasdaq.com", path: /^\/market-activity\/stocks\/([a-z0-9.-]+)/i },
  { source: "Stock Analysis", host: "stockanalysis.com", path: /^\/stocks\/([a-z0-9.-]+)/i },
  { source: "Finviz", host: "finviz.com", params: ["t"] },
  { source: "Fidelity", host: "fidelity.com", params: ["symbol"], title: IN_PARENS },
  { source: "Schwab", host: "schwab.com", params: ["symbol"], title: IN_PARENS },
  { source: "E-Trade", host: "etrade.com", params: ["symbol"], title: IN_PARENS },
];

// The same shape the agent accepts: AAPL, BRK.B, BRK-B.
const TICKER = /^[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z0-9]{1,3})?$/;

function clean(raw: string | undefined | null): string | undefined {
  if (!raw) return undefined;
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    // A malformed escape ("%E0") would throw out of tickerFromPage and leave
    // the previous page's ticker on the chip.
    return undefined;
  }
  // "NASDAQ:AAPL" -> "AAPL"
  const symbol = decoded.split(":").pop()!.trim().toUpperCase();
  return TICKER.test(symbol) ? symbol : undefined;
}

function siteFor(hostname: string): Site | undefined {
  const host = hostname.toLowerCase();
  return SITES.find((s) => host === s.host || host.endsWith(`.${s.host}`));
}

/** The ticker the open page is about, or undefined when it isn't a known quote or chart page. */
export function tickerFromPage(page: OpenPage): PageTicker | undefined {
  let url: URL;
  try {
    url = new URL(page.url ?? "");
  } catch {
    return undefined;
  }
  if (url.protocol !== "https:") return undefined;
  const site = siteFor(url.hostname);
  if (!site) return undefined;

  const fromPath = site.path ? clean(url.pathname.match(site.path)?.[1]) : undefined;
  const fromParams = site.params?.map((p) => clean(url.searchParams.get(p))).find(Boolean);
  const fromTitle = site.title ? clean(page.title?.match(site.title)?.[1]) : undefined;
  const ticker = fromPath ?? fromParams ?? fromTitle;
  return ticker ? { ticker, source: site.source } : undefined;
}
