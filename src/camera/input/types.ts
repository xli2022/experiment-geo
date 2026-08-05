/**
 * Camera input, decoupled from where it comes from.
 *
 * The fly camera reads a single accumulated state each frame rather than owning
 * listeners itself, so keyboard/mouse and touch can be active simultaneously —
 * which matters on hybrid devices, and means neither has to know about the
 * other.
 */
export interface FlyInputState {
  /** Strafe, -1 (left) to 1 (right). */
  moveRight: number;
  /** World-aligned vertical, -1 (down) to 1 (up). */
  moveUp: number;
  /** Forward, -1 (back) to 1 (forward). */
  moveForward: number;
  /** Yaw delta in radians for this frame. Consumed and reset by the camera. */
  lookDx: number;
  /** Pitch delta in radians for this frame. */
  lookDy: number;
  /** Speed multiplier is applied while true. */
  boost: boolean;
}

export function createInputState(): FlyInputState {
  return { moveRight: 0, moveUp: 0, moveForward: 0, lookDx: 0, lookDy: 0, boost: false };
}

export function resetInputState(s: FlyInputState): void {
  s.moveRight = 0;
  s.moveUp = 0;
  s.moveForward = 0;
  s.lookDx = 0;
  s.lookDy = 0;
  s.boost = false;
}

export interface InputSource {
  /**
   * Add this source's contribution to `state`.
   *
   * Additive rather than assigning, so several sources compose. Sources holding
   * per-frame deltas (mouse movement, touch drag) must zero them here, or the
   * camera keeps turning after the input stops.
   */
  sample(state: FlyInputState): void;
  dispose(): void;
}
