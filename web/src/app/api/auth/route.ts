import { NextRequest, NextResponse } from "next/server";
import { correctAnswers, createSession, SESSION_COOKIE, SESSION_MAX_AGE } from "@/lib/auth";

export function sessionCookie(request: NextRequest, session: string) {
  return {
    name: SESSION_COOKIE,
    value: session,
    httpOnly: true,
    secure: request.nextUrl.protocol === "https:",
    sameSite: "lax" as const,
    path: "/",
    maxAge: SESSION_MAX_AGE,
  };
}

export async function POST(request: NextRequest) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Thông tin không hợp lệ." }, { status: 400 });
  }
  if (!body || !correctAnswers(body.identity, body.technique)) {
    return NextResponse.json({ error: "Câu trả lời chưa đúng. Bạn vui lòng kiểm tra lại cả hai câu trả lời." }, { status: 401 });
  }
  const session = createSession();
  // The browser also keeps this signed value in localStorage so it can silently
  // restore the HttpOnly cookie if browser/dev-server state drops that cookie.
  const response = NextResponse.json({ ok: true, session }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(sessionCookie(request, session));
  return response;
}
