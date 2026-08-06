/**
 * Status readout: frame rate, camera altitude, and load progress.
 *
 * index.html ships the default city's credit in the markup rather than leaving
 * the line empty for JavaScript to fill, because ODbL requires it to be visible
 * without interaction — including before any script has run. Switching city
 * replaces it, since each source carries its own terms and showing the wrong
 * one is worse than showing none.
 */
export class Hud {
  private readonly el: HTMLElement;
  private readonly overlay: HTMLElement | null;
  private readonly overlayMsg: HTMLElement | null;
  private readonly attribution: HTMLElement | null;

  private frames = 0;
  private elapsed = 0;
  private fps = 0;

  constructor(elementId = 'stats') {
    const el = document.getElementById(elementId);
    if (!el) throw new Error(`HUD element #${elementId} not found`);
    this.el = el;
    this.overlay = document.getElementById('overlay');
    this.overlayMsg = document.getElementById('overlay-msg');
    this.attribution = document.getElementById('attribution');
  }

  /**
   * Replace the credit line. `html` comes from the city registry, which is
   * compile-time constants in this repository — never from a tileset.
   */
  setAttribution(html: string): void {
    if (this.attribution) this.attribution.innerHTML = html;
  }

  /** Show a blocking message over the scene. Pass null to hide it. */
  setOverlay(message: string | null): void {
    if (!this.overlay) return;
    if (message === null) {
      this.overlay.hidden = true;
    } else {
      this.overlay.hidden = false;
      if (this.overlayMsg) this.overlayMsg.textContent = message;
    }
  }

  update(dt: number, fields: Record<string, string>): void {
    this.frames++;
    this.elapsed += dt;
    if (this.elapsed >= 0.5) {
      this.fps = this.frames / this.elapsed;
      this.frames = 0;
      this.elapsed = 0;
    }

    const rows = [`${this.fps.toFixed(0)} fps`];
    for (const [key, value] of Object.entries(fields)) {
      rows.push(`${key} ${value}`);
    }
    this.el.textContent = rows.join('\n');
  }
}

/** Format a distance in metres as m or km, whichever reads better. */
export function formatDistance(metres: number): string {
  return metres >= 1000 ? `${(metres / 1000).toFixed(2)} km` : `${metres.toFixed(0)} m`;
}
