import type { City } from '../cities';

/**
 * The city selector.
 *
 * A plain `<select>` rather than a custom menu, because this is a control that
 * sits over a WebGL canvas that swallows pointer events: the native element
 * already handles keyboard, touch, and the platform's own dropdown, and does it
 * correctly on a phone where a hand-rolled menu would need rewriting.
 */
export class CityPicker {
  private readonly el: HTMLSelectElement;
  private busy = false;

  constructor(cities: readonly City[], current: City, onPick: (city: City) => void) {
    const el = document.getElementById('city');
    if (!(el instanceof HTMLSelectElement)) throw new Error('city picker element #city not found');
    this.el = el;

    for (const city of cities) {
      const option = document.createElement('option');
      option.value = city.id;
      option.textContent = city.label;
      el.append(option);
    }
    el.value = current.id;

    el.addEventListener('change', () => {
      const picked = cities.find((c) => c.id === el.value);
      // Ignore input while a city is still loading. Tearing down a tileset
      // mid-load leaves its in-flight requests writing into a disposed
      // renderer, and the select is the one control that can be hammered.
      if (!picked || this.busy) return;
      onPick(picked);
    });

    // The canvas takes pointer lock on click, and a locked pointer cannot open
    // a dropdown. Releasing it here means the picker works whether or not the
    // user is already flying.
    el.addEventListener('pointerdown', () => {
      if (document.pointerLockElement) document.exitPointerLock();
    });
  }

  /** Disable while a world loads, so a second pick cannot race the first. */
  setBusy(busy: boolean): void {
    this.busy = busy;
    this.el.disabled = busy;
  }

  /** Reflect a city chosen by something other than the dropdown. */
  setCurrent(city: City): void {
    this.el.value = city.id;
  }
}
