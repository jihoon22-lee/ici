#!/bin/sh
# Bootstrap a digest-verified native core, which validates and installs the full archive.
set -eu

ici_archive=
ici_sha256=
ici_prefix=${XDG_DATA_HOME:-${HOME:?HOME or an explicit environment is required}/.local/share}/ici/native
while [ "$#" -gt 0 ]; do
    case "$1" in
        --archive|--sha256|--prefix)
            [ "$#" -ge 2 ] || { echo 'Installer option requires a value' >&2; exit 2; }
            case "$1" in
                --archive) ici_archive=$2 ;;
                --sha256) ici_sha256=$2 ;;
                --prefix) ici_prefix=$2 ;;
            esac
            shift 2
            ;;
        --help)
            echo 'Usage: install-native.sh --archive FILE --sha256 EXPECTED_SHA256 [--prefix DIRECTORY]'
            exit 0
            ;;
        *) echo 'Unknown native installer option' >&2; exit 2 ;;
    esac
done
[ -n "$ici_archive" ] && [ -f "$ici_archive" ] || { echo 'A local native archive is required' >&2; exit 2; }
[ "${#ici_sha256}" -eq 64 ] || { echo 'A trusted lowercase SHA-256 is required' >&2; exit 2; }
case "$ici_sha256" in *[!0-9a-f]*) echo 'Invalid trusted SHA-256' >&2; exit 2 ;; esac
ici_work=$(mktemp -d "${TMPDIR:-/tmp}/ici-native-bootstrap.XXXXXXXX")
trap 'rm -rf -- "$ici_work"' 0
trap 'exit 130' INT
trap 'exit 143' TERM HUP
# Bound a private snapshot before verification; the caller's source path can
# change without changing the exact bytes that will execute from this directory.
if ! dd if="$ici_archive" of="$ici_work/archive.tar.gz" bs=65536 count=4097 iflag=fullblock status=none 2>"$ici_work/read-error"; then
    echo 'Native archive snapshot could not be read' >&2
    exit 2
fi
ici_size=$(wc -c < "$ici_work/archive.tar.gz")
[ "$ici_size" -le 268435456 ] || { echo 'Native archive exceeds the 256 MiB bound' >&2; exit 2; }
ici_actual=$(sha256sum -- "$ici_work/archive.tar.gz")
ici_actual=${ici_actual%% *}
[ "$ici_actual" = "$ici_sha256" ] || { echo 'Native archive SHA-256 does not match the trusted digest' >&2; exit 2; }
if ! tar -xzOf "$ici_work/archive.tar.gz" -- bin/ici > "$ici_work/ici" 2>"$ici_work/archive-error"; then
    echo 'Verified archive has no readable native bootstrap executable' >&2
    exit 2
fi
chmod 700 "$ici_work/ici"
"$ici_work/ici" setup --archive "$ici_work/archive.tar.gz" --sha256 "$ici_sha256" --prefix "$ici_prefix"
