import { describe, expect, it } from 'vitest';
import {
  BOOST_FACTOR,
  PITCH_LIMIT,
  SPEED_MAX,
  SPEED_MIN,
  clampPitch,
  damp,
  speedForAltitude,
} from '../src/camera/flyMath';

describe('speedForAltitude', () => {
  it('holds a usable floor near the ground', () => {
    expect(speedForAltitude(0)).toBe(SPEED_MIN);
    expect(speedForAltitude(5)).toBe(SPEED_MIN);
    // Below the point where alt * 0.5 overtakes the floor.
    expect(speedForAltitude(15)).toBe(SPEED_MIN);
  });

  it('scales with altitude through the useful range', () => {
    expect(speedForAltitude(100)).toBe(50);
    expect(speedForAltitude(500)).toBe(250);
    expect(speedForAltitude(5000)).toBe(2500);
  });

  it('caps so high-altitude flight stays controllable', () => {
    expect(speedForAltitude(100_000)).toBe(SPEED_MAX);
  });

  it('is monotonic — climbing never slows you down', () => {
    let previous = 0;
    for (let alt = 0; alt <= 20_000; alt += 250) {
      const speed = speedForAltitude(alt);
      expect(speed).toBeGreaterThanOrEqual(previous);
      previous = speed;
    }
  });

  it('applies boost as a multiplier', () => {
    expect(speedForAltitude(500, true)).toBe(250 * BOOST_FACTOR);
  });

  it('treats negative altitude as the floor rather than reversing', () => {
    expect(speedForAltitude(-100)).toBe(SPEED_MIN);
  });
});

describe('damp', () => {
  it('is frame-rate independent', () => {
    // Converging from 0 to 1 over one second should land in the same place
    // regardless of how many steps it took to get there. This is the whole
    // reason damp() exists instead of a fixed lerp factor.
    const rate = 8;
    const simulate = (fps: number): number => {
      const dt = 1 / fps;
      let value = 0;
      for (let i = 0; i < fps; i++) value += (1 - value) * damp(rate, dt);
      return value;
    };

    const at30 = simulate(30);
    const at144 = simulate(144);
    expect(Math.abs(at30 - at144)).toBeLessThan(0.001);

    // And it should actually be converging, not just consistently stuck.
    expect(at30).toBeGreaterThan(0.99);
  });

  it('returns 0 for no elapsed time and approaches 1 for long steps', () => {
    expect(damp(8, 0)).toBe(0);
    expect(damp(8, 10)).toBeGreaterThan(0.999);
  });

  it('converges faster at higher rates', () => {
    expect(damp(20, 1 / 60)).toBeGreaterThan(damp(5, 1 / 60));
  });
});

describe('clampPitch', () => {
  it('stops just short of vertical to keep yaw well-defined', () => {
    expect(clampPitch(Math.PI)).toBe(PITCH_LIMIT);
    expect(clampPitch(-Math.PI)).toBe(-PITCH_LIMIT);
    expect(PITCH_LIMIT).toBeLessThan(Math.PI / 2);
  });

  it('leaves ordinary angles alone', () => {
    expect(clampPitch(0)).toBe(0);
    expect(clampPitch(0.5)).toBe(0.5);
  });
});
