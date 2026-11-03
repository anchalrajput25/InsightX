import type { Metadata } from "next";
import { WhatIfScreen } from "@/components/whatif/whatif-screen";

export const metadata: Metadata = { title: "What-if" };

export default function WhatIfPage() {
  return <WhatIfScreen />;
}
