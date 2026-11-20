import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

function readCookie(name: string): string | undefined {
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${name}=`))
    ?.split("=")[1];
}

/** CSRF double-submit: unsafe requests carry the token from the readable cookie. */
const csrf: Middleware = {
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      const token = readCookie("dpac_csrf");
      if (token) request.headers.set("X-CSRF-Token", decodeURIComponent(token));
    }
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "include" });
api.use(csrf);

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

/** Unwrap an openapi-fetch result or throw a typed ApiError. */
export function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (res.error !== undefined || !res.response.ok) {
    const e = (res.error ?? {}) as { code?: string; message?: string; details?: unknown; detail?: unknown };
    throw new ApiError(res.response.status, e.code ?? "error", e.message ?? JSON.stringify(e.detail ?? e), e.details);
  }
  return res.data as T;
}

/** Multipart upload (openapi-fetch does not serialize FormData bodies generically). */
export async function uploadFiles(jobId: string, files: File[], visibility: "private" | "shared") {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  form.append("visibility", visibility);
  const token = readCookie("dpac_csrf");
  const res = await fetch(`/api/v1/jobs/${jobId}/attachments`, {
    method: "POST",
    body: form,
    credentials: "include",
    headers: token ? { "X-CSRF-Token": decodeURIComponent(token) } : {},
  });
  const body = await res.json();
  if (!res.ok) throw new ApiError(res.status, body.code ?? "error", body.message ?? "upload failed");
  return body as Schemas["AttachmentOut"][];
}
