#!/usr/bin/env bash
# Release checks for one native archive produced by scripts/build-native.sh:
# byte reproducibility against an independent build, exact notice/license
# packaging, static ELF linkage, no Python payload, rootless bootstrap into an
# isolated XDG root, offline analysis without Python on PATH, and the doctor
# report of a shadowing `ici`. It needs no network and no root.
#
# It complements, and does not replace, tests/rust-parity/distribution/
# verify_artifact.py (update/rollback/failure/private-CA corpus).
set -euo pipefail

ici_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ici_archive=
ici_compare=
ici_evidence=
ici_revision=
ici_version=
ici_allow_prerelease=0
ici_allow_snapshot=0
while (($#)); do
    case "$1" in
        --archive|--compare|--evidence|--expect-revision|--expect-version)
            (($# >= 2)) || { echo 'Release check option requires a value' >&2; exit 2; }
            case "$1" in
                --archive) ici_archive=$2 ;;
                --compare) ici_compare=$2 ;;
                --evidence) ici_evidence=$2 ;;
                --expect-revision) ici_revision=$2 ;;
                --expect-version) ici_version=$2 ;;
            esac
            shift 2
            ;;
        --allow-prerelease-version) ici_allow_prerelease=1; shift ;;
        --allow-development-snapshot) ici_allow_snapshot=1; shift ;;
        --help)
            echo 'Usage: check-native-release.sh --archive ARCHIVE --evidence DIRECTORY [--compare INDEPENDENT_ARCHIVE] [--expect-revision SHA] [--expect-version VERSION] [--allow-prerelease-version] [--allow-development-snapshot]'
            exit 0
            ;;
        *) echo 'Unknown release check option' >&2; exit 2 ;;
    esac
done
[[ -f "$ici_archive" && -n "$ici_evidence" ]] || { echo 'Provide an existing --archive and an --evidence directory' >&2; exit 2; }
for ici_command in jq tar gzip sha256sum readelf cmp mktemp sh; do
    command -v "$ici_command" >/dev/null || { echo "Required check tool is missing: $ici_command" >&2; exit 2; }
done
mkdir -p -- "$ici_evidence"
ici_evidence=$(cd -- "$ici_evidence" && pwd -P)
ici_work=$(mktemp -d "$ici_evidence/native-release-check.XXXXXXXX")
trap 'exit 130' INT
trap 'exit 143' TERM HUP
ici_log="$ici_evidence/check-native-release.log"
: > "$ici_log"
ici_fail() { echo "FAIL: $*" | tee -a "$ici_log" >&2; exit 1; }
ici_pass() { echo "PASS: $*" | tee -a "$ici_log"; }
ici_digest() { local ici_value; ici_value=$(sha256sum -- "$1"); printf '%s' "${ici_value%% *}"; }

ici_sha=$(ici_digest "$ici_archive")
echo "archive $(basename -- "$ici_archive") sha256 $ici_sha" | tee -a "$ici_log"
if [[ -n "$ici_compare" ]]; then
    cmp -s -- "$ici_archive" "$ici_compare" || ici_fail "independent build differs: $(ici_digest "$ici_compare")"
    ici_pass 'independent build is byte-identical'
fi
if [[ -f "$ici_archive.sha256" ]]; then
    [[ $(cat -- "$ici_archive.sha256") == "$ici_sha  $(basename -- "$ici_archive")" ]] || ici_fail 'checksum file does not name this archive digest'
    ici_pass 'checksum file matches'
fi

# Unpack with the system tar only after the member list is proven safe; the
# product's own installer re-verifies everything below independently.
tar -tzf "$ici_archive" > "$ici_work/members"
grep -Eq '(^/|(^|/)\.\.(/|$))' "$ici_work/members" && ici_fail 'archive contains an unsafe path'
mkdir -- "$ici_work/tree"
tar -xzf "$ici_archive" -C "$ici_work/tree" --no-same-owner --no-same-permissions
[[ -z $(find "$ici_work/tree" ! -type f ! -type d -print -quit) ]] || ici_fail 'archive contains a non-regular member'
ici_tree="$ici_work/tree"
ici_manifest="$ici_tree/manifest.json"
[[ $(jq -r '.schema_id, .schema_version' "$ici_manifest" | paste -sd' ') == 'ici.native-distribution 1' ]] || ici_fail 'manifest schema is not ici.native-distribution v1'
ici_listed=$(jq -r '.files | keys[]' "$ici_manifest" | LC_ALL=C sort)
ici_present=$(grep -vx 'manifest.json' "$ici_work/members" | grep -v '/$' | LC_ALL=C sort)
[[ "$ici_listed" == "$ici_present" ]] || ici_fail 'archive members differ from the manifest file list'
while IFS=$'\t' read -r ici_path ici_file_sha ici_file_size; do
    [[ $(ici_digest "$ici_tree/$ici_path") == "$ici_file_sha" && $(stat -c %s -- "$ici_tree/$ici_path") == "$ici_file_size" ]] || ici_fail "manifest digest or size differs for $ici_path"
done < <(jq -r '.files | to_entries[] | [.key, .value.sha256, .value.size] | @tsv' "$ici_manifest")
ici_pass 'every member is listed once with a matching digest'

ici_target=$(jq -r '.target' "$ici_manifest")
ici_manifest_version=$(jq -r '.version' "$ici_manifest")
ici_manifest_revision=$(jq -r '.build_inputs.source_revision' "$ici_manifest")
ici_snapshot=$(jq -r '.build_inputs.platform_evidence.development_snapshot' "$ici_manifest")
[[ $ici_target == x86_64-unknown-linux-musl ]] || ici_fail "release target is $ici_target, not the measured musl target"
[[ -z "$ici_version" || $ici_manifest_version == "$ici_version" ]] || ici_fail "archive version $ici_manifest_version is not $ici_version"
[[ -z "$ici_revision" || $ici_manifest_revision == "$ici_revision" ]] || ici_fail "archive source revision $ici_manifest_revision is not $ici_revision"
# A release needs a clean exact-source build of a final version. Candidates may
# carry a pre-release version; only local rehearsals accept dirty snapshots.
[[ $ici_allow_snapshot == 1 || $ici_snapshot == 0 ]] || ici_fail 'archive is a development snapshot (--allow-dirty build)'
[[ $ici_allow_prerelease == 1 || $ici_manifest_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || ici_fail "archive version $ici_manifest_version is not a final release version"
[[ $(basename -- "$ici_archive") == "ici-$ici_manifest_version-$ici_target.tar.gz" ]] || ici_fail 'archive file name does not match its manifest version and target'
ici_pass "manifest $ici_manifest_version $ici_target revision $ici_manifest_revision snapshot $ici_snapshot"

# No Python product may ride along (ADR-0008): no pyz, wheel, interpreter or module.
if grep -Eiq '(\.pyz$|\.whl$|\.py$|\.pyc$|(^|/)python[0-9.]*$|(^|/)lib/python)' "$ici_work/members"; then
    ici_fail 'archive contains a Python payload'
fi
ici_pass 'archive contains no Python product payload'

# Licenses: every retained source/third-party file is shipped byte-for-byte,
# the tool licenses equal their pins, and the generated notices name the
# static runtime components the core actually links.
ici_source_root="$ici_repo/packaging/native"
while IFS= read -r -d '' ici_file; do
    ici_relative=${ici_file#"$ici_source_root/"}
    [[ -f "$ici_tree/licenses/$ici_relative" ]] || ici_fail "missing retained notice licenses/$ici_relative"
    cmp -s -- "$ici_file" "$ici_tree/licenses/$ici_relative" || ici_fail "retained notice differs: licenses/$ici_relative"
done < <(find "$ici_source_root/sources" "$ici_source_root/third-party" -type f -print0 | LC_ALL=C sort -z)
for ici_file in LICENSE-ici-MIT.txt:ici-MIT.txt upstream-source-lock.json:upstream-source-lock.json upstream-notices.json:upstream-notices.json; do
    cmp -s -- "$ici_source_root/${ici_file%%:*}" "$ici_tree/licenses/${ici_file##*:}" || ici_fail "licenses/${ici_file##*:} differs from packaging/native"
done
cmp -s -- "$ici_repo/crates/ici-analysis/data/unicode-16/LICENSE.txt" "$ici_tree/licenses/Unicode-V3.txt" || ici_fail 'licenses/Unicode-V3.txt differs from the Unicode data license'
while IFS=$'\t' read -r ici_tool ici_license_sha ici_binary_sha; do
    [[ $(ici_digest "$ici_tree/licenses/$ici_tool.txt") == "$ici_license_sha" ]] || ici_fail "licenses/$ici_tool.txt is not the pinned upstream license"
    [[ -s "$ici_tree/licenses/$ici_tool-third-party.txt" ]] || ici_fail "licenses/$ici_tool-third-party.txt is missing"
    [[ $(ici_digest "$ici_tree/tools/bin/$ici_tool") == "$ici_binary_sha" ]] || ici_fail "tools/bin/$ici_tool is not the pinned binary"
done < <(jq -r '.tools[] | [.tool, .license_sha256, .binary_sha256] | @tsv' "$ici_source_root/tool-inputs.json")
ici_notices="$ici_tree/licenses/THIRD-PARTY-NOTICES.txt"
for ici_id in rust-1.99.1-standard-library musl-1.2.5 rust-1.99.1-compiler-rt-LICENSE.TXT rust-1.99.1-libunwind-LICENSE.TXT rust-1.99.1-llvm-CREDITS.TXT; do
    grep -Fqx "===== $ici_id =====" "$ici_notices" || ici_fail "THIRD-PARTY-NOTICES.txt has no $ici_id section"
done
[[ $(jq -r '.root_package' "$ici_tree/licenses/licenses.json") == ici-cli* ]] || ici_fail 'licenses.json does not describe the ici-cli package'
[[ $(jq -r '.notices_sha256' "$ici_tree/licenses/licenses.json") == $(ici_digest "$ici_notices") ]] || ici_fail 'licenses.json does not bind THIRD-PARTY-NOTICES.txt'
ici_pass 'Rust/musl/LLVM notices, third-party licenses and pinned tool licenses are packaged'

for ici_elf in bin/ici tools/bin/ruff tools/bin/ty; do
    ici_headers=$(readelf --program-headers --wide "$ici_tree/$ici_elf") || ici_fail "$ici_elf is not a readable ELF file"
    ici_dynamic=$(readelf --dynamic --wide "$ici_tree/$ici_elf") || ici_fail "$ici_elf is not a readable ELF file"
    [[ ! "$ici_headers" =~ INTERP && ! "$ici_dynamic" =~ NEEDED ]] || ici_fail "$ici_elf is not a static executable"
done
ici_pass 'core and tools have no ELF interpreter or shared-library dependency'

# Rootless bootstrap into an isolated XDG root: no HOME tree, no Python, no
# network. The bootstrap tools are linked into a private PATH directory.
ici_home="$ici_work/home"
ici_data="$ici_work/data"
ici_bin="$ici_work/bootstrap-bin"
mkdir -- "$ici_home" "$ici_data" "$ici_bin" "$ici_work/tmp"
for ici_tool in mktemp dd wc sha256sum tar gzip chmod rm od tr; do
    ln -s -- "$(command -v "$ici_tool")" "$ici_bin/$ici_tool"
done
ici_env=(env -i "HOME=$ici_home" "XDG_DATA_HOME=$ici_data" "XDG_CONFIG_HOME=$ici_work/config" "XDG_CACHE_HOME=$ici_work/cache" "TMPDIR=$ici_work/tmp" "LANG=C.UTF-8")
"${ici_env[@]}" "PATH=$ici_bin" "$(command -v sh)" "$ici_repo/scripts/install-native.sh" --archive "$ici_archive" --sha256 "$ici_sha" > "$ici_evidence/install.stdout" 2> "$ici_evidence/install.stderr" || ici_fail 'install-native.sh bootstrap failed'
ici_installed="$ici_data/ici/native/bin/ici"
[[ -x "$ici_installed" ]] || ici_fail 'bootstrap did not create the XDG entrypoint'
[[ -z $(find "$ici_home" -mindepth 1 -print -quit) ]] || ici_fail 'bootstrap wrote under HOME although XDG_DATA_HOME was set'
[[ $("${ici_env[@]}" PATH= "$ici_installed" --version) == "ici $ici_manifest_version" ]] || ici_fail 'installed ici --version differs from the manifest'
"${ici_env[@]}" PATH= "$ici_installed" setup --inspect --json --prefix "$ici_data/ici/native" > "$ici_evidence/inspect.json" || ici_fail 'setup --inspect failed'
ici_pass 'rootless XDG bootstrap installed the archive and the entrypoint reports its version'

# Offline analysis with the bundled tools and no Python anywhere on PATH.
ici_project="$ici_work/project"
mkdir -p -- "$ici_project/src/sample"
printf '"""Sample package."""\n' > "$ici_project/src/sample/__init__.py"
printf '"""Sample module."""\n\n\ndef add(left: int, right: int) -> int:\n    """Return the sum."""\n    return left + right\n' > "$ici_project/src/sample/core.py"
ici_status=0
(cd -- "$ici_project" && "${ici_env[@]}" PATH= "$ici_installed" analyze) > "$ici_evidence/analyze.stdout" 2> "$ici_evidence/analyze.stderr" || ici_status=$?
echo "analyze exit $ici_status" >> "$ici_log"
[[ $ici_status == 0 || $ici_status == 1 ]] || ici_fail "offline analyze exited $ici_status (3 means incomplete: the bundled tools were not used)"
[[ -s "$ici_project/.ici/rust-v1/result.json" && -s "$ici_project/.ici/rust-v1/result.html" ]] || ici_fail 'analyze did not save result.json and result.html under .ici/rust-v1'
[[ $(jq -r '.schema_id' "$ici_project/.ici/rust-v1/result.json") == ici.next.run ]] || ici_fail 'saved result is not an ici.next.run envelope'
if grep -Eq '<(script|link|img)[^>]+(src|href)="(https?:)?//' "$ici_project/.ici/rust-v1/result.html"; then
    ici_fail 'offline HTML loads an external resource'
fi
cp -- "$ici_project/.ici/rust-v1/result.json" "$ici_evidence/analyze-result.json"
ici_pass "offline analyze completed (exit $ici_status) without Python on PATH; HTML has no external resource"

# A foreign `ici` earlier on PATH (a pyz/uv/pip leftover) is reported by doctor.
mkdir -- "$ici_work/foreign"
printf '#!/bin/sh\nexit 99\n' > "$ici_work/foreign/ici"
chmod 755 -- "$ici_work/foreign/ici"
(cd -- "$ici_project" && "${ici_env[@]}" "PATH=$ici_work/foreign:$ici_data/ici/native/bin" ICI_PYTHON=/missing/legacy/python "$ici_installed" doctor) > "$ici_evidence/doctor.stdout" 2> "$ici_evidence/doctor.stderr" || true
grep -Fq "path    shadowing ici: $ici_work/foreign/ici" "$ici_evidence/doctor.stdout" || ici_fail 'doctor did not report the shadowing ici on PATH'
grep -Fq 'legacy  ICI_PYTHON=/missing/legacy/python' "$ici_evidence/doctor.stdout" || ici_fail 'doctor did not report the historical ICI_PYTHON variable'
ici_pass 'doctor reports a shadowing ici and the historical ICI_PYTHON variable'

jq -n --arg archive "$(basename -- "$ici_archive")" --arg sha "$ici_sha" --arg version "$ici_manifest_version" --arg revision "$ici_manifest_revision" --arg target "$ici_target" --argjson reproducible "$([[ -n "$ici_compare" ]] && echo true || echo false)" --argjson analyze "$ici_status" \
    '{schema:"ici.native-release-check/v1",archive:$archive,sha256:$sha,version:$version,source_revision:$revision,target:$target,independent_build_identical:$reproducible,offline_analyze_exit:$analyze,result:"PASS"}' > "$ici_evidence/check-native-release.json"
rm -rf -- "$ici_work"
ici_pass "all release checks passed for $ici_sha"
