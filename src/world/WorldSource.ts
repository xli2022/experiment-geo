import type * as THREE from 'three';

/**
 * Anything that puts world geometry into the scene.
 *
 * v1 has exactly one implementation — `TilesetSource`, which streams geometry
 * baked offline by OSM2World. The interface exists so a procedural source
 * (vector tiles extruded at runtime, for flying beyond the baked area) can be
 * added later without touching the camera, lighting or app shell.
 *
 * Implementations add plain three.js objects to the scene, so several sources
 * can coexist.
 */
export interface WorldSource {
  /** Add this source's objects to the scene. */
  attach(scene: THREE.Scene): void;

  /**
   * Advance streaming for this frame. Called before rendering, so any
   * load/unload decisions land in the same frame they were made.
   */
  update(camera: THREE.Camera, dt: number): void;

  /** Release GPU resources and detach from the scene. */
  dispose(): void;
}
