const ADMIN_KEY_STORAGE = "investorflip_admin_key";


async function secureStorage() {
  // Keep native secure-storage initialization out of the app bootstrap path.
  // The API client imports this module on the first screen, but SecureStore is
  // only needed when a private operation or Settings actually requests it.
  const { storage } = await import("@/src/utils/storage");
  return storage;
}

function browserStorage(): Storage | null {
  try {
    return typeof globalThis !== "undefined" && (globalThis as any).localStorage
      ? (globalThis as any).localStorage as Storage
      : null;
  } catch {
    return null;
  }
}


export async function getStoredAdminKey(): Promise<string> {
  // On web, prefer localStorage so a browser refresh/reboot does not silently
  // strand protected actions if the AsyncStorage shim has not hydrated yet.
  const browser = browserStorage();
  const browserKey = String(browser?.getItem(ADMIN_KEY_STORAGE) || "").trim();
  if (browserKey) return browserKey;

  const storage = await secureStorage();
  const key = String(await storage.secureGet(ADMIN_KEY_STORAGE, "") || "").trim();
  if (key && browser) {
    try { browser.setItem(ADMIN_KEY_STORAGE, key); } catch {}
  }
  return key;
}


export async function saveAdminKey(value: string): Promise<boolean> {
  const storage = await secureStorage();
  const browser = browserStorage();
  const key = value.trim();

  if (!key) {
    try { browser?.removeItem(ADMIN_KEY_STORAGE); } catch {}
    return storage.secureRemove(ADMIN_KEY_STORAGE);
  }

  let browserSaved = false;
  try {
    browser?.setItem(ADMIN_KEY_STORAGE, key);
    browserSaved = Boolean(browser);
  } catch {}
  const secureSaved = await storage.secureSet(ADMIN_KEY_STORAGE, key);
  return secureSaved || browserSaved;
}


export async function adminRequestHeaders(
  initial: Record<string, string> = {},
): Promise<Record<string, string>> {
  // During the private/demo phase, callers may proceed without a stored key.
  // Backend routes that still require admin auth will reject the request; the
  // spreadsheet intake upload is intentionally open for now.
  const key = await getStoredAdminKey();
  return key ? { ...initial, "X-Admin-Key": key } : initial;
}
