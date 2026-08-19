import type { Request, RequestHandler, Response } from "express";

export const MAX_LIMIT = 200;
export const DEFAULT_LIMIT = 50;

/** An error carrying the status the route should answer with. */
export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function badRequest(message: string): HttpError {
  return new HttpError(400, message);
}

export function notFound(message: string): HttpError {
  return new HttpError(404, message);
}

function single(value: unknown): string | undefined {
  if (typeof value === "string") {
    return value;
  }
  // Express turns a repeated parameter into an array. Take the last one
  // rather than refusing: a stale link is not worth an error page.
  if (Array.isArray(value) && typeof value[value.length - 1] === "string") {
    return value[value.length - 1] as string;
  }
  return undefined;
}

export function readQuery(req: Request, name: string): string | null {
  const value = single(req.query[name]);
  if (value === undefined || value.trim() === "") {
    return null;
  }
  return value;
}

export function requireQuery(req: Request, name: string): string {
  const value = readQuery(req, name);
  if (value === null) {
    throw badRequest(`Query parameter "${name}" is required`);
  }
  return value;
}

export function readFlag(req: Request, name: string): boolean {
  const value = readQuery(req, name);
  return value === "1" || value === "true";
}

export function readLimit(req: Request): number {
  const raw = readQuery(req, "limit");
  if (raw === null) {
    return DEFAULT_LIMIT;
  }
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) {
    throw badRequest('Query parameter "limit" must be a positive integer');
  }
  return Math.min(value, MAX_LIMIT);
}

export function readOffset(req: Request): number {
  const raw = readQuery(req, "offset");
  if (raw === null) {
    return 0;
  }
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) {
    throw badRequest('Query parameter "offset" must be zero or more');
  }
  return value;
}

export function readBodyString(
  body: unknown,
  name: string,
): string | undefined {
  if (body === null || typeof body !== "object") {
    throw badRequest("A JSON object body is required");
  }
  const value = (body as Record<string, unknown>)[name];
  if (value === undefined) {
    return undefined;
  }
  if (typeof value !== "string") {
    throw badRequest(`Field "${name}" must be a string`);
  }
  return value;
}

export function requireBodyString(body: unknown, name: string): string {
  const value = readBodyString(body, name);
  if (value === undefined || value.trim() === "") {
    throw badRequest(`Field "${name}" is required`);
  }
  return value;
}

// Express 4 does not follow a rejected promise to the error handler, so every
// async route is wrapped rather than each one carrying its own try/catch.
export function route(
  handler: (req: Request, res: Response) => Promise<void>,
): RequestHandler {
  return (req, res, next) => {
    handler(req, res).catch(next);
  };
}
