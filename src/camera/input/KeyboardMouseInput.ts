import type { FlyInputState, InputSource } from './types';

const KEY_BINDINGS = {
  KeyW: 'forward',
  KeyS: 'back',
  KeyA: 'left',
  KeyD: 'right',
  KeyE: 'up',
  KeyQ: 'down',
  ShiftLeft: 'boost',
  ShiftRight: 'boost',
  // Arrows look rather than move, because movement is already covered and
  // turning is not: mouse look needs pointer lock, so before you click the
  // canvas — or after Esc releases it — the camera can fly but cannot turn.
  // Mapping them to WASD's job would duplicate a capability instead of adding
  // the missing one.
  ArrowUp: 'lookUp',
  ArrowDown: 'lookDown',
  ArrowLeft: 'lookLeft',
  ArrowRight: 'lookRight',
} as const;

type Action = (typeof KEY_BINDINGS)[keyof typeof KEY_BINDINGS];

const INITIAL: Record<Action, boolean> = {
  forward: false,
  back: false,
  left: false,
  right: false,
  up: false,
  down: false,
  boost: false,
  lookUp: false,
  lookDown: false,
  lookLeft: false,
  lookRight: false,
};

export interface KeyboardMouseOptions {
  /** Radians of rotation per pixel of mouse movement. */
  lookSensitivity?: number;
  /** Radians per second of rotation while an arrow key is held. */
  keyLookRate?: number;
}

/** Desktop input: pointer-lock mouse look plus WASD/QE, and arrows to look. */
export class KeyboardMouseInput implements InputSource {
  private readonly canvas: HTMLCanvasElement;
  private readonly sensitivity: number;
  private readonly keyLookRate: number;
  private readonly keys: Record<Action, boolean> = { ...INITIAL };

  private pendingDx = 0;
  private pendingDy = 0;
  private locked = false;

  constructor(canvas: HTMLCanvasElement, options: KeyboardMouseOptions = {}) {
    this.canvas = canvas;
    this.sensitivity = options.lookSensitivity ?? 0.0022;
    // ~80°/s. Fast enough to come about without it feeling like a chore, slow
    // enough to frame a building with a tap. The camera's own look damping
    // smooths the start and stop, so this is the sustained rate, not a step.
    this.keyLookRate = options.keyLookRate ?? 1.4;

    canvas.addEventListener('click', this.requestLock);
    document.addEventListener('pointerlockchange', this.onLockChange);
    document.addEventListener('mousemove', this.onMouseMove);
    window.addEventListener('keydown', this.onKeyDown);
    window.addEventListener('keyup', this.onKeyUp);
    window.addEventListener('blur', this.clear);
  }

  get isLocked(): boolean {
    return this.locked;
  }

  sample(state: FlyInputState, dt: number): void {
    state.moveRight += Number(this.keys.right) - Number(this.keys.left);
    state.moveUp += Number(this.keys.up) - Number(this.keys.down);
    state.moveForward += Number(this.keys.forward) - Number(this.keys.back);
    state.boost ||= this.keys.boost;

    // Signs follow the mouse: there, moving right sends a negative lookDx and
    // the camera turns right, so ArrowRight has to be negative too.
    const step = this.keyLookRate * dt;
    state.lookDx += this.pendingDx + (Number(this.keys.lookLeft) - Number(this.keys.lookRight)) * step;
    state.lookDy += this.pendingDy + (Number(this.keys.lookUp) - Number(this.keys.lookDown)) * step;

    this.pendingDx = 0;
    this.pendingDy = 0;
  }

  dispose(): void {
    this.canvas.removeEventListener('click', this.requestLock);
    document.removeEventListener('pointerlockchange', this.onLockChange);
    document.removeEventListener('mousemove', this.onMouseMove);
    window.removeEventListener('keydown', this.onKeyDown);
    window.removeEventListener('keyup', this.onKeyUp);
    window.removeEventListener('blur', this.clear);
    if (this.locked) document.exitPointerLock();
  }

  private requestLock = (): void => {
    void this.canvas.requestPointerLock();
  };

  private onLockChange = (): void => {
    this.locked = document.pointerLockElement === this.canvas;
    if (!this.locked) this.clear();
  };

  private onMouseMove = (event: MouseEvent): void => {
    if (!this.locked) return;
    this.pendingDx -= event.movementX * this.sensitivity;
    this.pendingDy -= event.movementY * this.sensitivity;
  };

  private onKeyDown = (event: KeyboardEvent): void => {
    const action = KEY_BINDINGS[event.code as keyof typeof KEY_BINDINGS];
    if (!action) return;
    this.keys[action] = true;
    // Arrows suppress the browser's scroll unconditionally, not only under
    // pointer lock — they exist precisely for the case where the pointer is not
    // locked, and a key that turns the camera should not also move the page.
    if (this.locked || event.code.startsWith('Arrow')) event.preventDefault();
  };

  private onKeyUp = (event: KeyboardEvent): void => {
    const action = KEY_BINDINGS[event.code as keyof typeof KEY_BINDINGS];
    if (action) this.keys[action] = false;
  };

  /** Drop held keys — losing focus mid-move otherwise leaves them stuck on. */
  private clear = (): void => {
    Object.assign(this.keys, INITIAL);
  };
}
