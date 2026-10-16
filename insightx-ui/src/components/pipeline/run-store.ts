// Runs made on the Pipeline page are kept for this browser tab, like Ask
// answers. Answers from Ask chats in this tab are read (never written) so
// their steps can be opened here too.

import type { PipelineResult, Run } from "@/components/pipeline/pipeline-types";

const KEY = "insightx.pipeline.runs";
const ASK_PREFIX = "insightx.turns.";
const MAX_RUNS = 25;

export function loadRuns(): Run[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(KEY);
    const runs = raw ? (JSON.parse(raw) as Run[]) : [];
    return runs.filter((run) => run.state !== "running");
  } catch {
    return [];
  }
}

export function saveRuns(runs: Run[]): void {
  try {
    const settled = runs.filter((run) => run.state !== "running").slice(0, MAX_RUNS);
    window.sessionStorage.setItem(KEY, JSON.stringify(settled));
  } catch {
    // too large or blocked: the runs still show until the page is left
  }
}

interface AskTurn {
  id: string;
  question: string;
  state: string;
  kind?: string;
  result?: PipelineResult;
}

/** Answers given on the Ask page in this tab, newest first. */
export function loadAskRuns(): Run[] {
  if (typeof window === "undefined") return [];
  const runs: Run[] = [];
  try {
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (!key || !key.startsWith(ASK_PREFIX)) continue;
      const turns = JSON.parse(window.sessionStorage.getItem(key) ?? "[]") as AskTurn[];
      turns.forEach((turn, position) => {
        if (turn.state === "done" && turn.kind === "answer" && turn.result) {
          runs.push({ id: `ask-${key.slice(ASK_PREFIX.length)}-${turn.id}`, question: turn.question, source: "ask", at: position, state: "done", result: turn.result });
        }
      });
    }
  } catch {
    return runs;
  }
  return runs.reverse();
}
