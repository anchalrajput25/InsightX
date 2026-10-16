// One small fetch wrapper for every call to the InsightX backend.
// The browser calls /api/..., which next.config.ts forwards to FastAPI.

import { getSession } from "@/lib/session";

export const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /** The backend restarted and no longer knows the connected database. */
  get lostConnection(): boolean {
    return this.status === 404 && /connection session not found/i.test(this.detail);
  }

  get signedOut(): boolean {
    return this.status === 401;
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

interface RequestOptions {
  query?: Query;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

function authHeaders(): Record<string, string> {
  const session = getSession();
  if (session?.token) return { Authorization: `Bearer ${session.token}` };
  if (session?.localName) return { "X-InsightX-User": session.localName };
  return {};
}

function buildUrl(path: string, query?: Query): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${url}?${text}` : url;
}

function readDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : String(item)))
        .join("; ");
    }
  }
  return fallback;
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...authHeaders() };
  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = options.form;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), { method, headers, body, signal: options.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "Cannot reach the InsightX backend. Is it running?");
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!response.ok) {
    const fallback =
      response.status >= 500 && (response.status === 502 || response.status === 504 || !text)
        ? "The InsightX backend is not reachable. Start it, then try again."
        : `Request failed (${response.status})`;
    let detail = readDetail(parsed, typeof parsed === "string" && parsed.length < 300 ? parsed : fallback);
    if (response.status === 500 && /^internal server error$/i.test(detail.trim())) {
      detail = "The backend hit an error or did not answer in time. Check the backend window for the error.";
    }
    const error = new ApiError(response.status, detail);
    if (error.signedOut && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("insightx:signed-out"));
    }
    throw error;
  }
  return parsed as T;
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) => request<T>("GET", path, { query, signal }),
  post: <T>(path: string, json?: unknown) => request<T>("POST", path, { json: json ?? {} }),
  postForm: <T>(path: string, form: FormData) => request<T>("POST", path, { form }),
  put: <T>(path: string, json: unknown) => request<T>("PUT", path, { json }),
  patch: <T>(path: string, json: unknown) => request<T>("PATCH", path, { json }),
  delete: <T>(path: string, query?: Query) => request<T>("DELETE", path, { query }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.lostConnection) return "The backend restarted and forgot your database. Connect it again.";
    return error.detail;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
