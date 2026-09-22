// The agent streams Server-Sent Events: one JSON object per `data:` line,
// events separated by a blank line. See app.py for the producer.

export type AgentEvent =
  | { type: "tool"; name: string }
  | { type: "text"; text: string }
  | { type: "done"; request_id: string }
  | { type: "error"; message: string; request_id?: string };

const KNOWN = new Set(["tool", "text", "done", "error"]);

/**
 * Parse every complete event in `buffer`. Network chunks can split an event
 * anywhere, so the unfinished tail comes back as `rest` for the next call.
 */
export function parseSSE(buffer: string): { events: AgentEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: AgentEvent[] = [];
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) continue;
    try {
      const parsed = JSON.parse(data);
      if (parsed && KNOWN.has(parsed.type)) events.push(parsed as AgentEvent);
    } catch {
      // A malformed event is dropped rather than breaking the whole answer.
    }
  }
  return { events, rest };
}
