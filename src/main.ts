import * as THREE from 'three';
import { Viewer } from './engine/Viewer';
import { FlyCamera } from './camera/FlyCamera';
import { TilesetSource } from './world/TilesetSource';
import { setupLighting } from './scene/lighting';
import { Ground } from './scene/ground';
import { Hud, formatDistance } from './ui/Hud';
import { CityPicker } from './ui/CityPicker';
import { CITIES, cityFromLocation, rememberCity, type City } from './cities';
import { ERROR_TARGET, START_ALTITUDE } from './config';

const viewer = new Viewer(document.body);
const hud = new Hud();

const lighting = setupLighting(viewer.scene, viewer.renderer);

const flyCamera = new FlyCamera(viewer.camera, viewer.canvas);

let world: TilesetSource | null = null;
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

  world?.dispose();
  ground?.dispose();
  ground = null;
  if (next.ground) {
    ground = new Ground(next.ground);
    ground.attach(viewer.scene);
  }

  world = new TilesetSource(next.url, viewer.renderer, {
    errorTarget: ERROR_TARGET,
    anchor: next.anchor,
    onReady: ({ origin, radius }) => {
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
      // Re-enable the picker on failure, or a city that fails to load strands
      // the user with no way to choose another.
      picker.setBusy(false);
      hud.setOverlay(`Could not load ${next.label}.\n${error.message}`);
    },
  });

  world.attach(viewer.scene);
  if (window.app) window.app.world = world;
  // tools/capture-ortho.mjs reads this to georeference the raster it renders.
  (window as unknown as { __anchor?: unknown }).__anchor = next.anchor;
}

const picker = new CityPicker(CITIES, city, loadCity);

viewer.onUpdate((dt) => {
  flyCamera.update(dt);
  world?.update(viewer.camera, dt);
  // After the camera moves, so the shadow camera is fitted to where the view
  // actually is this frame rather than trailing it by one.
  lighting.update(viewer.camera);

  hud.update(dt, {
    alt: formatDistance(flyCamera.altitude),
    spd: `${flyCamera.speed.toFixed(0)} m/s`,
    tiles: String(world?.tiles.visibleTiles.size ?? 0),
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
      world: TilesetSource | null;
      flyCamera: FlyCamera;
      THREE: typeof THREE;
    };
  }
}
window.app = { viewer, world, flyCamera, THREE };

loadCity(city);
viewer.start();
