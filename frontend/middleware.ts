import { getToken } from "next-auth/jwt";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export async function middleware(request: NextRequest) {
  const token = await getToken({ req: request, secret: process.env.NEXTAUTH_SECRET });

  if (!token || token.error === "RefreshAccessTokenError") {
    // request.url's origin is not reliable when the app runs behind a remapped
    // host port (e.g. FRONTEND_PORT=3100 while Next.js listens on 3000 inside
    // the container): Next.js resolves it from the container's own bind port,
    // not the incoming Host header, producing a redirect to a port nothing is
    // listening on. NEXTAUTH_URL is the one env var already correctly wired to
    // the browser-facing origin (see docker-compose.yml), so the redirect's
    // origin is built from it; the path/query come from request.nextUrl, which
    // is unaffected by the host/port issue.
    const baseUrl = process.env.NEXTAUTH_URL ?? request.url;
    const callbackUrl = new URL(
      request.nextUrl.pathname + request.nextUrl.search,
      baseUrl
    );
    // authOptions.pages.signIn (lib/auth.ts) points at this app's own /login
    // page, not NextAuth's built-in one -- redirecting to /api/auth/signin
    // here would land the browser on a page NextAuth never renders once a
    // custom signIn page is configured, showing up as blank.
    const signInUrl = new URL("/login", baseUrl);
    signInUrl.searchParams.set("callbackUrl", callbackUrl.toString());
    return NextResponse.redirect(signInUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico|login|signup).*)"],
};
