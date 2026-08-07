import * as THREE from 'three';
import { TilesRenderer } from '3d-tiles-renderer';
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/plugins';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';
import type { WorldSource } from './WorldSource';
import { stylize, type StyleKind } from '../style/stylize';
import type { StyleProfile } from '../style/profiles';

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
  /** PBR overrides applied to every material as tiles stream in. */
  material?: { metalness?: number; roughness?: number };
  /**
   * Candidate visual treatment, applied as tiles load.
   *
   * Runtime rather than baked on purpose: the look has to be judged on real
   * geometry before anything is committed to an asset pipeline, and this way
   * abandoning a direction costs nothing.
   */
  style?: { profile: StyleProfile; kind: StyleKind };
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
  private readonly ktx2Loader: KTX2Loader;
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

    // Tokyo's textures are KTX2/Basis rather than WebP, which is a choice about
    // memory rather than download size. WebP is a transport format: it decodes
    // to RGBA8 and uploads uncompressed, so a 1024-square texture costs 4 MB of
    // VRAM however small the file was — measured on this bake, 0.4 MB of WebP
    // became 9 MB once decoded. KTX2 transcodes to whatever the GPU wants
    // (BC7, ASTC, ETC2) and *stays* compressed there, which is the constraint
    // that actually binds when hundreds of tiles are resident at once.
    //
    // It needs a transcoder to read at all, and detectSupport has to see the
    // real renderer — without it the loader cannot choose a target format and
    // every KTX2 texture fails. Vendored next to the Draco decoder so the app
    // keeps no third-party runtime dependency.
    this.ktx2Loader = new KTX2Loader();
    this.ktx2Loader.setTranscoderPath(`${import.meta.env.BASE_URL}basis/`);
    this.ktx2Loader.detectSupport(renderer);

    // Registering a GLTFLoader on the manager only reaches tiles whose content
    // *is* a .glb, which is what the Berlin bake produces. Tilesets from
    // elsewhere ship .b3dm, and its glTF payload is parsed by a loader the
    // renderer owns internally, so a manager handler never sees it — PLATEAU's
    // Tokyo tiles came out as empty sky, their Draco meshes undecodable and
    // their CESIUM_RTC origin unapplied, which drops the geometry near the
    // centre of the earth.
    //
    // This plugin configures that internal loader instead, and covers .glb by
    // the same route. autoDispose is off because dispose() below already owns
    // the DRACO loader, and disposing it twice throws.
    this.tiles.registerPlugin(
      new GLTFExtensionsPlugin({
        dracoLoader: this.dracoLoader,
        ktxLoader: this.ktx2Loader,
        autoDispose: false,
      }),
    );

    this.tiles.addEventListener('load-root-tileset', this.place);
    this.tiles.addEventListener('load-error', this.onLoadError);
    // Shadow flags have to be set as tiles stream in — they are created long
    // after the scene is built, and a mesh that neither casts nor receives is
    // simply absent from the shadow pass rather than erroring.
    this.tiles.addEventListener('load-model', this.onLoadModel);
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
    this.ktx2Loader.dispose();
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

  private onLoadModel = (event: { scene: THREE.Object3D }): void => {
    event.scene.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      // Every tile both casts and receives: a city block shadows the street,
      // and the same block is shadowed by its neighbour. Splitting the two
      // would save a little fill rate and lose most of the effect.
      mesh.castShadow = true;
      mesh.receiveShadow = true;

      // A note for anything that later asks where this geometry is: measure it
      // with vertices, not with its bounding box.
      //
      // Tile geometry arrives in ECEF-aligned axes, so no local axis is up —
      // at Tokyo, world-up is local (-0.62, 0.58, -0.53). A tile's 800 m of
      // horizontal spread therefore lives in all three local axes at once, and
      // `Box3.setFromObject` transforms the box's eight corners rather than the
      // points inside it, which turns that into ~1,700 m of apparent height.
      // Measured: the declared bounds are exactly tight in local space (volume
      // ratio 1.00 across every mesh), so there is nothing to correct here —
      // `setFromObject(object, true)` is the fix at the call site. Three
      // attempts at seating this city were thrown off by the loose form, one of
      // them reporting buildings 875 m underground that are 22 m underground.

      const pbr = this.opts.material;
      if (pbr) {
        for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
          const std = m as THREE.MeshStandardMaterial;
          if (!std) continue;
          if (pbr.metalness !== undefined) std.metalness = pbr.metalness;
          if (pbr.roughness !== undefined) std.roughness = pbr.roughness;
        }
      }

      // After the PBR overrides, so a style is layered on the same material
      // state the unstyled build renders with and the comparison is fair.
      const style = this.opts.style;
      if (style) stylize(mesh, style.profile, style.kind);
    });
  };
}
