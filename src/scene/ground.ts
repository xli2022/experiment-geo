import * as THREE from 'three';

/**
 * A flat sheet under cities whose tileset has no terrain of its own.
 *
 * The Berlin bake draws its own ground, because OSM2World renders landuse,
 * water and roads alongside the buildings. PLATEAU's `bldg` datasets are
 * buildings and nothing else, so without this Tokyo is a few hundred towers
 * hanging in mid-air with sky underneath them — the shadows land nowhere and
 * there is no horizon to read height against.
 *
 * Deliberately a plain sheet rather than fetched terrain. PLATEAU does publish
 * elevation separately, but Shinjuku is flat to within a few metres over the
 * area fetched, so a sheet costs one draw call and is honest about being a
 * backdrop rather than pretending to survey data it does not have.
 */
export interface GroundOptions {
  /** Sheet colour. Should sit against the city, not compete with it. */
  color?: string;
  /**
   * Half-width in metres. Wants to reach past the fog, or its edge shows as a
   * hard line against the sky.
   */
  extent?: number;
  /**
   * Metres below the origin. The anchor puts y = 0 at the ellipsoid, and
   * PLATEAU's buildings start at their real base height, so a sheet exactly at
   * zero cuts through them.
   */
  depth?: number;
}

export class Ground {
  private readonly mesh: THREE.Mesh;
  private readonly geometry: THREE.PlaneGeometry;
  private readonly material: THREE.MeshStandardMaterial;
  private scene: THREE.Scene | null = null;

  constructor(options: GroundOptions = {}) {
    const { color = '#8d9285', extent = 20_000, depth = 0 } = options;

    this.geometry = new THREE.PlaneGeometry(extent * 2, extent * 2);
    this.material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(color),
      roughness: 1,
      metalness: 0,
    });
    this.mesh = new THREE.Mesh(this.geometry, this.material);
    this.mesh.rotation.x = -Math.PI / 2;
    this.mesh.position.y = -depth;
    // Receives so the towers cast onto it — that shadow is most of what sells
    // the contact. Casting would be pointless and would fight the shadow
    // camera's fit, since the sheet spans the whole scene.
    this.mesh.receiveShadow = true;
    this.mesh.castShadow = false;
    // Large flat surfaces are the cheapest thing to get wrong in a depth
    // buffer, and this one underlies everything; drawing it first with the
    // renderer's default sort keeps it out of the way.
    this.mesh.renderOrder = -1;
  }

  attach(scene: THREE.Scene): void {
    this.scene = scene;
    scene.add(this.mesh);
  }

  dispose(): void {
    this.scene?.remove(this.mesh);
    this.geometry.dispose();
    this.material.dispose();
    this.scene = null;
  }
}
