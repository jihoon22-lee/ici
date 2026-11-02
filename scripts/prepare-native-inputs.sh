#!/usr/bin/env bash
# Prepare the pinned inputs scripts/build-native.sh consumes: the native Ruff/ty
# binaries with their upstream licenses, the official Rust musl standard library
# and an isolated musl 1.2.5 C SDK. Every byte is checked against the digests in
# packaging/native/{tool-inputs,upstream-source-lock}.json before it is used.
#
# Downloads use curl over HTTPS with the system trust store and the caller's
# proxy variables. --from DIRECTORY uses already downloaded files instead and
# makes no network request; files are selected by their pinned SHA-256, never
# by name. Nothing is installed outside --output and nothing needs root.
set -euo pipefail

ici_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ici_output=
ici_from=
while (($#)); do
    case "$1" in
        --output|--from)
            (($# >= 2)) || { echo 'Input preparation option requires a value' >&2; exit 2; }
            case "$1" in
                --output) ici_output=$2 ;;
                --from) ici_from=$2 ;;
            esac
            shift 2
            ;;
        --help)
            echo 'Usage: prepare-native-inputs.sh --output DIRECTORY [--from DOWNLOADED_INPUTS]'
            echo 'Writes DIRECTORY/native-build.env with the build-native.sh inputs.'
            exit 0
            ;;
        *) echo 'Unknown input preparation option' >&2; exit 2 ;;
    esac
done
[[ -n "$ici_output" ]] || { echo 'Provide --output for the prepared inputs' >&2; exit 2; }
for ici_command in jq tar xz gzip sha256sum make gcc find sort rustc; do
    command -v "$ici_command" >/dev/null || { echo "Required preparation tool is missing: $ici_command" >&2; exit 2; }
done
[[ -n "$ici_from" ]] || command -v curl >/dev/null || { echo 'Required preparation tool is missing: curl' >&2; exit 2; }
mkdir -p -- "$ici_output"
ici_output=$(cd -- "$ici_output" && pwd -P)
# The environment file is consumed as KEY=VALUE lines (GITHUB_ENV or a shell),
# so the prepared paths are restricted to characters that need no quoting.
[[ "$ici_output" =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo 'The output path must contain only ASCII letters, digits, dot, underscore, hyphen and slash' >&2; exit 2; }
case "$ici_output/" in "$ici_repo"/*)
    [[ "$ici_output/" == "$ici_repo"/target/* ]] || { echo 'Inside the checkout, prepared inputs must live under target/ so the release source tree stays clean' >&2; exit 2; } ;;
esac
[[ -z $(find "$ici_output" -mindepth 1 -maxdepth 1 -print -quit) ]] || { echo 'The output directory must be empty' >&2; exit 2; }
if [[ -n "$ici_from" ]]; then
    ici_from=$(cd -- "$ici_from" && pwd -P)
fi

ici_tools_lock="$ici_repo/packaging/native/tool-inputs.json"
ici_source_lock="$ici_repo/packaging/native/upstream-source-lock.json"
ici_target=$(jq -r '.core_runtime.target' "$ici_source_lock")
[[ $ici_target == x86_64-unknown-linux-musl ]] || { echo 'The source lock does not pin the musl target' >&2; exit 2; }
ici_downloads="$ici_output/downloads"
mkdir -- "$ici_downloads"
trap 'exit 130' INT
trap 'exit 143' TERM HUP

ici_digest() {
    local ici_value
    ici_value=$(sha256sum -- "$1")
    printf '%s' "${ici_value%% *}"
}

# ici_fetch URL SHA256 -> prints the verified content-addressed path.
ici_fetch() {
    local ici_url=$1 ici_sha=$2 ici_path="$ici_downloads/$2" ici_candidate
    [[ $ici_sha =~ ^[0-9a-f]{64}$ ]] || { echo "Invalid pinned digest for $ici_url" >&2; exit 2; }
    [[ $ici_url == https://* ]] || { echo "Pinned input is not an HTTPS URL: $ici_url" >&2; exit 2; }
    if [[ ! -f "$ici_path" ]]; then
        if [[ -n "$ici_from" ]]; then
            while IFS= read -r -d '' ici_candidate; do
                if [[ $(ici_digest "$ici_candidate") == "$ici_sha" ]]; then
                    cp -- "$ici_candidate" "$ici_path.partial"
                    break
                fi
            done < <(find -L "$ici_from" -maxdepth 3 -type f -size -300M -print0 | LC_ALL=C sort -z)
            [[ -f "$ici_path.partial" ]] || { echo "No file under --from has the pinned digest of $ici_url" >&2; exit 2; }
        else
            curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
                --tlsv1.2 --retry 3 --max-time 600 --max-filesize 314572800 \
                --output "$ici_path.partial" -- "$ici_url"
        fi
        [[ $(ici_digest "$ici_path.partial") == "$ici_sha" ]] || { rm -f -- "$ici_path.partial"; echo "Downloaded bytes do not match the pinned digest: $ici_url" >&2; exit 2; }
        mv -- "$ici_path.partial" "$ici_path"
    fi
    printf '%s' "$ici_path"
}

# Native Ruff/ty: archive, extracted binary and license, each against its own pin.
ici_tools="$ici_output/tools"
while IFS=$'\t' read -r ici_tool ici_tool_target ici_url ici_sha ici_size ici_license_url ici_license_sha ici_binary_sha ici_binary_size; do
    [[ $ici_tool_target == "$ici_target" ]] || { echo "Pinned $ici_tool target differs from the core target" >&2; exit 2; }
    ici_archive=$(ici_fetch "$ici_url" "$ici_sha")
    [[ $(stat -c %s -- "$ici_archive") == "$ici_size" ]] || { echo "Pinned $ici_tool archive size differs" >&2; exit 2; }
    ici_license=$(ici_fetch "$ici_license_url" "$ici_license_sha")
    ici_member="$ici_tool-$ici_target/$ici_tool"
    mkdir -p -- "$ici_tools/$ici_tool"
    tar -xzf "$ici_archive" -C "$ici_tools/$ici_tool" --no-same-owner --no-same-permissions -- "$ici_member"
    ici_binary="$ici_tools/$ici_tool/$ici_member"
    [[ -f "$ici_binary" && ! -L "$ici_binary" ]] || { echo "Pinned $ici_tool archive has no regular $ici_member" >&2; exit 2; }
    [[ $(ici_digest "$ici_binary") == "$ici_binary_sha" && $(stat -c %s -- "$ici_binary") == "$ici_binary_size" ]] || { echo "Extracted $ici_tool binary does not match its pin" >&2; exit 2; }
    chmod 755 -- "$ici_binary"
    cp -- "$ici_license" "$ici_tools/$ici_tool/UPSTREAM-LICENSE"
done < <(jq -r '.tools[] | [.tool, .target, .url, .archive_sha256, (.archive_size|tostring), .license_url, .license_sha256, .binary_sha256, (.binary_size|tostring)] | @tsv' "$ici_tools_lock")
[[ -x "$ici_tools/ruff/ruff-$ici_target/ruff" && -x "$ici_tools/ty/ty-$ici_target/ty" ]] || { echo 'The tool lock must pin both ruff and ty' >&2; exit 2; }

# Official Rust standard library for the target, installed by copying the
# component tree (the component's own install script is not executed).
ici_sdk="$ici_output/sdk"
ici_std_url=$(jq -r '.core_runtime.std_component_url' "$ici_source_lock")
ici_std_sha=$(jq -r '.core_runtime.std_component_sha256' "$ici_source_lock")
ici_std_size=$(jq -r '.core_runtime.std_component_size' "$ici_source_lock")
ici_std_tree_expected=$(jq -r '.core_runtime.std_component_file_tree_sha256' "$ici_source_lock")
ici_rust_version=$(jq -r '.core_runtime.rust_version' "$ici_source_lock")
ici_std_archive=$(ici_fetch "$ici_std_url" "$ici_std_sha")
[[ $(stat -c %s -- "$ici_std_archive") == "$ici_std_size" ]] || { echo 'Pinned Rust standard-library archive size differs' >&2; exit 2; }
mkdir -p -- "$ici_sdk/build" "$ici_sdk/rust-sysroot/lib/rustlib"
tar -xJf "$ici_std_archive" -C "$ici_sdk/build" --no-same-owner --no-same-permissions
ici_component="$ici_sdk/build/rust-std-$ici_rust_version-$ici_target/rust-std-$ici_target/lib/rustlib/$ici_target"
[[ -d "$ici_component/lib" && ! -L "$ici_component" ]] || { echo 'The Rust standard-library component has an unexpected layout' >&2; exit 2; }
[[ -z $(find "$ici_component" -type l -print -quit) ]] || { echo 'The Rust standard-library component contains links' >&2; exit 2; }
cp -R -- "$ici_component" "$ici_sdk/rust-sysroot/lib/rustlib/$ici_target"
ici_std_tree=$(cd -- "$ici_sdk/rust-sysroot/lib/rustlib/$ici_target/lib" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum)
[[ ${ici_std_tree%% *} == "$ici_std_tree_expected" ]] || { echo 'The installed standard-library files do not match the pinned file tree' >&2; exit 2; }
# Host crates (proc-macros, build scripts) keep using the pinned host toolchain,
# exactly as in the measured isolated SDK.
ici_host_version=$(cd -- "$ici_repo" && rustc --version | awk '{print $2}')
[[ $ici_host_version == "$ici_rust_version" ]] || { echo "The host Rust toolchain must be $ici_rust_version" >&2; exit 2; }
ici_host_sysroot=$(cd -- "$ici_repo" && rustc --print sysroot)
ici_host_triple=$(cd -- "$ici_repo" && rustc -vV | sed -n 's/^host: //p')
[[ $ici_host_triple == x86_64-unknown-linux-gnu && -d "$ici_host_sysroot/lib/rustlib/$ici_host_triple" ]] || { echo 'The native build host must be x86_64-unknown-linux-gnu' >&2; exit 2; }
ln -s -- "$ici_host_sysroot/lib/rustlib/$ici_host_triple" "$ici_sdk/rust-sysroot/lib/rustlib/$ici_host_triple"

# Isolated musl C SDK. Only its headers and musl-gcc wrapper compile bundled C
# sources; the final link uses the official self-contained CRT/libc above.
ici_musl_version=$(jq -r '.musl_notice.version' "$ici_source_lock")
ici_musl_url=$(jq -r '.musl_notice.source_archive_url' "$ici_source_lock")
ici_musl_sha=$(jq -r '.musl_notice.source_archive_sha256' "$ici_source_lock")
ici_musl_archive=$(ici_fetch "$ici_musl_url" "$ici_musl_sha")
tar -xzf "$ici_musl_archive" -C "$ici_sdk/build" --no-same-owner --no-same-permissions
ici_musl_source="$ici_sdk/build/musl-$ici_musl_version"
[[ -f "$ici_musl_source/configure" ]] || { echo 'The musl source archive has an unexpected layout' >&2; exit 2; }
ici_notice_sha=$(jq -r '.musl_notice.notice_sha256' "$ici_source_lock")
[[ $(ici_digest "$ici_musl_source/COPYRIGHT") == "$ici_notice_sha" ]] || { echo 'The musl COPYRIGHT differs from the retained notice' >&2; exit 2; }
(
    cd -- "$ici_musl_source"
    CC=gcc ./configure --prefix="$ici_sdk/musl" --disable-shared > "$ici_sdk/build/musl-configure.log" 2>&1
    make -j2 > "$ici_sdk/build/musl-make.log" 2>&1
    make install > "$ici_sdk/build/musl-install.log" 2>&1
) || { echo "musl SDK build failed; see $ici_sdk/build/musl-*.log" >&2; exit 2; }
[[ -x "$ici_sdk/musl/bin/musl-gcc" && -f "$ici_sdk/musl/include/stdio.h" ]] || { echo 'The musl SDK build did not produce musl-gcc and headers' >&2; exit 2; }
printf '#include <stdio.h>\n#include <string.h>\nint ici_probe(const char *text) { return (int)strlen(text) + EOF; }\n' > "$ici_sdk/build/probe.c"
"$ici_sdk/musl/bin/musl-gcc" -c -o "$ici_sdk/build/probe.o" "$ici_sdk/build/probe.c" || { echo 'The isolated musl compiler cannot compile against its headers' >&2; exit 2; }

{
    printf 'CC_%s=%s\n' "${ici_target//-/_}" "$ici_sdk/musl/bin/musl-gcc"
    printf 'AR_%s=%s\n' "${ici_target//-/_}" "$(command -v ar)"
    ici_linker_key="CARGO_TARGET_${ici_target^^}_LINKER"
    printf '%s=%s\n' "${ici_linker_key//-/_}" "$(command -v cc)"
    printf 'ICI_NATIVE_TOOLS_DIR=%s\n' "$ici_tools"
    printf 'ICI_NATIVE_SYSROOT=%s\n' "$ici_sdk/rust-sysroot"
    printf 'ICI_NATIVE_SYSROOT_SHA256=%s\n' "$ici_std_sha"
} > "$ici_output/native-build.env"
echo "Prepared pinned native build inputs in $ici_output"
