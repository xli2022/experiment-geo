import * as THREE from 'three';
import { Viewer } from './engine/Viewer';
import { FlyCamera } from './camera/FlyCamera';
import { TilesetSource } from './world/TilesetSource';
import { setupLighting } from './scene/lighting';
import { Ground } from './scene/ground';
import { Hud, formatDistance } from './ui/Hud';
import { CityPicker } from './ui/CityPicker';
import { CITIES, cityFromLocation, layerUrl, rememberCity, type City } from './cities';
import { ERROR_TARGET, START_ALTITUDE } from './config';

const viewer = new Viewer(document.body);
const hud = new Hud();

const lighting = setupLighting(viewer.scene, viewer.renderer);

const flyCamera = new FlyCamera(viewer.camera, viewer.canvas);

/** Every tileset of the current city. The first is the one framing waits on. */
let worlds: TilesetSource[] = [];
let ground: Ground | null = null;
let city = cityFromLocation();

/**
 * Tear down whatever world is loaded and bring up another.
 *
 * Disposing first rather than cross-fading: two tilesets in the scene at once
 * would both be streaming against the same memory budget, and the old one is
 * about to be thrown away regardless. A blank moment under the overlay is
 * cheaper than a stall.
 */
function loadCity(next: City): void {
  city = next;
  picker.setBusy(true);
  hud.setOverlay(`Loading ${next.label}…`);
  hud.setAttribution(next.attribution);
  rememberCity(next);

  for (const w of worlds) w.dispose();
  worlds = [];
  ground?.dispose();
  ground = null;
  if (next.ground) {
    ground = new Ground(next.ground);
    ground.attach(viewer.scene);
  }

  // Every layer is anchored to the same point, so they land in one frame
  // rather than each recentring on its own bounding sphere.
  worlds = next.layers.map((layer, i) =>
    new TilesetSource(layerUrl(next, layer), viewer.renderer, {
      errorTarget: layer.errorTarget ?? ERROR_TARGET,
      anchor: next.anchor,
      material: next.material,
      // Only the first layer drives framing and the overlay. The rest stream in
      // behind it; waiting for all of them would hold a blank screen until the
      // slowest backdrop finished.
      onReady: i > 0 ? undefined : ({ origin, radius }) => {
      // The tileset recentres itself on its own bounding sphere, so the world
      // origin is its middle. Back off far enough to see the whole thing.
      const distance = Math.max(radius * 1.2, next.startAltitude ?? START_ALTITUDE);
      viewer.camera.position.set(0, distance * 0.6, distance);
      viewer.camera.lookAt(0, 0, 0);
      // The controller holds its own orientation state, so it has to be told
      // about the lookAt or it will overwrite it on the next frame.
      flyCamera.syncFromCamera();

      hud.setOverlay(null);
      picker.setBusy(false);
        console.info(
          `${next.label} ready at ${origin.lat.toFixed(5)}, ${origin.lon.toFixed(5)} ` +
            `(radius ${radius.toFixed(0)} m)`,
        );
      },
      onError: (error) => {
        console.error(error);
        // A backdrop layer failing should not strand the city — only the layer
        // that framing depends on puts the overlay up.
        if (i > 0) return;
        picker.setBusy(false);
        hud.setOverlay(`Could not load ${next.label}.\n${error.message}`);
      },
    }),
  );

  for (const w of worlds) w.attach(viewer.scene);
  if (window.app) window.app.worlds = worlds;
  // tools/capture-ortho.mjs reads this to georeference the raster it renders.
  (window as unknown as { __anchor?: unknown }).__anchor = next.anchor;
}

const picker = new CityPicker(CITIES, city, loadCity);

viewer.onUpdate((dt) => {
  flyCamera.update(dt);
  for (const w of worlds) w.update(viewer.camera, dt);
  // After the camera moves, so the shadow camera is fitted to where the view
  // actually is this frame rather than trailing it by one.
  lighting.update(viewer.camera);

  hud.update(dt, {
    alt: formatDistance(flyCamera.altitude),
    spd: `${flyCamera.speed.toFixed(0)} m/s`,
    tiles: String(worlds.reduce((n, w) => n + w.tiles.visibleTiles.size, 0)),
    ...(flyCamera.isLocked ? {} : { '': CONTROL_HINT }),
  });
});

// Coarse pointers get the touch scheme, so tell them about that instead of
// asking them to click and press keys they do not have.
const CONTROL_HINT = matchMedia('(pointer: coarse)').matches
  ? 'left: move · right: look · buttons: climb'
  : 'click to fly · WASD + QE · arrows look · shift';

// Expose for debugging from the console.
declare global {
  interface Window {
    app?: {
      viewer: Viewer;
      worlds: TilesetSource[];
      flyCamera: FlyCamera;
      THREE: typeof THREE;
    };
  }
}
window.app = { viewer, worlds, flyCamera, THREE };

loadCity(city);
viewer.start();
