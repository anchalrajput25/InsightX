import type { Metadata } from "next";
import { MonitorScreen } from "@/components/llmops/monitor-screen";

export const metadata: Metadata = { title: "Monitor" };

export default function Page() {
  return <MonitorScreen />;
}
