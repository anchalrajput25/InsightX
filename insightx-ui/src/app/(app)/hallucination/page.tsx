import type { Metadata } from "next";
import { HallucinationScreen } from "@/components/llmops/hallucination-screen";

export const metadata: Metadata = { title: "Hallucination" };

export default function Page() {
  return <HallucinationScreen />;
}
