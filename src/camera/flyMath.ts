/**
 * Pure maths behind the fly camera.
 *
 * Kept separate from the input/DOM plumbing so the parts that decide how the
 * camera *feels* can be unit-tested without a browser.
 */

export const SPEED_MIN = 8; // m/s, near the ground
export const SPEED_MAX = 4000; // m/s, high above the world
export const SPEED_PER_METRE = 0.5; // m/s of speed per metre of altitude
export const BOOST_FACTOR = 4;

/**
 * Movement speed as a function of altitude.
 *
 * This is the single most important feel detail in the whole camera. At 5 km up
 * a fixed 10 m/s feels frozen; at 20 m the same speed feels like a missile.
 * Scaling with altitude keeps apparent motion roughly constant, so the controls
 * behave the same whether you're inspecting a doorway or crossing the city.
 */
export function speedForAltitude(altitude: number, boosting = false): number {
  const base = clamp(altitude * SPEED_PER_METRE, SPEED_MIN, SPEED_MAX);
  return boosting ? base * BOOST_FACTOR : base;
}

/**
 * Frame-rate-independent exponential smoothing factor.
 *
 * Returns the fraction to move from `current` toward `target` this frame:
 *
 *     value += (target - value) * damp(rate, dt)
 *
 * A naive `lerp(current, target, 0.1)` is tempting but wrong — it converges
 * over a fixed number of *frames*, so the camera feels different at 30 fps and
 * 144 fps. This converges over a fixed amount of *time*.
 *
 * @param rate Higher converges faster. ~10 is snappy, ~3 is floaty.
 */
export function damp(rate: number, dt: number): number {
  return 1 - Math.exp(-rate * dt);
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Clamp pitch to just inside vertical.
 *
 * Exactly ±90° makes the yaw axis degenerate (the camera's forward vector
 * becomes parallel to world up), which produces a visible snap as the
 * look direction flips. Stopping just short avoids it and costs nothing —
 * you can still look essentially straight up and down.
 */
export const PITCH_LIMIT = Math.PI / 2 - 0.001;

export function clampPitch(pitch: number): number {
  return clamp(pitch, -PITCH_LIMIT, PITCH_LIMIT);
}

/**
 * Map a stick offset in pixels to a -1..1 vector, with a deadzone.
 *
 * The rescale past the deadzone matters: without it, control jumps straight
 * from nothing to `deadzone` worth of speed the moment the thumb crosses the
 * threshold. With it, movement starts from zero exactly where the deadzone
 * ends, which is the difference between a stick that feels sloppy and one that
 * feels precise.
 */
export function stickVector(
  dx: number,
  dy: number,
  radius: number,
  deadzone: number,
): { x: number; y: number } {
  const dist = Math.hypot(dx, dy);
  const dead = radius * deadzone;
  if (dist <= dead) return { x: 0, y: 0 };

  const clamped = Math.min(dist, radius);
  const t = (clamped - dead) / (radius - dead);
  return { x: (dx / dist) * t, y: (dy / dist) * t };
}
