import { api } from "@/lib/api/client";
import type { ChatListItem, ChatMessage, InvestigationReport, QueryResponse } from "@/lib/types";

export const questionsApi = {
  ask: (connectionId: string, question: string, threadId: string, useCache = true) =>
    api.post<QueryResponse>("/query", {
      connection_id: connectionId,
      question,
      thread_id: threadId,
      use_cache: useCache,
    }),
  investigate: (connectionId: string, question: string, threadId: string) =>
    api.post<InvestigationReport>("/investigate", { connection_id: connectionId, question, thread_id: threadId }),
  looksLikeWhy: (question: string) => api.get<{ investigation: boolean }>("/investigate/route", { question }),
  threadHistory: (threadId: string, connectionId: string) =>
    api.get<{ thread_id: string; conversation_history: ChatMessage[] }>(`/threads/${threadId}/history`, {
      connection_id: connectionId,
    }),
  chats: () => api.get<{ chats: ChatListItem[] }>("/chats"),
  saveChats: (chats: ChatListItem[]) => api.put<{ chats: ChatListItem[] }>("/chats", { chats }),
};
