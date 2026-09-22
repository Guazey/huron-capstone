import { describe, expect, it } from "vitest";

import { isVerifiedLink } from "./App";
import { parseSSE } from "./sse";

describe("isVerifiedLink", () => {
  const sources = ["https://finance.yahoo.com/quote/TSLA/", "https://apnews.com/a"];

  it("allows a link the tools returned", () => {
    expect(isVerifiedLink("https://apnews.com/a", sources)).toBe(true);
  });

  it("blocks a plausible link the model invented", () => {
    expect(isVerifiedLink("https://finance.yahoo.com/search?q=TSLA", sources)).toBe(false);
  });

  it("blocks non-https and missing links", () => {
    expect(isVerifiedLink("javascript:alert(1)", ["javascript:alert(1)"])).toBe(false);
    expect(isVerifiedLink("VKTX", sources)).toBe(false);
    expect(isVerifiedLink(undefined, sources)).toBe(false);
    expect(isVerifiedLink("https://apnews.com/a", undefined)).toBe(false);
  });
});

describe("sources event", () => {
  it("parses", () => {
    const { events } = parseSSE('data: {"type": "sources", "urls": ["https://apnews.com/a"]}\n\n');
    expect(events).toEqual([{ type: "sources", urls: ["https://apnews.com/a"] }]);
  });
});
