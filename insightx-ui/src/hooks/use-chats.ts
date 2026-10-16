"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { questionsApi } from "@/lib/api/questions";
import { truncate } from "@/lib/format";
import type { ChatListItem } from "@/lib/types";

const KEY = ["chats"] as const;
const PLACEHOLDER_TITLES = new Set(["New Chat", "New Investigation", ""]);

export function chatTitle(question: string): string {
  return truncate(question.replace(/\s+/g, " "), 60);
}

/** The chat list, shared with the Streamlit app (same /chats store). */
export function useChats(enabled = true) {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => (await questionsApi.chats()).chats,
    enabled,
    select: (chats) => [...chats].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))),
  });
}

export function useChatMutations() {
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: async (change: (chats: ChatListItem[]) => ChatListItem[]) => {
      const current = (await questionsApi.chats()).chats;
      return (await questionsApi.saveChats(change(current))).chats;
    },
    onSuccess: (chats) => queryClient.setQueryData(KEY, chats),
  });

  /** After a question: put the chat at the top, named after its first question. */
  const touch = (threadId: string, question: string) =>
    save.mutateAsync((chats) => {
      const now = new Date().toISOString();
      const existing = chats.find((chat) => chat.thread_id === threadId);
      if (existing) {
        return chats.map((chat) =>
          chat.thread_id === threadId
            ? { ...chat, updated_at: now, title: PLACEHOLDER_TITLES.has(chat.title) ? chatTitle(question) : chat.title }
            : chat,
        );
      }
      return [{ thread_id: threadId, title: chatTitle(question), created_at: now, updated_at: now }, ...chats];
    });

  const rename = (threadId: string, title: string) =>
    save.mutateAsync((chats) => chats.map((chat) => (chat.thread_id === threadId ? { ...chat, title: title.trim() || chat.title } : chat)));

  const remove = (threadId: string) => save.mutateAsync((chats) => chats.filter((chat) => chat.thread_id !== threadId));

  return { touch, rename, remove, saving: save.isPending };
}

export function isPlaceholderChat(chat: ChatListItem): boolean {
  return PLACEHOLDER_TITLES.has(chat.title);
}
