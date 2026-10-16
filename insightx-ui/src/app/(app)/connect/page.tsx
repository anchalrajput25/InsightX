import type { Metadata } from "next";
import { ConnectScreen } from "@/components/connect/connect-screen";

export const metadata: Metadata = { title: "Database" };

export default function ConnectPage() {
  return <ConnectScreen />;
}
