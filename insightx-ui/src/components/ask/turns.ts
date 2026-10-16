// The turns of a chat as shown on the Ask page. Full answers (tables,
// charts, investigations) are kept for this browser tab only; the backend
// keeps the text of every chat, which is what older chats show.

import type { ChatMessage, InvestigationReport, QueryResponse } from "@/lib/types";

export type Mode = "auto" | "ask" | "investigate";

export type Turn =
  | { id: string; question: string; state: "pending"; kind: "answer" | "investigation"; startedAt: number }
  | { id: string; question: string; state: "error"; kind: "answer" | "investigation"; error: string; lostConnection?: boolean }
  | { id: string; question: string; state: "done"; kind: "answer"; result: QueryResponse }
  | { id: string; question: string; state: "done"; kind: "investigation"; result: InvestigationReport }
  | { id: string; question: string; state: "text"; answer: string };

const memory = new Map<string, Turn[]>();
const KEY = (threadId: string) => `insightx.turns.${threadId}`;

export function loadTurns(threadId: string): Turn[] | null {
  const cached = memory.get(threadId);
  if (cached) return cached;
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(KEY(threadId));
    if (!raw) return null;
    const turns = JSON.parse(raw) as Turn[];
    memory.set(threadId, turns);
    return turns;
  } catch {
    return null;
  }
}

export function saveTurns(threadId: string, turns: Turn[]): void {
  const settled = turns.filter((turn) => turn.state !== "pending");
  memory.set(threadId, settled);
  try {
    window.sessionStorage.setItem(KEY(threadId), JSON.stringify(settled));
  } catch {
    // too large or blocked: the in-memory copy still works for this tab
  }
}

/** Old chats: pair the saved user and assistant messages into text turns. */
export function turnsFromHistory(history: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  let pending: string | null = null;
  history.forEach((message, index) => {
    if (message.role === "user") {
      if (pending !== null) turns.push({ id: `h${index}`, question: pending, state: "text", answer: "" });
      pending = message.content;
    } else if (pending !== null) {
      turns.push({ id: `h${index}`, question: pending, state: "text", answer: message.content });
      pending = null;
    }
  });
  if (pending !== null) turns.push({ id: "hlast", question: pending, state: "text", answer: "" });
  return turns;
}
