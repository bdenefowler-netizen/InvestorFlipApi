import { API_BASE } from "./api";

export type CountyUploadSource = "tad" | "tax" | "pre_foreclosure" | "probate" | "code_violations" | "owner";

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
  source_type?: CountyUploadSource | "auto";
  categories?: string[];
  rows_read: number;
  accepted: number;
  rejected: number;
  inserted: number;
  updated: number;
  staged?: number;
  property_ids: string[];
  files?: Array<{
    file: string;
    status: string;
    source_type?: string;
    categories?: string[];
    rows?: number;
    accepted?: number;
    inserted?: number;
    updated?: number;
    staged?: number;
    reason?: string;
    sheets?: Array<{ sheet: string; rows: number }>;
  }>;
  enrichment?: {
    deferred?: boolean;
    message?: string;
    county?: {
      live_checked?: number;
      enriched?: number;
      tad_lookups?: number;
      missing?: number;
    };
  };
};

const SOURCE_PREFIX: Record<CountyUploadSource, string> = {
  tad: "tad",
  tax: "taxroll",
  pre_foreclosure: "preforeclosure",
  probate: "probate",
  code_violations: "code_violation",
  owner: "owner",
};

export async function uploadCountyFile(
  asset: PublicUploadAsset,
  source: CountyUploadSource,
): Promise<PublicUploadResult> {
  const form = new FormData();
  const originalName = asset.name || "county-import.xlsx";
  const uploadName = `${SOURCE_PREFIX[source]}__${originalName}`;

  if (asset.file) {
    form.append("file", asset.file, uploadName);
  } else {
    form.append("file", {
      uri: asset.uri,
      name: uploadName,
      type: asset.mimeType || "application/octet-stream",
    } as any);
  }
  form.append("source_type", source);

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
