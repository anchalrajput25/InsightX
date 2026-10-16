import type { Metadata } from "next";
import { QualityScreen } from "@/components/llmops/quality-screen";

export const metadata: Metadata = { title: "Quality" };

export default function Page() {
  return <QualityScreen />;
}
