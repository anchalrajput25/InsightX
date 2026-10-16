"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { MessageSquare, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/api/client";
import { stripEmoji, timeAgo } from "@/lib/format";
import { isPlaceholderChat, useChatMutations, useChats } from "@/hooks/use-chats";
import type { ChatListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

function groupLabel(updated: string): string {
  const date = new Date(updated);
  if (Number.isNaN(date.getTime())) return "Older";
  const days = (Date.now() - date.getTime()) / 86_400_000;
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return "Today";
  if (days < 2) return "Yesterday";
  if (days < 7) return "This week";
  if (days < 31) return "This month";
  return "Older";
}

export function ChatList({ onNavigate }: { onNavigate?: () => void }) {
  const chats = useChats();
  const { rename, remove } = useChatMutations();
  const params = useSearchParams();
  const current = params.get("chat");
  const [renaming, setRenaming] = React.useState<ChatListItem | null>(null);
  const [deleting, setDeleting] = React.useState<ChatListItem | null>(null);
  const [title, setTitle] = React.useState("");

  if (chats.isPending) {
    return (
      <div className="space-y-1.5 px-2" aria-busy>
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-7 w-full" />
        ))}
      </div>
    );
  }
  if (chats.isError) {
    return (
      <p className="px-3 text-xs text-muted-foreground">
        Chats could not load.{" "}
        <button type="button" className="underline underline-offset-2" onClick={() => void chats.refetch()}>
          Retry
        </button>
      </p>
    );
  }
  const visible = (chats.data ?? []).filter((chat) => !isPlaceholderChat(chat));
  if (visible.length === 0) {
    return <p className="px-3 text-xs leading-relaxed text-muted-foreground">Your chats appear here after your first question.</p>;
  }

  const groups: { label: string; items: ChatListItem[] }[] = [];
  for (const chat of visible) {
    const label = groupLabel(chat.updated_at);
    const group = groups.find((g) => g.label === label);
    if (group) group.items.push(chat);
    else groups.push({ label, items: [chat] });
  }

  return (
    <>
      <nav aria-label="Chats" className="space-y-3">
        {groups.map((group) => (
          <div key={group.label}>
            <p className="px-3 pb-1 text-[11px] font-medium text-muted-foreground/80">{group.label}</p>
            <ul className="space-y-px">
              {group.items.map((chat) => {
                const active = chat.thread_id === current;
                return (
                  <li key={chat.thread_id} className="group/chat relative">
                    <Link
                      href={`/ask?chat=${encodeURIComponent(chat.thread_id)}`}
                      onClick={onNavigate}
                      title={`${stripEmoji(chat.title)}, updated ${timeAgo(chat.updated_at)}`}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex h-8 items-center gap-2 rounded-md pl-3 pr-8 text-[13px] text-sidebar-foreground/85 transition-colors hover:bg-sidebar-active hover:text-foreground",
                        active && "bg-sidebar-active text-foreground",
                      )}
                    >
                      <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{stripEmoji(chat.title)}</span>
                    </Link>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          type="button"
                          aria-label={`Options for ${stripEmoji(chat.title)}`}
                          className="absolute right-1 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground focus-visible:opacity-100 group-hover/chat:opacity-100 data-[state=open]:opacity-100"
                        >
                          <MoreHorizontal className="size-3.5" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" side="right" className="w-40">
                        <DropdownMenuItem
                          onSelect={() => {
                            setTitle(stripEmoji(chat.title));
                            setRenaming(chat);
                          }}
                        >
                          <Pencil /> Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem className="text-critical focus:text-critical" onSelect={() => setDeleting(chat)}>
                          <Trash2 /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <Dialog open={renaming !== null} onOpenChange={(open) => !open && setRenaming(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Rename chat</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!renaming) return;
              rename(renaming.thread_id, title)
                .then(() => setRenaming(null))
                .catch((error: unknown) => toast.error("Could not rename the chat", { description: errorMessage(error) }));
            }}
            className="space-y-4"
          >
            <Input aria-label="Chat name" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={80} autoFocus />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setRenaming(null)}>
                Cancel
              </Button>
              <Button type="submit" disabled={!title.trim()}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
            <AlertDialogDescription>
              &ldquo;{deleting ? stripEmoji(deleting.title) : ""}&rdquo; is removed from your chat list. Your question history keeps its entries.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              destructive
              onClick={() => {
                if (!deleting) return;
                remove(deleting.thread_id)
                  .then(() => toast.success("Chat deleted"))
                  .catch((error: unknown) => toast.error("Could not delete the chat", { description: errorMessage(error) }));
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
