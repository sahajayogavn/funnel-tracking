import { NextRequest, NextResponse } from "next/server";
import { validSession } from "@/lib/auth";
import { sessionCookie } from "../route";

export async function POST(request: NextRequest) {
  let session: unknown;
  try {
    ({ session } = await request.json());
  } catch {
    return NextResponse.json({ error: "Thông tin phiên không hợp lệ." }, { status: 400 });
  }
  if (typeof session !== "string" || !validSession(session)) {
    return NextResponse.json({ error: "Phiên đã hết hạn." }, { status: 401, headers: { "Cache-Control": "no-store" } });
  }
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(sessionCookie(request, session));
  return response;
}
