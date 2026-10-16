import type { Metadata } from "next";
import { RoutingScreen } from "@/components/llmops/routing-screen";

export const metadata: Metadata = { title: "Smart routing" };

export default function Page() {
  return <RoutingScreen />;
}
