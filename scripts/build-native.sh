#!/usr/bin/env bash
# Build a native candidate from an immutable source snapshot and pinned tool bytes.
set -euo pipefail
ici_original_arguments=("$@")

ici_repo=${ICI_NATIVE_REPO_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}
ici_target=x86_64-unknown-linux-musl
ici_output=
ici_tools=${ICI_NATIVE_TOOL_INPUT_DIR:-}
ici_allow_dirty=0
ici_target_sysroot=
ici_target_sysroot_sha=
ici_build_id=
while (($#)); do
    case "$1" in
        --target|--output|--tools-dir|--sysroot|--sysroot-sha256|--build-id)
            (($# >= 2)) || { echo 'Build option requires a value' >&2; exit 2; }
            case "$1" in
                --target) ici_target=$2 ;;
                --output) ici_output=$2 ;;
                --tools-dir) ici_tools=$2 ;;
                --sysroot) ici_target_sysroot=$2 ;;
                --sysroot-sha256) ici_target_sysroot_sha=$2 ;;
                --build-id) ici_build_id=$2 ;;
            esac
            shift 2
            ;;
        --allow-dirty) ici_allow_dirty=1; shift ;;
        --help)
            echo 'Usage: build-native.sh --tools-dir PREPARED_INPUTS [--target TRIPLE] [--output ARCHIVE] [--sysroot DIRECTORY --sysroot-sha256 ARCHIVE_SHA256] [--build-id NAME] [--allow-dirty]'
            exit 0
            ;;
        *) echo 'Unknown native build option' >&2; exit 2 ;;
    esac
done
[[ -z "$ici_build_id" || "$ici_build_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ ]] || { echo 'Build identity must contain at most 64 ASCII letters, digits, underscores or hyphens' >&2; exit 2; }
case "$ici_target" in x86_64-unknown-linux-gnu|x86_64-unknown-linux-musl) ;; *) echo 'Native target is untested' >&2; exit 2 ;; esac
for ici_command in cargo rustc rustup git jq tar sha256sum readelf flock find sort xargs readlink timeout; do
    command -v "$ici_command" >/dev/null || { echo "Required build tool is missing: $ici_command" >&2; exit 2; }
done
[[ -n "$ici_tools" ]] || { echo 'Provide --tools-dir with the pinned Ruff/ty binaries and upstream licenses' >&2; exit 2; }
ici_tools=$(cd -- "$ici_tools" && pwd -P)
if [[ $ici_allow_dirty == 0 && -n $(git -C "$ici_repo" status --porcelain) ]]; then
    echo 'Native release builds require a clean source tree; --allow-dirty creates a development candidate snapshot' >&2
    exit 2
fi
if [[ -n "$ici_target_sysroot" || -n "$ici_target_sysroot_sha" ]]; then
    [[ -d "$ici_target_sysroot" && $ici_target_sysroot_sha =~ ^[0-9a-f]{64}$ ]] || { echo 'Custom sysroot requires an existing directory and a trusted target std archive SHA-256' >&2; exit 2; }
    ici_target_sysroot=$(cd -- "$ici_target_sysroot" && pwd -P)
fi
if [[ ${ICI_NATIVE_SCRIPT_SNAPSHOT:-0} != 1 ]]; then
    ici_script_sha=$(sha256sum "${BASH_SOURCE[0]}"); ici_script_sha=${ici_script_sha%% *}
    mkdir -p "$ici_repo/target/distribution/script-launchers"
    ici_launcher="$ici_repo/target/distribution/script-launchers/$ici_script_sha.sh"
    if [[ ! -e "$ici_launcher" ]]; then
        cp -- "${BASH_SOURCE[0]}" "$ici_launcher"
        chmod 700 "$ici_launcher"
    fi
    ici_actual=$(sha256sum "$ici_launcher"); ici_actual=${ici_actual%% *}
    [[ $ici_actual == "$ici_script_sha" ]] || { echo 'Private native build launcher does not match its source digest' >&2; exit 2; }
    exec env ICI_NATIVE_SCRIPT_SNAPSHOT=1 ICI_NATIVE_REPO_ROOT="$ici_repo" bash "$ici_launcher" "${ici_original_arguments[@]}"
fi
if [[ ${ICI_NATIVE_LOCK_HELD:-0} != 1 ]]; then
    exec flock /tmp/paseo-products-20261028-heavy.lock env ICI_NATIVE_LOCK_HELD=1 "$0" "${ici_original_arguments[@]}"
fi
ici_script_sha=$(sha256sum "${BASH_SOURCE[0]}"); ici_script_sha=${ici_script_sha%% *}
ici_actual=$(sha256sum "$ici_repo/scripts/build-native.sh"); ici_actual=${ici_actual%% *}
[[ $ici_actual == "$ici_script_sha" ]] || { echo 'The native build script changed while queued; rerun the new source' >&2; exit 2; }
export CARGO_BUILD_JOBS=2 RAYON_NUM_THREADS=2 RUST_TEST_THREADS=2 CARGO_INCREMENTAL=0
ici_revision=$(git -C "$ici_repo" rev-parse HEAD)
ici_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$ici_repo" show -s --format=%ct HEAD)}
[[ $ici_epoch =~ ^[0-9]+$ ]] || { echo 'SOURCE_DATE_EPOCH must be an unsigned integer' >&2; exit 2; }
export SOURCE_DATE_EPOCH="$ici_epoch"
ici_toolchain=$(cd -- "$ici_repo" && rustc --version | awk '{print $2}')
[[ $ici_toolchain == 1.99.1 ]] || { echo 'The native build requires Rust 1.99.1' >&2; exit 2; }
mkdir -p "$ici_repo/target/distribution/sources"
ici_work=$(mktemp -d "$ici_repo/target/distribution/build-inputs.XXXXXXXX")
trap 'rm -rf -- "$ici_work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
# Paths, ownership and times are normalized before hashing. No .git, caches,
# virtualenvs or build products are added to this captured source inventory.
git -C "$ici_repo" ls-files --cached --others --exclude-standard -z | LC_ALL=C sort -zu > "$ici_work/source-files"
tar --create --file="$ici_work/source.tar" --directory="$ici_repo" --format=ustar --mtime=@0 --owner=0 --group=0 --numeric-owner --mode=u=rwX,go=rX --null --verbatim-files-from --files-from="$ici_work/source-files"
ici_tree=$(sha256sum "$ici_work/source.tar"); ici_tree=${ici_tree%% *}
ici_source="$ici_repo/target/distribution/sources/$ici_tree"
if [[ ! -d "$ici_source" ]]; then
    mkdir "$ici_work/source"
    tar --extract --file="$ici_work/source.tar" --directory="$ici_work/source" --no-same-owner --no-same-permissions
    mv -- "$ici_work/source" "$ici_source"
fi
ici_verify_snapshot() {
    local ici_snapshot_digest
    if [[ -n $(find "$ici_source" -type l -print -quit) ]]; then
        echo 'Native build source snapshots require regular files; external symbolic-link content is not an immutable build input' >&2
        exit 2
    fi
    find "$ici_source" -type f -printf '%P\0' | LC_ALL=C sort -z > "$ici_work/snapshot-files"
    cmp -s "$ici_work/source-files" "$ici_work/snapshot-files" || { echo 'The native source snapshot inventory changed' >&2; exit 2; }
    ici_snapshot_digest=$(tar --create --file=- --directory="$ici_source" --format=ustar --mtime=@0 --owner=0 --group=0 --numeric-owner --mode=u=rwX,go=rX --null --verbatim-files-from --files-from="$ici_work/source-files" | sha256sum)
    [[ ${ici_snapshot_digest%% *} == "$ici_tree" ]] || { echo 'The native source snapshot bytes changed' >&2; exit 2; }
}
ici_verify_snapshot
ici_cargo_home=${CARGO_HOME:-$HOME/.cargo}
ici_sysroot=$(cd -- "$ici_source" && rustc --print sysroot)
ici_catalog_digest=$(sha256sum "$ici_sysroot/share/doc/rust/COPYRIGHT-library.html"); ici_catalog_digest=${ici_catalog_digest%% *}
ici_expected_catalog=$(jq -r '.core_runtime.standard_library_catalog_sha256' "$ici_source/packaging/native/upstream-source-lock.json")
[[ $ici_catalog_digest == "$ici_expected_catalog" ]] || { echo 'The installed Rust standard-library notice catalogue does not match its pinned official component' >&2; exit 2; }
ici_std_lib="${ici_target_sysroot:-$ici_sysroot}/lib/rustlib/$ici_target/lib"
[[ -d "$ici_std_lib" ]] || { echo 'The fixed target standard library is missing; install the verified component or pass an isolated --sysroot' >&2; exit 2; }
ici_std_tree=$(cd -- "$ici_std_lib" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum); ici_std_tree=${ici_std_tree%% *}
if [[ "$ici_target" == x86_64-unknown-linux-musl ]]; then
    ici_expected_std_archive=$(jq -r '.core_runtime.std_component_sha256' "$ici_source/packaging/native/upstream-source-lock.json")
    ici_expected_std_tree=$(jq -r '.core_runtime.std_component_file_tree_sha256' "$ici_source/packaging/native/upstream-source-lock.json")
    [[ -z "$ici_target_sysroot_sha" || $ici_target_sysroot_sha == "$ici_expected_std_archive" ]] || { echo 'The target standard-library archive digest is not the pinned official component' >&2; exit 2; }
    [[ $ici_std_tree == "$ici_expected_std_tree" ]] || { echo 'The target standard-library files do not match the pinned official component' >&2; exit 2; }
    ici_target_sysroot_sha=$ici_expected_std_archive
fi
export CARGO_TARGET_DIR="$ici_repo/target/distribution/compiled${ici_build_id:+-$ici_build_id}"
ici_rust_flags=(-C target-cpu=x86-64 "--remap-path-prefix=$ici_source=/ici" "--remap-path-prefix=$ici_cargo_home=/cargo" "--remap-path-prefix=$ici_sysroot=/rust-toolchain" "--remap-path-prefix=$CARGO_TARGET_DIR=/build")
printf -v CARGO_ENCODED_RUSTFLAGS '%s\x1f' "${ici_rust_flags[@]}"
export CARGO_ENCODED_RUSTFLAGS=${CARGO_ENCODED_RUSTFLAGS%$'\x1f'}
unset RUSTFLAGS
export CFLAGS="-O2 -march=x86-64 -mtune=generic -ffile-prefix-map=$ici_source=/ici -ffile-prefix-map=$ici_cargo_home=/cargo -ffile-prefix-map=$CARGO_TARGET_DIR=/build"
(
    cd -- "$ici_source"
    cargo build --release --locked -p ici-distribution --bin ici-dist
    if [[ -n "$ici_target_sysroot" ]]; then
        export CARGO_ENCODED_RUSTFLAGS="$CARGO_ENCODED_RUSTFLAGS"$'\x1f'"--sysroot"$'\x1f'"$ici_target_sysroot"
        export CARGO_ENCODED_RUSTFLAGS="$CARGO_ENCODED_RUSTFLAGS"$'\x1f'"--remap-path-prefix=$ici_target_sysroot=/target-sdk"
    fi
    if [[ "$ici_target" == x86_64-unknown-linux-musl ]]; then
        export CARGO_ENCODED_RUSTFLAGS="$CARGO_ENCODED_RUSTFLAGS"$'\x1f'"-C"$'\x1f'"target-feature=+crt-static"$'\x1f'"-C"$'\x1f'"link-self-contained=yes"
    fi
    cargo build --release --locked -p ici-cli --target "$ici_target"
    cargo metadata --locked --filter-platform "$ici_target" --format-version 1 > "$ici_work/cargo-metadata.json"
)
ici_builder="$CARGO_TARGET_DIR/release/ici-dist"
ici_core="$CARGO_TARGET_DIR/$ici_target/release/ici"
ici_version=$(jq -r '.packages[]|select(.name=="ici-cli")|.version' "$ici_work/cargo-metadata.json")
if [[ -z "$ici_output" ]]; then ici_output="$ici_repo/dist/ici-$ici_version-$ici_target.tar.gz"; fi
case "$ici_output" in /*) ;; *) ici_output="$PWD/$ici_output" ;; esac
[[ ! -e "$ici_output" && ! -e "$ici_output.sha256" ]] || { echo 'Native archive or checksum output already exists' >&2; exit 2; }
ici_program_headers=$(readelf --program-headers --wide "$ici_core")
ici_dynamic=$(readelf --dynamic --wide "$ici_core")
if [[ "$ici_target" == x86_64-unknown-linux-musl ]]; then
    if [[ "$ici_program_headers" =~ INTERP || "$ici_dynamic" =~ NEEDED ]]; then
        echo 'The musl native core must have no ELF interpreter or dynamic library dependencies; use a linker compatible with Rust self-contained CRT objects' >&2
        exit 2
    fi
fi
ici_probe=$(timeout 10 env -i PATH='' "$ici_core" --version)
[[ $ici_probe == "ici $ici_version" ]] || { echo 'Native core version probe does not match the candidate metadata' >&2; exit 2; }
for ici_tool in ruff ty; do
    ici_tool_binary="$ici_tools/$ici_tool/$ici_tool-x86_64-unknown-linux-musl/$ici_tool"
    ici_tool_version=$(jq -r --arg tool "$ici_tool" '.tools[]|select(.tool==$tool)|.version' "$ici_source/packaging/native/tool-inputs.json")
    ici_probe=$(timeout 10 env -i PATH='' "$ici_tool_binary" --version)
    [[ $ici_probe == "$ici_tool $ici_tool_version" ]] || { echo 'Pinned native tool failed its host version probe' >&2; exit 2; }
done
ici_lock=$(sha256sum "$ici_source/Cargo.lock"); ici_lock=${ici_lock%% *}
ici_glibc=$(readelf --wide --dyn-syms "$ici_core" | sed -n 's/.*@GLIBC_\([0-9.]*\).*/\1/p' | LC_ALL=C sort -V | tail -1)
ici_needed=$(readelf --dynamic "$ici_core" | sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p' | LC_ALL=C sort | paste -sd, -)
ici_cpu=$(sed -n '/^model name[[:space:]]*: /{s/^model name[[:space:]]*: //;p;q;}' /proc/cpuinfo)
ici_kernel=$(uname -sr)
ici_cc_key="CC_${ici_target//-/_}"
ici_cc=${!ici_cc_key:-${CC:-cc}}
ici_cc_path=$(command -v -- "$ici_cc") || { echo 'Native C compiler must be a directly addressable executable' >&2; exit 2; }
ici_cc_path=$(readlink -f -- "$ici_cc_path")
ici_cc_digest=$(sha256sum -- "$ici_cc_path"); ici_cc_digest=${ici_cc_digest%% *}
ici_cc_version=$("$ici_cc_path" --version | sed -n '1p')
ici_cc_frontend=$("$ici_cc_path" -print-prog-name=cc1)
ici_cc_frontend=$(command -v -- "$ici_cc_frontend") || { echo 'The native C compiler frontend could not be identified' >&2; exit 2; }
ici_cc_frontend_digest=$(sha256sum -- "$ici_cc_frontend"); ici_cc_frontend_digest=${ici_cc_frontend_digest%% *}
ici_linker_key="CARGO_TARGET_${ici_target^^}_LINKER"; ici_linker_key=${ici_linker_key//-/_}
ici_linker=${!ici_linker_key:-cc}
ici_linker_path=$(command -v -- "$ici_linker") || { echo 'Native linker must be a directly addressable executable' >&2; exit 2; }
ici_linker_path=$(readlink -f -- "$ici_linker_path")
ici_linker_digest=$(sha256sum -- "$ici_linker_path"); ici_linker_digest=${ici_linker_digest%% *}
ici_linker_version=$("$ici_linker_path" --version | sed -n '1p')
ici_linker_backend=$("$ici_linker_path" -print-prog-name=ld)
ici_linker_backend=$(command -v -- "$ici_linker_backend") || { echo 'The native linker backend could not be identified' >&2; exit 2; }
ici_linker_backend_digest=$(sha256sum -- "$ici_linker_backend"); ici_linker_backend_digest=${ici_linker_backend_digest%% *}
ici_ar_key="AR_${ici_target//-/_}"
ici_ar=${!ici_ar_key:-${AR:-ar}}
ici_ar_path=$(command -v -- "$ici_ar") || { echo 'Native archiver must be a directly addressable executable' >&2; exit 2; }
ici_ar_path=$(readlink -f -- "$ici_ar_path")
ici_ar_digest=$(sha256sum -- "$ici_ar_path"); ici_ar_digest=${ici_ar_digest%% *}
ici_ar_version=$("$ici_ar_path" --version | sed -n '1p')
ici_rustc_path=$(cd -- "$ici_source" && rustup which rustc)
ici_rustc_digest=$(sha256sum -- "$ici_rustc_path"); ici_rustc_digest=${ici_rustc_digest%% *}
jq -n --arg revision "$ici_revision" --arg tree "$ici_tree" --arg lock "$ici_lock" --arg target "$ici_target" --argjson epoch "$ici_epoch" --arg glibc "$ici_glibc" --arg needed "$ici_needed" --arg cpu "$ici_cpu" --arg kernel "$ici_kernel" --arg dirty "$ici_allow_dirty" --arg cc "$ici_cc_version" --arg cc_digest "$ici_cc_digest" --arg rustc_digest "$ici_rustc_digest" --arg target_std "$ici_target_sysroot_sha" --arg std_tree "$ici_std_tree" --arg script_sha "$ici_script_sha" '{source_revision:$revision,source_tree_sha256:$tree,cargo_lock_sha256:$lock,rust_toolchain:"1.99.1",target:$target,profile:"release",source_date_epoch:$epoch,rust_flags:["-C target-cpu=x86-64","--remap-path-prefix=<source>=/ici","--remap-path-prefix=<cargo-home>=/cargo","--remap-path-prefix=<sysroot>=/rust-toolchain"],platform_evidence:{support_status:"limited",tested_scope:"native core/Ruff/ty version probes on recorded host; full release acceptance remains separate",kernel:$kernel,tested_cpu:$cpu,compile_cpu:"x86-64",minimum_cpu_status:"limited",minimum_cpu_evidence:"baseline compilation; no older hardware execution asserted",required_glibc_symbol_version:$glibc,dynamic_libraries:$needed,development_snapshot:$dirty,c_flags:"-O2 -march=x86-64 -mtune=generic; normalized source/cache paths",c_compiler:$cc,c_compiler_driver_sha256:$cc_digest,rustc_sha256:$rustc_digest,target_std_archive_sha256:$target_std,target_std_file_tree_sha256:$std_tree,build_script_sha256:$script_sha}}' > "$ici_work/build-inputs.json"
if [[ -n "$ici_target_sysroot" ]]; then
    jq '.rust_flags += ["--sysroot=<target-sdk>", "--remap-path-prefix=<target-sdk>=/target-sdk"]' "$ici_work/build-inputs.json" > "$ici_work/build-inputs-next.json"
    mv -- "$ici_work/build-inputs-next.json" "$ici_work/build-inputs.json"
fi
jq '.rust_flags += ["--remap-path-prefix=<build>=/build"] | .platform_evidence.c_flags = "-O2 -march=x86-64 -mtune=generic; normalized source/cache/build paths"' "$ici_work/build-inputs.json" > "$ici_work/build-inputs-next.json"
mv -- "$ici_work/build-inputs-next.json" "$ici_work/build-inputs.json"
if [[ "$ici_target" == x86_64-unknown-linux-musl ]]; then
    jq '.rust_flags += ["-C target-feature=+crt-static", "-C link-self-contained=yes"] | .platform_evidence.elf_linkage = "static; no PT_INTERP or DT_NEEDED"' "$ici_work/build-inputs.json" > "$ici_work/build-inputs-next.json"
    mv -- "$ici_work/build-inputs-next.json" "$ici_work/build-inputs.json"
fi
jq --arg linker "$ici_linker_version" --arg linker_digest "$ici_linker_digest" --arg ar "$ici_ar_version" --arg ar_digest "$ici_ar_digest" --arg cc_frontend "$ici_cc_frontend_digest" --arg linker_backend "$ici_linker_backend_digest" '.platform_evidence += {linker_driver:$linker,linker_driver_sha256:$linker_digest,linker_backend_sha256:$linker_backend,archiver:$ar,archiver_sha256:$ar_digest,c_compiler_frontend_sha256:$cc_frontend}' "$ici_work/build-inputs.json" > "$ici_work/build-inputs-next.json"
mv -- "$ici_work/build-inputs-next.json" "$ici_work/build-inputs.json"
# Preserve the actual standard-library notice catalog and its referenced texts.
jq -n --arg root "$ici_sysroot/share/doc/rust" '[{id:"rust-1.99.1-standard-library",source:"https://static.rust-lang.org/dist/channel-rust-1.99.1.toml",declared_license:"Rust standard-library catalog; individual component terms are retained",path:($root+"/COPYRIGHT-library.html")}]' > "$ici_work/license-extras.json"
for ici_file in "$ici_sysroot"/share/doc/rust/licenses/*.txt; do
    jq --arg path "$ici_file" --arg id "rust-1.99.1-$(basename -- "$ici_file")" '.+[{id:$id,source:"Rust 1.99.1 installed toolchain license text",declared_license:"See retained source terms",path:$path}]' "$ici_work/license-extras.json" > "$ici_work/license-extras-next.json"
    mv -- "$ici_work/license-extras-next.json" "$ici_work/license-extras.json"
done
if [[ "$ici_target" == x86_64-unknown-linux-musl ]]; then
    jq --arg path "$ici_source/packaging/native/third-party/musl-1.2.5-COPYRIGHT.txt" '.+[{id:"musl-1.2.5",source:"https://musl.libc.org/releases/musl-1.2.5.tar.gz#musl-1.2.5/COPYRIGHT",declared_license:"MIT plus retained component notices; static runtime",path:$path}]' "$ici_work/license-extras.json" > "$ici_work/license-extras-next.json"
    mv -- "$ici_work/license-extras-next.json" "$ici_work/license-extras.json"
fi
jq --arg root "$ici_source/packaging/native" '[.native_standard_library_notices[]|select(.rust_version=="1.99.1")|.files[]|{id:("rust-1.99.1-"+(.path|split("/")|last)),source:.source_url,declared_license:"Exact upstream LLVM native-runtime license or contributor credits; applicability follows individual source terms",path:($root+"/"+.path)}]' "$ici_source/packaging/native/upstream-source-lock.json" > "$ici_work/native-runtime-extras.json"
jq -s '.[0]+.[1]' "$ici_work/license-extras.json" "$ici_work/native-runtime-extras.json" > "$ici_work/license-extras-next.json"
mv -- "$ici_work/license-extras-next.json" "$ici_work/license-extras.json"
"$ici_builder" notices --metadata "$ici_work/cargo-metadata.json" --package ici-cli --target "$ici_target" --extras "$ici_work/license-extras.json" --output "$ici_work/THIRD-PARTY-NOTICES.txt" --inventory "$ici_work/licenses.json" > "$ici_work/license-result.json"
jq -n --arg tools "$ici_tools" --arg source "$ici_source" '{ruff:{binary:($tools+"/ruff/ruff-x86_64-unknown-linux-musl/ruff"),license:($tools+"/ruff/UPSTREAM-LICENSE"),notices:($source+"/packaging/native/third-party/ruff-NOTICES.txt")},ty:{binary:($tools+"/ty/ty-x86_64-unknown-linux-musl/ty"),license:($tools+"/ty/UPSTREAM-LICENSE"),notices:($source+"/packaging/native/third-party/ty-NOTICES.txt")}}' > "$ici_work/tool-sources.json"
"$ici_builder" tool-pack --directory "$ici_work/tool-pack" --sources "$ici_work/tool-sources.json" --output "$ici_work/tool-files.json" > "$ici_work/tool-result.json"
cat > "$ici_work/README.txt" <<EOF
ici $ici_version native candidate for $ici_target

Run bin/ici --version, then bin/ici analyze PROJECT.
This archive includes the native Ruff and ty tools and all recorded notices.
Project tests and deployment are explicit operations.

Install or update through: bin/ici setup --archive ARCHIVE --sha256 TRUSTED_SHA256 --prefix DIRECTORY
Inspect the selection through: bin/ici setup --inspect --prefix DIRECTORY
Return to the previous verified artifact through: bin/ici setup --rollback --prefix DIRECTORY
Rollback preserves current user configuration and result data.

The manifest records measured host requirements and the exact build inputs.
Current candidate measurements do not establish untested platforms or older CPUs.
Third-party terms and any upstream notice gaps are recorded under licenses/.
EOF
jq --arg core "$ici_core" --arg work "$ici_work" --arg source "$ici_source" '.+[{path:"bin/ici",source:$core,executable:true},{path:"README.txt",source:($work+"/README.txt"),executable:false},{path:"licenses/ici-MIT.txt",source:($source+"/packaging/native/LICENSE-ici-MIT.txt"),executable:false},{path:"licenses/Unicode-V3.txt",source:($source+"/crates/ici-analysis/data/unicode-16/LICENSE.txt"),executable:false},{path:"licenses/THIRD-PARTY-NOTICES.txt",source:($work+"/THIRD-PARTY-NOTICES.txt"),executable:false},{path:"licenses/licenses.json",source:($work+"/licenses.json"),executable:false},{path:"licenses/upstream-source-lock.json",source:($source+"/packaging/native/upstream-source-lock.json"),executable:false},{path:"licenses/upstream-notices.json",source:($source+"/packaging/native/upstream-notices.json"),executable:false}]' "$ici_work/tool-files.json" > "$ici_work/files.json"
while IFS= read -r -d '' ici_file; do
    ici_relative=${ici_file#"$ici_source/packaging/native/"}
    jq --arg path "licenses/$ici_relative" --arg source "$ici_file" '.+[{path:$path,source:$source,executable:false}]' "$ici_work/files.json" > "$ici_work/files-next.json"
    mv -- "$ici_work/files-next.json" "$ici_work/files.json"
done < <(find "$ici_source/packaging/native/sources" "$ici_source/packaging/native/third-party" -type f -print0 | LC_ALL=C sort -z)
"$ici_builder" bundle --version "$ici_version" --inputs "$ici_work/build-inputs.json" --files "$ici_work/files.json" --output "$ici_output"
ici_verify_snapshot
ici_archive_digest=$(sha256sum -- "$ici_output"); ici_archive_digest=${ici_archive_digest%% *}
(set -o noclobber; printf '%s  %s\n' "$ici_archive_digest" "$(basename -- "$ici_output")" > "$ici_output.sha256")
if [[ $ici_allow_dirty == 0 ]]; then
    [[ $(git -C "$ici_repo" rev-parse HEAD) == "$ici_revision" && -z $(git -C "$ici_repo" status --porcelain) ]] || { echo 'The source tree changed during the build; the candidate is not an exact-HEAD release artifact' >&2; exit 2; }
fi
