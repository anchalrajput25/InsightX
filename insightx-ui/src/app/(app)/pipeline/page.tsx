import type { Metadata } from "next";
import { PipelineScreen } from "@/components/pipeline/pipeline-screen";

export const metadata: Metadata = { title: "Pipeline" };

export default function Page() {
  return <PipelineScreen />;
}
