import * as THREE from 'three';

export interface SkyOptions {
  /** Colour of the sky/horizon and the fog. */
  skyColor?: THREE.ColorRepresentation;
  /** Bounce colour from the ground. */
  groundColor?: THREE.ColorRepresentation;
  /** Distance at which fog fully occludes, in metres. */
  fogFar?: number;
}

/**
 * Lighting and atmosphere for PBR geometry.
 *
 * OSM2World emits physically-based materials, and PBR without an environment
 * map reads flat and muddy — the specular response has nothing to reflect. So
 * rather than relying on direct lights alone, this builds a gradient
 * environment map from the sky and ground colours and hands it to the scene.
 * It is cheap, needs no HDR asset, and is the single biggest look lever here.
 */
export function setupLighting(
  scene: THREE.Scene,
  renderer: THREE.WebGLRenderer,
  options: SkyOptions = {},
): void {
  const skyColor = new THREE.Color(options.skyColor ?? 0x8fb8de);
  const groundColor = new THREE.Color(options.groundColor ?? 0x5a5348);
  const fogFar = options.fogFar ?? 12_000;

  scene.background = skyColor;
  // Linear fog over a wide band hides the hard edge of the baked area, which
  // is doing real work here rather than being decoration.
  scene.fog = new THREE.Fog(skyColor, fogFar * 0.15, fogFar);

  const hemi = new THREE.HemisphereLight(skyColor, groundColor, 1.6);
  scene.add(hemi);

  const sun = new THREE.DirectionalLight(0xfff4e6, 2.2);
  sun.position.set(0.6, 1, 0.35).normalize().multiplyScalar(1000);
  scene.add(sun);

  scene.environment = buildGradientEnvironment(renderer, skyColor, groundColor);
  scene.environmentIntensity = 1.0;
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
