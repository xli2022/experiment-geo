import * as THREE from 'three';
import { Viewer } from './engine/Viewer';
import { FlyCamera } from './camera/FlyCamera';
import { TilesetSource } from './world/TilesetSource';
import { setupLighting } from './scene/lighting';
import { Hud, formatDistance } from './ui/Hud';
import { ERROR_TARGET, START_ALTITUDE, TILESET_ANCHOR, TILESET_URL } from './config';

const viewer = new Viewer(document.body);
const hud = new Hud();

setupLighting(viewer.scene, viewer.renderer);

const flyCamera = new FlyCamera(viewer.camera, viewer.canvas);

const world = new TilesetSource(TILESET_URL, viewer.renderer, {
  errorTarget: ERROR_TARGET,
  anchor: TILESET_ANCHOR,
  onReady: ({ origin, radius }) => {
    // The tileset recentres itself on its own bounding sphere, so the world
    // origin is its middle. Back off far enough to see the whole thing.
    const distance = Math.max(radius * 1.2, START_ALTITUDE);
    viewer.camera.position.set(0, distance * 0.6, distance);
    viewer.camera.lookAt(0, 0, 0);
    // The controller holds its own orientation state, so it has to be told
    // about the lookAt or it will overwrite it on the next frame.
    flyCamera.syncFromCamera();

    hud.setOverlay(null);
    console.info(
      `Tileset ready at ${origin.lat.toFixed(5)}, ${origin.lon.toFixed(5)} ` +
        `(radius ${radius.toFixed(0)} m)`,
    );
  },
  onError: (error) => {
    console.error(error);
    hud.setOverlay(`Could not load the world.\n${error.message}`);
  },
});

world.attach(viewer.scene);
hud.setOverlay('Loading the world…');

viewer.onUpdate((dt) => {
  flyCamera.update(dt);
  world.update(viewer.camera, dt);

  hud.update(dt, {
    alt: formatDistance(flyCamera.altitude),
    spd: `${flyCamera.speed.toFixed(0)} m/s`,
    tiles: String(world.tiles.visibleTiles.size),
    ...(flyCamera.isLocked ? {} : { '': CONTROL_HINT }),
  });
});

// Coarse pointers get the touch scheme, so tell them about that instead of
// asking them to click and press keys they do not have.
const CONTROL_HINT = matchMedia('(pointer: coarse)').matches
  ? 'left: move · right: look · buttons: climb'
  : 'click to fly · WASD + QE · shift';

viewer.start();

// Expose for debugging from the console.
declare global {
  interface Window {
    app?: { viewer: Viewer; world: TilesetSource; flyCamera: FlyCamera; THREE: typeof THREE };
  }
}
window.app = { viewer, world, flyCamera, THREE };
// tools/capture-ortho.mjs reads this to georeference the raster it renders.
(window as unknown as { __anchor?: unknown }).__anchor = TILESET_ANCHOR ?? null;
