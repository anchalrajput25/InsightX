import type { Metadata } from "next";
import { EvalsScreen } from "@/components/llmops/evals-screen";

export const metadata: Metadata = { title: "Evals" };

export default function Page() {
  return <EvalsScreen />;
}
