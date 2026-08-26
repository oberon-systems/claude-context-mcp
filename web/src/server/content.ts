import { API_TOKEN, API_URL } from "./args.js";

// Whole file bodies. A page of them would be measured in megabytes, so the
// dashboard asks for this much and no more.
export const MAX_CONTENT = 100_000;

export type FileText = {
  content: string | null;
  chars: number;
  truncated: boolean;
  reason: string | null;
};

/** Read one file of an indexed tree, through the API that owns the mounts.
 *
 * The dashboard has no copy of any tree and no business growing one: the deny
 * list that keeps a key out of an HTTP reply lives in the API, in Python, in
 * one place.
 */
export class UpstreamError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Call the API, and turn its failures into ones this service can pass on. */
export async function upstream<T>(
  method: string,
  path: string,
  params: Record<string, string> = {},
  body?: unknown,
): Promise<T> {
  const target = new URL(path, API_URL);
  for (const [key, value] of Object.entries(params)) {
    target.searchParams.set(key, value);
  }
  let response: globalThis.Response;
  try {
    response = await fetch(target, {
      method,
      headers: {
        Authorization: `Bearer ${API_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new UpstreamError(502, `the API is not reachable at ${API_URL}`);
  }
  const text = await response.text();
  const parsed = text === "" ? null : (JSON.parse(text) as unknown);
  if (!response.ok) {
    const detail = (parsed as { detail?: string } | null)?.detail;
    throw new UpstreamError(
      response.status,
      detail ?? `the API answered ${response.status}`,
    );
  }
  return parsed as T;
}

export async function fileText(
  project: string,
  path: string,
): Promise<FileText> {
  let body: { content: string };
  try {
    body = await upstream<{ content: string }>("GET", "/content", {
      project,
      path,
      limit: String(MAX_CONTENT + 1),
    });
  } catch (error) {
    return {
      content: null,
      chars: 0,
      truncated: false,
      reason:
        error instanceof UpstreamError ? error.message : "the API call failed",
    };
  }
  const truncated = body.content.length > MAX_CONTENT;
  return {
    content: truncated ? body.content.slice(0, MAX_CONTENT) : body.content,
    chars: truncated ? MAX_CONTENT : body.content.length,
    truncated,
    reason: null,
  };
}
