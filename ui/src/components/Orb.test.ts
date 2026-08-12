/**
 * The orb's arithmetic, tested without a canvas.
 *
 * One of these is a security-adjacent test rather than a graphics one: `displacement` returning
 * exactly 1 for a null reading is how red line 10 reaches the animation. A sphere that breathed
 * anyway would be a plausible-looking placeholder for a sensor that reported nothing.
 */

import { describe, expect, it } from "vitest";

import { coreLayout, displacement, lerpColour, project, spherePoints } from "./Orb";

describe("spherePoints", () => {
  it("returns one point per column on every ring, poles included", () => {
    // rows + 1 rings: the poles are rings of their own, not the gaps between rings.
    expect(spherePoints(4, 8)).toHaveLength((4 + 1) * 8);
  });

  it("places every point on the unit sphere", () => {
    const offSurface = spherePoints(12, 24).filter((point) => {
      const length = Math.hypot(point.x, point.y, point.z);
      return Math.abs(length - 1) > 1e-9;
    });

    expect(offSurface).toEqual([]);
  });

  it("spreads points across both poles rather than bunching at one", () => {
    const heights = spherePoints(12, 24).map((point) => point.y);

    expect(Math.min(...heights)).toBeCloseTo(-1, 6);
    expect(Math.max(...heights)).toBeCloseTo(1, 6);
  });

  it("returns an empty shell for a zero-column request rather than dividing by zero", () => {
    expect(spherePoints(12, 0)).toEqual([]);
  });
});

describe("lerpColour", () => {
  const blue = [111, 123, 255] as const;
  const teal = [111, 227, 200] as const;

  it("stays put at zero", () => {
    expect(lerpColour(blue, teal, 0)).toEqual([...blue]);
  });

  it("arrives at one", () => {
    expect(lerpColour(blue, teal, 1)).toEqual([...teal]);
  });

  it("lands halfway at a half", () => {
    expect(lerpColour(blue, teal, 0.5)).toEqual([111, 175, 227.5]);
  });
});

describe("coreLayout", () => {
  it("centres the core in the space right of the rail", () => {
    // 1400 wide, 74 of it rail: the free stage is 74..1400, whose middle is 737 - which is 37 right
    // of the window's own middle.
    expect(coreLayout(1400, 900, false, false).offsetX).toBeCloseTo(37, 6);
  });

  it("moves the core left, not right, when the dock opens", () => {
    const open = coreLayout(1400, 900, true, false);
    const shut = coreLayout(1400, 900, false, false);

    expect(open.offsetX).toBeLessThan(shut.offsetX);
  });

  it("shrinks the core rather than letting the dock cover it, once width is what binds", () => {
    // Tall and narrow, so the free width is the limit. On a wide screen the core is already
    // height-limited and the dock only moves it - which is why this picks a shape where the
    // shrinking is the visible behaviour.
    const open = coreLayout(1000, 1400, true, false);
    const shut = coreLayout(1000, 1400, false, false);

    expect(open.radius).toBeLessThan(shut.radius);
  });

  it("never grows the core when the dock opens, whatever the shape of the window", () => {
    const shapes: readonly (readonly [number, number])[] = [
      [1400, 900],
      [1000, 1400],
      [1920, 1080],
      [800, 600],
    ];

    for (const [width, height] of shapes) {
      const open = coreLayout(width, height, true, false);
      const shut = coreLayout(width, height, false, false);

      expect(open.radius).toBeLessThanOrEqual(shut.radius);
    }
  });

  it("shrinks the core to clear a caption instead of drawing underneath it", () => {
    // The decisive one: the mockup's core swallowed its own caption at this size. The reserved band
    // below the core is what keeps the brain's own words readable.
    const captioned = coreLayout(1600, 950, false, true);
    const bare = coreLayout(1600, 950, false, false);

    expect(captioned.radius).toBeLessThan(bare.radius);
  });

  it("keeps the whole core and its outer ring inside the free stage", () => {
    const width = 1600;
    const height = 950;
    const { radius, offsetX } = coreLayout(width, height, true, true);

    const centre = width / 2 + offsetX;
    const dock = Math.min(472, width * 0.46);

    expect(centre - radius * 1.42).toBeGreaterThanOrEqual(74);
    expect(centre + radius * 1.42).toBeLessThanOrEqual(width - dock);
  });

  it("keeps the core clear of the chrome above and the caption below", () => {
    const height = 950;
    const { radius, offsetY } = coreLayout(1600, height, false, true);
    const centre = height / 2 + offsetY;

    expect(centre - radius).toBeGreaterThanOrEqual(92);
    expect(centre + radius).toBeLessThanOrEqual(height - 330);
  });

  it("never returns a negative radius, however little room there is", () => {
    // A phone in landscape leaves less vertical room than the reserved bands want. Clamping at zero
    // draws nothing; a negative radius would draw the shell inside out.
    expect(coreLayout(320, 200, true, true).radius).toBeGreaterThanOrEqual(0);
  });
});

describe("displacement", () => {
  const point = { x: 0.4, y: 0.6, z: 0.69 };

  it("leaves the sphere undeformed when there is no reading", () => {
    const overTime = [0, 0.5, 1, 7.25].map((seconds) => displacement(point, seconds, null));

    expect(overTime).toEqual([1, 1, 1, 1]);
  });

  it("leaves the sphere undeformed at zero load, which is a reading of zero rather than none", () => {
    expect(displacement(point, 3, 0)).toBe(1);
  });

  it("deforms the sphere once there is load to show", () => {
    expect(displacement(point, 3, 0.8)).not.toBe(1);
  });

  it("keeps the deformation bounded so the shell cannot invert", () => {
    const extremes = Array.from({ length: 400 }, (_, step) => displacement(point, step / 20, 1));

    expect(Math.min(...extremes)).toBeGreaterThan(0.5);
    expect(Math.max(...extremes)).toBeLessThan(1.5);
  });
});

describe("project", () => {
  it("draws nearer points larger than farther ones", () => {
    const near = project({ x: 0, y: 0, z: 0.9 }, 100);
    const far = project({ x: 0, y: 0, z: -0.9 }, 100);

    expect(near.scale).toBeGreaterThan(far.scale);
  });

  it("leaves the centre of the sphere at the centre of the canvas", () => {
    expect(project({ x: 0, y: 0, z: 0 }, 100)).toMatchObject({ x: 0, y: 0 });
  });
});
