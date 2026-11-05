"use client";

// The landing hero: a faceted "data crystal" inside a graph of linked nodes,
// with a thin measuring ring. It follows a list of camera shots (shots.ts):
// centred under the headline, beside the text, close up in a corner, back
// out again. Between shots its facets and nodes drift apart and come back
// together. Colours adapt as the page turns from dark to silver. Soft light
// and reflections come from local light shapes (no downloaded environment
// map). Loaded lazily, never on app pages.

import * as React from "react";
import * as THREE from "three";
import { Canvas, useFrame } from "@react-three/fiber";
import { Environment, Lightformer, PerformanceMonitor } from "@react-three/drei";
import { SHOTS, sampleShot } from "@/components/landing/shots";

export interface CrystalSceneProps {
  /** Page scroll progress 0..1, written by the page (no re-render per frame). */
  progress?: { current: number };
  /** Quieter version for the sign-in page: breathes with time, ignores scroll. */
  soft?: boolean;
  className?: string;
}

const NODE_DARK = new THREE.Color("#a78bfa");
const NODE_LIGHT = new THREE.Color("#5b21b6");
const EDGE_DARK = new THREE.Color("#8b5cf6");
const EDGE_LIGHT = new THREE.Color("#3b2a6b");
const RING_DARK = new THREE.Color("#ffffff");
const RING_LIGHT = new THREE.Color("#15161b");
const RING_RADIUS = 2.15;

function fibonacciSphere(count: number, radius: number): THREE.Vector3[] {
  const points: THREE.Vector3[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const theta = golden * i;
    points.push(new THREE.Vector3(Math.cos(theta) * r, y, Math.sin(theta) * r).multiplyScalar(radius));
  }
  return points;
}

function nearestEdges(points: THREE.Vector3[], neighbours: number): [number, number][] {
  const seen = new Set<string>();
  const edges: [number, number][] = [];
  points.forEach((point, i) => {
    points
      .map((other, j) => ({ j, d: i === j ? Infinity : point.distanceToSquared(other) }))
      .sort((a, b) => a.d - b.d)
      .slice(0, neighbours)
      .forEach(({ j }) => {
        const key = i < j ? `${i}-${j}` : `${j}-${i}`;
        if (!seen.has(key)) {
          seen.add(key);
          edges.push([i, j]);
        }
      });
  });
  return edges;
}

/** Thin circle with a cross mark on its left and right, like a measuring tool. */
function ringGeometry(): THREE.BufferGeometry {
  const points: number[] = [];
  const segments = 160;
  for (let i = 0; i < segments; i++) {
    const a0 = (i / segments) * Math.PI * 2;
    const a1 = ((i + 1) / segments) * Math.PI * 2;
    points.push(Math.cos(a0) * RING_RADIUS, Math.sin(a0) * RING_RADIUS, 0, Math.cos(a1) * RING_RADIUS, Math.sin(a1) * RING_RADIUS, 0);
  }
  const arm = 0.11;
  for (const side of [-1, 1]) {
    const cx = side * RING_RADIUS;
    points.push(cx - arm, 0, 0, cx + arm, 0, 0, cx, -arm, 0, cx, arm, 0);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

function Crystal({ progress, soft = false }: CrystalSceneProps) {
  const group = React.useRef<THREE.Group>(null);
  const ring = React.useRef<THREE.LineSegments>(null);
  const nodes = React.useRef<THREE.InstancedMesh>(null);
  const nodeMaterial = React.useRef<THREE.MeshBasicMaterial>(null);
  const edgeMaterial = React.useRef<THREE.LineBasicMaterial>(null);
  const ringMaterial = React.useRef<THREE.LineBasicMaterial>(null);
  const wireMaterial = React.useRef<THREE.MeshBasicMaterial>(null);
  const first = sampleShot(0);
  const motion = React.useRef({
    explode: 0,
    x: soft ? 1.15 : first.x,
    y: soft ? -0.15 : first.y,
    scale: soft ? 0.85 : first.scale,
    ring: soft ? 0 : first.ring,
    graph: 1,
    tone: 0,
    spin: 0,
  });
  const dummy = React.useMemo(() => new THREE.Object3D(), []);
  const colour = React.useMemo(() => new THREE.Color(), []);

  // Faceted core: every triangle is its own piece, so it can move out along its normal.
  const core = React.useMemo(() => {
    const geometry = new THREE.IcosahedronGeometry(1, 1).toNonIndexed();
    const position = geometry.getAttribute("position") as THREE.BufferAttribute;
    const original = Float32Array.from(position.array as Float32Array);
    const outward = new Float32Array(original.length);
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const c = new THREE.Vector3();
    for (let face = 0; face < position.count / 3; face++) {
      a.fromBufferAttribute(position, face * 3);
      b.fromBufferAttribute(position, face * 3 + 1);
      c.fromBufferAttribute(position, face * 3 + 2);
      const centre = a.add(b).add(c).divideScalar(3).normalize();
      for (let k = 0; k < 3; k++) outward.set([centre.x, centre.y, centre.z], (face * 3 + k) * 3);
    }
    geometry.computeVertexNormals();
    return { geometry, original, outward };
  }, []);

  const graph = React.useMemo(() => {
    const points = fibonacciSphere(soft ? 56 : 96, 1.75);
    const edges = nearestEdges(points, 3);
    const lines = new THREE.BufferGeometry();
    lines.setAttribute("position", new THREE.BufferAttribute(new Float32Array(edges.length * 6), 3));
    return { points, edges, lines };
  }, [soft]);

  const ringLines = React.useMemo(() => ringGeometry(), []);

  React.useEffect(
    () => () => {
      core.geometry.dispose();
      graph.lines.dispose();
      ringLines.dispose();
    },
    [core, graph, ringLines],
  );

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;
    const steps = SHOTS.length - 1;
    const s = soft ? 0 : Math.min(1, Math.max(0, progress?.current ?? 0));
    const u = s * steps;
    const shot = sampleShot(u);
    const m = motion.current;

    // Assembled at every section, apart while moving between them.
    const explodeTarget = soft ? 0.18 + 0.16 * Math.sin(t * 0.6) : 0.5 - 0.5 * Math.cos(2 * Math.PI * u);

    m.explode = THREE.MathUtils.damp(m.explode, explodeTarget, 4, delta);
    m.x = THREE.MathUtils.damp(m.x, soft ? 1.15 : shot.x, 3, delta);
    m.y = THREE.MathUtils.damp(m.y, soft ? -0.15 : shot.y, 3, delta);
    m.scale = THREE.MathUtils.damp(m.scale, soft ? 0.85 : shot.scale, 3, delta);
    m.ring = THREE.MathUtils.damp(m.ring, soft ? 0 : shot.ring, 3, delta);
    m.graph = THREE.MathUtils.damp(m.graph, soft ? 1 : shot.graph, 3, delta);
    m.tone = THREE.MathUtils.damp(m.tone, soft ? 0 : shot.tone, 3, delta);
    m.spin = THREE.MathUtils.damp(m.spin, s * Math.PI * 3, 3, delta);

    const position = core.geometry.getAttribute("position") as THREE.BufferAttribute;
    const array = position.array as Float32Array;
    const spread = m.explode * 0.55;
    for (let i = 0; i < array.length; i++) array[i] = (core.original[i] ?? 0) + (core.outward[i] ?? 0) * spread;
    position.needsUpdate = true;

    const radial = 1 + m.explode * 0.55;
    const lineArray = graph.lines.getAttribute("position").array as Float32Array;
    graph.points.forEach((point, i) => {
      dummy.position.copy(point).multiplyScalar(radial);
      // Keep the dots small on screen even when the crystal is shown close up.
      dummy.scale.setScalar((0.75 + 0.35 * Math.sin(t * 1.4 + i)) / Math.pow(Math.max(m.scale, 0.5), 0.75));
      dummy.updateMatrix();
      nodes.current?.setMatrixAt(i, dummy.matrix);
    });
    if (nodes.current) nodes.current.instanceMatrix.needsUpdate = true;
    graph.edges.forEach(([i, j], e) => {
      const p = graph.points[i];
      const q = graph.points[j];
      if (!p || !q) return;
      lineArray.set([p.x * radial, p.y * radial, p.z * radial, q.x * radial, q.y * radial, q.z * radial], e * 6);
    });
    graph.lines.getAttribute("position").needsUpdate = true;

    // Colours follow the page: violet glow on dark, deep ink on silver.
    if (nodeMaterial.current) {
      nodeMaterial.current.color.copy(colour.copy(NODE_DARK).lerp(NODE_LIGHT, m.tone));
      nodeMaterial.current.opacity = m.graph;
    }
    if (edgeMaterial.current) {
      edgeMaterial.current.color.copy(colour.copy(EDGE_DARK).lerp(EDGE_LIGHT, m.tone));
      edgeMaterial.current.opacity = (soft ? 0.22 : 0.32) * m.graph;
    }
    if (wireMaterial.current) wireMaterial.current.opacity = 0.35 - 0.15 * m.tone;
    if (ringMaterial.current) {
      ringMaterial.current.color.copy(colour.copy(RING_DARK).lerp(RING_LIGHT, m.tone));
      ringMaterial.current.opacity = m.ring * (0.22 + 0.2 * m.tone);
    }

    if (group.current) {
      group.current.rotation.y = m.spin + t * (soft ? 0.12 : 0.08);
      group.current.rotation.x = 0.35 * Math.sin(m.spin * 0.5) + 0.12;
      group.current.position.set(m.x, m.y, 0);
      group.current.scale.setScalar(m.scale);
    }
    if (ring.current) {
      ring.current.position.set(m.x, m.y, 0);
      ring.current.scale.setScalar(m.scale * (1 + 0.04 * m.explode));
      ring.current.visible = m.ring > 0.01;
    }
  });

  return (
    <>
      <group ref={group}>
        <mesh geometry={core.geometry}>
          <meshPhysicalMaterial
            color="#16112b"
            metalness={0.6}
            roughness={0.14}
            clearcoat={1}
            clearcoatRoughness={0.08}
            flatShading
            envMapIntensity={1.5}
          />
        </mesh>
        <mesh geometry={core.geometry}>
          <meshBasicMaterial ref={wireMaterial} color={EDGE_DARK} wireframe transparent opacity={0.35} toneMapped={false} />
        </mesh>
        <instancedMesh ref={nodes} args={[undefined, undefined, graph.points.length]}>
          <sphereGeometry args={[0.028, 10, 10]} />
          <meshBasicMaterial ref={nodeMaterial} color={NODE_DARK} transparent toneMapped={false} />
        </instancedMesh>
        <lineSegments geometry={graph.lines}>
          <lineBasicMaterial ref={edgeMaterial} color={EDGE_DARK} transparent opacity={0.3} depthWrite={false} />
        </lineSegments>
      </group>
      <lineSegments ref={ring} geometry={ringLines}>
        <lineBasicMaterial ref={ringMaterial} color={RING_DARK} transparent opacity={0} depthWrite={false} toneMapped={false} />
      </lineSegments>
    </>
  );
}

export default function CrystalScene({ className, ...props }: CrystalSceneProps) {
  const [dpr, setDpr] = React.useState(1.5);
  return (
    <Canvas
      className={className}
      dpr={dpr}
      camera={{ position: [0, 0, 6.2], fov: 40 }}
      gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      aria-hidden
    >
      <PerformanceMonitor onDecline={() => setDpr(1)} onIncline={() => setDpr(1.5)} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[3, 4, 5]} intensity={1.4} />
      <pointLight position={[-4, -2, 2]} intensity={28} color="#7c3aed" />
      <pointLight position={[4, 1.5, -3]} intensity={16} color="#38bdf8" />
      <Crystal {...props} />
      <Environment resolution={128}>
        <Lightformer form="rect" intensity={2.4} position={[0, 3, -4]} scale={[9, 3, 1]} />
        <Lightformer form="ring" color="#8b5cf6" intensity={4} position={[-4, 0, 2]} scale={3} />
        <Lightformer form="rect" intensity={1.4} position={[4, -1, 2]} scale={[3, 6, 1]} />
      </Environment>
    </Canvas>
  );
}
