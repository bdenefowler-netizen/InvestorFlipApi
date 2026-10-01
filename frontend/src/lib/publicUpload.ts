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
  transport?: {
    mode?: "chunked";
    upload_id?: string;
    chunks?: number;
    bytes_received?: number;
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

const CHUNK_BYTES = 8 * 1024 * 1024;
const CHUNK_THRESHOLD_BYTES = 8 * 1024 * 1024;

async function responseJson(response: Response): Promise<any> {
  return response.json().catch(() => ({}));
}

function stageError(stage: string, error: unknown): Error {
  const message = error instanceof Error ? error.message : String(error);
  return new Error(`${stage}: ${message}`);
}

async function fetchStage(stage: string, input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (error) {
    throw stageError(stage, error);
  }
}

async function ensureBlob(asset: PublicUploadAsset): Promise<Blob> {
  if (asset.file) return asset.file;
  const response = await fetchStage("Reading selected file", asset.uri);
  if (!response.ok) {
    throw new Error(`Reading selected file failed (${response.status})`);
  }
  return response.blob();
}

function makeUploadId(): string {
  const randomUUID = (globalThis as any)?.crypto?.randomUUID;
  if (typeof randomUUID === "function") return randomUUID.call((globalThis as any).crypto);
  return `if_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

async function directUpload(
  asset: PublicUploadAsset,
  source: CountyUploadSource,
  uploadName: string,
): Promise<PublicUploadResult> {
  const form = new FormData();

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

  const response = await fetchStage(
    "Direct upload",
    `${API_BASE}/api/import/bulk/upload-workbook`,
    { method: "POST", body: form },
  );
  const data = await responseJson(response);
  if (!response.ok) {
    throw new Error(data.detail || `Direct upload failed (${response.status})`);
  }
  return data as PublicUploadResult;
}

async function chunkedUpload(blob: Blob, uploadName: string): Promise<PublicUploadResult> {
  const uploadId = makeUploadId();
  const totalSize = blob.size;
  const totalChunks = Math.ceil(totalSize / CHUNK_BYTES);
  let stage = "Preparing chunked upload";

  try {
    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
      const start = chunkIndex * CHUNK_BYTES;
      const end = Math.min(start + CHUNK_BYTES, totalSize);
      const piece = blob.slice(start, end);

      const form = new FormData();
      form.append("upload_id", uploadId);
      form.append("chunk_index", String(chunkIndex));
      form.append("total_chunks", String(totalChunks));
      form.append("filename", uploadName);
      form.append("total_size", String(totalSize));
      form.append("chunk", piece, `${uploadName}.part`);

      stage = `Chunk ${chunkIndex + 1}/${totalChunks}`;
      const response = await fetchStage(
        stage,
        `${API_BASE}/api/import/bulk/upload-workbook/chunk`,
        { method: "POST", body: form },
      );
      const data = await responseJson(response);
      if (!response.ok) {
        throw new Error(`${stage} failed (${response.status}): ${data.detail || "server rejected chunk"}`);
      }
    }

    const complete = new FormData();
    complete.append("upload_id", uploadId);
    complete.append("total_chunks", String(totalChunks));
    complete.append("filename", uploadName);
    complete.append("total_size", String(totalSize));

    stage = "Finalizing upload after all chunks arrived";
    const response = await fetchStage(
      stage,
      `${API_BASE}/api/import/bulk/upload-workbook/complete`,
      { method: "POST", body: complete },
    );
    const data = await responseJson(response);
    if (!response.ok) {
      throw new Error(`${stage} failed (${response.status}): ${data.detail || "server rejected finalization"}`);
    }
    return data as PublicUploadResult;
  } catch (error) {
    fetch(`${API_BASE}/api/import/bulk/upload-workbook/chunk/${encodeURIComponent(uploadId)}`, {
      method: "DELETE",
    }).catch(() => undefined);

    if (error instanceof Error && error.message.startsWith(stage)) {
      throw error;
    }
    throw stageError(stage, error);
  }
}

export async function uploadCountyFile(
  asset: PublicUploadAsset,
  source: CountyUploadSource,
): Promise<PublicUploadResult> {
  const originalName = asset.name || "county-import.xlsx";
  const uploadName = `${SOURCE_PREFIX[source]}__${originalName}`;
  const knownSize = asset.file?.size ?? asset.size ?? null;

  if (knownSize !== null && knownSize <= CHUNK_THRESHOLD_BYTES) {
    return directUpload(asset, source, uploadName);
  }

  const blob = await ensureBlob(asset);
  if (blob.size <= CHUNK_THRESHOLD_BYTES) {
    return directUpload({ ...asset, file: blob, size: blob.size }, source, uploadName);
  }

  return chunkedUpload(blob, uploadName);
}
