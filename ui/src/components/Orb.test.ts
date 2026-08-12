/**
 * The orb's arithmetic, tested without a canvas.
 *
 * One of these is a security-adjacent test rather than a graphics one: `displacement` returning
 * exactly 1 for a null reading is how red line 10 reaches the animation. A sphere that breathed
 * anyway would be a plausible-looking placeholder for a sensor that reported nothing.
 */

import { describe, expect, it } from "vitest";

import { displacement, project, spherePoints } from "./Orb";

describe("spherePoints", () => {
  it("returns the number of points it was asked for", () => {
    expect(spherePoints(64)).toHaveLength(64);
  });

  it("places every point on the unit sphere", () => {
    const offSurface = spherePoints(200).filter((point) => {
      const length = Math.hypot(point.x, point.y, point.z);
      return Math.abs(length - 1) > 1e-9;
    });

    expect(offSurface).toEqual([]);
  });

  it("spreads points across both poles rather than bunching at one", () => {
    const heights = spherePoints(200).map((point) => point.y);

    expect(Math.min(...heights)).toBeCloseTo(-1, 6);
    expect(Math.max(...heights)).toBeCloseTo(1, 6);
  });

  it("does not divide by zero for a single point", () => {
    expect(spherePoints(1)).toEqual([{ x: 1, y: 0, z: 0 }]);
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
