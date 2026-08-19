import pg from "pg";

const { Pool } = pg;

export const dbPool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

// Without a listener an error on an idle connection reaches the process as an
// uncaught exception and takes the dashboard down with it.
dbPool.on("error", (error) => {
  console.error("Idle database client error:", error);
});

/** Read a count() back as a number. */
export function count(value: string | number | null): number {
  return value === null ? 0 : Number(value);
}
