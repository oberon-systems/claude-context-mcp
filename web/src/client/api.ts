export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function unwrap(response: Response): Promise<unknown> {
  const text = await response.text();
  let body: unknown = null;
  if (text !== "") {
    try {
      body = JSON.parse(text);
    } catch {
      body = { error: text };
    }
  }
  if (!response.ok) {
    const message =
      body !== null &&
      typeof body === "object" &&
      typeof (body as { error?: unknown }).error === "string"
        ? (body as { error: string }).error
        : `Request failed with status ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return body;
}

export function query(params: Record<string, string | number | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered === "" ? "" : `?${rendered}`;
}

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { signal });
  return (await unwrap(response)) as T;
}

async function write<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return (await unwrap(response)) as T;
}

export const post = <T>(path: string, body?: unknown) =>
  write<T>("POST", path, body);
export const patch = <T>(path: string, body?: unknown) =>
  write<T>("PATCH", path, body);
export const put = <T>(path: string, body?: unknown) =>
  write<T>("PUT", path, body);
export const remove = <T>(path: string, body?: unknown) =>
  write<T>("DELETE", path, body);
