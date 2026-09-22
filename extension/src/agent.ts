import { invocationUrl } from "./config";
import { type AgentEvent, parseSSE } from "./sse";

export class AuthError extends Error {}

/** AgentCore requires session IDs of at least 33 characters; this is 44. */
export function newSessionId(): string {
  return `sidebar-${crypto.randomUUID()}`;
}

/** Ask the agent one question and call `onEvent` for each streamed event. */
export async function askAgent(
  prompt: string,
  sessionId: string,
  accessToken: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(invocationUrl(), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
    },
    body: JSON.stringify({ prompt }),
    signal,
  });
  if (response.status === 401 || response.status === 403) {
    throw new AuthError("Your sign-in expired. Sign in again.");
  }
  if (!response.ok || !response.body) {
    throw new Error(`The agent returned HTTP ${response.status}. Try again in a moment.`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSSE(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
  parseSSE(buffer + "\n\n").events.forEach(onEvent);
}
