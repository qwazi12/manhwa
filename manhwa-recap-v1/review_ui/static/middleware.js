import { NextResponse } from 'next/server';

export function middleware(req) {
  const basicAuth = req.headers.get('authorization');
  const basicAuthUser = process.env.BASIC_AUTH_USER;
  const basicAuthPassword = process.env.BASIC_AUTH_PASSWORD;

  if (basicAuthUser && basicAuthPassword) {
    if (basicAuth) {
      const authValue = basicAuth.split(' ')[1];
      try {
        const decoded = atob(authValue);
        const [user, pwd] = decoded.split(':');

        if (user === basicAuthUser && pwd === basicAuthPassword) {
          // Authentication successful. Proceed and inject SHARED_SECRET
          const requestHeaders = new Headers(req.headers);
          if (process.env.SHARED_SECRET) {
            requestHeaders.set('x-shared-secret', process.env.SHARED_SECRET);
          }
          return NextResponse.next({
            request: {
              headers: requestHeaders,
            },
          });
        }
      } catch (e) {
        // Silent catch for bad/malformed auth header formats
      }
    }

    // Return 401 response with WWW-Authenticate header to prompt browser login
    return new Response('Authentication Required', {
      status: 401,
      headers: {
        'WWW-Authenticate': 'Basic realm="Recap Studio"',
      },
    });
  }

  // If Basic Auth is not configured (e.g. local dev), allow request to pass through
  return NextResponse.next();
}

export const config = {
  matcher: ['/:path*'],
};
