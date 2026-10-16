"use client";

import * as React from "react";
import { ArrowUp, Compass, Loader2, MessageSquareText, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/ui/tooltip";
import type { Mode } from "@/components/ask/turns";
import { cn } from "@/lib/utils";

const MODES: { value: Mode; label: string; icon: typeof Wand2; hint: string }[] = [
  { value: "auto", label: "Auto", icon: Wand2, hint: "Picks Ask or Investigate from your question" },
  { value: "ask", label: "Ask", icon: MessageSquareText, hint: "Get an answer with SQL, a table and a chart" },
  { value: "investigate", label: "Investigate", icon: Compass, hint: "Find out why a number changed" },
];

export function Composer({
  onSend,
  busy,
  disabled,
  mode,
  onModeChange,
  autoFocus,
}: {
  onSend: (question: string) => void;
  busy: boolean;
  disabled?: boolean;
  mode: Mode;
  onModeChange: (mode: Mode) => void;
  autoFocus?: boolean;
}) {
  const [text, setText] = React.useState("");
  const ref = React.useRef<HTMLTextAreaElement>(null);

  React.useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [text]);

  const send = () => {
    const question = text.trim();
    if (!question || busy || disabled) return;
    onSend(question);
    setText("");
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
      className={cn(
        "fx-surface rounded-2xl border bg-card/90 shadow-[0_8px_30px_-12px_rgba(0,0,0,0.45)] backdrop-blur-md transition-[border-color,box-shadow] duration-200 focus-within:border-primary/40 focus-within:shadow-[0_0_0_4px_color-mix(in_oklch,var(--primary)_10%,transparent),0_12px_40px_-12px_var(--glow)]",
        busy && "fx-beam",
      )}
    >
      <label htmlFor="question" className="sr-only">
        Your question
      </label>
      <textarea
        id="question"
        ref={ref}
        rows={1}
        value={text}
        autoFocus={autoFocus}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            send();
          }
        }}
        placeholder={mode === "investigate" ? "Why did revenue drop last month?" : "Ask anything about your data…"}
        className="scrollbar-thin block max-h-[200px] min-h-[52px] w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[15px] leading-relaxed placeholder:text-muted-foreground/70 focus:outline-none disabled:cursor-not-allowed"
      />
      <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5 pt-1">
        <div role="radiogroup" aria-label="Answer mode" className="flex items-center gap-0.5 rounded-lg bg-muted/60 p-0.5">
          {MODES.map((item) => {
            const active = mode === item.value;
            return (
              <Hint key={item.value} label={item.hint}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => onModeChange(item.value)}
                  className={cn(
                    "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground",
                    active && "bg-card text-foreground shadow-[inset_0_1px_0_var(--edge),0_1px_3px_rgb(0_0_0/0.25)] [&_svg]:text-primary",
                  )}
                >
                  <item.icon className="size-3.5" />
                  <span className="hidden sm:inline">{item.label}</span>
                </button>
              </Hint>
            );
          })}
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden text-[11px] text-muted-foreground md:inline">Enter to send, Shift+Enter for a new line</span>
          <Button type="submit" size="icon-sm" className="rounded-lg cursor-pointer" disabled={!text.trim() || busy || disabled} aria-label="Send question">
            {busy ? <Loader2 className="animate-spin" /> : <ArrowUp />}
          </Button>
        </div>
      </div>
    </form>
  );
}
