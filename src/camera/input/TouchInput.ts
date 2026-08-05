import { stickVector } from '../flyMath';
import type { FlyInputState, InputSource } from './types';

export interface TouchOptions {
  /** Radians of rotation per pixel dragged. */
  lookSensitivity?: number;
  /** Pixels from the stick origin for full deflection. */
  stickRadius?: number;
  /** Fraction of the radius ignored, to stop a resting thumb drifting. */
  deadzone?: number;
}

interface Stick {
  touchId: number;
  originX: number;
  originY: number;
  x: number;
  y: number;
}

/**
 * Touch controls for the fly camera.
 *
 * Mobile has neither pointer lock nor a keyboard, so the desktop scheme
 * transfers not at all. The layout here is the one that works for flying:
 *
 *   left half   floating virtual stick — strafe and forward/back
 *   right half  drag to look
 *   buttons     climb / descend / boost, thumb-reachable at the bottom right
 *
 * The stick is *floating* — it materialises wherever the left thumb lands
 * rather than sitting at a fixed spot. On a phone held any number of ways, a
 * fixed stick is a constant small aiming task; a floating one never is.
 *
 * Touches are tracked by identifier, so look and movement work simultaneously
 * and neither steals the other's finger.
 */
export class TouchInput implements InputSource {
  private readonly target: HTMLElement;
  private readonly root: HTMLElement;
  private readonly stickBase: HTMLElement;
  private readonly stickKnob: HTMLElement;
  private readonly opts: Required<TouchOptions>;

  private stick: Stick | null = null;
  private lookTouchId: number | null = null;
  private lastLookX = 0;
  private lastLookY = 0;
  private pendingDx = 0;
  private pendingDy = 0;

  private climb = 0;
  private boosting = false;

  constructor(target: HTMLElement, options: TouchOptions = {}) {
    this.target = target;
    this.opts = {
      lookSensitivity: options.lookSensitivity ?? 0.005,
      stickRadius: options.stickRadius ?? 64,
      deadzone: options.deadzone ?? 0.12,
    };

    this.root = document.createElement('div');
    this.root.className = 'touch-controls';
    this.root.innerHTML = `
      <div class="touch-stick" hidden>
        <div class="touch-stick-knob"></div>
      </div>
      <div class="touch-buttons">
        <button class="touch-btn" data-action="up" aria-label="Climb">▲</button>
        <button class="touch-btn" data-action="down" aria-label="Descend">▼</button>
        <button class="touch-btn touch-btn-boost" data-action="boost" aria-label="Boost">»</button>
      </div>`;
    document.body.appendChild(this.root);

    this.stickBase = this.root.querySelector('.touch-stick') as HTMLElement;
    this.stickKnob = this.root.querySelector('.touch-stick-knob') as HTMLElement;

    for (const btn of this.root.querySelectorAll<HTMLElement>('.touch-btn')) {
      btn.addEventListener('pointerdown', this.onButtonDown);
      btn.addEventListener('pointerup', this.onButtonUp);
      btn.addEventListener('pointercancel', this.onButtonUp);
      btn.addEventListener('pointerleave', this.onButtonUp);
      // Otherwise a held button starts a text selection / callout on iOS.
      btn.addEventListener('contextmenu', (e) => e.preventDefault());
    }

    target.addEventListener('touchstart', this.onTouchStart, { passive: false });
    target.addEventListener('touchmove', this.onTouchMove, { passive: false });
    target.addEventListener('touchend', this.onTouchEnd);
    target.addEventListener('touchcancel', this.onTouchEnd);
  }

  sample(state: FlyInputState): void {
    if (this.stick) {
      const { x, y } = this.normalisedStick();
      state.moveRight += x;
      state.moveForward -= y; // screen-down is backwards
    }
    state.moveUp += this.climb;
    state.lookDx += this.pendingDx;
    state.lookDy += this.pendingDy;
    state.boost ||= this.boosting;

    this.pendingDx = 0;
    this.pendingDy = 0;
  }

  dispose(): void {
    this.target.removeEventListener('touchstart', this.onTouchStart);
    this.target.removeEventListener('touchmove', this.onTouchMove);
    this.target.removeEventListener('touchend', this.onTouchEnd);
    this.target.removeEventListener('touchcancel', this.onTouchEnd);
    this.root.remove();
  }

  /** Stick deflection as -1..1 per axis, with the deadzone removed. */
  private normalisedStick(): { x: number; y: number } {
    if (!this.stick) return { x: 0, y: 0 };
    return stickVector(
      this.stick.x - this.stick.originX,
      this.stick.y - this.stick.originY,
      this.opts.stickRadius,
      this.opts.deadzone,
    );
  }

  private onTouchStart = (event: TouchEvent): void => {
    // Stops the page scrolling, rubber-banding and double-tap zooming under the
    // camera. touch-action:none in CSS covers most of it; this covers the rest.
    event.preventDefault();

    for (const touch of Array.from(event.changedTouches)) {
      const leftHalf = touch.clientX < window.innerWidth / 2;
      if (leftHalf && !this.stick) {
        this.stick = {
          touchId: touch.identifier,
          originX: touch.clientX,
          originY: touch.clientY,
          x: touch.clientX,
          y: touch.clientY,
        };
        this.showStick();
      } else if (!leftHalf && this.lookTouchId === null) {
        this.lookTouchId = touch.identifier;
        this.lastLookX = touch.clientX;
        this.lastLookY = touch.clientY;
      }
    }
  };

  private onTouchMove = (event: TouchEvent): void => {
    event.preventDefault();

    for (const touch of Array.from(event.changedTouches)) {
      if (this.stick && touch.identifier === this.stick.touchId) {
        this.stick.x = touch.clientX;
        this.stick.y = touch.clientY;
        this.updateStickVisual();
      } else if (touch.identifier === this.lookTouchId) {
        this.pendingDx -= (touch.clientX - this.lastLookX) * this.opts.lookSensitivity;
        this.pendingDy -= (touch.clientY - this.lastLookY) * this.opts.lookSensitivity;
        this.lastLookX = touch.clientX;
        this.lastLookY = touch.clientY;
      }
    }
  };

  private onTouchEnd = (event: TouchEvent): void => {
    for (const touch of Array.from(event.changedTouches)) {
      if (this.stick && touch.identifier === this.stick.touchId) {
        this.stick = null;
        this.hideStick();
      } else if (touch.identifier === this.lookTouchId) {
        this.lookTouchId = null;
      }
    }
  };

  private onButtonDown = (event: PointerEvent): void => {
    event.preventDefault();
    const action = (event.currentTarget as HTMLElement).dataset.action;
    if (action === 'up') this.climb = 1;
    else if (action === 'down') this.climb = -1;
    else if (action === 'boost') this.boosting = true;
    (event.currentTarget as HTMLElement).classList.add('is-active');
  };

  private onButtonUp = (event: PointerEvent): void => {
    const el = event.currentTarget as HTMLElement;
    const action = el.dataset.action;
    if (action === 'up' && this.climb > 0) this.climb = 0;
    else if (action === 'down' && this.climb < 0) this.climb = 0;
    else if (action === 'boost') this.boosting = false;
    el.classList.remove('is-active');
  };

  private showStick(): void {
    if (!this.stick) return;
    this.stickBase.hidden = false;
    this.stickBase.style.left = `${this.stick.originX}px`;
    this.stickBase.style.top = `${this.stick.originY}px`;
    this.updateStickVisual();
  }

  private hideStick(): void {
    this.stickBase.hidden = true;
    this.stickKnob.style.transform = 'translate(-50%, -50%)';
  }

  private updateStickVisual(): void {
    if (!this.stick) return;
    let dx = this.stick.x - this.stick.originX;
    let dy = this.stick.y - this.stick.originY;
    const dist = Math.hypot(dx, dy);
    if (dist > this.opts.stickRadius) {
      dx = (dx / dist) * this.opts.stickRadius;
      dy = (dy / dist) * this.opts.stickRadius;
    }
    this.stickKnob.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;
  }
}
