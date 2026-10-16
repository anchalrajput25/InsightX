"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { MessageSquare, Moon, Plus, Search, Sparkles, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut } from "@/components/ui/command";
import { ALL_NAV } from "@/components/layout/nav";
import { isPlaceholderChat, useChats } from "@/hooks/use-chats";
import { stripEmoji, timeAgo } from "@/lib/format";

export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const { resolvedTheme, setTheme } = useTheme();
  const chats = useChats(open);
  const [text, setText] = React.useState("");

  React.useEffect(() => {
    if (!open) setText("");
  }, [open]);

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };

  const question = text.trim();
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Search pages and chats, or type a question…" value={text} onValueChange={setText} />
      <CommandList>
        <CommandEmpty>Nothing found.</CommandEmpty>
        {question.length > 2 && (
          <CommandGroup heading="Ask">
            <CommandItem value={`ask ${question}`} onSelect={() => go(`/ask?q=${encodeURIComponent(question)}`)}>
              <Sparkles className="text-primary" />
              <span className="truncate">Ask: {question}</span>
              <CommandShortcut>Enter</CommandShortcut>
            </CommandItem>
          </CommandGroup>
        )}
        <CommandGroup heading="Go to">
          <CommandItem value="new chat" onSelect={() => go("/ask")}>
            <Plus /> New chat
          </CommandItem>
          {ALL_NAV.map((item) => (
            <CommandItem key={item.href} value={`${item.label} ${item.description}`} onSelect={() => go(item.href)}>
              <item.icon />
              <span>{item.label}</span>
              <span className="ml-2 truncate text-xs text-muted-foreground">{item.description}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        {(chats.data ?? []).some((chat) => !isPlaceholderChat(chat)) && (
          <CommandGroup heading="Chats">
            {(chats.data ?? [])
              .filter((chat) => !isPlaceholderChat(chat))
              .slice(0, 30)
              .map((chat) => (
                <CommandItem key={chat.thread_id} value={`chat ${chat.title} ${chat.thread_id}`} onSelect={() => go(`/ask?chat=${encodeURIComponent(chat.thread_id)}`)}>
                  <MessageSquare />
                  <span className="truncate">{stripEmoji(chat.title)}</span>
                  <CommandShortcut>{timeAgo(chat.updated_at)}</CommandShortcut>
                </CommandItem>
              ))}
          </CommandGroup>
        )}
        <CommandGroup heading="Preferences">
          <CommandItem
            value="theme toggle dark light"
            onSelect={() => {
              setTheme(resolvedTheme === "dark" ? "light" : "dark");
              onOpenChange(false);
            }}
          >
            {resolvedTheme === "dark" ? <Sun /> : <Moon />}
            Switch to {resolvedTheme === "dark" ? "light" : "dark"} theme
          </CommandItem>
        </CommandGroup>
      </CommandList>
      <div className="flex items-center gap-3 border-t px-3 py-2 text-[11px] text-muted-foreground">
        <Search className="size-3" />
        <span>Arrows to move, Enter to open, Esc to close</span>
      </div>
    </CommandDialog>
  );
}
