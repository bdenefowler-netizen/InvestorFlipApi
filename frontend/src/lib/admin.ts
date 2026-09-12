const ADMIN_KEY_STORAGE = "investorflip_admin_key";


async function secureStorage() {
  // Keep native secure-storage initialization out of the app bootstrap path.
  // The API client imports this module on the first screen, but SecureStore is
  // only needed when Settings actually requests it.
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
  // TEMPORARY DEMO MODE: do not attach X-Admin-Key at all.
  // This keeps spreadsheet uploads as a plain multipart POST and avoids the
  // stale-key/custom-header path while we get data flowing. Backend protection
  // can be restored after the upload workflow is proven reliable.
  return initial;
}
