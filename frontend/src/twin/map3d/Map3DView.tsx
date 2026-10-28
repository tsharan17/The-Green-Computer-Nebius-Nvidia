import { Canvas, type ThreeEvent, useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import type { Overlay, Scene, SceneNode } from "./model";
import type { Camera, Palette } from "./palette";

function CameraRig({ cam, extent, center }: { cam: Camera; extent: number; center: [number, number, number] }) {
  const { camera, invalidate, size } = useThree();
  useEffect(() => {
    const aspect = (camera as THREE.PerspectiveCamera).aspect || 1.5;
    const r = cam.distance * extent * Math.max(1, 1.4 / aspect); // back off on narrow screens
    const [cx, cy, cz] = center;
    camera.position.set(cx + r * Math.cos(cam.elevation) * Math.sin(cam.azimuth), cy + r * Math.sin(cam.elevation), cz + r * Math.cos(cam.elevation) * Math.cos(cam.azimuth));
    camera.lookAt(cx, cy, cz);
    invalidate();
  }, [cam, extent, center, camera, invalidate, size]);
  return null;
}

function labelTexture(text: string, ink: string, bg: string) {
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d")!;
  const font = "600 40px 'IBM Plex Sans', system-ui, sans-serif";
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 28;
  c.width = w;
  c.height = 60;
  ctx.font = font;
  ctx.fillStyle = bg;
  ctx.globalAlpha = 0.88;
  ctx.fillRect(0, 0, w, 60);
  ctx.globalAlpha = 1;
  ctx.fillStyle = ink;
  ctx.textBaseline = "middle";
  ctx.fillText(text, 14, 31);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return { tex, aspect: w / 60 };
}

function Label({ text, pos, pal, scale }: { text: string; pos: [number, number, number]; pal: Palette; scale: number }) {
  const { tex, aspect } = useMemo(() => labelTexture(text, pal.ink, pal.panel), [text, pal.ink, pal.panel]);
  useEffect(() => () => tex.dispose(), [tex]);
  return (
    <sprite position={pos} scale={[0.32 * scale * aspect, 0.32 * scale, 1]} renderOrder={10}>
      <spriteMaterial map={tex} depthTest={false} transparent />
    </sprite>
  );
}

function Node({ n, pal, overlay, active, selected, onSelect }: {
  n: SceneNode; pal: Palette; overlay: Overlay; active: boolean; selected: boolean; onSelect: (key: string) => void;
}) {
  const color = useMemo(() => {
    const base = new THREE.Color(n.kind === "board" ? pal.s3 : pal.line);
    if (overlay === "heat" && n.heatShare !== null) base.lerp(new THREE.Color(pal.heat), 0.15 + 0.85 * n.heatShare);
    return base;
  }, [n, pal, overlay]);
  const hollow = n.side === "bottom";
  return (
    <group position={n.pos}>
      <mesh
        onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect(n.key); }}
        onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { document.body.style.cursor = ""; }}
      >
        <boxGeometry args={n.size} />
        <meshStandardMaterial color={color} wireframe={hollow} transparent={n.kind === "board"} opacity={n.kind === "board" ? 0.55 : 1}
          emissive={active ? new THREE.Color(pal.kelly) : new THREE.Color(0)} emissiveIntensity={active ? 0.65 : 0} />
      </mesh>
      {(selected || active) && (
        <mesh>
          <boxGeometry args={[n.size[0] * 1.06 + 0.04, n.size[1] * 1.06 + 0.04, n.size[2] * 1.06 + 0.04]} />
          <meshBasicMaterial color={selected ? pal.ink : pal.kelly} wireframe />
        </mesh>
      )}
    </group>
  );
}

function Edge({ a, b, color, radius }: { a: THREE.Vector3; b: THREE.Vector3; color: string; radius: number }) {
  const { mid, quat, len } = useMemo(() => {
    const dir = new THREE.Vector3().subVectors(b, a);
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
    return { mid: new THREE.Vector3().addVectors(a, b).multiplyScalar(0.5), quat: q, len: dir.length() };
  }, [a, b]);
  return (
    <mesh position={mid} quaternion={quat}>
      <cylinderGeometry args={[radius, radius, len, 10]} />
      <meshStandardMaterial color={color} />
    </mesh>
  );
}

export default function Map3DView({ scene, overlay, activeKeys, pathUse, selected, onSelect, cam, pal, onReady }: {
  scene: Scene;
  overlay: Overlay;
  activeKeys: Set<string>;
  pathUse: Map<number, number>; // system -> bandwidth utilisation of the running operator (0..1+)
  selected: string | null;
  onSelect: (key: string | null) => void;
  cam: Camera;
  pal: Palette;
  onReady?: () => void;
}) {
  const byKey = useMemo(() => new Map(scene.nodes.map((n) => [n.key, n])), [scene]);
  const k = Math.max(1, scene.extent / 7); // labels grow with the scene so they stay readable
  const top = (n: SceneNode) => new THREE.Vector3(n.pos[0], n.pos[1] + n.size[1] / 2, n.pos[2]);
  return (
    <Canvas frameloop="demand" dpr={[1, 2]} camera={{ fov: 40, near: 0.1, far: 500 }} onPointerMissed={() => onSelect(null)}
      onCreated={() => onReady?.()} gl={{ preserveDrawingBuffer: true, antialias: true }}>
      <color attach="background" args={[pal.bg]} />
      <hemisphereLight args={[pal.panel, pal.line, 0.9]} />
      <ambientLight intensity={0.35} />
      <directionalLight position={[6, 12, 8]} intensity={1.2} />
      <directionalLight position={[-8, 6, -6]} intensity={0.35} />
      <CameraRig cam={cam} extent={scene.extent} center={scene.center} />
      {scene.systems.map((s, i) => s.label && (
        <Label key={`sys${i}`} text={s.label} pos={[s.offsetX, 2.6, -4.6]} pal={pal} scale={1.6 * k} />
      ))}
      {scene.layout === "logical" && scene.systems.map((s, i) => (
        <mesh key={`floor${i}`} position={[s.offsetX, -0.03, 0.6]} rotation={[-Math.PI / 2, 0, 0]}>
          <planeGeometry args={[11, 10.4]} />
          <meshStandardMaterial color={pal.panel} />
        </mesh>
      ))}
      {scene.nodes.map((n) => (
        <Node key={n.key} n={n} pal={pal} overlay={overlay} active={overlay === "activity" && activeKeys.has(n.key)}
          selected={selected === n.key} onSelect={onSelect} />
      ))}
      {scene.nodes.filter((n) => n.label || n.key === selected).map((n) => (
        <Label key={`l-${n.key}`} pos={[n.pos[0], n.pos[1] + n.size[1] / 2 + 0.3, n.pos[2]]} pal={pal} scale={(n.kind === "board" || n.size[0] > 1.5 || scene.layout === "physical" ? 1 : 0.7) * k}
          text={`${n.name}${overlay === "heat" && n.heatW !== null ? ` · ${n.heatW.toPrecision(2)} W` : ""}${overlay === "activity" && activeKeys.has(n.key) ? " · busy" : ""}`} />
      ))}
      {scene.edges.map((e) => {
        const a = byKey.get(e.from);
        const b = byKey.get(e.to);
        if (!a || !b) return null;
        const use = e.tracePath ? pathUse.get(e.system) ?? 0 : 0;
        const r = e.tracePath ? 0.035 + 0.12 * Math.min(use, 1.2) : e.kind === "net" ? 0.012 : 0.025;
        const color = e.tracePath ? pal.s1 : e.kind === "thermal" ? pal.heat : pal.muted;
        return <Edge key={e.key} a={top(a)} b={top(b)} color={color} radius={r} />;
      })}
    </Canvas>
  );
}
