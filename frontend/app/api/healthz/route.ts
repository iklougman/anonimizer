import { NextResponse } from "next/server";
import { getHealthStatus } from "@/lib/health";

export function GET() {
  return NextResponse.json(getHealthStatus());
}
