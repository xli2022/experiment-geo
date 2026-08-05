import * as THREE from 'three';
import { TilesRenderer } from '3d-tiles-renderer';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import type { WorldSource } from './WorldSource';

export interface TilesetSourceOptions {
  /**
   * Target screen-space error in pixels. Lower loads more detail and costs
   * more memory; higher is coarser but cheaper.
   */
  errorTarget?: number;
  /** Maximum tiles held in the LRU cache before eviction starts. */
  maxCachedTiles?: number;
  /**
   * Geodetic point (degrees) to place at the world origin.
   *
   * Without this the tileset re-centres on its own bounding sphere, which for a
   * city-wide tileset is its geometric middle — tens of kilometres from
   * wherever you actually downloaded detail, leaving the camera parked over
   * empty space. Set it when using a partial subtree of a large tileset.
   */
  anchor?: { lat: number; lon: number; height?: number };
  /** Called once the root tileset is parsed and the world has been placed. */
  onReady?: (info: TilesetInfo) => void;
  onError?: (error: Error) => void;
}

export interface TilesetInfo {
  /** Geodetic position the world was recentred on, in **degrees**. */
  origin: { lat: number; lon: number; height: number };
  /** Radius of the tileset's bounding sphere, in metres. */
  radius: number;
}

const tmpSphere = new THREE.Sphere();
const tmpMatrix = new THREE.Matrix4();
const tmpCarto = { lat: 0, lon: 0, height: 0 };

/**
 * Streams OGC 3D Tiles baked offline by OSM2World.
 *
 * 3D Tiles are georeferenced in earth-centred, earth-fixed (ECEF) coordinates,
 * where a city sits ~6,400 km from the origin on an arbitrarily tilted axis.
 * Rendering there directly would both look wrong and burn all of float32's
 * precision on the offset. So once the root tileset loads we re-anchor the
 * whole group: the tileset's centre goes to the world origin, with the local
 * east-north-up frame aligned to three.js's Y-up convention.
 */
export class TilesetSource implements WorldSource {
  readonly tiles: TilesRenderer;

  private readonly dracoLoader: DRACOLoader;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly opts: TilesetSourceOptions;
  private scene: THREE.Scene | null = null;
  private placed = false;

  constructor(url: string, renderer: THREE.WebGLRenderer, options: TilesetSourceOptions = {}) {
    this.renderer = renderer;
    this.opts = options;

    this.tiles = new TilesRenderer(url);
    this.tiles.errorTarget = options.errorTarget ?? 12;
    if (options.maxCachedTiles !== undefined) {
      this.tiles.lruCache.maxSize = options.maxCachedTiles;
    }

    // Tiles are Draco-compressed by tools/optimize-tiles.sh (a ~26x saving, and
    // the thing that makes the bake shippable at all), so the loader needs a
    // decoder or every tile fails with "No DRACOLoader instance provided".
    // The decoder is vendored into public/draco rather than pulled from a CDN,
    // so the app has no third-party runtime dependency.
    this.dracoLoader = new DRACOLoader();
    this.dracoLoader.setDecoderPath(`${import.meta.env.BASE_URL}draco/`);

    const gltfLoader = new GLTFLoader(this.tiles.manager);
    gltfLoader.setDRACOLoader(this.dracoLoader);
    // TilesRenderer looks these up as the literal strings 'path.gltf'/'path.glb'.
    this.tiles.manager.addHandler(/\.gltf$/, gltfLoader);
    this.tiles.manager.addHandler(/\.glb$/, gltfLoader);

    this.tiles.addEventListener('load-root-tileset', this.place);
    this.tiles.addEventListener('load-error', this.onLoadError);
  }

  attach(scene: THREE.Scene): void {
    this.scene = scene;
    scene.add(this.tiles.group);
  }

  update(camera: THREE.Camera, _dt: number): void {
    // setCamera is idempotent — it returns false if the camera is already
    // registered — so this also handles the camera being swapped at runtime.
    if (!this.tiles.hasCamera(camera)) {
      this.tiles.setCamera(camera);
    }
    this.tiles.setResolutionFromRenderer(camera, this.renderer);
    this.tiles.update();
  }

  dispose(): void {
    this.tiles.removeEventListener('load-root-tileset', this.place);
    this.tiles.removeEventListener('load-error', this.onLoadError);
    this.scene?.remove(this.tiles.group);
    this.tiles.dispose();
    this.dracoLoader.dispose();
    this.scene = null;
  }

  /**
   * Move the tileset from ECEF to a local Y-up frame centred on its own
   * bounding sphere.
   */
  private place = (): void => {
    if (this.placed) return;
    if (!this.tiles.getBoundingSphere(tmpSphere)) return;
    this.placed = true;

    let lat: number;
    let lon: number;
    let height: number;

    if (this.opts.anchor) {
      lat = THREE.MathUtils.degToRad(this.opts.anchor.lat);
      lon = THREE.MathUtils.degToRad(this.opts.anchor.lon);
      height = this.opts.anchor.height ?? 0;
    } else {
      ({ lat, lon, height } = this.tiles.ellipsoid.getPositionToCartographic(
        tmpSphere.center,
        tmpCarto,
      ));
    }

    // ENU frame at the tileset centre, expressed in ECEF. Inverting it maps
    // ECEF into that local frame, putting the centre at the origin.
    this.tiles.ellipsoid.getEastNorthUpFrame(lat, lon, height, tmpMatrix);
    tmpMatrix.invert();

    // ENU is Z-up (east, north, up); three.js is Y-up. Rotating -90° about X
    // maps east->+x, up->+y, north->-z.
    this.tiles.group.matrix
      .makeRotationX(-Math.PI / 2)
      .multiply(tmpMatrix);
    this.tiles.group.matrix.decompose(
      this.tiles.group.position,
      this.tiles.group.quaternion,
      this.tiles.group.scale,
    );
    this.tiles.group.updateMatrixWorld(true);

    this.opts.onReady?.({
      // getPositionToCartographic returns radians; callers want degrees.
      origin: { lat: THREE.MathUtils.radToDeg(lat), lon: THREE.MathUtils.radToDeg(lon), height },
      // With an explicit anchor the tileset's own radius is meaningless for
      // framing — it describes the whole city, not the part that was fetched.
      radius: this.opts.anchor ? 0 : tmpSphere.radius,
    });
  };

  private onLoadError = (event: { error: Error; url: string | URL }): void => {
    this.opts.onError?.(
      new Error(`Failed to load ${String(event.url)}: ${event.error.message}`),
    );
  };
}
