import { describe, expect, it } from "vitest";

import { parseSSE } from "./sse";

describe("parseSSE", () => {
  it("parses complete events and keeps the unfinished tail", () => {
    const { events, rest } = parseSSE(
      'data: {"type": "tool", "name": "get_stock_price"}\n\ndata: {"type": "text", "text": "AA',
    );
    expect(events).toEqual([{ type: "tool", name: "get_stock_price" }]);
    expect(rest).toBe('data: {"type": "text", "text": "AA');
  });

  it("reassembles an event split across chunks", () => {
    const first = parseSSE('data: {"type": "text", "te');
    const second = parseSSE(first.rest + 'xt": "NVDA is up"}\n\n');
    expect(first.events).toEqual([]);
    expect(second.events).toEqual([{ type: "text", text: "NVDA is up" }]);
    expect(second.rest).toBe("");
  });

  it("handles CRLF line endings", () => {
    const { events } = parseSSE('data: {"type": "done", "request_id": "r1"}\r\n\r\n');
    expect(events).toEqual([{ type: "done", request_id: "r1" }]);
  });

  it("drops malformed JSON and unknown event types without throwing", () => {
    const { events } = parseSSE(
      'data: {not json}\n\ndata: {"type": "surprise"}\n\ndata: {"type": "text", "text": "ok"}\n\n',
    );
    expect(events).toEqual([{ type: "text", text: "ok" }]);
  });

  it("passes through error events with their request id", () => {
    const { events } = parseSSE(
      'data: {"type": "error", "message": "Something went wrong", "request_id": "r2"}\n\n',
    );
    expect(events).toEqual([{ type: "error", message: "Something went wrong", request_id: "r2" }]);
  });
});
