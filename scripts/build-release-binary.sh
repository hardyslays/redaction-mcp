#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

version=$(uv version --short)
machine=$(uname -m)
artifact_name="redaction-mcp-linux-${machine}-v${version}"
build_env="$project_root/build/release-venv"

uv venv --clear --python 3.12.8 "$build_env"
VIRTUAL_ENV="$build_env" uv sync --active --python 3.12.8 --group dev

"$build_env/bin/pyinstaller" \
    --clean \
    --noconfirm \
    --onefile \
    --name "$artifact_name" \
    --paths src \
    --specpath build \
    src/redaction_mcp/cli/main.py

uv build --python 3.12.8 --out-dir dist
tar -C dist -czf "dist/$artifact_name.tar.gz" "$artifact_name"

wheel_name="redaction_mcp-${version}-py3-none-any.whl"
sdist_name="redaction_mcp-${version}.tar.gz"
(
    cd dist
    sha256sum \
        "$artifact_name" \
        "$artifact_name.tar.gz" \
        "$wheel_name" \
        "$sdist_name" > SHA256SUMS
)

printf 'Built release assets in dist/:\n'
printf '  %s\n' \
    "$artifact_name" \
    "$artifact_name.tar.gz" \
    "$wheel_name" \
    "$sdist_name" \
    SHA256SUMS
