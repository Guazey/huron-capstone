import { describe, expect, it } from "vitest";

import { newSessionId } from "./agent";
import {
  authorizeUrl,
  base64url,
  codeFromRedirect,
  emailFromIdToken,
  pkceChallenge,
  randomString,
} from "./auth";

describe("PKCE", () => {
  it("matches the RFC 7636 appendix B example", async () => {
    expect(await pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });

  it("base64url has no padding or unsafe characters", () => {
    expect(base64url(new Uint8Array([251, 255, 191]))).toBe("-_-_");
    expect(randomString()).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });
});

describe("authorizeUrl", () => {
  it("asks for a code with an S256 challenge", () => {
    const url = new URL(
      authorizeUrl({
        loginHost: "https://example.auth.us-west-2.amazoncognito.com",
        clientId: "client123",
        redirectUri: "https://abc.chromiumapp.org/",
        state: "s1",
        challenge: "c1",
      }),
    );
    expect(url.pathname).toBe("/oauth2/authorize");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      response_type: "code",
      client_id: "client123",
      redirect_uri: "https://abc.chromiumapp.org/",
      scope: "openid email",
      state: "s1",
      code_challenge: "c1",
      code_challenge_method: "S256",
    });
  });
});

describe("codeFromRedirect", () => {
  const base = "https://abc.chromiumapp.org/";

  it("returns the code when state matches", () => {
    expect(codeFromRedirect(`${base}?code=xyz&state=s1`, "s1")).toBe("xyz");
  });

  it("rejects a state mismatch", () => {
    expect(() => codeFromRedirect(`${base}?code=xyz&state=evil`, "s1")).toThrow(/state mismatch/);
  });

  it("surfaces an OAuth error", () => {
    expect(() =>
      codeFromRedirect(`${base}?error=access_denied&error_description=nope&state=s1`, "s1"),
    ).toThrow(/nope/);
  });

  it("rejects a redirect with no code", () => {
    expect(() => codeFromRedirect(`${base}?state=s1`, "s1")).toThrow(/no authorization code/);
  });
});

describe("emailFromIdToken", () => {
  it("reads the email claim for display", () => {
    const payload = btoa(JSON.stringify({ email: "user@example.com" }))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    expect(emailFromIdToken(`h.${payload}.sig`)).toBe("user@example.com");
  });

  it("returns undefined for garbage", () => {
    expect(emailFromIdToken("not-a-jwt")).toBeUndefined();
    expect(emailFromIdToken(undefined)).toBeUndefined();
  });
});

describe("newSessionId", () => {
  it("meets AgentCore's 33-character minimum and is unique", () => {
    const a = newSessionId();
    expect(a.length).toBeGreaterThanOrEqual(33);
    expect(a).not.toBe(newSessionId());
  });
});
