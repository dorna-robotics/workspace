// compress.mjs — the compression step of compress.sh, for one file: node compress.mjs IN OUT
//
// The same pipeline as `gltf-transform optimize --compress draco` (dedup, instance, flatten, join, weld,
// simplify, prune, Draco), with two differences so material names survive:
//   - materials with different names are never merged (dedup keeps unique names), even when their
//     values are the same: a name like "Steel_-_Satin" or "Glass_(Clear)" says what the part is made of;
//   - no palette textures (the CLI's default packs all materials into one generated "PaletteMaterial").
// Meshes are still joined where they share a material, so pieces of one material become one draw call.
// Accuracy as the CLI's defaults: simplification may move the surface by at most 0.0001 of the mesh's
// size (SIMPLIFY_ERROR overrides it), and Draco stores positions with 14 bits, normals with 10.
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
  simplify({ simplifier: MeshoptSimplifier, error: Number(process.env.SIMPLIFY_ERROR || 0.0001) }),
  prune(),
  draco(),
);
await io.write(OUT, doc);
