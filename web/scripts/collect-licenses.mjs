// Write the license text of every production web dependency into the built bundle.
// `pnpm licenses list --prod --json` is the source of truth; no extra dependency is added.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const command = process.platform === "win32" ? "cmd.exe" : pnpm;
const args =
  process.platform === "win32"
    ? ["/c", pnpm, "licenses", "list", "--json", "--prod"]
    : ["licenses", "list", "--json", "--prod"];
const output = execFileSync(command, args, {
  encoding: "utf8",
  maxBuffer: 64 * 1024 * 1024,
});
const groups = JSON.parse(output);
const seen = new Set();
const sections = [
  "Fin-Agent third-party notices for the web bundle",
  "",
  "The complete license text of every production package resolved by pnpm is below.",
  "The app source is MIT; the full notice file also ships at this path in the image.",
  "",
];

const licenseFiles = (directory) => {
  if (!existsSync(directory)) return [];
  const names = readdirSync(directory);
  return names
    .filter((name) => /^(LICEN[CS]E|COPYING|NOTICE)/i.test(name))
    .map((name) => join(directory, name));
};

for (const entries of Object.values(groups)) {
  for (const entry of entries) {
    const key = `${entry.name}@${(entry.versions || []).join(",")}`;
    if (seen.has(key)) continue;
    seen.add(key);
    sections.push(`## ${entry.name} ${(entry.versions || []).join(", ")}`);
    sections.push(`License: ${entry.license || "see text"}`);
    sections.push("");
    const texts = entry.paths.flatMap(licenseFiles).map((path) => readFileSync(path, "utf8"));
    if (texts.length === 0) {
      sections.push(`No license file was packaged with ${entry.name}.`);
    } else {
      sections.push(texts.join("\n\n"));
    }
    sections.push("\n");
  }
}

mkdirSync("public", { recursive: true });
writeFileSync("public/THIRD_PARTY_NOTICES.txt", sections.join("\n"), "utf8");
