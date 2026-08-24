import type { NextFunction, Request, Response } from "express";

// The MCP server guards the mirror image of this: it refuses any request that
// carries an Origin at all, because no MCP client sends one. A browser sends
// one on every non-GET, so here the header is checked rather than rejected.
const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// Compose sets WEB_ALLOWED_HOSTS from GATEWAY_HOSTS in .env, so that is the
// variable to name when the check refuses a request behind the gateway.
function allowedHosts(port: number): Set<string> {
  const configured = process.env.WEB_ALLOWED_HOSTS;
  if (configured !== undefined && configured.trim() !== "") {
    return new Set(
      configured
        .split(",")
        .map((entry) => entry.trim().toLowerCase())
        .filter((entry) => entry !== ""),
    );
  }
  return new Set([
    `localhost:${port}`,
    `127.0.0.1:${port}`,
    `[::1]:${port}`,
    "web:3002",
  ]);
}

export function makeGuard(port: number) {
  const hosts = allowedHosts(port);
  const anyHost = hosts.has("*");

  return function guard(req: Request, res: Response, next: NextFunction): void {
    const host = (req.headers.host ?? "").toLowerCase();
    if (!anyHost && !hosts.has(host)) {
      res.status(403).json({
        error:
          `Host "${host}" is not allowed. Add it to GATEWAY_HOSTS in the ` +
          "stack's .env, then recreate the containers with `make down && " +
          "make up`; * there answers to any host. Running this service " +
          "outside compose, the variable read here is WEB_ALLOWED_HOSTS.",
      });
      return;
    }

    if (!WRITES.has(req.method)) {
      next();
      return;
    }

    // A same-origin write is the only kind this service accepts. Together with
    // the JSON content type below, that leaves no cross-site form able to
    // reach a route: a form cannot send application/json, and a fetch that
    // can is preflighted.
    const origin = req.headers.origin;
    if (typeof origin === "string" && origin !== "") {
      const expected = `${req.protocol}://${host}`;
      if (origin.toLowerCase() !== expected.toLowerCase()) {
        res
          .status(403)
          .json({ error: `Cross-origin write from "${origin}" refused` });
        return;
      }
    }

    const contentType = req.headers["content-type"] ?? "";
    if (!contentType.toLowerCase().startsWith("application/json")) {
      res
        .status(415)
        .json({ error: "Writes must carry a application/json body" });
      return;
    }

    next();
  };
}
