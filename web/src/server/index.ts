import express from "express";

const PORT = Number(process.env.PORT ?? 3002);

const app = express();

app.get("/api/health", (_req, res) => {
  res.json({ status: "ok" });
});

const server = app.listen(PORT, "0.0.0.0", () => {
  console.log(`Dashboard listening on port ${PORT}`);
});

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => {
    server.close(() => process.exit(0));
  });
}
