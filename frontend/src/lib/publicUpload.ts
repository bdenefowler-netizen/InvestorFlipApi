import { API_BASE } from "./api";

export type PublicUploadAsset = {
  uri: string;
  name?: string | null;
  mimeType?: string | null;
  size?: number | null;
  file?: Blob;
};

export type PublicUploadResult = {
  ok: boolean;
  filename: string;
  categories?: string[];
  rows_read: number;
  accepted: number;
  rejected: number;
  inserted: number;
  updated: number;
  property_ids: string[];
  files?: Array<{
    file: string;
    status: string;
    categories?: string[];
    rows?: number;
    accepted?: number;
    inserted?: number;
    updated?: number;
    reason?: string;
    sheets?: Array<{ sheet: string; rows: number }>;
  }>;
  enrichment?: {
    county?: {
      live_checked?: number;
      enriched?: number;
      tad_lookups?: number;
      missing?: number;
    };
  };
};

export async function uploadCountyFile(asset: PublicUploadAsset): Promise<PublicUploadResult> {
  const form = new FormData();
  if (asset.file) {
    form.append("file", asset.file, asset.name || "county-import.xlsx");
  } else {
    form.append(
      "file",
      {
        uri: asset.uri,
        name: asset.name || "county-import.xlsx",
        type: asset.mimeType || "application/octet-stream",
      } as any,
    );
  }

  // User-facing ADD intake is intentionally public and does not send admin credentials.
  const response = await fetch(`${API_BASE}/api/import/bulk/upload-workbook`, {
    method: "POST",
    body: form,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error((data as any).detail || `Upload failed (${response.status})`);
  }
  return data as PublicUploadResult;
}
