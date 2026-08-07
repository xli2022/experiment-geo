import * as THREE from 'three';

/** Height field as written by tools/fetch-dem.py. */
export interface HeightField {
  attribution: string;
  north: number;
  south: number;
  west: number;
  east: number;
  resolution: number;
  /** Metres above sea level, row-major from the north-west corner. */
  heights: number[];
}

/**
 * The ground under cities whose tileset carries no terrain of its own.
 *
 * Berlin's bake draws its own, because OSM2World renders landuse, water and
 * roads alongside the buildings. PLATEAU publishes buildings, roads and
 * vegetation with no relief at all — its catalogue has no `dem` for Shinjuku —
 * so without this Tokyo is towers hanging in mid-air.
 *
 * A flat sheet cannot substitute, and the numbers say why: GSI's survey puts
 * Shinjuku's ground between 4.5 m and 43.9 m. PLATEAU places every building at
 * its true elevation, so against a single plane at the median, 43% of building
 * meshes floated more than 10 m — and a 25 m float displaces its shadow by 32 m
 * at this sun angle, which is what makes shadows read as detached from the
 * buildings casting them.
 *
 * Deriving the surface from the buildings instead was tried twice and does not
 * work. "The lowest geometry in a patch of city is the ground there" holds at
 * tile scale and fails at cell scale: most cells never contain a base, so their
 * lowest vertex is a roof, and every rejection threshold ends up classifying
 * the real ground as the outlier. So this uses survey data.
 */
export interface GroundOptions {
  /** Sheet colour where no height field is supplied. */
  color?: string;
  /**
   * Half-width in metres. Wants to reach past the fog, or its edge shows as a
   * hard line against the sky.
   */
  extent?: number;
  /**
   * Where the world origin sits, so the field can be placed against it.
   *
   * `height` is the ellipsoidal height that maps to y = 0, and the field is
   * measured from the same ellipsoid — tools/fetch-dem.py adds the geoid
   * undulation for exactly this reason — so the surface simply subtracts it.
   * Two datums were the whole bug: GSI reports height above mean sea level and
   * the globe draws the ellipsoid, which at Shinjuku differ by 37.08 m, and
   * mixing them put the ground below the city standing on it.
   */
  anchor?: { lat: number; lon: number; height?: number };
}
// There is deliberately no vertical fudge factor here. There was one, and a
// -55 m value in it hid the datum mistake above for as long as it existed: the
// city looked roughly seated, so nothing pointed at the 37 m the two datums
// actually disagreed by. If a city sits wrong, the number to change is its
// anchor height or its terrain, both of which mean something.

export class Ground {
  private mesh: THREE.Mesh;
  private geometry: THREE.BufferGeometry;
  private readonly material: THREE.MeshStandardMaterial;
  private scene: THREE.Scene | null = null;
  private readonly anchor: { lat: number; lon: number; height?: number } | null;
  private readonly offset: number;

  constructor(options: GroundOptions = {}) {
    const { color = '#8d9285', extent = 13_000, anchor } = options;
    this.anchor = anchor ?? null;
    // y = 0 is the anchor's ellipsoidal height, and the field is ellipsoidal,
    // so placing it is a subtraction and nothing more.
    this.offset = -(anchor?.height ?? 0);

    this.geometry = new THREE.PlaneGeometry(extent * 2, extent * 2);
    this.material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(color),
      roughness: 1,
      metalness: 0,
    });
    this.mesh = new THREE.Mesh(this.geometry, this.material);
    this.mesh.rotation.x = -Math.PI / 2;
    // Receives so the towers cast onto it — that shadow is most of what sells
    // the contact. Casting would be pointless and would fight the shadow
    // camera's fit, since the sheet spans the whole scene.
    this.mesh.receiveShadow = true;
    this.mesh.castShadow = false;
    this.mesh.renderOrder = -1;
  }

  /**
   * Replace the flat sheet with a surveyed surface.
   *
   * The field covers a few kilometres and the sheet covers tens, so the mesh is
   * built in two parts: a dense grid over the surveyed area, and the flat
   * remainder around it held at the field's edge height so there is no step
   * where one becomes the other.
   */
  setHeightField(field: HeightField): void {
    if (!this.anchor) return;

    const res = field.resolution;
    // Local ENU metres per degree at this latitude. Over a few kilometres the
    // flat-earth approximation is accurate to centimetres, which is far below
    // what a terrain mesh needs.
    const mPerLat = 111_132.0;
    const mPerLon = 111_320.0 * Math.cos((this.anchor.lat * Math.PI) / 180);

    const x0 = (field.west - this.anchor.lon) * mPerLon;
    const x1 = (field.east - this.anchor.lon) * mPerLon;
    // World +z runs south, so the north edge is the most negative z.
    const z0 = -(field.north - this.anchor.lat) * mPerLat;
    const z1 = -(field.south - this.anchor.lat) * mPerLat;

    const geometry = new THREE.PlaneGeometry(x1 - x0, z1 - z0, res - 1, res - 1);
    const pos = geometry.getAttribute('position') as THREE.BufferAttribute;
    for (let i = 0; i < pos.count; i++) {
      const col = i % res;
      const row = Math.floor(i / res);
      // PlaneGeometry's rows run +Y first, which after the -90° X rotation is
      // north first — the same order the field is stored in.
      pos.setZ(i, field.heights[row * res + col]! + this.offset);
    }
    pos.needsUpdate = true;
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();

    const surveyed = new THREE.Mesh(geometry, this.material);
    surveyed.rotation.x = -Math.PI / 2;
    surveyed.position.set((x0 + x1) / 2, 0, (z0 + z1) / 2);
    surveyed.receiveShadow = true;
    surveyed.castShadow = false;
    surveyed.renderOrder = -1;

    // The surround sits at the field's median so the join is not a cliff. It
    // exists to fill the horizon out to the fog, not to claim any accuracy.
    const sorted = [...field.heights].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)]! + this.offset;
    this.mesh.position.y = median;

    if (this.scene) this.scene.add(surveyed);
    this.surveyed = surveyed;
  }

  private surveyed: THREE.Mesh | null = null;

  attach(scene: THREE.Scene): void {
    this.scene = scene;
    scene.add(this.mesh);
    if (this.surveyed) scene.add(this.surveyed);
  }

  dispose(): void {
    this.scene?.remove(this.mesh);
    if (this.surveyed) {
      this.scene?.remove(this.surveyed);
      this.surveyed.geometry.dispose();
      this.surveyed = null;
    }
    this.geometry.dispose();
    this.material.dispose();
    this.scene = null;
  }
}
