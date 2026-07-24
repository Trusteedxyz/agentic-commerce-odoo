#!/usr/bin/env bash
# Packages the installable Odoo module zip.
# Folder root inside the zip == Odoo technical name == "trusteed"
# (must match the addon's directory name so Odoo's addons loader can find it,
# and __manifest__.py so `env['ir.module.module']` resolves it correctly).
# Usage: bash bin/build-zip.sh
set -euo pipefail

MODULE_SLUG="trusteed"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(grep '"version"' "${REPO_DIR}/__manifest__.py" | head -1 | sed 's/.*"\([0-9.]*\)".*/\1/')
OUTPUT_DIR="${REPO_DIR}/dist"
OUTPUT="${OUTPUT_DIR}/trusteed-agentic-commerce-odoo-${VERSION}.zip"

echo "==> Packaging ${MODULE_SLUG} v${VERSION}"

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT
STAGE="${TEMP_DIR}/${MODULE_SLUG}"
mkdir -p "${STAGE}"

# Runtime files/folders the installed addon needs.
cp "${REPO_DIR}/__init__.py"      "${STAGE}/"
cp "${REPO_DIR}/__manifest__.py"  "${STAGE}/"
cp "${REPO_DIR}/hooks.py"         "${STAGE}/"
cp "${REPO_DIR}/README.md"        "${STAGE}/"
cp "${REPO_DIR}"/README.*.md      "${STAGE}/" 2>/dev/null || true
cp "${REPO_DIR}/LICENSE"          "${STAGE}/" 2>/dev/null || true
cp -r "${REPO_DIR}/controllers/"  "${STAGE}/controllers/"
cp -r "${REPO_DIR}/data/"         "${STAGE}/data/"
cp -r "${REPO_DIR}/i18n/"         "${STAGE}/i18n/"
cp -r "${REPO_DIR}/models/"       "${STAGE}/models/"
cp -r "${REPO_DIR}/security/"     "${STAGE}/security/"
cp -r "${REPO_DIR}/static/"       "${STAGE}/static/"
cp -r "${REPO_DIR}/utils/"        "${STAGE}/utils/"
cp -r "${REPO_DIR}/views/"        "${STAGE}/views/"

# Strip anything that shouldn't ship (tests, caches, dev-only scripts).
find "${STAGE}" -name "*.pyc" -delete
find "${STAGE}" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "${STAGE}" -name ".DS_Store" -delete

mkdir -p "${OUTPUT_DIR}"
rm -f "${OUTPUT}"
( cd "${TEMP_DIR}" && zip -rq "${OUTPUT}" "${MODULE_SLUG}" )

echo ""
echo "==> Package built:"
echo "    ${OUTPUT}"
echo "    $(du -h "${OUTPUT}" | cut -f1)"
echo ""
echo "==> Structure (root folder == addon technical name):"
unzip -l "${OUTPUT}" | awk '{print $4}' | grep -E "^${MODULE_SLUG}/[^/]*$" | head -20
echo ""
echo "==> Install: extract into your Odoo addons_path as 'trusteed/', then"
echo "    Settings > Activate developer mode > Apps > Update Apps List > search Trusteed > Install."
