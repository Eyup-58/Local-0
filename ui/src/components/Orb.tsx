/**
 * The point-cloud sphere at the centre of the panel.
 *
 * It is a readout, not decoration, and that distinction is the whole reason it is written the way
 * it is. Its motion is driven by the sample the sidecar actually sent: `energy` is real load, and
 * when there is no reading `energy` is null rather than zero. A null orb sits perfectly still in a
 * dim colour, because a sphere breathing gently at "0% load" is exactly the plausible-looking
 * placeholder red line 10 forbids - it would read as a calm machine rather than as a missing
 * sensor.
 *
 * Canvas 2D and no dependency. A point cloud of a few hundred dots is arithmetic; reaching for a
 * 3D library to draw it would add a rendering engine to a panel whose whole claim is that it is
 * small. There is no WebGL context here either, so nothing competes with the GPU it is measuring.
 */

import { useEffect, useRef } from "react";

export interface Point {
  readonly x: number;
  readonly y: number;
  readonly z: number;
}

/** How the orb is behaving, decided by the caller from real state - never from a timer. */
export type OrbMood = "offline" | "idle" | "attention" | "unguarded";

const MOOD_COLOURS: Record<OrbMood, readonly [number, number, number]> = {
  // Dim slate. Deliberately the least alive colour on the page: no link means no data, and the
  // centre of the panel should not look busy while that is true.
  offline: [74, 95, 122],
  idle: [255, 95, 158],
  attention: [240, 179, 87],
  // Trust mode. The guard still runs, but nothing stops to ask - worth being loud about.
  unguarded: [224, 112, 90],
};

const DOT_COUNT = 520;
const BASE_RADIUS = 0.78;
const PERSPECTIVE = 2.6;
const SPIN_PER_SECOND = 0.16;

/**
 * Points spread evenly over a unit sphere by the golden-angle spiral.
 *
 * A latitude/longitude grid is the obvious alternative and it bunches hard at the poles, which
 * reads as two bright spots rather than as an even shell.
 */
export function spherePoints(count: number): Point[] {
  const golden = Math.PI * (3 - Math.sqrt(5));

  return Array.from({ length: count }, (_, index) => {
    const y = count === 1 ? 0 : 1 - (index / (count - 1)) * 2;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * index;

    return { x: Math.cos(theta) * ring, y, z: Math.sin(theta) * ring };
  });
}

/**
 * How far a point is pushed out of the sphere, as a multiplier on its radius.
 *
 * Returns exactly 1 when there is no reading. That is the rule this function exists to hold: with
 * `energy` null the shell is a perfect, still sphere, and no motion is invented for a sensor that
 * said nothing.
 */
export function displacement(point: Point, seconds: number, energy: number | null): number {
  if (energy === null) return 1;

  const swell = Math.sin(point.x * 2.7 + seconds * 1.6) * Math.cos(point.y * 2.3 - seconds * 1.1);
  return 1 + energy * 0.26 * swell;
}

/** Perspective projection onto the canvas. `scale` is the depth cue the dot is drawn with. */
export function project(point: Point, radius: number): { x: number; y: number; scale: number } {
  const depth = PERSPECTIVE / (PERSPECTIVE - point.z);

  return { x: point.x * radius * depth, y: point.y * radius * depth, scale: depth };
}

export interface OrbProps {
  /** 0-1 from the live sample, or null when nothing has been measured. */
  readonly energy: number | null;
  readonly mood: OrbMood;
  readonly label: string;
}

export function Orb({ energy, mood, label }: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Read inside the animation frame rather than closed over, so a new sample does not restart the
  // loop and visibly jump the rotation.
  const live = useRef({ energy, mood });
  live.current = { energy, mood };

  useEffect(() => {
    const canvas = canvasRef.current;
    // jsdom has no 2D context. A panel that threw here would take the whole page down in tests.
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const points = spherePoints(DOT_COUNT);
    let frame = 0;
    const started = performance.now();

    const resize = () => {
      const ratio = window.devicePixelRatio || 1;
      const box = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(box.width * ratio));
      canvas.height = Math.max(1, Math.round(box.height * ratio));
    };

    const draw = (stamp: number) => {
      const seconds = (stamp - started) / 1000;
      const { energy: level, mood: tone } = live.current;
      const [red, green, blue] = MOOD_COLOURS[tone];

      const width = canvas.width;
      const height = canvas.height;
      const radius = Math.min(width, height) * 0.5 * BASE_RADIUS;
      const spin = seconds * SPIN_PER_SECOND * Math.PI;

      context.clearRect(0, 0, width, height);
      context.save();
      context.translate(width / 2, height / 2);

      for (const point of points) {
        const push = displacement(point, seconds, level);
        const cos = Math.cos(spin);
        const sin = Math.sin(spin);
        const spun: Point = {
          x: point.x * cos - point.z * sin,
          y: point.y,
          z: point.x * sin + point.z * cos,
        };

        const flat = project(spun, radius * push);
        // Depth as opacity: the far half of the shell reads through the near half instead of
        // being painted over it, which is what makes it look like a volume rather than a disc.
        const front = (spun.z + 1) / 2;
        const alpha = 0.12 + front * 0.72;
        const size = Math.max(0.5, flat.scale * (level === null ? 0.9 : 1.05));

        context.fillStyle = `rgba(${red}, ${green}, ${blue}, ${alpha.toFixed(3)})`;
        context.beginPath();
        context.arc(flat.x, flat.y, size, 0, Math.PI * 2);
        context.fill();
      }

      context.restore();
      frame = requestAnimationFrame(draw);
    };

    resize();
    frame = requestAnimationFrame(draw);

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  return (
    <div className="orb">
      <div className={`orb__ring orb__ring--${mood}`} aria-hidden="true" />
      <canvas ref={canvasRef} className="orb__canvas" role="img" aria-label={label} />
    </div>
  );
}
