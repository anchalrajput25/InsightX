// One "camera shot" per landing section. The 3D crystal and the page colours
// both follow this list, so the object, the measuring ring and the
// dark-to-silver background always move together with the scroll.

export interface Shot {
  id: string;
  /** Crystal position and size in scene units. */
  x: number;
  y: number;
  scale: number;
  /** Measuring ring visibility 0..1. */
  ring: number;
  /** Node graph visibility 0..1 (lower in close-ups so text stays clear). */
  graph: number;
  /** 0 = dark page, 1 = silver page. */
  tone: number;
}

export const SHOTS: Shot[] = [
  { id: "intro", x: 0, y: -1.75, scale: 0.92, ring: 1, graph: 1, tone: 0 },
  { id: "ask", x: 1.55, y: 0, scale: 0.8, ring: 0, graph: 1, tone: 1 },
  { id: "safe", x: -2.55, y: -1.35, scale: 2.3, ring: 0, graph: 0.15, tone: 1 },
  { id: "why", x: 1.55, y: 0.05, scale: 0.8, ring: 0, graph: 1, tone: 1 },
  { id: "whatif", x: -2.45, y: 1.45, scale: 2.1, ring: 0, graph: 0.15, tone: 1 },
  { id: "measured", x: 1.5, y: 0, scale: 0.72, ring: 1, graph: 1, tone: 1 },
  { id: "inside", x: 0.4, y: 3.6, scale: 0.6, ring: 0, graph: 0, tone: 1 },
  { id: "start", x: 0, y: -1.75, scale: 0.92, ring: 1, graph: 1, tone: 0 },
];

function smooth(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return c * c * (3 - 2 * c);
}

/** Blend between shots; `u` runs from 0 (first section) to SHOTS.length - 1 (last). */
export function sampleShot(u: number): Omit<Shot, "id"> {
  const max = SHOTS.length - 1;
  const clamped = Math.min(max, Math.max(0, u));
  const i = Math.min(max - 1, Math.floor(clamped));
  const a = SHOTS[i] ?? SHOTS[0]!;
  const b = SHOTS[i + 1] ?? a;
  // Hold each shot for a while around its section, then move.
  const t = smooth((clamped - i - 0.15) / 0.7);
  const mix = (p: number, q: number) => p + (q - p) * t;
  return {
    x: mix(a.x, b.x),
    y: mix(a.y, b.y),
    scale: mix(a.scale, b.scale),
    ring: mix(a.ring, b.ring),
    graph: mix(a.graph, b.graph),
    tone: mix(a.tone, b.tone),
  };
}
