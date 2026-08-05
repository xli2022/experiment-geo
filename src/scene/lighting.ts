import * as THREE from 'three';

export interface SkyOptions {
  /** Colour of the sky/horizon and the fog. */
  skyColor?: THREE.ColorRepresentation;
  /** Bounce colour from the ground. */
  groundColor?: THREE.ColorRepresentation;
  /** Distance at which fog fully occludes, in metres. */
  fogFar?: number;
  /** Half-width of the shadowed area at low altitude, in metres. */
  shadowRadius?: number;
  /** Shadow map resolution. */
  shadowMapSize?: number;
}

export interface SceneLighting {
  sun: THREE.DirectionalLight;
  /** Refit the sun's shadow camera around the viewer. Call once per frame. */
  update(camera: THREE.Camera): void;
}

// The direction sunlight arrives from: mid-morning and off to one side, so
// streets running either way get a lit face and a shaded one.
const SUN_DIRECTION = new THREE.Vector3(0.55, 0.72, 0.42).normalize();

const tmpTarget = new THREE.Vector3();
const tmpForward = new THREE.Vector3();
const tmpLightMatrix = new THREE.Matrix4();
const tmpLightInverse = new THREE.Matrix4();
const ORIGIN = new THREE.Vector3();

/**
 * Lighting and atmosphere for flat-shaded low-poly geometry.
 *
 * The balance is deliberately sun-dominant. Ambient light is what flattens
 * faceted geometry: under a strong hemisphere term every face of a box
 * receives nearly the same amount of light and the form disappears, which is
 * much of why untextured buildings read as coloured cubes. A directional key
 * with modest fill does the opposite — each face of the same box lands on a
 * different value, and the shape reads with no texture at all.
 */
export function setupLighting(
  scene: THREE.Scene,
  renderer: THREE.WebGLRenderer,
  options: SkyOptions = {},
): SceneLighting {
  const skyColor = new THREE.Color(options.skyColor ?? 0x8fb8de);
  const groundColor = new THREE.Color(options.groundColor ?? 0x5a5348);
  const fogFar = options.fogFar ?? 12_000;
  const shadowRadius = options.shadowRadius ?? 420;
  const mapSize = options.shadowMapSize ?? 2048;

  scene.background = skyColor;
  // Linear fog over a wide band hides the hard edge of the baked area, which
  // is doing real work here rather than being decoration.
  scene.fog = new THREE.Fog(skyColor, fogFar * 0.15, fogFar);

  // Fill, not key. Enough to keep shadowed faces off black, and no more.
  const hemi = new THREE.HemisphereLight(skyColor, groundColor, 0.55);
  scene.add(hemi);

  const sun = new THREE.DirectionalLight(0xfff2e0, 2.9);
  sun.position.copy(SUN_DIRECTION).multiplyScalar(1000);
  sun.castShadow = true;
  sun.shadow.mapSize.set(mapSize, mapSize);
  // Normal bias only; constant bias stays at zero.
  //
  // `shadow.bias` is normalised over the shadow camera's depth range, and this
  // camera's far plane is thousands of metres out, so even -0.0005 works out to
  // metres of depth offset — enough to leave whole facades marginally in or out
  // of shadow and flipping between the two. `normalBias` is in world units
  // instead, so it stays a fixed physical nudge (about one shadow texel here)
  // no matter how the camera is fitted.
  sun.shadow.normalBias = 0.6;
  sun.shadow.bias = 0;
  scene.add(sun);
  scene.add(sun.target);

  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  scene.environment = buildGradientEnvironment(renderer, skyColor, groundColor);
  // Low. The environment map is here to tint shadowed surfaces with skylight,
  // not to light the scene — that is the sun's job now.
  scene.environmentIntensity = 0.35;

  return {
    sun,
    update: (camera) => fitShadowCamera(sun, camera, shadowRadius, mapSize),
  };
}

/**
 * Round the covered radius up to a discrete step.
 *
 * If the radius tracked altitude continuously, every frame would rescale the
 * shadow map and every shadow edge would crawl as you climb. Stepping means it
 * only rescales occasionally, and between steps the projection is stable.
 */
function quantizeRadius(radius: number): number {
  const step = 2 ** Math.ceil(Math.log2(radius / 64));
  return 64 * step;
}

/**
 * Keep the shadow camera tight around what the viewer can actually see.
 *
 * One ortho shadow camera covering the whole 5 km world at 2048px would give
 * ~2.5 m per shadow texel — coarser than most of the buildings casting the
 * shadows. Instead it tracks the camera and scales with altitude: near the
 * ground the covered area is small and shadows are sharp, and from high up
 * they coarsen, which is where they matter least.
 */
function fitShadowCamera(
  sun: THREE.DirectionalLight,
  camera: THREE.Camera,
  baseRadius: number,
  mapSize: number,
): void {
  const altitude = Math.max(camera.position.y, 1);
  const radius = quantizeRadius(THREE.MathUtils.clamp(altitude * 1.1, baseRadius, 4000));

  // Bias the covered area forward — the camera usually looks ahead and down
  // rather than straight at its own feet.
  camera.getWorldDirection(tmpForward);
  tmpForward.y = 0;
  if (tmpForward.lengthSq() > 1e-6) tmpForward.normalize().multiplyScalar(radius * 0.45);
  else tmpForward.set(0, 0, 0);

  tmpTarget.set(camera.position.x + tmpForward.x, 0, camera.position.z + tmpForward.z);

  // Snap the centre to whole shadow-map texels.
  //
  // Without this the map slides by a fraction of a texel every frame, so which
  // texel each surface samples changes constantly and every shadow edge crawls
  // while the camera moves — the artifact reads as shimmering along every
  // wall. Snapping in the light's own basis makes the map move in discrete
  // texel steps instead, so the sampling stays put between steps.
  const texel = (2 * radius) / mapSize;
  tmpLightMatrix.lookAt(SUN_DIRECTION, ORIGIN, THREE.Object3D.DEFAULT_UP);
  tmpLightInverse.copy(tmpLightMatrix).invert();

  tmpTarget.applyMatrix4(tmpLightInverse);
  tmpTarget.x = Math.round(tmpTarget.x / texel) * texel;
  tmpTarget.y = Math.round(tmpTarget.y / texel) * texel;
  tmpTarget.applyMatrix4(tmpLightMatrix);

  sun.target.position.copy(tmpTarget);
  sun.target.updateMatrixWorld();
  sun.position.copy(tmpTarget).addScaledVector(SUN_DIRECTION, radius * 3);

  const cam = sun.shadow.camera;
  cam.left = -radius;
  cam.right = radius;
  cam.top = radius;
  cam.bottom = -radius;
  cam.near = 1;
  cam.far = radius * 6;
  cam.updateProjectionMatrix();
}

/** Render a sky/ground gradient into a PMREM cubemap for image-based lighting. */
function buildGradientEnvironment(
  renderer: THREE.WebGLRenderer,
  skyColor: THREE.Color,
  groundColor: THREE.Color,
): THREE.Texture {
  const scene = new THREE.Scene();
  const geometry = new THREE.SphereGeometry(1, 32, 16);
  const material = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      topColor: { value: skyColor.clone().multiplyScalar(1.1) },
      bottomColor: { value: groundColor },
    },
    vertexShader: /* glsl */ `
      varying vec3 vWorldPosition;
      void main() {
        vWorldPosition = ( modelMatrix * vec4( position, 1.0 ) ).xyz;
        gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 topColor;
      uniform vec3 bottomColor;
      varying vec3 vWorldPosition;
      void main() {
        float h = normalize( vWorldPosition ).y;
        gl_FragColor = vec4( mix( bottomColor, topColor, smoothstep( -0.25, 0.35, h ) ), 1.0 );
      }
    `,
  });
  scene.add(new THREE.Mesh(geometry, material));

  const pmrem = new THREE.PMREMGenerator(renderer);
  const target = pmrem.fromScene(scene);

  geometry.dispose();
  material.dispose();
  pmrem.dispose();

  return target.texture;
}
