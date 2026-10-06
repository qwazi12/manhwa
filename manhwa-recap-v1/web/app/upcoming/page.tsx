"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Upcoming moved into Library → Scheduled for processing (owner, 2026-10-06). */
export default function Upcoming() {
  const router = useRouter();
  useEffect(() => { router.replace("/library?view=scheduled"); }, [router]);
  return null;
}
