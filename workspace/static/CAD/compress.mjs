// compress.mjs — the compression step of compress.sh, for one file: node compress.mjs IN OUT
//
// The same pipeline as `gltf-transform optimize --compress draco` (dedup, instance, flatten, join, weld,
// simplify, prune, Draco), with two differences so material names survive:
//   - materials with different names are never merged (dedup keeps unique names), even when their
//     values are the same: a name like "Steel_-_Satin" or "Glass_(Clear)" says what the part is made of;
//   - no palette textures (the CLI's default packs all materials into one generated "PaletteMaterial").
// Meshes are still joined where they share a material, so pieces of one material become one draw call.
// Tuned for small files: simplification may move the surface by up to 0.005 of the mesh's size
// (about 0.8 mm on a 150 mm part; SIMPLIFY_ERROR overrides it), positions are stored with 12 bits
// (1/4096 of the mesh's size) and normals with 8. On a 140 000-triangle tool rack: 5 MB -> 60 KB
// with no visible change.
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { dedup, instance, flatten, join, weld, simplify, prune, draco } from "@gltf-transform/functions";
import { MeshoptSimplifier } from "meshoptimizer";
import draco3d from "draco3dgltf";

const [, , IN, OUT] = process.argv;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  "draco3d.decoder": await draco3d.createDecoderModule(),
  "draco3d.encoder": await draco3d.createEncoderModule(),
});
const doc = await io.read(IN);
await doc.transform(
  dedup({ keepUniqueNames: true }),
  instance({ min: 5 }),
  flatten(),
  join(),
  weld(),
  simplify({ simplifier: MeshoptSimplifier, error: Number(process.env.SIMPLIFY_ERROR || 0.005) }),
  prune(),
  draco({ quantizePosition: 12, quantizeNormal: 8 }),
);
await io.write(OUT, doc);
