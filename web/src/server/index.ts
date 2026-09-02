import express from "express";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { HttpError, route } from "./args.js";
import { dbPool } from "./db.js";
import { makeGuard } from "./guard.js";
import { proxyViewer, VIEWER_ROUTES } from "./proxy.js";
import { nodesRouter } from "./routes/nodes.js";
import { plansRouter } from "./routes/plans.js";
import { projectsRouter } from "./routes/projects.js";
import { settingsRouter } from "./routes/settings.js";
import { suggestionsRouter } from "./routes/suggestions.js";

const PORT = Number(process.env.PORT ?? 3002);
const CLIENT_DIR = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "client",
);

const app = express();
app.disable("x-powered-by");

const api = express.Router();
api.use(makeGuard(PORT));
api.use(express.json({ limit: "2mb" }));

api.get(
  "/health",
  route(async (_req, res) => {
    await dbPool.query("SELECT 1");
    res.json({ status: "ok", db: "ok" });
  }),
);

api.use(projectsRouter);
api.use(nodesRouter);
api.use(plansRouter);
api.use(suggestionsRouter);
api.use(settingsRouter);

api.use((_req, res) => {
  res.status(404).json({ error: "No such endpoint" });
});

api.use(
  (
    error: unknown,
    _req: express.Request,
    res: express.Response,
    _next: express.NextFunction,
  ) => {
    if (error instanceof HttpError) {
      res.status(error.status).json({ error: error.message });
      return;
    }
    console.error("Request failed:", error);
    res.status(503).json({ error: String(error) });
  },
);

app.use("/api", api);

for (const viewerRoute of VIEWER_ROUTES) {
  app.get(viewerRoute, route(proxyViewer));
}

app.use(express.static(CLIENT_DIR, { index: false }));

// A probe for something this stack does not serve must fail as a 404 rather
// than as a page. `/.well-known/oauth-*` is the case that bit: a client
// checking whether the MCP server is OAuth-protected got 200 and HTML, and
// broke on parsing it instead of concluding that it is not.
app.get("/.well-known/*", (_req, res) => {
  res.status(404).type("text/plain").send("Not found");
});

// Every other address is a client route, and the client owns the 404.
app.get("*", (_req, res) => {
  res.sendFile(path.join(CLIENT_DIR, "index.html"));
});

const server = app.listen(PORT, "0.0.0.0", () => {
  console.log(`Dashboard listening on port ${PORT}`);
});

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => {
    server.close(() => {
      dbPool.end().finally(() => process.exit(0));
    });
  });
}
