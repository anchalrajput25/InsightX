"use client";

// Chooses between the 3D crystal and a still picture of it. The 3D code is
// a separate chunk loaded only here (dynamic import, no server render), so
// app pages never download three.js. Reduced motion, phones, weak devices
// and browsers without WebGL get the still picture.

import * as React from "react";
import dynamic from "next/dynamic";
import { cn } from "@/lib/utils";
import type { CrystalSceneProps } from "@/components/landing/crystal-scene";

const CrystalScene = dynamic(() => import("@/components/landing/crystal-scene"), {
  ssr: false,
  loading: () => <StaticCrystal />,
});

function canRender3D(): boolean {
  if (typeof window === "undefined") return false;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  if (window.matchMedia("(max-width: 767px), (pointer: coarse)").matches) return false;
  const nav = navigator as Navigator & { deviceMemory?: number };
  if ((nav.hardwareConcurrency ?? 8) < 4 || (nav.deviceMemory ?? 8) < 4) return false;
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

/** A still, lightweight version of the crystal (SVG), used as fallback and while the 3D loads. */
export function StaticCrystal({ className }: { className?: string }) {
  const outer = [0, 60, 120, 180, 240, 300].map((deg) => {
    const r = (deg * Math.PI) / 180;
    return [200 + 120 * Math.cos(r), 200 + 120 * Math.sin(r)];
  });
  const inner = [30, 150, 270].map((deg) => {
    const r = (deg * Math.PI) / 180;
    return [200 + 58 * Math.cos(r), 200 + 58 * Math.sin(r)];
  });
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 flex items-center justify-center", className)}>
      <div className="absolute size-[520px] rounded-full bg-[radial-gradient(circle,rgb(124_58_237/0.28),transparent_65%)] blur-2xl" />
      <svg viewBox="0 0 400 400" className="relative size-[min(70vw,440px)] opacity-80">
        <defs>
          <linearGradient id="crystal-face" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#2a1f52" />
            <stop offset="100%" stopColor="#0d0a1c" />
          </linearGradient>
        </defs>
        <polygon points={outer.map((p) => p.join(",")).join(" ")} fill="url(#crystal-face)" stroke="#8b5cf6" strokeOpacity="0.6" />
        {outer.map(([x, y], i) => {
          const [tx, ty] = inner[Math.floor(i / 2)] ?? [200, 200];
          return <line key={`o${i}`} x1={x} y1={y} x2={tx} y2={ty} stroke="#a78bfa" strokeOpacity="0.45" />;
        })}
        <polygon points={inner.map((p) => p.join(",")).join(" ")} fill="none" stroke="#c4b5fd" strokeOpacity="0.55" />
        {outer.map(([x, y], i) => (
          <circle key={`n${i}`} cx={x} cy={y} r="3" fill="#c4b5fd" />
        ))}
      </svg>
    </div>
  );
}

export function CrystalBackdrop({ className, ...props }: CrystalSceneProps & { className?: string }) {
  const [mode, setMode] = React.useState<"pending" | "3d" | "still">("pending");
  React.useEffect(() => setMode(canRender3D() ? "3d" : "still"), []);
  return (
    <div aria-hidden className={cn("pointer-events-none", className)}>
      {mode === "3d" ? <CrystalScene {...props} className="!absolute inset-0" /> : <StaticCrystal />}
    </div>
  );
}
