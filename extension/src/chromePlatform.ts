import type { Platform } from "./platform";

export const chromePlatform: Platform = {
  storage: {
    get: async <T>(key: string) => (await chrome.storage.session.get(key))[key] as T | undefined,
    set: (key, value) => chrome.storage.session.set({ [key]: value }),
    remove: (key) => chrome.storage.session.remove(key),
  },
  redirectUri: async () => chrome.identity.getRedirectURL(),
  async launchAuthFlow(url) {
    const redirect = await chrome.identity.launchWebAuthFlow({ url, interactive: true });
    if (!redirect) throw new Error("Sign-in was cancelled.");
    return redirect;
  },
  async endLoginSession(url) {
    // Not interactive: Cognito redirects straight back, so no window opens.
    await chrome.identity.launchWebAuthFlow({ url, interactive: false });
  },
};
