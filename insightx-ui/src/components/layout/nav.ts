import {
  Activity,
  BellRing,
  FlaskConical,
  Gauge,
  ScanSearch,
  Split,
  ThumbsUp,
  Cpu,
  Database,
  History,
  LayoutDashboard,
  MessageSquareText,
  UserRound,
  Workflow,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  description: string;
  /** Needs a connected database to be useful. */
  needsDatabase?: boolean;
}

export const NAV_MAIN: NavItem[] = [
  { href: "/ask", label: "Ask", icon: MessageSquareText, description: "Ask questions and investigate changes", needsDatabase: true },
  { href: "/pipeline", label: "Pipeline", icon: Workflow, description: "See every step behind an answer" },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, description: "Overview of the whole database", needsDatabase: true },
  { href: "/alerts", label: "Alerts", icon: BellRing, description: "Watch metrics and get told when they move" },
  { href: "/history", label: "History", icon: History, description: "Every question you asked" },
];

export const NAV_MONITOR: NavItem[] = [
  { href: "/traces", label: "Traces", icon: Activity, description: "Step-by-step timing of each run" },
  { href: "/usage", label: "AI usage", icon: Cpu, description: "AI calls, tokens and model routes" },
];

export const NAV_LLMOPS: NavItem[] = [
  { href: "/monitor", label: "Monitor", icon: Gauge, description: "Live health, SLOs and model status" },
  { href: "/evals", label: "Evals", icon: FlaskConical, description: "Accuracy on golden questions" },
  { href: "/hallucination", label: "Hallucination", icon: ScanSearch, description: "Answers checked against their data" },
  { href: "/quality", label: "Quality", icon: ThumbsUp, description: "Feedback loop and review queue" },
  { href: "/routing", label: "Smart routing", icon: Split, description: "Pick the best free model per question" },
];

export const NAV_SETTINGS: NavItem[] = [
  { href: "/connect", label: "Database", icon: Database, description: "Connect or switch databases" },
  { href: "/account", label: "Account", icon: UserRound, description: "Your profile and password" },
];

export const ALL_NAV = [...NAV_MAIN, ...NAV_MONITOR, ...NAV_LLMOPS, ...NAV_SETTINGS];

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
