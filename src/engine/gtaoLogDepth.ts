import type * as THREE from 'three';
import type { GTAOPass } from 'three/examples/jsm/postprocessing/GTAOPass.js';

/**
 * Teach GTAO to read a logarithmic depth buffer.
 *
 * three.js injects `USE_LOGARITHMIC_DEPTH_BUFFER` into *every* material when the
 * renderer asks for log depth, including the `MeshNormalMaterial` GTAOPass uses
 * for its own depth prepass. So the depth texture the AO pass samples holds
 *
 *     depth = log2( 1 + w ) / log2( far + 1 )        // w = -viewZ
 *
 * while `getViewPosition` unprojects it as if it were ordinary projected z.
 * The result is not subtly off: at an 80 km far plane a surface 100 m away
 * reconstructs as 3.4 m away, and its neighbour a metre behind it reconstructs
 * 4 mm further. Every occlusion test then answers on rounding noise, and what
 * reaches the screen is the pass's own sampling pattern — a fixed halftone of
 * dots over every facade, which reads exactly like z-fighting and survives any
 * amount of work on the geometry.
 *
 * Both shaders that consume depth (the AO pass and its poisson denoiser) share
 * one `getViewPosition`, so recovering the view distance there fixes the whole
 * pass. Nothing else in either shader touches raw depth.
 */
export function patchGtaoForLogDepth(pass: GTAOPass, renderer: THREE.WebGLRenderer): void {
  if (!renderer.capabilities.logarithmicDepthBuffer) return;

  const gtao = pass.gtaoMaterial as THREE.ShaderMaterial;
  const denoise = pass.pdMaterial as THREE.ShaderMaterial;

  // The denoiser has no far-plane uniform of its own. Share the AO pass's, so
  // the per-frame `cameraFar` update GTAOPass already does reaches both.
  const cameraFar = gtao.uniforms.cameraFar;
  if (!cameraFar) throw new Error('GTAO log-depth patch: no cameraFar uniform to share');
  denoise.uniforms.cameraFar = cameraFar;

  for (const material of [gtao, denoise]) {
    material.fragmentShader = linearizeViewPosition(material.fragmentShader);
    material.needsUpdate = true;
  }
}

/** The unprojection at the end of `getViewPosition`, matched loosely on whitespace. */
const UNPROJECT =
  /vec4\s+viewSpacePosition\s*=\s*cameraProjectionMatrixInverse\s*\*\s*clipSpacePosition;\s*return\s+viewSpacePosition\.xyz\s*\/\s*viewSpacePosition\.w;/;

// Unproject the pixel's ray to the near plane, then walk along it to the
// distance the log-depth encoding actually names. Equivalent to the original
// for a standard buffer, and correct for this one.
const LINEARIZED = /* glsl */ `
			clipSpacePosition.z = -1.0;
			vec4 nearPosition = cameraProjectionMatrixInverse * clipSpacePosition;
			vec3 nearPoint = nearPosition.xyz / nearPosition.w;
			float viewDistance = exp2( depth * log2( cameraFar + 1.0 ) ) - 1.0;
			return nearPoint * ( viewDistance / max( 1e-6, -nearPoint.z ) );`;

function linearizeViewPosition(fragmentShader: string): string {
  if (!UNPROJECT.test(fragmentShader)) {
    // Loud rather than silent: if a three.js upgrade rewrites the shader, the
    // failure mode without this is a frame full of halftone that looks like a
    // geometry bug and sends you hunting in the wrong place for a week.
    throw new Error('GTAO log-depth patch: getViewPosition no longer matches the expected shader');
  }

  let patched = fragmentShader.replace(UNPROJECT, LINEARIZED.trim());
  if (!/uniform\s+float\s+cameraFar\s*;/.test(patched)) {
    patched = patched.replace(
      /uniform\s+mat4\s+cameraProjectionMatrixInverse\s*;/,
      '$&\n\t\tuniform float cameraFar;',
    );
  }
  return patched;
}
