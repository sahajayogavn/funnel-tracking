import { NextRequest, NextResponse } from "next/server";
import { correctAnswers, createSession, SESSION_COOKIE, SESSION_MAX_AGE } from "@/lib/auth";

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
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(SESSION_COOKIE, createSession(), {
    httpOnly: true,
    secure: request.nextUrl.protocol === "https:",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  return response;
}
