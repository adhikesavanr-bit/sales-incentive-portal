/**
 * Proxies /api/* to the backend, at request time.
 *
 * Why a route handler rather than a rewrite in next.config.mjs: `rewrites()` is
 * evaluated during `next build` and serialised into the routes manifest, so
 * `process.env` there holds whatever existed at build time — nothing, in a
 * container build. A route handler runs per request, inside the running
 * server, so it reads the real environment.
 *
 * The browser therefore only ever talks to this origin. Nothing about the API
 * location is compiled into the bundle, and there are no cross-origin
 * requests to configure.
 */
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

// Hop-by-hop and host-specific headers must not be forwarded.
const STRIP = new Set([
  "host", "connection", "keep-alive", "transfer-encoding",
  "upgrade", "proxy-authenticate", "proxy-authorization", "te", "trailer",
  "content-length",
]);

async function proxy(request: NextRequest, path: string[]) {
  const target = `${API_BASE_URL}/api/${path.join("/")}${request.nextUrl.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIP.has(key.toLowerCase())) headers.set(key, value);
  });

  const hasBody = !["GET", "HEAD"].includes(request.method);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      cache: "no-store",
    });
  } catch (err) {
    // A failure here means the API is unreachable, not that the request was
    // rejected. Say so, rather than letting the browser report a bare
    // "Failed to fetch".
    console.error("API proxy error", { target, err });
    return NextResponse.json(
      { detail: "The incentive service is not reachable. Try again shortly." },
      { status: 502 },
    );
  }

  const out = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP.has(key.toLowerCase())) out.set(key, value);
  });

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: out,
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(r: NextRequest, c: Ctx) {
  return proxy(r, (await c.params).path);
}
export async function POST(r: NextRequest, c: Ctx) {
  return proxy(r, (await c.params).path);
}
export async function PUT(r: NextRequest, c: Ctx) {
  return proxy(r, (await c.params).path);
}
export async function PATCH(r: NextRequest, c: Ctx) {
  return proxy(r, (await c.params).path);
}
export async function DELETE(r: NextRequest, c: Ctx) {
  return proxy(r, (await c.params).path);
}
