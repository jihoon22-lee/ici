"""Pin strict CPython character-codec observations as inert native mapping data.

Run with CPython 3.14.7 -I -B. Writing requires --write; --check never changes
source or expected evidence. No Python executable/module enters a native bundle.
"""

import argparse
import array
import encodings
import encodings.aliases
import hashlib
import importlib
import json
import pkgutil
import struct
import subprocess
import sys
from pathlib import Path

UNDEFINED = 0xFFFFFFFF
MULTI = (
    "big5",
    "big5hkscs",
    "cp932",
    "cp949",
    "cp950",
    "euc_jis_2004",
    "euc_jisx0213",
    "euc_jp",
    "euc_kr",
    "gb18030",
    "gb2312",
    "gbk",
    "johab",
    "shift_jis",
    "shift_jis_2004",
    "shift_jisx0213",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_decode(raw, name):
    try:
        return raw.decode(name, errors="strict")
    except UnicodeError:
        return None


def observations(name):
    state = hashlib.sha256()
    accepted = {1: 0, 2: 0}
    for width in (1, 2):
        state.update(bytes([width]))
        for value in range(256**width):
            raw = value.to_bytes(width, "big")
            text = strict_decode(raw, name)
            state.update(raw)
            if text is None:
                state.update(b"\x00")
            else:
                encoded = text.encode("utf-8")
                state.update(b"\x01" + struct.pack("<I", len(encoded)) + encoded)
                accepted[width] += 1
    return {"all_single_pair_sha256": state.hexdigest(), "accepted": accepted}


def generate():
    root = Path(__file__).resolve().parents[3]
    data_root = root / "crates/ici-analysis/data/python-codecs"
    output = {}
    combined = []
    combined_index = {}

    def entry(text):
        if text is None:
            return UNDEFINED
        if len(text) == 1:
            return ord(text)
        assert len(text) == 2, repr(text)
        pair = tuple(map(ord, text))
        if pair not in combined_index:
            combined_index[pair] = len(combined)
            combined.append(pair)
        return 0x80000000 | combined_index[pair]

    def table_bytes(values):
        result = array.array("I", values)
        assert result.itemsize == 4
        if sys.byteorder != "little":
            result.byteswap()
        return result.tobytes()

    records = {}
    for module in sorted(pkgutil.iter_modules(encodings.__path__), key=lambda item: item.name):
        try:
            implementation = importlib.import_module("encodings." + module.name)
        except ImportError:
            continue
        table = getattr(implementation, "decoding_table", None)
        if isinstance(table, str) and len(table) == 256:
            name = module.name
            values = [UNDEFINED if scalar == "\ufffe" else ord(scalar) for scalar in table]
            filename = name + ".single.bin"
            output[filename] = table_bytes(values)
            records[name] = {"kind": "Single", "single": filename, "double": None, "triple": None}
    output["ascii.single.bin"] = table_bytes(
        [value if value < 128 else UNDEFINED for value in range(256)]
    )
    records["ascii"] = {
        "kind": "Single",
        "single": "ascii.single.bin",
        "double": None,
        "triple": None,
    }
    for name in MULTI:
        single = [entry(strict_decode(bytes([byte]), name)) for byte in range(256)]
        double = [UNDEFINED] * 65536
        for first in range(256):
            if single[first] != UNDEFINED:
                continue
            for second in range(256):
                double[first * 256 + second] = entry(strict_decode(bytes([first, second]), name))
        filenames = {"single": name + ".single.bin", "double": name + ".double.bin", "triple": None}
        output[filenames["single"]] = table_bytes(single)
        output[filenames["double"]] = table_bytes(double)
        if name in ("euc_jp", "euc_jis_2004", "euc_jisx0213"):
            triple = [
                entry(strict_decode(bytes([0x8F, second, third]), name))
                for second in range(256)
                for third in range(256)
            ]
            filenames["triple"] = name + ".triple.bin"
            output[filenames["triple"]] = table_bytes(triple)
        kind = {"euc_kr": "EucKr", "gb18030": "Gb18030"}.get(name, "Double")
        records[name] = {"kind": kind, **filenames}
    leading, vowel, trailing = [None] * 19, [None] * 21, [None] * 28
    for scalar in range(0xAC00, 0xD7A4):
        encoded = chr(scalar).encode("euc_kr")
        if len(encoded) == 8:
            index = scalar - 0xAC00
            for table, position, value in (
                (leading, index // 588, encoded[3]),
                (vowel, index // 28 % 21, encoded[5]),
                (trailing, index % 28, encoded[7]),
            ):
                assert table[position] in (None, value)
                table[position] = value
    assert None not in leading + vowel + trailing
    hangul_hash = hashlib.sha256()
    for l_index, l_byte in enumerate(leading):
        for v_index, v_byte in enumerate(vowel):
            for t_index, t_byte in enumerate(trailing):
                raw = bytes([0xA4, 0xD4, 0xA4, l_byte, 0xA4, v_byte, 0xA4, t_byte])
                text = raw.decode("euc_kr")
                assert text == chr(0xAC00 + (l_index * 21 + v_index) * 28 + t_index)
                hangul_hash.update(raw + text.encode())
    ranges = []
    gb_hash = hashlib.sha256()
    for pointer in range(126 * 10 * 126 * 10):
        index = pointer
        fourth = index % 10 + 0x30
        index //= 10
        third = index % 126 + 0x81
        index //= 126
        second = index % 10 + 0x30
        first = index // 10 + 0x81
        raw = bytes([first, second, third, fourth])
        text = strict_decode(raw, "gb18030")
        gb_hash.update(raw)
        if text is None:
            gb_hash.update(b"\x00")
            continue
        assert len(text) == 1
        scalar = ord(text)
        gb_hash.update(b"\x01" + text.encode())
        if (
            ranges
            and ranges[-1][1] + 1 == pointer
            and ranges[-1][2] + pointer - ranges[-1][0] == scalar
        ):
            ranges[-1][1] = pointer
        else:
            ranges.append([pointer, pointer, scalar])
    native = [
        "// Generated strict CPython 3.14.7 codec observations; see data/python-codecs/README.txt."
    ]
    names = sorted(records)
    native.append("pub(crate) const TABLES: &[Table] = &[")
    for name in names:
        record = records[name]
        native += [
            "    Table {",
            f'        name: "{name}",',
            f"        kind: Kind::{record['kind']},",
        ]
        for key in ("single", "double", "triple"):
            filename = record[key]
            included = f'include_bytes!("../data/python-codecs/{filename}")' if filename else "None"
            if key != "single" and filename:
                included = "Some(" + included + ")"
            native.append(f"        {key}: {included},")
        native.append("    },")
    native.append("];")
    aliases = {name: name for name in names}
    aliases["latin_1"] = "iso8859_1"
    for alias, name in encodings.aliases.aliases.items():
        name = "iso8859_1" if name == "latin_1" else name
        if name in records or name in ("utf_8", "utf_8_sig"):
            aliases[alias] = name
    aliases.update(utf_8="utf_8", utf_8_sig="utf_8_sig")
    native.append("pub(crate) const ALIASES: &[(&str, Codec)] = &[")
    for alias, name in sorted(aliases.items()):
        variant = (
            "Codec::Utf8"
            if name in ("utf_8", "utf_8_sig")
            else f"Codec::Table({names.index(name)})"
        )
        native.append(f'    ("{alias}", {variant}),')
    native.append("];")
    native.append("pub(crate) const COMBINED: &[(u32, u32)] = &[")
    native += [f"    ({first}, {second})," for first, second in combined]
    native.append("];")
    for name, values in (("LEADING", leading), ("VOWEL", vowel), ("TRAILING", trailing)):
        native.append(f"pub(crate) const EUCKR_{name}: &[u8] = &{values!r};")
    native.append("pub(crate) const GB18030_RANGES: &[(u32, u32, u32)] = &[")
    native += [f"    ({first}, {last}, {scalar})," for first, last, scalar in ranges]
    native.append("];")
    generated = subprocess.run(
        ["rustfmt", "--edition", "2024", "--emit", "stdout"],
        input="\n".join(native) + "\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.encode()
    manifest = {
        "format": "ici.python-codec-observations/v1",
        "reference_python": "3.14.7",
        "source_url": "https://github.com/python/cpython/tree/v3.14.7/Lib/encodings",
        "cjk_source_url": "https://github.com/python/cpython/tree/v3.14.7/Modules/cjkcodecs",
        "license_url": "https://raw.githubusercontent.com/python/cpython/v3.14.7/LICENSE",
        "license_sha256": sha((data_root / "LICENSE.txt").read_bytes()),
        "cjk_notice_sha256": sha((data_root / "NOTICE-cjkcodecs.txt").read_bytes()),
        "storage": "UTF-32LE scalar entries; ffffffff undefined; high-bit entries index COMBINED; no decoder code or runtime",
        "files": {
            name: {"sha256": sha(data), "size": len(data)} for name, data in sorted(output.items())
        },
        "generated_rust_sha256": sha(generated),
        "codecs": {},
        "aliases": aliases,
        "euc_kr_all_hangul_sha256": hangul_hash.hexdigest(),
        "gb18030_all_four_byte_sha256": gb_hash.hexdigest(),
        "gb18030_range_count": len(ranges),
    }
    for name in names:
        print("observing", name, flush=True)
        manifest["codecs"][name] = {**records[name], **observations(name)}
    return root, output, generated, manifest


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    assert sys.version_info[:3] == (3, 14, 7), "Use the pinned reference CPython 3.14.7"
    assert sys.flags.isolated and sys.dont_write_bytecode, "Run Python with -I -B"
    root, output, native, manifest = generate()
    directory = root / "crates/ici-analysis/data/python-codecs"
    paths = {directory / name: data for name, data in output.items()}
    paths[root / "crates/ici-analysis/src/codec_tables.rs"] = native
    paths[directory / "manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode()
    for path, data in paths.items():
        if args.write:
            path.write_bytes(data)
        else:
            assert path.read_bytes() == data, str(path)
    print(
        f"{len(manifest['codecs'])} strict codec tables {'written' if args.write else 'verified'}"
    )


if __name__ == "__main__":
    main()
