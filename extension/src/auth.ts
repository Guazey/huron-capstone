// Sign-in with Cognito's hosted login page, OAuth authorization code + PKCE.
// No client secret exists: this is a public client living in a browser.
// Tokens live in the platform's memory-only storage, gone when the host quits.

import { config } from "./config";
import { platform } from "./platform";

const STORAGE_KEY = "auth";
const REFRESH_MARGIN_MS = 60_000;

export type Session = {
  accessToken: string;
  refreshToken?: string;
  expiresAt: number;
  email?: string;
};

// ---------------------------------------------------------------- pure helpers

export function base64url(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomString(byteLength = 32): string {
  return base64url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

export async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

export function authorizeUrl(opts: {
  loginHost: string;
  clientId: string;
  redirectUri: string;
  state: string;
  challenge: string;
}): string {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: opts.clientId,
    redirect_uri: opts.redirectUri,
    scope: "openid email",
    state: opts.state,
    code_challenge: opts.challenge,
    code_challenge_method: "S256",
  });
  return `${opts.loginHost}/oauth2/authorize?${params}`;
}

/** Pull the code out of the redirect, refusing anything that fails the state check. */
export function codeFromRedirect(redirect: string, expectedState: string): string {
  const params = new URL(redirect).searchParams;
  if (params.get("error")) {
    throw new Error(`Sign-in failed: ${params.get("error_description") ?? params.get("error")}`);
  }
  if (params.get("state") !== expectedState) {
    throw new Error("Sign-in failed: state mismatch. Try again.");
  }
  const code = params.get("code");
  if (!code) throw new Error("Sign-in failed: no authorization code returned.");
  return code;
}

/** Email claim for display only. The runtime, not this code, verifies tokens. */
export function emailFromIdToken(idToken?: string): string | undefined {
  if (!idToken) return undefined;
  try {
    const payload = idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(payload)).email;
  } catch {
    return undefined;
  }
}

// ---------------------------------------------------------------- platform glue

async function tokenRequest(body: Record<string, string>): Promise<Session> {
  const response = await fetch(`${config.loginHost}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: config.clientId, ...body }),
  });
  if (!response.ok) throw new Error(`Token request failed (HTTP ${response.status}).`);
  const t = await response.json();
  return {
    accessToken: t.access_token,
    refreshToken: t.refresh_token ?? body.refresh_token,
    expiresAt: Date.now() + t.expires_in * 1000,
    email: emailFromIdToken(t.id_token),
  };
}

async function save(session: Session | null): Promise<void> {
  if (session) await platform().storage.set(STORAGE_KEY, session);
  else await platform().storage.remove(STORAGE_KEY);
}

export async function currentSession(): Promise<Session | null> {
  return (await platform().storage.get<Session>(STORAGE_KEY)) ?? null;
}

export async function signIn(): Promise<Session> {
  const redirectUri = await platform().redirectUri();
  const verifier = randomString();
  const state = randomString(16);
  const redirect = await platform().launchAuthFlow(
    authorizeUrl({
      loginHost: config.loginHost,
      clientId: config.clientId,
      redirectUri,
      state,
      challenge: await pkceChallenge(verifier),
    }),
  );
  const session = await tokenRequest({
    grant_type: "authorization_code",
    code: codeFromRedirect(redirect, state),
    redirect_uri: redirectUri,
    code_verifier: verifier,
  });
  const previous = await currentSession();
  session.email ??= previous?.email;
  await save(session);
  return session;
}

/** A usable access token, refreshed if it's about to expire; null if signed out. */
export async function accessToken(): Promise<string | null> {
  const session = await currentSession();
  if (!session) return null;
  if (session.expiresAt - REFRESH_MARGIN_MS > Date.now()) return session.accessToken;
  if (!session.refreshToken) {
    await save(null);
    return null;
  }
  try {
    const refreshed = await tokenRequest({
      grant_type: "refresh_token",
      refresh_token: session.refreshToken,
    });
    refreshed.email ??= session.email;
    await save(refreshed);
    return refreshed.accessToken;
  } catch {
    await save(null);
    return null;
  }
}

export async function signOut(): Promise<void> {
  const session = await currentSession();
  await save(null);
  if (session?.refreshToken) {
    // Best effort: local sign-out already happened even if this fails.
    await fetch(`${config.loginHost}/oauth2/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: session.refreshToken, client_id: config.clientId }),
    }).catch(() => undefined);
  }
}
