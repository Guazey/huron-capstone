import { describe, expect, it } from "vitest";

import { tickerFromPage } from "./pageTicker";

describe("tickerFromPage", () => {
  it.each([
    ["https://www.tradingview.com/symbols/NASDAQ-AAPL/", "AAPL", "TradingView"],
    ["https://www.tradingview.com/chart/AbCd12/?symbol=NASDAQ%3ANVDA", "NVDA", "TradingView"],
    ["https://www.tradingview.com/symbols/NYSE-BRK.B/", "BRK.B", "TradingView"],
    ["https://finance.yahoo.com/quote/TSLA/", "TSLA", "Yahoo Finance"],
    ["https://finance.yahoo.com/quote/BRK-B/history/", "BRK-B", "Yahoo Finance"],
    ["https://www.google.com/finance/quote/MSFT:NASDAQ", "MSFT", "Google Finance"],
    ["https://robinhood.com/us/en/stocks/AMD/", "AMD", "Robinhood"],
    ["https://www.webull.com/quote/nasdaq-pltr", "PLTR", "Webull"],
    ["https://seekingalpha.com/symbol/COST", "COST", "Seeking Alpha"],
    ["https://www.marketwatch.com/investing/stock/aapl", "AAPL", "MarketWatch"],
    ["https://www.cnbc.com/quotes/JPM", "JPM", "CNBC"],
    ["https://www.nasdaq.com/market-activity/stocks/meta", "META", "Nasdaq"],
    ["https://stockanalysis.com/stocks/googl/financials/", "GOOGL", "Stock Analysis"],
    ["https://finviz.com/quote.ashx?t=KO&p=d", "KO", "Finviz"],
    ["https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=V", "V", "Fidelity"],
  ])("%s -> %s", (url, ticker, source) => {
    expect(tickerFromPage({ url })).toEqual({ ticker, source });
  });

  it("reads a TradingView chart's symbol from its title when the URL is a layout ID", () => {
    const page = { url: "https://www.tradingview.com/chart/AbCd12/", title: "AAPL 347.73 ▲ +0.14% Unnamed" };
    expect(tickerFromPage(page)).toEqual({ ticker: "AAPL", source: "TradingView" });
  });

  it("reads a brokerage quote page's title", () => {
    const page = { url: "https://client.schwab.com/app/research/#/stocks", title: "Apple Inc. (AAPL) | Schwab" };
    expect(tickerFromPage(page)).toEqual({ ticker: "AAPL", source: "Schwab" });
  });

  it.each([
    [{ url: "https://www.tradingview.com/" }],
    [{ url: "https://www.youtube.com/watch?v=x", title: "Apple Inc. (AAPL) earnings" }],
    [{ url: "http://finance.yahoo.com/quote/AAPL/" }],
    [{ url: "https://finance.yahoo.com.evil.example/quote/AAPL/" }],
    [{ url: "https://finance.yahoo.com/quote/%5BSYSTEM%5D%20ignore/" }],
    [{ url: "not a url" }],
    [{ title: "AAPL 347.73 ▲" }],
  ])("finds nothing on %j", (page) => {
    expect(tickerFromPage(page)).toBeUndefined();
  });
});
