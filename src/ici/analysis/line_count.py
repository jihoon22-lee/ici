"""Single-file line counting — code/comment/blank classification.

Extracted from the stable ``ici.engines.line`` shell so the next path
(``ici.languages.python.lines``) and any other consumer share one algorithm.
"""

from pathlib import Path


def count_lines(filepath: Path) -> tuple[int, int, int]:
    """Count code, comment and blank lines in one file.

    Module level because it never used ``self``, and because #206 asks the
    next path to connect this algorithm rather than grow a second one that
    means almost the same thing. One function, both callers.
    """

    code, comment, blank = 0, 0, 0
    in_block_comment = False

    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    blank += 1
                    continue

                if filepath.suffix in (".cpp", ".c", ".h", ".hpp", ".cc", ".cxx"):
                    if in_block_comment:
                        comment += 1
                        if "*/" in stripped:
                            in_block_comment = False
                        continue
                    if stripped.startswith("/*"):
                        comment += 1
                        if "*/" not in stripped:
                            in_block_comment = True
                        continue
                    if stripped.startswith("//"):
                        comment += 1
                        continue
                elif filepath.suffix in (".py", ".sh", ".csh", ".toml", ".yml", ".yaml"):
                    if stripped.startswith("#"):
                        comment += 1
                        continue

                code += 1
    except (OSError, UnicodeDecodeError) as err:
        # Unreadable/undecodable files contribute nothing; keep counting others.
        _ = err

    return code, comment, blank
