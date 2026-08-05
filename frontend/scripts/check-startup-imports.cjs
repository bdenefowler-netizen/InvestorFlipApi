const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const admin = fs.readFileSync(path.join(root, "src/lib/admin.ts"), "utf8");
const add = fs.readFileSync(path.join(root, "app/(tabs)/add.tsx"), "utf8");
const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));

if (/^import\s+\{\s*storage\s*\}\s+from\s+["']@\/src\/utils\/storage["']/m.test(admin)) {
  throw new Error("admin.ts must not initialize native storage during app bootstrap");
}

if (!/await\s+import\(["']@\/src\/utils\/storage["']\)/.test(admin)) {
  throw new Error("admin.ts is missing its deferred storage import");
}

if (pkg.dependencies?.["expo-document-picker"] || /expo-document-picker/.test(add)) {
  throw new Error("The recovery build must not package or load DocumentPicker");
}

console.log("Startup native imports are deferred and DocumentPicker is quarantined.");
