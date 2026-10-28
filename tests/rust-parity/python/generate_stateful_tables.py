"""Observe strict CPython ISO-2022 character sets for native state machines.

Development only. Run CPython 3.14.7 -I -B; --write changes pinned evidence.
"""

import argparse
import array
import base64
import codecs
import encodings.aliases
import hashlib
import json
import subprocess
import sys
from pathlib import Path

NAMES = (
    "iso2022_jp",
    "iso2022_jp_1",
    "iso2022_jp_2",
    "iso2022_jp_2004",
    "iso2022_jp_3",
    "iso2022_jp_ext",
    "iso2022_kr",
)
UNDEFINED = 0xFFFFFFFF


def sha(data):
    return hashlib.sha256(data).hexdigest()


def observations(name, prefix, width):
    digest = hashlib.sha256()
    for value in range(256**width):
        raw = value.to_bytes(width, "big")
        digest.update(raw)
        try:
            text = (prefix + raw).decode(name).encode()
        except UnicodeError:
            digest.update(b"\x00")
        else:
            digest.update(b"\x01" + len(text).to_bytes(4, "little") + text)
    return digest.hexdigest()


def transitions(name, fragments):
    seed = 0xC0DECAFE
    digest = hashlib.sha256()
    accepted = 0

    def next_value():
        nonlocal seed
        seed ^= (seed << 13) & 0xFFFFFFFF
        seed ^= seed >> 17
        seed ^= (seed << 5) & 0xFFFFFFFF
        return seed

    for _ in range(4096):
        raw = bytearray()
        for _ in range(next_value() % 12):
            value = next_value()
            if value % 3 == 0:
                raw.append(next_value() % 256)
            else:
                raw.extend(fragments[value % len(fragments)])
        digest.update(len(raw).to_bytes(4, "little") + raw)
        try:
            text = raw.decode(name).encode()
        except UnicodeError:
            digest.update(b"\x00")
        else:
            accepted += 1
            digest.update(b"\x01" + len(text).to_bytes(4, "little") + text)
    return {"sha256": digest.hexdigest(), "accepted": accepted}


def generate():
    root = Path(__file__).resolve().parents[3]
    combined = []
    output = {}
    records = {}
    native = ["// Generated CPython 3.14.7 observations; see data/python-codecs/README.txt."]

    def entry(text):
        if len(text) == 1:
            return ord(text)
        assert len(text) == 2, repr(text)
        pair = tuple(map(ord, text))
        if pair not in combined:
            combined.append(pair)
        return 0x80000000 | combined.index(pair)

    def store(values):
        values = array.array("I", values)
        if sys.byteorder != "little":
            values.byteswap()
        raw = values.tobytes()
        name = "iso2022-" + sha(raw)[:16] + ".bin"
        output[name] = raw
        return name

    for name in NAMES:
        designations = []
        table_digest = hashlib.sha256()
        for mark in range(256):
            if mark == ord("B"):
                continue
            width = 2 if mark & 128 else 1
            prefix = b"\x1b$(" + bytes([mark & 127]) if width == 2 else b"\x1b(" + bytes([mark])
            decoder = codecs.getincrementaldecoder(name)()
            try:
                assert decoder.decode(prefix, final=True) == ""
            except UnicodeError:
                continue
            state = decoder.getstate()
            values = [UNDEFINED] * 256**width
            for value in range(256**width):
                raw = value.to_bytes(width, "big")
                # Native control/escape handling precedes designation lookup.
                if not 32 <= raw[0] < 128:
                    continue
                decoder.setstate(state)
                try:
                    text = decoder.decode(raw, final=True)
                except UnicodeError:
                    continue
                if len(text) in (1, 2):
                    values[value] = entry(text)
            filename = store(values)
            designations.append(
                {
                    "mark": mark,
                    "width": width,
                    "file": filename,
                    "prefix_base64": base64.b64encode(prefix).decode(),
                    "all_input_sha256": observations(name, prefix, width),
                }
            )
            table_digest.update(bytes([mark, width]) + output[filename])
        # G2 single-shift has a distinct historical Greek mapping and accepts
        # different high bytes from a modern ISO-8859-7 single-byte decoder.
        g2 = []
        if name == "iso2022_jp_2":
            for mark in (ord("B"), ord("A"), ord("F")):
                values = []
                for value in range(256):
                    try:
                        text = (b"\x1b." + bytes([mark]) + b"\x1bN" + bytes([value])).decode(name)
                        values.append(entry(text))
                    except UnicodeError:
                        values.append(UNDEFINED)
                g2.append({"mark": mark, "file": store(values)})
        records[name] = {
            "designations": designations,
            "g2": g2,
            "observations_sha256": table_digest.hexdigest(),
            "ascii_single_sha256": observations(name, b"", 1),
            "ascii_pair_sha256": observations(name, b"", 2),
        }
    records["hz"] = {
        "ascii_single_sha256": observations("hz", b"", 1),
        "ascii_pair_sha256": observations("hz", b"", 2),
        "shifted_pair_sha256": observations("hz", b"~{", 2),
    }
    fragments = [
        b"plain",
        b"\n",
        b"\r",
        b"\x0e",
        b"\x0f",
        b"\x1b",
        b"\x1b$",
        b"\x1b(B",
        b"\x1b$B",
        b"\x1b$)C",
        b"\x1b$(D",
        b"\x1b$(O",
        b"\x1b$(P",
        b"\x1b$(Q",
        b"\x1b(J",
        b"\x1b(I",
        b"\x1b.A",
        b"\x1b.F",
        b"\x1bNB",
        b"\x1bNi",
        b"\x1bNa",
        b"\x1b&@\x1b$B",
        b"\x1b&@\x1b$A",
        b"\x1b[",
        b"\x1bN\xff",
        b"\x1b(B\x1bN",
        b"\x1b" + b" " * 16,
        b"~{",
        b"~}",
        b"~~",
        b"~\n",
        b"F|K\\",
        b"!!",
        b"$w",
        b"0!",
        b"\x1b)J",
        b"\x1b$(A",
        b"\x1b.C",
    ]
    for name in (*NAMES, "hz"):
        records[name]["transition_observations"] = transitions(name, fragments)
    native.append("const ISO_TABLES: &[IsoTable] = &[")
    for name in NAMES:
        record = records[name]
        native += [
            "IsoTable {",
            f'name: "{name}",',
            f"shift: {str(name == 'iso2022_kr').lower()},",
            "designations: &[",
        ]
        for row in record["designations"]:
            native.append(
                f'({row["mark"]}, {row["width"]}, include_bytes!("../data/python-codecs/{row["file"]}")),'
            )
        native += ["],", "g2: &["]
        for row in record["g2"]:
            native.append(
                f'({row["mark"]}, include_bytes!("../data/python-codecs/{row["file"]}")),'
            )
        native += ["],", "},"]
    native += ["];", "const ISO_COMBINED: &[(u32, u32)] = &["]
    native += [f"({first}, {second})," for first, second in combined]
    native += ["];", "const ISO_ALIASES: &[(&str, usize)] = &["]
    aliases = {name: NAMES.index(name) for name in NAMES}
    aliases.update(
        {
            alias: NAMES.index(name)
            for alias, name in encodings.aliases.aliases.items()
            if name in NAMES
        }
    )
    native += [f'("{alias}", {index}),' for alias, index in sorted(aliases.items())]
    native.append("];")
    source = subprocess.run(
        ["rustfmt", "--edition", "2024", "--emit", "stdout"],
        input="\n".join(native),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.encode()
    manifest = {
        "format": "ici.python-stateful-codec-observations/v1",
        "reference_python": "3.14.7",
        "source_url": "https://github.com/python/cpython/blob/v3.14.7/Modules/cjkcodecs/_codecs_iso2022.c",
        "codecs": records,
        "files": {
            name: {"sha256": sha(data), "size": len(data)} for name, data in sorted(output.items())
        },
        "generated_rust_sha256": sha(source),
        "aliases": aliases,
        "transition_fragments_base64": [base64.b64encode(raw).decode() for raw in fragments],
        "transition_seed": 0xC0DECAFE,
        "transition_count": 4096,
    }
    paths = {
        root / "crates/ici-analysis/data/python-codecs" / name: data
        for name, data in output.items()
    }
    paths[root / "crates/ici-analysis/data/python-codecs/stateful-manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode()
    paths[root / "crates/ici-analysis/src/stateful_tables.rs"] = source
    return paths, records


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    assert sys.version_info[:3] == (3, 14, 7)
    assert sys.flags.isolated and sys.dont_write_bytecode
    paths, records = generate()
    for path, data in paths.items():
        if args.write:
            path.write_bytes(data)
        else:
            assert path.read_bytes() == data, path
    print(
        f"{len(records)} strict ISO-2022 designation sets {'written' if args.write else 'verified'}"
    )


if __name__ == "__main__":
    main()
