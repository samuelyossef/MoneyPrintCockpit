#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const versionFile = path.join(root, "VERSION");
const packageFile = path.join(root, "package.json");
const semver = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

function readState() {
  const version = fs.readFileSync(versionFile, "utf8").trim();
  const packageJson = JSON.parse(fs.readFileSync(packageFile, "utf8"));
  if (!semver.test(version)) throw new Error(`VERSION não contém SemVer válido: ${version}`);
  return { version, packageJson };
}

function check() {
  const { version, packageJson } = readState();
  if (packageJson.version !== version) {
    throw new Error(`Versões divergentes: VERSION=${version}, package.json=${packageJson.version}`);
  }
  console.log(`MoneyPrinter ${version}: versão consistente`);
}

function bump(target) {
  const { version, packageJson } = readState();
  if (packageJson.version !== version) {
    throw new Error(`Versões divergentes: VERSION=${version}, package.json=${packageJson.version}. Corrija a divergência antes de incrementar.`);
  }
  let next = target;
  if (["major", "minor", "patch"].includes(target)) {
    const [major, minor, patch] = version.split("-")[0].split(".").map(Number);
    next = target === "major" ? `${major + 1}.0.0`
      : target === "minor" ? `${major}.${minor + 1}.0`
      : `${major}.${minor}.${patch + 1}`;
  }
  if (!semver.test(next)) throw new Error("Use major, minor, patch ou uma versão SemVer estável como 1.3.0.");
  packageJson.version = next;
  fs.writeFileSync(versionFile, `${next}\n`);
  fs.writeFileSync(packageFile, `${JSON.stringify(packageJson, null, 2)}\n`);
  console.log(`Versão atualizada: ${version} → ${next}. Registre as mudanças no CHANGELOG.md antes de publicar.`);
}

try {
  const [command, target] = process.argv.slice(2);
  if (command === "check") check();
  else if (command === "bump" && target) bump(target);
  else throw new Error("Uso: node scripts/version.js check | bump <major|minor|patch|x.y.z>");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
