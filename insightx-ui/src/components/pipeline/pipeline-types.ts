// Extra fields the backend's /query answer already carries for its
// pipeline view (see QueryResponse in backend/main.py). Kept here so the
// shared types file stays as it is.

import type { QueryResponse } from "@/lib/types";

export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

export interface CheckResult {
  valid?: boolean;
  safe?: boolean;
  status?: string;
  reason?: string;
  errors?: string[];
  warnings?: string[];
  details?: Record<string, Json>;
  [key: string]: unknown;
}

export interface Candidate {
  index: number;
  sql: string;
  status?: string;
  error?: string;
  row_count?: number;
  selected?: boolean;
  cluster?: number;
  [key: string]: unknown;
}

export interface PipelineResult extends QueryResponse {
  rewritten_query?: Json;
  query_variants?: string[];
  decomposition?: Json;
  retrieved_schema?: Json;
  graph_context?: Record<string, Json>;
  schema_linking?: Json;
  linked_schema?: Record<string, Json>;
  sql_candidates?: Candidate[];
  sql_selection?: Record<string, Json>;
  sanity?: Record<string, Json>;
  generated_sql?: string;
  corrected_sql?: string;
  safety_result?: CheckResult;
  validation_result?: CheckResult;
  result_validation?: CheckResult;
}

/** One question run shown on the Pipeline page. */
export type Run =
  | { id: string; question: string; source: "pipeline" | "ask"; at: number; state: "running" }
  | { id: string; question: string; source: "pipeline" | "ask"; at: number; state: "error"; error: string }
  | { id: string; question: string; source: "pipeline" | "ask"; at: number; state: "done"; result: PipelineResult };
