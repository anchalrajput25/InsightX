"use client";

import * as React from "react";
import { Mic, Square, Volume2 } from "lucide-react";
import { toast } from "sonner";
import { Hint } from "@/components/ui/tooltip";
import { useSpeaker } from "@/lib/speech";
import { cn } from "@/lib/utils";

/** Small round mic button for the question box. */
export function MicButton({
  listening,
  supported,
  disabled,
  onClick,
  className,
}: {
  listening: boolean;
  supported: boolean;
  disabled?: boolean;
  onClick: () => void;
  className?: string;
}) {
  const label = !supported ? "Voice works in Chrome or Edge" : listening ? "Stop listening" : "Speak your question";
  return (
    <Hint label={label}>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={listening}
        className={cn(
          "relative inline-flex size-8 cursor-pointer items-center justify-center rounded-lg border bg-card text-muted-foreground transition-colors duration-200 hover:border-primary/40 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50",
          listening && "border-critical/50 bg-critical/10 text-critical hover:text-critical",
          className,
        )}
      >
        {listening && <span aria-hidden className="absolute inset-0 animate-ping rounded-lg bg-critical/25" />}
        {listening ? <Square className="relative size-3.5 fill-current" /> : <Mic className="relative size-4" />}
      </button>
    </Hint>
  );
}

/** Shows a voice error once, as a toast. */
export function useVoiceErrorToast(error: string) {
  React.useEffect(() => {
    if (error) toast.error(error);
  }, [error]);
}

/** "Listen" button that reads an answer out loud. */
export function ListenButton({ text, className }: { text: string; className?: string }) {
  const { speaking, play, stop } = useSpeaker();
  if (!text.trim()) return null;
  return (
    <button
      type="button"
      onClick={() => (speaking ? stop() : play(text))}
      aria-pressed={speaking}
      className={cn(
        "inline-flex cursor-pointer items-center gap-1.5 rounded-full border bg-surface px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground",
        speaking && "border-primary/40 text-primary",
        className,
      )}
    >
      {speaking ? <Square className="size-3 fill-current" /> : <Volume2 className="size-3.5" />}
      {speaking ? "Stop" : "Listen"}
    </button>
  );
}
