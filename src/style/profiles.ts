import * as THREE from 'three';

/**
 * Candidate visual targets, so the choice can be looked at rather than argued.
 *
 * The roadmap's Phase 1 asks for a stylized vertical slice before any pipeline
 * work, and the specific thing worth de-risking is that PLATEAU's LOD2 is
 * *photo-textured with lighting already baked into the photograph*. Stylizing
 * that is a different problem from stylizing untextured massing, and every
 * concept image of the target so far has been of the latter.
 *
 * So these are arranged as a ladder from "keep the photograph" to "discard it",
 * and the question each one answers is whether the city still reads as Shinjuku
 * at that rung.
 *
 * What the first pass established, so the next one does not repeat it:
 *
 * `graded` works. Desaturating the photograph and pulling it toward a city
 * palette keeps every facade PLATEAU surveyed, reads as deliberate rather than
 * scanned, and costs one uniform.
 *
 * `palette` proves the mechanism. Buildings take individually distinct colours
 * with no metadata at all — 6,245 of them recovered from the geometry — so the
 * per-building styling the roadmap wants is not blocked on the feature-ID work,
 * only made permanent by it.
 *
 * `palette` with banded *lighting* is the target, and it reads. Flat colour per
 * building, cel boundaries on the massing, Shinjuku's real street layout and
 * density intact, at ground level and from 700 m.
 *
 * Banding the finished frame is what does not work, and it is worth keeping
 * written down because it is the "blanket cartoon filter" the roadmap lists
 * under Avoid — the reason turns out to be measurable rather than aesthetic.
 * Quantizing R, G and B independently at four levels lands a muted `#b9a894` in
 * three different bands and returns saturated orange; the whole palette came
 * back as primaries. Quantizing luminance and rescaling to match is no better,
 * because it divides by luminance and every channel above the average clips.
 * Colour cannot be quantized and stay itself. Light can, so stylize.ts bands
 * the lighting term and leaves albedo untouched.
 */
export type StyleId = 'source' | 'graded' | 'toon' | 'palette';

export interface StyleProfile {
  readonly id: StyleId;
  readonly label: string;
  /** Posterize the final image into this many luminance bands. 0 = off. */
  readonly bands: number;
  /** How far to pull the photograph toward its palette colour, 0..1. */
  readonly paletteMix: number;
  /** How far to desaturate the photograph before mixing, 0..1. */
  readonly desaturate: number;
}

export const STYLES: Record<StyleId, StyleProfile> = {
  // The control. Every comparison is meaningless without it.
  source: { id: 'source', label: 'Source (photo LOD2)', bands: 0, paletteMix: 0, desaturate: 0 },
  // Keep the photograph, calm it down, tint toward the city palette. The
  // cheapest rung, and the one that keeps the most of what PLATEAU surveyed.
  graded: { id: 'graded', label: 'Graded photo', bands: 0, paletteMix: 0.45, desaturate: 0.55 },
  // Same, plus posterized light. This is the rung where it starts to read as
  // drawn rather than photographed.
  toon: { id: 'toon', label: 'Graded + toon bands', bands: 4, paletteMix: 0.45, desaturate: 0.55 },
  // Discard the photograph entirely: flat palette colour per building, banded
  // light. The roadmap's stated target, and the most expensive to be wrong
  // about, because it throws away the texture work the dataset came with.
  palette: { id: 'palette', label: 'Flat palette per building', bands: 4, paletteMix: 1, desaturate: 1 },
};

export const DEFAULT_STYLE: StyleId = 'source';

/**
 * A city-wide palette, fixed rather than derived per tile.
 *
 * tools/stylize-tiles.py quantizes each tile against its own adaptive palette
 * and its comment explains the tradeoff — a shared palette banded badly at tile
 * seams, so per-tile drift was the lesser evil. Seeding per *building* removes
 * that dilemma rather than trading against it: colour stops being a property of
 * which tile a pixel landed in.
 *
 * Warm neutrals with a few desaturated accents — Tokyo's building stock is
 * mostly concrete, tile and painted steel, and a saturated palette reads as a
 * toy rather than a miniature.
 */
export const CITY_PALETTE: readonly THREE.Color[] = [
  '#d9d4c8', '#c8c2b4', '#b3aea3', '#9ea69e', '#8f9aa3',
  '#b9a894', '#a8927e', '#cfc7bd', '#96a2ab', '#c2b8a8',
  '#8c9490', '#ada494',
].map((hex) => new THREE.Color(hex).convertSRGBToLinear());

/** Muted ground colours for roads and vegetation, kept out of the building range. */
export const BACKDROP_PALETTE: readonly THREE.Color[] = [
  '#8a8f83', '#7f8a76', '#94958a',
].map((hex) => new THREE.Color(hex).convertSRGBToLinear());

export function styleFromLocation(search = window.location.search): StyleProfile {
  const want = new URLSearchParams(search).get('style');
  return (want && STYLES[want as StyleId]) || STYLES[DEFAULT_STYLE];
}
