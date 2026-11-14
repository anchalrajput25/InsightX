"use client";

// Voice in and voice out, using the browser's own free speech tools
// (Web Speech API). Works in Chrome and Edge. Nothing new to install.

import * as React from "react";

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
};

type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function speechLang(): string {
  if (typeof navigator === "undefined") return "en-IN";
  const lang = navigator.language || "en-IN";
  return lang.toLowerCase().startsWith("en") ? lang : "en-IN";
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Microphone is blocked. Click the lock icon in the address bar and allow the microphone.",
  "service-not-allowed": "Microphone is blocked. Click the lock icon in the address bar and allow the microphone.",
  "no-speech": "I did not hear anything. Tap the mic and try again.",
  "audio-capture": "No microphone was found on this device.",
  network: "Voice needs an internet connection. Please try again.",
};

export type ListenState = "idle" | "listening" | "unsupported";

/**
 * Listen once. `interim` is the live text while the person speaks;
 * `onFinal` gets the full sentence when they stop.
 */
export function useSpeechInput({ onFinal }: { onFinal: (text: string) => void }) {
  const [state, setState] = React.useState<ListenState>("idle");
  const [interim, setInterim] = React.useState("");
  const [error, setError] = React.useState("");
  const recRef = React.useRef<Recognition | null>(null);
  const finalRef = React.useRef("");
  const onFinalRef = React.useRef(onFinal);
  onFinalRef.current = onFinal;

  React.useEffect(() => {
    if (!recognitionCtor()) setState("unsupported");
    return () => recRef.current?.abort();
  }, []);

  const start = React.useCallback(() => {
    const Ctor = recognitionCtor();
    if (!Ctor) {
      setState("unsupported");
      setError("Voice works in Chrome or Edge. Please open InsightX there to use the mic.");
      return;
    }
    stopSpeaking();
    recRef.current?.abort();
    const rec = new Ctor();
    rec.lang = speechLang();
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    finalRef.current = "";
    setInterim("");
    setError("");
    rec.onresult = (event) => {
      let live = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const said = result?.[0]?.transcript ?? "";
        if (result?.isFinal) finalRef.current += said;
        else live += said;
      }
      setInterim((finalRef.current + live).trim());
    };
    rec.onerror = (event) => {
      if (event.error !== "aborted") setError(ERRORS[event.error] ?? "Voice stopped. Please try again.");
    };
    rec.onend = () => {
      recRef.current = null;
      setState("idle");
      const text = finalRef.current.trim();
      setInterim("");
      if (text) onFinalRef.current(text);
    };
    recRef.current = rec;
    try {
      rec.start();
      setState("listening");
    } catch {
      setError("Voice could not start. Please try again.");
      setState("idle");
    }
  }, []);

  const stop = React.useCallback(() => recRef.current?.stop(), []);
  const toggle = React.useCallback(() => (recRef.current ? stop() : start()), [start, stop]);

  return { state, listening: state === "listening", supported: state !== "unsupported", interim, error, start, stop, toggle };
}

/** Text the speaker should not read: markdown marks, SQL fences, long numbers lists. */
function cleanForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[*_`#>|]/g, " ")
    .replace(/^\s*[-•]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 900);
}

export function canSpeak(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function speak(text: string, onEnd?: () => void): void {
  if (!canSpeak()) return;
  const clean = cleanForSpeech(text);
  if (!clean) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.lang = speechLang();
  utterance.rate = 1;
  const voices = window.speechSynthesis.getVoices();
  const voice = voices.find((v) => v.lang === utterance.lang) ?? voices.find((v) => v.lang.startsWith("en"));
  if (voice) utterance.voice = voice;
  if (onEnd) {
    utterance.onend = onEnd;
    utterance.onerror = onEnd;
  }
  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel();
}

/** State for a "Listen" button: is this text being read right now? */
export function useSpeaker() {
  const [speaking, setSpeaking] = React.useState(false);
  const speakingRef = React.useRef(false);
  speakingRef.current = speaking;
  React.useEffect(
    () => () => {
      if (speakingRef.current) stopSpeaking();
    },
    [],
  );
  const play = React.useCallback((text: string) => {
    setSpeaking(true);
    speak(text, () => setSpeaking(false));
  }, []);
  const stop = React.useCallback(() => {
    stopSpeaking();
    setSpeaking(false);
  }, []);
  return { speaking, play, stop };
}
