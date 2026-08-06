import * as THREE from 'three';
import { clampPitch, damp, speedForAltitude } from './flyMath';
import { KeyboardMouseInput } from './input/KeyboardMouseInput';
import { TouchInput } from './input/TouchInput';
import { createInputState, resetInputState, type InputSource } from './input/types';

export interface FlyCameraOptions {
  /** Smoothing rate for movement. Higher is snappier. */
  moveDamping?: number;
  /** Smoothing rate for look direction. Higher is snappier. */
  lookDamping?: number;
  /** Ground height in metres, used to derive altitude for the speed curve. */
  groundHeight?: number;
}

/**
 * Six-degree-of-freedom free-fly camera.
 *
 * Deliberately does not allow roll: for a viewer (as opposed to a flight sim)
 * it causes more disorientation than it adds, and it makes "which way is up"
 * ambiguous when you stop moving.
 *
 * Input comes from `InputSource`s rather than listeners owned here, so
 * keyboard/mouse and touch both work — and can be active at once on hybrid
 * devices without either knowing about the other.
 */
export class FlyCamera {
  private readonly camera: THREE.PerspectiveCamera;
  private readonly opts: Required<FlyCameraOptions>;
  private readonly sources: InputSource[] = [];
  private readonly state = createInputState();
  private readonly keyboardMouse: KeyboardMouseInput;

  /** Target orientation, driven by input. */
  private targetYaw = 0;
  private targetPitch = 0;
  /** Smoothed orientation, what the camera actually uses. */
  private yaw = 0;
  private pitch = 0;

  private readonly velocity = new THREE.Vector3();
  private readonly desired = new THREE.Vector3();
  private readonly euler = new THREE.Euler(0, 0, 0, 'YXZ');

  constructor(
    camera: THREE.PerspectiveCamera,
    canvas: HTMLCanvasElement,
    options: FlyCameraOptions = {},
  ) {
    this.camera = camera;
    this.opts = {
      moveDamping: options.moveDamping ?? 8,
      lookDamping: options.lookDamping ?? 25,
      groundHeight: options.groundHeight ?? 0,
    };

    this.keyboardMouse = new KeyboardMouseInput(canvas);
    this.sources.push(this.keyboardMouse);

    // Attached whenever touch is available rather than on a device sniff:
    // laptops with touchscreens get both, and nothing is shown until a touch
    // actually happens.
    if (hasTouch()) {
      this.sources.push(new TouchInput(canvas));
    }

    this.syncFromCamera();
  }

  /** True while the desktop pointer lock is engaged. Always false on touch. */
  get isLocked(): boolean {
    return this.keyboardMouse.isLocked;
  }

  /** Height above ground in metres. */
  get altitude(): number {
    return this.camera.position.y - this.opts.groundHeight;
  }

  get speed(): number {
    return speedForAltitude(this.altitude, this.state.boost);
  }

  /**
   * Adopt the camera's current orientation as the new target.
   *
   * Call this after moving or re-aiming the camera externally (e.g. framing the
   * world once it loads). Without it, the next update() would immediately
   * overwrite that orientation with whatever this controller was last holding,
   * silently discarding a `lookAt`.
   */
  syncFromCamera(): void {
    this.euler.setFromQuaternion(this.camera.quaternion, 'YXZ');
    this.yaw = this.targetYaw = this.euler.y;
    this.pitch = this.targetPitch = clampPitch(this.euler.x);
    this.velocity.set(0, 0, 0);
  }

  update(dt: number): void {
    resetInputState(this.state);
    for (const source of this.sources) source.sample(this.state, dt);

    this.targetYaw += this.state.lookDx;
    this.targetPitch = clampPitch(this.targetPitch + this.state.lookDy);

    // Look: smooth toward the input-driven target.
    const lookAlpha = damp(this.opts.lookDamping, dt);
    this.yaw += (this.targetYaw - this.yaw) * lookAlpha;
    this.pitch += (this.targetPitch - this.pitch) * lookAlpha;
    this.euler.set(this.pitch, this.yaw, 0, 'YXZ');
    this.camera.quaternion.setFromEuler(this.euler);

    // Movement: build a desired velocity, then smooth toward it. Analogue
    // sources can exceed 1 per axis when combined, so clamp rather than
    // normalise — normalising would make a half-deflected stick full speed.
    this.desired.set(
      THREE.MathUtils.clamp(this.state.moveRight, -1, 1),
      THREE.MathUtils.clamp(this.state.moveUp, -1, 1),
      -THREE.MathUtils.clamp(this.state.moveForward, -1, 1),
    );

    if (this.desired.lengthSq() > 0) {
      if (this.desired.lengthSq() > 1) this.desired.normalize();
      this.desired.multiplyScalar(this.speed);
      // Horizontal movement follows where you look; vertical stays world-aligned
      // so climb/descend always mean straight down/up regardless of pitch.
      const vertical = this.desired.y;
      this.desired.y = 0;
      this.desired.applyQuaternion(this.camera.quaternion);
      this.desired.y += vertical;
    }

    const moveAlpha = damp(this.opts.moveDamping, dt);
    this.velocity.addScaledVector(this.desired.sub(this.velocity), moveAlpha);
    this.camera.position.addScaledVector(this.velocity, dt);
  }

  dispose(): void {
    for (const source of this.sources) source.dispose();
    this.sources.length = 0;
  }
}

function hasTouch(): boolean {
  return 'ontouchstart' in window || navigator.maxTouchPoints > 0;
}
