// What the shared UI needs from its host. The Chrome extension and the desktop
// app each provide one; everything else in src/ is the same code for both.

export type Platform = {
  /** Memory-only storage: cleared when the host (Chrome, or the app) quits. */
  storage: {
    get<T>(key: string): Promise<T | undefined>;
    set(key: string, value: unknown): Promise<void>;
    remove(key: string): Promise<void>;
  };
  /** Where Cognito sends the browser back to after sign-in. */
  redirectUri(): string;
  /** Show Cognito's login page and resolve with the URL it redirected to. */
  launchAuthFlow(url: string): Promise<string>;
  /** Open a link outside the app. Unset means a plain target="_blank" works. */
  openExternal?(url: string): void;
  /** Hide the window. Unset means the host has its own close control. */
  hide?(): void;
};

let current: Platform | undefined;

export function setPlatform(platform: Platform): void {
  current = platform;
}

export function platform(): Platform {
  if (!current) throw new Error("setPlatform() must run before the app renders.");
  return current;
}
