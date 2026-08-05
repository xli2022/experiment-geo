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
};

export interface KeyboardMouseOptions {
  /** Radians of rotation per pixel of mouse movement. */
  lookSensitivity?: number;
}

/** Desktop input: pointer-lock mouse look plus WASD/QE. */
export class KeyboardMouseInput implements InputSource {
  private readonly canvas: HTMLCanvasElement;
  private readonly sensitivity: number;
  private readonly keys: Record<Action, boolean> = { ...INITIAL };

  private pendingDx = 0;
  private pendingDy = 0;
  private locked = false;

  constructor(canvas: HTMLCanvasElement, options: KeyboardMouseOptions = {}) {
    this.canvas = canvas;
    this.sensitivity = options.lookSensitivity ?? 0.0022;

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

  sample(state: FlyInputState): void {
    state.moveRight += Number(this.keys.right) - Number(this.keys.left);
    state.moveUp += Number(this.keys.up) - Number(this.keys.down);
    state.moveForward += Number(this.keys.forward) - Number(this.keys.back);
    state.lookDx += this.pendingDx;
    state.lookDy += this.pendingDy;
    state.boost ||= this.keys.boost;

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
    if (this.locked) event.preventDefault();
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
