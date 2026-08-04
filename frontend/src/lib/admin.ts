const ADMIN_KEY_STORAGE = "investorflip_admin_key";


async function secureStorage() {
  // Keep native secure-storage initialization out of the app bootstrap path.
  // The API client imports this module on the first screen, but SecureStore is
  // only needed when a private operation or Settings actually requests it.
  const { storage } = await import("@/src/utils/storage");
  return storage;
}


export async function getStoredAdminKey(): Promise<string> {
  const storage = await secureStorage();
  return String(await storage.secureGet(ADMIN_KEY_STORAGE, "") || "").trim();
}


export async function saveAdminKey(value: string): Promise<boolean> {
  const storage = await secureStorage();
  const key = value.trim();
  if (!key) return storage.secureRemove(ADMIN_KEY_STORAGE);
  return storage.secureSet(ADMIN_KEY_STORAGE, key);
}


export async function adminRequestHeaders(
  initial: Record<string, string> = {},
): Promise<Record<string, string>> {
  const key = await getStoredAdminKey();
  if (!key) {
    throw new Error("Set your Railway admin key in Settings before running imports or enrichment.");
  }
  return { ...initial, "X-Admin-Key": key };
}
