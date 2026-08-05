import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { GTAOPass } from 'three/examples/jsm/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';

export interface ViewerOptions {
  /** Distance to the near clipping plane, in metres. */
  near?: number;
  /** Distance to the far clipping plane, in metres. */
  far?: number;
  /** Vertical field of view, in degrees. */
  fov?: number;
  /** Screen-space ambient occlusion. Off falls back to a plain forward render. */
  ambientOcclusion?: boolean;
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
  private composer: EffectComposer | null = null;

  constructor(container: HTMLElement = document.body, options: ViewerOptions = {}) {
    const { near = 0.5, far = 80_000, fov = 60, ambientOcclusion = true } = options;

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

    if (ambientOcclusion) this.composer = this.buildComposer();

    this.resize();
    window.addEventListener('resize', this.resize);
  }

  /**
   * Draw one frame.
   *
   * Callers must go through this rather than `renderer.render` directly — with
   * ambient occlusion enabled the frame is assembled by the composer, and
   * bypassing it silently renders a version of the scene the user never sees.
   */
  render(): void {
    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);
  }

  /**
   * Ground-contact occlusion, which is what stops buildings looking pasted onto
   * the map. A flat-shaded city has no texture detail to imply contact, so
   * without it the join between a wall and the pavement is a hard colour change
   * and nothing else.
   */
  private buildComposer(): EffectComposer {
    const composer = new EffectComposer(this.renderer);
    composer.addPass(new RenderPass(this.scene, this.camera));

    const gtao = new GTAOPass(this.scene, this.camera, 1, 1);
    // Metres, not the default's unitless small number: this scene is a city,
    // so occlusion should gather over a wall-to-pavement distance rather than
    // the centimetres that suit a single object.
    gtao.updateGtaoMaterial({ radius: 2.5, distanceExponent: 1.0, thickness: 1.0, scale: 1.0 });
    composer.addPass(gtao);

    // The composer works in linear space, so the conversion the renderer would
    // normally do at the end has to happen explicitly as the last pass.
    composer.addPass(new OutputPass());
    return composer;
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
    this.composer?.dispose();
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
    this.render();
  };

  private resize = (): void => {
    const width = window.innerWidth;
    const height = window.innerHeight;
    this.renderer.setSize(width, height, false);
    this.composer?.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  };
}
