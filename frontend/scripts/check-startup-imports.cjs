const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const admin = fs.readFileSync(path.join(root, "src/lib/admin.ts"), "utf8");
const add = fs.readFileSync(path.join(root, "app/(tabs)/add.tsx"), "utf8");

if (/^import\s+\{\s*storage\s*\}\s+from\s+["']@\/src\/utils\/storage["']/m.test(admin)) {
  throw new Error("admin.ts must not initialize native storage during app bootstrap");
}

if (/^import\s+\*\s+as\s+DocumentPicker\s+from\s+["']expo-document-picker["']/m.test(add)) {
  throw new Error("Add route must load DocumentPicker only when the picker is opened");
}

if (!/await\s+import\(["']@\/src\/utils\/storage["']\)/.test(admin)) {
  throw new Error("admin.ts is missing its deferred storage import");
}

if (!/await\s+import\(["']expo-document-picker["']\)/.test(add)) {
  throw new Error("Add route is missing its deferred DocumentPicker import");
}

console.log("Startup native imports are deferred.");
