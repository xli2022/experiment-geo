import * as THREE from 'three';
import { seedByComponent } from './components';
import { BACKDROP_PALETTE, CITY_PALETTE, type StyleProfile } from './profiles';

export type StyleKind = 'building' | 'backdrop';

/**
 * Apply a candidate style to a tile as it streams in.
 *
 * This is a prototype for judging a look, deliberately built where the roadmap
 * says to build it: inside the existing load-model hook, changing no asset and
 * no pipeline stage. Nothing here is baked, so any of it can be abandoned for
 * the cost of deleting a file — which is the entire point of proving the art
 * direction at runtime before committing to an offline bake.
 *
 * Two things are done in the shader rather than to the texture:
 *
 * The palette mix replaces the photograph *after* it is sampled, so the same
 * material serves every rung of the ladder and switching between them is a
 * uniform change rather than a re-bake.
 *
 * The posterization happens after tone mapping, in display space. Banding the
 * linear radiance instead puts the steps where ACES then smooths them back
 * out, which produces a muddy half-effect that reads as a bug rather than a
 * style.
 */
export function stylize(mesh: THREE.Mesh, profile: StyleProfile, kind: StyleKind): void {
  if (profile.id === 'source') return;

  // Per-building colour needs per-building identity. Recovered from the
  // geometry here; see components.ts for why that is a stand-in rather than a
  // solution.
  const palette = kind === 'building' ? CITY_PALETTE : BACKDROP_PALETTE;
  if (profile.paletteMix > 0 && !mesh.geometry.getAttribute('aStyleColor')) {
    seedByComponent(mesh.geometry, palette);
  }

  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];

  for (const material of materials) {
    const m = material as THREE.MeshStandardMaterial;
    if (!m || (m as { __styled?: boolean }).__styled) continue;
    (m as { __styled?: boolean }).__styled = true;

    m.onBeforeCompile = (shader) => {
      shader.uniforms.uPaletteMix = { value: profile.paletteMix };
      shader.uniforms.uDesaturate = { value: profile.desaturate };
      shader.uniforms.uBands = { value: profile.bands };

      shader.vertexShader = shader.vertexShader
        .replace(
          '#include <common>',
          `#include <common>
           attribute vec3 aStyleColor;
           varying vec3 vStyleColor;`,
        )
        .replace(
          '#include <begin_vertex>',
          `#include <begin_vertex>
           vStyleColor = aStyleColor;`,
        );

      shader.fragmentShader = shader.fragmentShader
        .replace(
          '#include <common>',
          `#include <common>
           uniform float uPaletteMix;
           uniform float uDesaturate;
           uniform float uBands;
           varying vec3 vStyleColor;`,
        )
        .replace(
          '#include <map_fragment>',
          `#include <map_fragment>
           if ( uPaletteMix > 0.0 ) {
             float lum = dot( diffuseColor.rgb, vec3( 0.2126, 0.7152, 0.0722 ) );
             // Desaturate toward the photograph's own luminance first, so the
             // mix keeps the facade's light and shade instead of flattening it.
             vec3 base = mix( diffuseColor.rgb, vec3( lum ), uDesaturate );
             // At full mix the photograph is gone, but its luminance still
             // modulates the flat colour — otherwise every building becomes a
             // single unshaded slab and the massing disappears.
             vec3 tinted = vStyleColor * ( 0.65 + 0.7 * lum );
             diffuseColor.rgb = mix( base, tinted, uPaletteMix );
           }`,
        )
        .replace(
          '#include <colorspace_fragment>',
          `#include <colorspace_fragment>
           if ( uBands > 0.0 ) {
             // Posterize in a perceptual space, whatever space this target is.
             //
             // Banding after <colorspace_fragment> looks like it lands in
             // display sRGB, and standalone it would. But Viewer renders the
             // scene into an EffectComposer target for GTAO, and that target is
             // linear — so linearToOutputTexel is the identity here and the
             // values arriving are linear radiance, where most of a city sits
             // below 0.25 and collapses into one or two bands. Measured, that
             // made the textured rung indistinguishable from the flat one:
             // both came out uniform grey, which read as the style failing
             // rather than the shader being in the wrong space.
             //
             // The gamma round trip makes the steps land where the eye puts
             // them and hands back whatever the target expects.
             vec3 disp = pow( max( gl_FragColor.rgb, vec3( 0.0 ) ), vec3( 1.0 / 2.2 ) );
             // Band centres, not edges: floor(x*n+0.5)/n makes the lowest band
             // exactly 0, and that crushed most of the city to pure black.
             //
             // Per channel rather than by luminance: rescaling a colour to a
             // quantized luminance divides by that luminance, so any channel
             // above the average clips, and a palette of muted greys and sands
             // came out saturated blue and orange.
             disp = ( floor( disp * uBands ) + 0.5 ) / uBands;
             gl_FragColor.rgb = pow( disp, vec3( 2.2 ) );
           }`,
        );
    };

    // Without this every style shares one compiled program, because
    // onBeforeCompile is not part of three's default cache key — the first
    // variant to compile wins and the rest silently render as that one.
    m.customProgramCacheKey = () => `style:${profile.id}:${kind}`;
    m.needsUpdate = true;
  }
}
