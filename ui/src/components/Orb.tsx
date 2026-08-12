/**
 * The point-grid core at the centre of the panel.
 *
 * It is a readout, not decoration, and that distinction is the whole reason it is written the way
 * it is. Its motion is driven by the sample the sidecar actually sent: `energy` is real load, and
 * when there is no reading `energy` is null rather than zero. A null core sits perfectly still in a
 * dim colour, because a sphere breathing gently at "0% load" is exactly the plausible-looking
 * placeholder invariant 10 forbids - it would read as a calm machine rather than as a missing
 * sensor. Its colour is the turn the brain reported, eased towards over about a second; nothing
 * here advances that turn on a timer.
 *
 * Canvas 2D and no dependency. The mockup drew this with a Three.js shader over eleven thousand
 * points, and that is the one thing from the design that could not be taken: this panel reports GPU
 * utilization, and a WebGL loop running behind the number would be inflating the figure it draws.
 * A few thousand dots and two rings is arithmetic, and it leaves the measurement honest.
 */

import { useEffect, useRef } from "react";

import type { TurnStateName } from "../contracts/types";

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

/**
 * The colour of each reported turn, matching the pill in the top bar and the track at the bottom.
 *
 * Looked up from `turn` and interpolated towards over time. When the brain stops reporting, the
 * colour settles on the last state it was told about and stays there.
 */
const TURN_COLOURS: Record<TurnStateName, readonly [number, number, number]> = {
  idle: [111, 123, 255],
  listening: [95, 208, 255],
  thinking: [255, 165, 61],
  tool_running: [185, 240, 74],
  speaking: [111, 227, 200],
};

const BASE_RADIUS = 0.72;
const PERSPECTIVE = 2.6;
const SPIN_PER_SECOND = 0.16;

/** Rows and columns of the lat/long grid. */
const GRID_ROWS = 38;
const GRID_COLS = 76;

/** Orbital rings, as [radius multiplier, alpha]. Each carries tick marks just outside it. */
const RINGS: readonly (readonly [number, number])[] = [
  [1.28, 0.3],
  [1.42, 0.16],
];
const RING_TICKS = 48;
/** How far the ring planes are tilted away from the viewer, as a y-axis squash. */
const RING_TILT = 0.34;

const RAIL_WIDTH = 74;
const DOCK_WIDTH = 472;

/** Top bar plus the reading strip hanging below it. */
const CHROME_TOP = 92;
/** The caption stack and the control track, with and without a caption line to make room for. */
const RESERVE_WITH_CAPTION = 330;
const RESERVE_BARE = 240;
/** The outermost ring sits at this multiple of the core radius, so it sets the width needed. */
const WIDEST_RING = 1.42;

/**
 * Points on a latitude/longitude grid over the unit sphere.
 *
 * A golden-angle spiral spreads more evenly and is what this drew before. The grid is the
 * orchestration-centre design's own geometry, and the banding it produces towards the poles is
 * exactly what makes the shell read as a wireframe globe rather than as a cloud of dots.
 */
export function spherePoints(rows: number, cols: number): Point[] {
  const points: Point[] = [];

  for (let row = 0; row <= rows; row += 1) {
    const phi = (row / rows) * Math.PI;
    const ring = Math.sin(phi);

    for (let col = 0; col < cols; col += 1) {
      const theta = (col / cols) * Math.PI * 2;
      points.push({ x: Math.cos(theta) * ring, y: Math.cos(phi), z: Math.sin(theta) * ring });
    }
  }

  return points;
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

/** Moves `from` a fraction `k` of the way to `to`. The caller corrects `k` for frame rate. */
export function lerpColour(
  from: readonly [number, number, number],
  to: readonly [number, number, number],
  k: number,
): [number, number, number] {
  return [
    from[0] + (to[0] - from[0]) * k,
    from[1] + (to[1] - from[1]) * k,
    from[2] + (to[2] - from[2]) * k,
  ];
}

/**
 * Where the core sits and how big it is, in CSS pixels, given the space actually free.
 *
 * The canvas spans the whole window but the free stage does not: the rail takes the left, the dock
 * takes the right when it is out, and the chrome takes a band off the top and a deeper one off the
 * bottom. Centring in the window instead of in that free box is what would put the core behind the
 * dock and underneath its own caption.
 *
 * The radius is whichever of the two constraints binds first - the free width once the outer ring
 * is accounted for, or the free height. That is what makes the core shrink rather than overlap when
 * a caption appears or a panel opens.
 */
export function coreLayout(
  width: number,
  height: number,
  dockOpen: boolean,
  hasCaption: boolean,
): { radius: number; offsetX: number; offsetY: number } {
  const dock = dockOpen ? Math.min(DOCK_WIDTH, width * 0.46) : 0;
  const freeWidth = Math.max(0, width - RAIL_WIDTH - dock);

  const bottom = height - (hasCaption ? RESERVE_WITH_CAPTION : RESERVE_BARE);
  const freeHeight = Math.max(0, bottom - CHROME_TOP);

  return {
    // 0.92 leaves the ring clear of the edge rather than flush against it.
    radius: Math.max(0, Math.min((freeWidth * 0.92) / (2 * WIDEST_RING), freeHeight * 0.46)),
    offsetX: RAIL_WIDTH + freeWidth / 2 - width / 2,
    offsetY: CHROME_TOP + freeHeight / 2 - height / 2,
  };
}

export interface OrbProps {
  /** 0-1 from the live sample, or null when nothing has been measured. */
  readonly energy: number | null;
  /** The turn the brain reported. Chooses the colour; never advanced here. */
  readonly turn: TurnStateName;
  readonly mood: OrbMood;
  /** Whether the dock is out, so the core can keep its own centre in the space that is left. */
  readonly dockOpen: boolean;
  /** Whether a line is showing under the core, so it can shrink to clear it. */
  readonly hasCaption: boolean;
  readonly label: string;
}

export function Orb({ energy, turn, mood, dockOpen, hasCaption, label }: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Read inside the animation frame rather than closed over, so a new sample does not restart the
  // loop and visibly jump the rotation.
  const live = useRef({ energy, turn, mood, dockOpen, hasCaption });
  live.current = { energy, turn, mood, dockOpen, hasCaption };

  useEffect(() => {
    const canvas = canvasRef.current;
    // jsdom has no 2D context. A panel that threw here would take the whole page down in tests.
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const points = spherePoints(GRID_ROWS, GRID_COLS);
    let frame = 0;
    const started = performance.now();
    let last = started;
    // Held across frames so a state change eases in rather than snapping.
    let colour: [number, number, number] = [...TURN_COLOURS[live.current.turn]];
    // Eased across frames so opening the dock or gaining a caption glides the core rather than
    // teleporting it.
    let shiftX = 0;
    let shiftY = 0;
    let radius = 0;

    const resize = () => {
      const ratio = window.devicePixelRatio || 1;
      const box = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(box.width * ratio));
      canvas.height = Math.max(1, Math.round(box.height * ratio));
    };

    const drawRing = (radius: number, alpha: number, spin: number) => {
      const [red, green, blue] = colour.map(Math.round);

      context.strokeStyle = `rgba(${red}, ${green}, ${blue}, ${alpha})`;
      context.lineWidth = 1;
      context.beginPath();
      for (let step = 0; step <= 120; step += 1) {
        const angle = (step / 120) * Math.PI * 2 + spin;
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius * RING_TILT;
        if (step === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.stroke();

      context.fillStyle = `rgba(${red}, ${green}, ${blue}, ${Math.min(1, alpha + 0.25)})`;
      for (let tick = 0; tick < RING_TICKS; tick += 1) {
        const angle = (tick / RING_TICKS) * Math.PI * 2 + spin;
        context.beginPath();
        context.arc(
          Math.cos(angle) * (radius + 4),
          Math.sin(angle) * (radius + 4) * RING_TILT,
          1.1,
          0,
          Math.PI * 2,
        );
        context.fill();
      }
    };

    const draw = (stamp: number) => {
      const seconds = (stamp - started) / 1000;
      const delta = Math.min(0.05, (stamp - last) / 1000);
      last = stamp;

      const { energy: level, turn: state, mood: tone, dockOpen: docked, hasCaption: captioned } =
        live.current;
      // Offline and trust mode keep their own colour: a dead link must not be tinted with a turn
      // the panel can no longer see, and approval being off outranks whatever the brain is doing.
      const target =
        tone === "offline" || tone === "unguarded" ? MOOD_COLOURS[tone] : TURN_COLOURS[state];
      // Frame-rate corrected easing: the same visual speed at 30fps and 144fps.
      colour = lerpColour(colour, target, 1 - Math.pow(0.001, delta));
      const [red, green, blue] = colour.map(Math.round);

      const width = canvas.width;
      const height = canvas.height;
      const ratio = window.devicePixelRatio || 1;
      const spin = seconds * SPIN_PER_SECOND * Math.PI;

      // Laid out in CSS pixels, then scaled: the reserved bands are chrome measurements and chrome
      // is not drawn at device resolution.
      const want = coreLayout(width / ratio, height / ratio, docked, captioned);
      const ease = 1 - Math.pow(0.02, delta);
      shiftX += (want.offsetX * ratio - shiftX) * ease;
      shiftY += (want.offsetY * ratio - shiftY) * ease;
      radius += (want.radius * ratio * BASE_RADIUS - radius) * ease;

      context.clearRect(0, 0, width, height);
      context.save();
      context.translate(width / 2 + shiftX, height / 2 + shiftY);

      for (const [multiplier, alpha] of RINGS) {
        drawRing(radius * multiplier, alpha, spin * 0.4);
      }

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
        // Depth as opacity: the far half of the shell reads through the near half instead of being
        // painted over it, which is what makes it look like a volume rather than a disc.
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
      <canvas ref={canvasRef} className="orb__canvas" role="img" aria-label={label} />
    </div>
  );
}
