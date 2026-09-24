// Node module-resolution hook for `npm test`. App sources use extensionless
// relative imports (bundler style); Node needs the file extension, so a failed
// relative specifier is retried with ".ts", ".tsx" and "/index.ts". Resolved
// TypeScript files are tagged "module-typescript" so Node strips types without
// guessing the module format.

const EXTENSIONS = [".ts", ".tsx", "/index.ts"];

function isRelative(specifier) {
  return specifier.startsWith("./") || specifier.startsWith("../");
}

function withTsFormat(result) {
  if (/\.tsx?$/.test(new URL(result.url).pathname)) {
    return { ...result, format: "module-typescript" };
  }
  return result;
}

export async function resolve(specifier, context, nextResolve) {
  try {
    return withTsFormat(await nextResolve(specifier, context));
  } catch (error) {
    if (error?.code !== "ERR_MODULE_NOT_FOUND" || !isRelative(specifier)) throw error;
    for (const ext of EXTENSIONS) {
      try {
        return withTsFormat(await nextResolve(specifier + ext, context));
      } catch {
        // try the next candidate
      }
    }
    throw error;
  }
}
