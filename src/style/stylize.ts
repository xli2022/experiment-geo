import * as THREE from 'three';
import { seedByComponent } from './components';
import { BACKDROP_PALETTE, CITY_PALETTE, DETAIL_SPREAD, type StyleProfile } from './profiles';

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
 * The banding quantizes the *lighting term*, recovered by dividing the outgoing
 * light by albedo, so the palette passes through untouched. Posterizing the
 * finished colour instead — the obvious version — turns muted greys into
 * saturated primaries, because R, G and B land in different bands.
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
      shader.uniforms.uDetail = { value: profile.detail };
      shader.uniforms.uSigns = { value: profile.signs };

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
           uniform float uDetail;
           uniform float uSigns;
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
             // Only a trace of the photograph's luminance survives into the
             // flat colour. An earlier version leaned on it heavily, because
             // without it every building was an unshaded slab — but that was
             // compensating for having no toon shading yet. Now the banded
             // lighting term supplies the massing, so the albedo can be nearly
             // flat, which is the whole point of a palette.
             vec3 tinted = vStyleColor * ( 0.88 + 0.24 * lum );
             vec3 styled = mix( base, tinted, uPaletteMix );

             #ifdef USE_MAP
             if ( uDetail > 0.0 || uSigns > 0.0 ) {
               // Put the windows back without putting the photograph back.
               //
               // A facade's texture is two signals at once: what colour the
               // building is, and where its openings are. The palette should
               // replace the first and keep the second, and they separate by
               // frequency — a blur subtracted from the texture leaves the
               // window grid, the door, the balcony edge, and discards the
               // concrete's colour and the sun's gradient across it.
               //
               // The blur has to be a constant size *on screen*, not in the
               // texture. A fixed mip is fixed in texture space, so on a facade
               // drawn small it averages the whole building and the difference
               // collapses; windows faded with distance and varied building to
               // building with texture resolution. Deriving the current level
               // from the UV derivatives and offsetting from that keeps the
               // comparison at a constant screen scale.
               vec2 texel = vMapUv * vec2( textureSize( map, 0 ) );
               vec2 ddx = dFdx( texel );
               vec2 ddy = dFdy( texel );
               float lod = 0.5 * log2( max( dot( ddx, ddx ), dot( ddy, ddy ) ) );
               vec3 local = textureLod( map, vMapUv, lod + ${DETAIL_SPREAD.toFixed(1)} ).rgb;

               // Ratio, not difference. How much darker a window is *than its
               // own wall* is a property of the window; how many units darker
               // it is also encodes how bright the photograph was that day. The
               // difference form made a dimly-lit facade's windows nearly
               // invisible while a sunlit one's were harsh.
               const vec3 luma = vec3( 0.2126, 0.7152, 0.0722 );
               float lo = dot( local, luma ) + 0.015;
               float hi = dot( diffuseColor.rgb, luma ) + 0.015;
               float rel = hi / lo - 1.0;
               // Compress with a gamma below 1 so faint detail is lifted and
               // strong detail does not run away — most facades carry far less
               // contrast than the few that carry a lot.
               float gain = sign( rel ) * pow( min( abs( rel ), 4.0 ), 0.7 ) * uDetail;
               // Asymmetric: a window is much darker than its wall, and nothing
               // on a facade is twice as bright as one.
               styled *= clamp( 1.0 + gain, 0.12, 2.0 );

               // Shinjuku is largely signage, and it is the one thing no
               // procedural rule reproduces — real shopfronts in real places.
               // Saturation identifies them without a mask: concrete, tile and
               // glass sit near neutral, a sign almost never does.
               //
               // Relative saturation for the same reason as above. Absolute
               // max-minus-min scales with brightness, so a deep red sign in
               // shadow scored below a pale beige wall in sun and was dropped.
               float mx = max( diffuseColor.r, max( diffuseColor.g, diffuseColor.b ) );
               float mn = min( diffuseColor.r, min( diffuseColor.g, diffuseColor.b ) );
               float sat = ( mx - mn ) / ( mx + 0.02 );
               // Lifted slightly on the way through: signage is lit from
               // itself as much as from the sun, and the toon banding below
               // would otherwise fold it into the wall's band.
               styled = mix( styled, diffuseColor.rgb * 1.3, smoothstep( 0.22, 0.46, sat ) * uSigns );
             }
             #endif

             diffuseColor.rgb = styled;
           }`,
        )
        .replace(
          '#include <opaque_fragment>',
          `if ( uBands > 0.0 ) {
             // Toon shading: quantize the light, never the colour.
             //
             // The obvious version of this posterizes the finished frame, and
             // it is wrong for a reason worth keeping written down. Banding R,
             // G and B independently at four levels puts a muted #b9a894 into
             // three different bands and returns saturated orange — measured on
             // this bake, a palette of greys and sands came back as primaries.
             // Banding luminance instead and rescaling the colour to match is
             // no better: it divides by luminance, so any channel above the
             // average clips. Colour cannot be quantized and stay itself.
             //
             // Light can. outgoingLight is albedo times the lighting term, so
             // dividing it back out recovers that term, and quantizing it there
             // leaves albedo exactly intact — every palette entry survives, and
             // the steps land on the shading where the eye expects a cel
             // boundary. This is what toon shading is; the frame filter was
             // only ever an impression of it.
             //
             // Approximate in that outgoingLight also carries specular and
             // emissive. At the metalness 0, roughness 1 these cities use, that
             // is a small share of a mostly diffuse surface.
             vec3 albedo = max( diffuseColor.rgb, vec3( 1e-3 ) );
             float lit = dot( outgoingLight / albedo, vec3( 0.2126, 0.7152, 0.0722 ) );
             // Band centres, not edges: floor(x*n+0.5)/n makes the lowest band
             // exactly 0, so every surface below it goes pure black — which
             // swallowed most of the city on the first attempt.
             float q = ( floor( lit * uBands ) + 0.5 ) / uBands;
             outgoingLight = albedo * q;
           }
           #include <opaque_fragment>`,
        );
    };

    // Without this every style shares one compiled program, because
    // onBeforeCompile is not part of three's default cache key — the first
    // variant to compile wins and the rest silently render as that one.
    m.customProgramCacheKey = () => `style:${profile.id}:${kind}`;
    m.needsUpdate = true;
  }
}
