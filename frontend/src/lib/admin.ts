import { storage } from "@/src/utils/storage";


const ADMIN_KEY_STORAGE = "investorflip_admin_key";


export async function getStoredAdminKey(): Promise<string> {
  return String(await storage.secureGet(ADMIN_KEY_STORAGE, "") || "").trim();
}


export async function saveAdminKey(value: string): Promise<boolean> {
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
