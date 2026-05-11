import { NextRequest, NextResponse } from 'next/server';
import { getIronSession } from 'iron-session';
import type { SessionData } from '@/lib/session';

const sessionOptions = {
  password: process.env.SESSION_SECRET ?? 'fallback-dev-secret-change-in-production!!',
  cookieName: 'logistica_session',
  cookieOptions: { secure: process.env.NODE_ENV === 'production' },
};

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Public routes
  if (pathname.startsWith('/login') || pathname.startsWith('/api/auth') || pathname.startsWith('/_next') || pathname === '/manifest.json') {
    return NextResponse.next();
  }

  const res = NextResponse.next();
  const session = await getIronSession<SessionData>(req, res, sessionOptions);

  if (!session.userId) {
    return NextResponse.redirect(new URL('/login', req.url));
  }

  // Role guard
  if (pathname.startsWith('/admin') && session.role !== 'ADMIN') {
    return NextResponse.redirect(new URL('/driver', req.url));
  }
  if (pathname.startsWith('/driver') && session.role !== 'DRIVER') {
    return NextResponse.redirect(new URL('/admin', req.url));
  }

  return res;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|icons).*)'],
};
