import * as THREE from 'three';

export interface ViewerOptions {
  /** Distance to the near clipping plane, in metres. */
  near?: number;
  /** Distance to the far clipping plane, in metres. */
  far?: number;
  /** Vertical field of view, in degrees. */
  fov?: number;
}

/**
 * Owns the WebGL context, scene, camera and render loop.
 *
 * The camera is a plain PerspectiveCamera; whatever drives it (FlyCamera,
 * an orbit rig, a cutscene) is deliberately not this class's concern.
 */
export class Viewer {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly canvas: HTMLCanvasElement;

  private readonly clock = new THREE.Clock();
  private readonly updaters: ((dt: number) => void)[] = [];
  private running = false;
  private frameHandle = 0;

  constructor(container: HTMLElement = document.body, options: ViewerOptions = {}) {
    const { near = 0.5, far = 80_000, fov = 60 } = options;

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      // Scenes here span metres to tens of kilometres. A standard depth buffer
      // z-fights badly across that range; log depth costs a little fill rate
      // and removes the problem entirely.
      logarithmicDepthBuffer: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;

    this.canvas = this.renderer.domElement;
    container.appendChild(this.canvas);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(fov, 1, near, far);

    this.resize();
    window.addEventListener('resize', this.resize);
  }

  /** Register a per-frame callback. Returns an unsubscribe function. */
  onUpdate(fn: (dt: number) => void): () => void {
    this.updaters.push(fn);
    return () => {
      const i = this.updaters.indexOf(fn);
      if (i >= 0) this.updaters.splice(i, 1);
    };
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.clock.start();
    this.tick();
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.frameHandle);
  }

  dispose(): void {
    this.stop();
    window.removeEventListener('resize', this.resize);
    this.renderer.dispose();
    this.canvas.remove();
  }

  private tick = (): void => {
    if (!this.running) return;
    this.frameHandle = requestAnimationFrame(this.tick);

    // Clamp dt so a background tab or a long stall doesn't teleport the camera
    // on the first frame back.
    const dt = Math.min(this.clock.getDelta(), 0.1);

    for (const update of this.updaters) update(dt);
    this.renderer.render(this.scene, this.camera);
  };

  private resize = (): void => {
    const width = window.innerWidth;
    const height = window.innerHeight;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  };
}
