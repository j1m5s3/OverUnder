// Loaded with `node --import ./tests/register-ts.mjs`; see resolve-ts.mjs.
import { register } from "node:module";

register("./resolve-ts.mjs", import.meta.url);
