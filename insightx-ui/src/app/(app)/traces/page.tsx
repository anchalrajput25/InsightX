import type { Metadata } from "next";
import { TracesScreen } from "@/components/traces/traces-screen";

export const metadata: Metadata = { title: "Traces" };

export default function Page() {
  return <TracesScreen />;
}
