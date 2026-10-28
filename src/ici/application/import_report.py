"""Import external evidence without treating it as an ici execution or gate."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

MAX_BYTES = 32 * 1024 * 1024
SCHEMA_ID = "ici.auxiliary"


def _location(path, line=None):
    if not path:
        return None
    parsed = urlparse(str(path))
    decoded = unquote(parsed.path)
    candidate = PurePosixPath(decoded)
    if (
        parsed.scheme
        or parsed.netloc
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in decoded
    ):
        raise ValueError(f"external location must be project relative: {path!r}")
    if line is not None and int(line) < 1:
        raise ValueError("location line must be positive")
    return {"path": candidate.as_posix(), "line": int(line) if line is not None else None}


def _sarif(text):
    document = json.loads(text)
    if document.get("version") != "2.1.0" or not isinstance(document.get("runs"), list):
        raise ValueError("expected SARIF 2.1.0 runs")
    records = []
    for run in document["runs"]:
        tool = run["tool"]["driver"]["name"]
        for finding in run.get("results", []):
            locations = []
            for entry in finding.get("locations", []) + finding.get("relatedLocations", []):
                physical = entry.get("physicalLocation", {})
                artifact = physical.get("artifactLocation", {})
                uri = artifact.get("uri")
                if uri is None and "index" in artifact:
                    uri = run.get("artifacts", [])[artifact["index"]]["location"]["uri"]
                location = _location(uri, physical.get("region", {}).get("startLine"))
                if location:
                    locations.append(location)
            message = finding.get("message", {})
            records.append(
                {
                    "kind": "diagnostic",
                    "provider": tool,
                    "rule": finding.get("ruleId", ""),
                    "status": finding.get("level", "warning"),
                    "message": message.get("text", message.get("markdown", "")),
                    "locations": locations,
                    "suppressed": bool(finding.get("suppressions")),
                }
            )
    return records


def _junit(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("JUnit DTD/entity declarations are not supported")
    root = ElementTree.fromstring(text)
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("expected JUnit testsuite/testsuites")
    records = []
    for case in root.iter("testcase"):
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        detail = error if error is not None else failure
        status = (
            "error"
            if error is not None
            else "failed"
            if failure is not None
            else "skipped"
            if skipped is not None
            else "passed"
        )
        location = _location(case.get("file"))
        native_line = int(case.get("line")) if case.get("line") is not None else None
        if native_line is not None and native_line < 0:
            raise ValueError("JUnit native line cannot be negative")
        records.append(
            {
                "kind": "test",
                "provider": "junit",
                "name": case.get("name", ""),
                "suite": case.get("classname", ""),
                "native_line": native_line,
                "line_base": "producer-defined",
                "status": status,
                "duration": case.get("time"),
                "message": (detail.get("message", "") or detail.text or "")
                if detail is not None
                else "",
                "locations": [location] if location else [],
            }
        )
    return records


def _lcov(text):
    records = []
    current = None
    for line in text.splitlines():
        if line.startswith("SF:"):
            if current is not None:
                raise ValueError("LCOV record missing end_of_record")
            current = {
                "kind": "coverage",
                "provider": "lcov",
                "location": _location(line[3:]),
                "lines": [],
            }
        elif line.startswith("DA:"):
            if current is None:
                raise ValueError("LCOV DA without SF")
            row, count, *_ = line[3:].split(",")
            if int(row) < 1 or int(count) < 0:
                raise ValueError("LCOV line/count outside bounds")
            current["lines"].append({"line": int(row), "hits": int(count)})
        elif line == "end_of_record":
            if current is None:
                raise ValueError("LCOV end without SF")
            records.append(current)
            current = None
    if current is not None or not records:
        raise ValueError("LCOV requires complete source records")
    return records


def import_report(source: Path, format: str, *, scope: str, observed_at: str | None = None):
    if source.stat().st_size > MAX_BYTES:
        raise ValueError("report exceeds 32 MiB import limit")
    payload = source.read_bytes()
    if observed_at is not None:
        stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("--observed-at requires an explicit timezone")
    parsers = {"sarif": _sarif, "junit": _junit, "lcov": _lcov}
    if format not in parsers:
        raise ValueError("format must be sarif, junit or lcov")
    if not scope.strip():
        raise ValueError("scope must describe the producing run")
    try:
        records = parsers[format](payload.decode("utf-8"))
    except (KeyError, IndexError, TypeError, AttributeError, ElementTree.ParseError) as error:
        raise ValueError(f"malformed {format}: {error}") from error
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "format": format,
        "source": {"path": str(source), "sha256": hashlib.sha256(payload).hexdigest()},
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "observed_at": observed_at,
        "scope": scope,
        "evidence": "imported",
        "gate": "NOT_EVALUATED",
        "limitations": [
            "External producer evidence; ici did not execute or verify this run",
            "Observation time is unknown"
            if observed_at is None
            else "Observation time supplied by importer caller",
        ],
        "records": records,
    }


def load_auxiliary(path: Path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("auxiliary document exceeds 32 MiB")
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("schema_id") != SCHEMA_ID
        or document.get("schema_version") != 1
    ):
        raise ValueError("expected ici.auxiliary v1")
    if document.get("gate") != "NOT_EVALUATED" or document.get("evidence") != "imported":
        raise ValueError("auxiliary evidence cannot claim an ici gate")
    if not isinstance(document.get("records"), list) or not isinstance(
        document.get("source"), dict
    ):
        raise ValueError("invalid auxiliary records/provenance")
    if (
        document.get("format") not in {"sarif", "junit", "lcov"}
        or not isinstance(document.get("scope"), str)
        or not document["scope"].strip()
    ):
        raise ValueError("invalid auxiliary format/scope")
    source = document["source"]
    if not isinstance(source.get("path"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", str(source.get("sha256", ""))
    ):
        raise ValueError("invalid auxiliary source checksum")
    for key in ("imported_at", "observed_at"):
        value = document.get(key)
        if key == "observed_at" and key in document and value is None:
            continue
        if (
            not isinstance(value, str)
            or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None
        ):
            raise ValueError(f"invalid auxiliary {key}")
    if any(not isinstance(record, dict) for record in document["records"]):
        raise ValueError("invalid auxiliary record")
    return document
