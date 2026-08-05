import * as THREE from 'three';
import { clampPitch, damp, speedForAltitude } from './flyMath';

export interface FlyCameraOptions {
  /** Radians of rotation per pixel of mouse movement. */
  lookSensitivity?: number;
  /** Smoothing rate for movement. Higher is snappier. */
  moveDamping?: number;
  /** Smoothing rate for look direction. Higher is snappier. */
  lookDamping?: number;
  /** Ground height in metres, used to derive altitude for the speed curve. */
  groundHeight?: number;
}

const KEY_BINDINGS: Record<string, keyof typeof INITIAL_INPUT> = {
  KeyW: 'forward',
  KeyS: 'back',
  KeyA: 'left',
  KeyD: 'right',
  KeyE: 'up',
  KeyQ: 'down',
  Space: 'up',
  ShiftLeft: 'boost',
  ShiftRight: 'boost',
};

const INITIAL_INPUT = {
  forward: false,
  back: false,
  left: false,
  right: false,
  up: false,
  down: false,
  boost: false,
};

/**
 * Six-degree-of-freedom free-fly camera driven by pointer lock.
 *
 * Deliberately does not allow roll: for a viewer (as opposed to a flight sim)
 * it causes more disorientation than it adds, and it makes "which way is up"
 * ambiguous when you stop moving.
 */
export class FlyCamera {
  private readonly camera: THREE.PerspectiveCamera;
  private readonly canvas: HTMLCanvasElement;
  private readonly opts: Required<FlyCameraOptions>;

  private readonly input = { ...INITIAL_INPUT };

  /** Target orientation, driven directly by the mouse. */
  private targetYaw = 0;
  private targetPitch = 0;
  /** Smoothed orientation, what the camera actually uses. */
  private yaw = 0;
  private pitch = 0;

  private readonly velocity = new THREE.Vector3();
  private locked = false;

  constructor(
    camera: THREE.PerspectiveCamera,
    canvas: HTMLCanvasElement,
    options: FlyCameraOptions = {},
  ) {
    this.camera = camera;
    this.canvas = canvas;
    this.opts = {
      lookSensitivity: options.lookSensitivity ?? 0.0022,
      moveDamping: options.moveDamping ?? 8,
      lookDamping: options.lookDamping ?? 25,
      groundHeight: options.groundHeight ?? 0,
    };

    // Adopt the camera's current orientation so enabling the controls doesn't
    // snap the view somewhere else.
    const euler = new THREE.Euler().setFromQuaternion(camera.quaternion, 'YXZ');
    this.yaw = this.targetYaw = euler.y;
    this.pitch = this.targetPitch = clampPitch(euler.x);

    canvas.addEventListener('click', this.requestLock);
    document.addEventListener('pointerlockchange', this.onLockChange);
    document.addEventListener('mousemove', this.onMouseMove);
    window.addEventListener('keydown', this.onKeyDown);
    window.addEventListener('keyup', this.onKeyUp);
    window.addEventListener('blur', this.clearInput);
  }

  get isLocked(): boolean {
    return this.locked;
  }

  /** Height above ground in metres. */
  get altitude(): number {
    return this.camera.position.y - this.opts.groundHeight;
  }

  get speed(): number {
    return speedForAltitude(this.altitude, this.input.boost);
  }

  update(dt: number): void {
    // Look: smooth toward the mouse-driven target.
    const lookAlpha = damp(this.opts.lookDamping, dt);
    this.yaw += (this.targetYaw - this.yaw) * lookAlpha;
    this.pitch += (this.targetPitch - this.pitch) * lookAlpha;

    this.camera.quaternion.setFromEuler(new THREE.Euler(this.pitch, this.yaw, 0, 'YXZ'));

    // Movement: build a desired velocity in camera space, then smooth toward it.
    const desired = new THREE.Vector3(
      Number(this.input.right) - Number(this.input.left),
      Number(this.input.up) - Number(this.input.down),
      Number(this.input.back) - Number(this.input.forward),
    );

    if (desired.lengthSq() > 0) {
      desired.normalize().multiplyScalar(this.speed);
      // Horizontal movement follows where you look; vertical stays world-aligned
      // so Q/E always mean "straight down/up" regardless of pitch.
      const vertical = desired.y;
      desired.y = 0;
      desired.applyQuaternion(this.camera.quaternion);
      desired.y += vertical;
    }

    const moveAlpha = damp(this.opts.moveDamping, dt);
    this.velocity.addScaledVector(desired.sub(this.velocity), moveAlpha);
    this.camera.position.addScaledVector(this.velocity, dt);
  }

  dispose(): void {
    this.canvas.removeEventListener('click', this.requestLock);
    document.removeEventListener('pointerlockchange', this.onLockChange);
    document.removeEventListener('mousemove', this.onMouseMove);
    window.removeEventListener('keydown', this.onKeyDown);
    window.removeEventListener('keyup', this.onKeyUp);
    window.removeEventListener('blur', this.clearInput);
    if (this.locked) document.exitPointerLock();
  }

  private requestLock = (): void => {
    void this.canvas.requestPointerLock();
  };

  private onLockChange = (): void => {
    this.locked = document.pointerLockElement === this.canvas;
    if (!this.locked) this.clearInput();
  };

  private onMouseMove = (event: MouseEvent): void => {
    if (!this.locked) return;
    this.targetYaw -= event.movementX * this.opts.lookSensitivity;
    this.targetPitch = clampPitch(this.targetPitch - event.movementY * this.opts.lookSensitivity);
  };

  private onKeyDown = (event: KeyboardEvent): void => {
    const action = KEY_BINDINGS[event.code];
    if (!action) return;
    this.input[action] = true;
    if (this.locked) event.preventDefault();
  };

  private onKeyUp = (event: KeyboardEvent): void => {
    const action = KEY_BINDINGS[event.code];
    if (action) this.input[action] = false;
  };

  /** Drop all held keys — otherwise losing focus mid-move leaves it stuck on. */
  private clearInput = (): void => {
    Object.assign(this.input, INITIAL_INPUT);
  };
}
