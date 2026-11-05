import type { Metadata } from "next";
import { HomeGate } from "@/components/landing/home-gate";

export const metadata: Metadata = {
  title: { absolute: "InsightX · Ask your data anything" },
  description:
    "Plain-English questions become safe, read-only SQL, every number is checked against the real rows, and InsightX explains why a metric changed.",
};

export default function Home() {
  return <HomeGate />;
}
