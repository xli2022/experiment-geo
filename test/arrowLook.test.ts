import { afterEach, describe, expect, it, vi } from 'vitest';
import { KeyboardMouseInput } from '../src/camera/input/KeyboardMouseInput';
import { createInputState, resetInputState } from '../src/camera/input/types';

/**
 * Arrow-key look is the one input in the scheme that is a *rate* — the key
 * reports only that it is down, so the angle has to come from the frame time.
 * Get that wrong and turning is faster on a fast machine, which is the same
 * class of bug the damping tests guard against.
 *
 * There is no DOM in this environment, so the listeners are captured from a
 * stubbed window rather than dispatched against a real one. That is enough:
 * everything under test is what `sample` does with keys that are held.
 */
type Handler = (event: { code: string; preventDefault(): void }) => void;

function mount(): { input: KeyboardMouseInput; press: (code: string) => void; release: (code: string) => void } {
  const handlers: Record<string, Handler> = {};
  const target = {
    addEventListener: (type: string, fn: Handler) => {
      handlers[type] = fn;
    },
    removeEventListener: () => {},
    pointerLockElement: null,
  };
  vi.stubGlobal('window', target);
  vi.stubGlobal('document', target);

  const input = new KeyboardMouseInput(target as unknown as HTMLCanvasElement);
  const send = (type: string, code: string) => handlers[type]?.({ code, preventDefault: () => {} });
  return {
    input,
    press: (code) => send('keydown', code),
    release: (code) => send('keyup', code),
  };
}

let mountedInput: KeyboardMouseInput | null = null;

afterEach(() => {
  mountedInput?.dispose();
  mountedInput = null;
  vi.unstubAllGlobals();
});

describe('arrow-key look', () => {
  it('turns at the same total angle regardless of frame rate', () => {
    const held = (steps: number) => {
      const { input, press } = mount();
      mountedInput = input;
      press('ArrowLeft');
      const state = createInputState();
      let total = 0;
      for (let i = 0; i < steps; i++) {
        resetInputState(state);
        input.sample(state, 1 / steps);
        total += state.lookDx;
      }
      input.dispose();
      mountedInput = null;
      return total;
    };
    // One second of holding, at 30 fps and at 144 fps.
    expect(held(30)).toBeCloseTo(held(144), 10);
  });

  it('turns the same way the mouse does', () => {
    const yaw = (code: string) => {
      const { input, press } = mount();
      mountedInput = input;
      press(code);
      const state = createInputState();
      input.sample(state, 1 / 60);
      input.dispose();
      mountedInput = null;
      return state;
    };
    // FlyCamera does `targetYaw += lookDx`, and yaw increases anticlockwise, so
    // turning right has to be negative — matching what the mouse produces when
    // it moves right.
    expect(yaw('ArrowRight').lookDx).toBeLessThan(0);
    expect(yaw('ArrowLeft').lookDx).toBeGreaterThan(0);
    // Pitch: up is positive, and FlyCamera clamps it short of vertical.
    expect(yaw('ArrowUp').lookDy).toBeGreaterThan(0);
    expect(yaw('ArrowDown').lookDy).toBeLessThan(0);
  });

  it('does not move the camera — arrows look, WASD moves', () => {
    const { input, press } = mount();
    mountedInput = input;
    for (const code of ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']) press(code);
    const state = createInputState();
    input.sample(state, 1 / 60);
    expect(state.moveForward).toBe(0);
    expect(state.moveRight).toBe(0);
    expect(state.moveUp).toBe(0);
  });

  it('stops turning when the key comes up', () => {
    const { input, press, release } = mount();
    mountedInput = input;
    press('ArrowLeft');
    const state = createInputState();
    input.sample(state, 1 / 60);
    expect(state.lookDx).toBeGreaterThan(0);

    release('ArrowLeft');
    resetInputState(state);
    input.sample(state, 1 / 60);
    expect(state.lookDx).toBe(0);
  });

  it('opposite arrows cancel rather than fighting', () => {
    const { input, press } = mount();
    mountedInput = input;
    press('ArrowLeft');
    press('ArrowRight');
    const state = createInputState();
    input.sample(state, 1 / 60);
    expect(state.lookDx).toBe(0);
  });
});
