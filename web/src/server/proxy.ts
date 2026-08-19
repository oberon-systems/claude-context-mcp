import type { Request, Response } from "express";
import { Readable } from "node:stream";

const VIEWER_URL = process.env.VIEWER_URL ?? "http://viewer:3001";

// The viewer rewrites the drawing library's script tag to an absolute path, so
// the iframe asks THIS origin for it. Proxying the page without the library
// leaves a blank frame.
export const VIEWER_ROUTES = ["/graph", "/vis-network.min.js"] as const;

function upstreamFor(req: Request): string {
  const base = new URL(VIEWER_URL);
  const target = new URL(req.path === "/graph" ? "/graph" : req.path, base);
  const project = req.query.project;
  if (typeof project === "string" && project !== "") {
    target.searchParams.set("project", project);
  }
  return target.toString();
}

export async function proxyViewer(req: Request, res: Response): Promise<void> {
  let upstream: globalThis.Response;
  try {
    upstream = await fetch(upstreamFor(req), { redirect: "follow" });
  } catch (error) {
    res
      .status(502)
      .type("text/plain")
      .send(
        `The viewer service is not reachable at ${VIEWER_URL}. ` +
          `Is it running? (${String(error)})`,
      );
    return;
  }

  res.status(upstream.status);
  const type = upstream.headers.get("content-type");
  if (type !== null) {
    res.setHeader("Content-Type", type);
  }
  const cache = upstream.headers.get("cache-control");
  if (cache !== null) {
    res.setHeader("Cache-Control", cache);
  }

  if (upstream.body === null) {
    res.end();
    return;
  }
  Readable.fromWeb(
    upstream.body as Parameters<typeof Readable.fromWeb>[0],
  ).pipe(res);
}
