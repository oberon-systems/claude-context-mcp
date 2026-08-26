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
export async function fileText(
  project: string,
  path: string,
): Promise<FileText> {
  const target = new URL("/content", API_URL);
  target.searchParams.set("project", project);
  target.searchParams.set("path", path);
  target.searchParams.set("limit", String(MAX_CONTENT + 1));

  let upstream: globalThis.Response;
  try {
    upstream = await fetch(target, {
      headers: { Authorization: `Bearer ${API_TOKEN}` },
    });
  } catch {
    return {
      content: null,
      chars: 0,
      truncated: false,
      reason: `the API is not reachable at ${API_URL}`,
    };
  }
  if (!upstream.ok) {
    const detail = (await upstream.json().catch(() => null)) as {
      detail?: string;
    } | null;
    return {
      content: null,
      chars: 0,
      truncated: false,
      reason: detail?.detail ?? `the API answered ${upstream.status}`,
    };
  }
  const body = (await upstream.json()) as { content: string };
  const truncated = body.content.length > MAX_CONTENT;
  return {
    content: truncated ? body.content.slice(0, MAX_CONTENT) : body.content,
    chars: truncated ? MAX_CONTENT : body.content.length,
    truncated,
    reason: null,
  };
}
