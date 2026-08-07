import * as THREE from 'three';

/**
 * Give every building in a tile its own stable number, without any metadata.
 *
 * A flat-palette style needs one colour per *building*. PLATEAU merges a whole
 * city block into a single mesh, so per-mesh colouring paints whole blocks, and
 * hashing world position slices colours straight through buildings that
 * straddle a grid line. Neither is the look being evaluated.
 *
 * The geometry itself has the answer: LOD2 buildings are separate closed
 * solids, so they are disconnected components of the mesh.
 *
 * But not of its *index buffer*. glTF splits a vertex wherever its normal or UV
 * differs, so a cube's six faces share no indices at all — measured on this
 * bake, index-only union-find returned 50,664 components across 275k vertices,
 * five vertices each. Those are faces. Welding by position first is what turns
 * them back into buildings, and it costs a hash of every vertex at load.
 *
 * This is a stand-in for the feature IDs in PLATEAU's batch table, which
 * tools/b3dm-to-glb.py currently drops. It cannot distinguish two buildings
 * that genuinely touch, and it has to be recomputed on every load — good
 * enough to judge a look, not good enough to ship.
 */
export function seedByComponent(
  geometry: THREE.BufferGeometry,
  palette: readonly THREE.Color[],
): number {
  const pos = geometry.getAttribute('position');
  const n = pos.count;
  if (!n) return 0;

  const parent = new Int32Array(n);
  for (let i = 0; i < n; i++) parent[i] = i;

  const find = (a: number): number => {
    let r = a;
    while (parent[r] !== r) r = parent[r]!;
    // Path compression, so the second pass is effectively flat.
    while (parent[a] !== r) {
      const next = parent[a]!;
      parent[a] = r;
      a = next;
    }
    return r;
  };
  const union = (a: number, b: number): void => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent[rb] = ra;
  };

  // Weld first. Two vertices at the same point belong to the same solid
  // however the index buffer splits them, and this is the step that makes the
  // difference between finding buildings and finding faces.
  //
  // A millimetre grid: Draco quantized these positions onto a ~0.7 mm lattice,
  // so coincident vertices survive decode as exactly equal and anything this
  // rounds together was already the same point.
  const weld = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    const key = `${Math.round(pos.getX(i) * 1000)},${Math.round(pos.getY(i) * 1000)},${Math.round(pos.getZ(i) * 1000)}`;
    const first = weld.get(key);
    if (first === undefined) weld.set(key, i);
    else union(first, i);
  }

  const index = geometry.getIndex();
  if (index) {
    for (let i = 0; i < index.count; i += 3) {
      const a = index.getX(i);
      const b = index.getX(i + 1);
      const c = index.getX(i + 2);
      union(a, b);
      union(b, c);
    }
  } else {
    for (let i = 0; i + 2 < n; i += 3) {
      union(i, i + 1);
      union(i + 1, i + 2);
    }
  }

  // Write the chosen colour per vertex rather than an index to look up in the
  // shader.
  //
  // An index has to survive interpolation, and it does not: a `varying float`
  // is interpolated across the triangle, so a seed of 3871 arrives at some
  // fragments as 3870.9997, `mod(seed, 12)` lands one entry over, and the
  // facade comes out stippled with two colours. Three identical vec3s
  // interpolate to exactly that vec3, so carrying the colour is both simpler
  // and exact. It also avoids indexing a uniform array by a computed value,
  // which is not portable GLSL.
  const label = new Map<number, number>();
  const colors = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const r = find(i);
    let id = label.get(r);
    if (id === undefined) {
      id = label.size;
      label.set(r, id);
    }
    // Hashed, so neighbouring buildings do not land on neighbouring palette
    // entries and give the city visible bands of colour.
    const c = palette[hash(id) % palette.length]!;
    colors[i * 3] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;
  }
  geometry.setAttribute('aStyleColor', new THREE.BufferAttribute(colors, 3));
  return label.size;
}

/** Small integer hash, kept in a range a float32 varying carries exactly. */
function hash(id: number): number {
  let x = (id + 1) * 2654435761;
  x ^= x >>> 15;
  x = Math.imul(x, 2246822519);
  x ^= x >>> 13;
  return (x >>> 0) % 4096;
}
