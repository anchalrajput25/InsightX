import type { Metadata } from "next";
import { UsageScreen } from "@/components/usage/usage-screen";

export const metadata: Metadata = { title: "AI usage" };

export default function Page() {
  return <UsageScreen />;
}
