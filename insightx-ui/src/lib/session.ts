// Who is signed in, and which database is connected. Kept in this browser
// only (localStorage); the backend remains the source of truth.

import type { Connection, User } from "@/lib/types";

export interface Session {
  token?: string;
  expiresAt?: number;
  user?: User;
  /** Sign-in turned off on the backend: the name sent as X-InsightX-User. */
  localName?: string;
}

const SESSION_KEY = "insightx.session";
const CONNECTION_KEY = "insightx.connection";

function read<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage blocked: the app still works for this tab
  }
}

export function getSession(): Session | null {
  const session = read<Session>(SESSION_KEY);
  if (session?.token && session.expiresAt && session.expiresAt * 1000 < Date.now()) return null;
  return session;
}

export function setSession(session: Session | null): void {
  write(SESSION_KEY, session);
}

export function getConnection(): Connection | null {
  return read<Connection>(CONNECTION_KEY);
}

export function setConnection(connection: Connection | null): void {
  write(CONNECTION_KEY, connection);
}
