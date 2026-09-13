"""The published JSON Schemas and the code must not drift apart.

`src/ici/schemas/*.schema.json` is the contract other tools read; no ici code
loads it at runtime, which follows the repository's existing practice for
`ici-result-v3.schema.json` and `ici-compilation-export-v1.schema.json`. That
practice has a known weakness worth naming: those schemas are only validated by
tests that `import jsonschema`, and `jsonschema` is not a dependency — so in
this container, and in CI, that validation is skipped. A published schema
nobody checks slowly stops describing the code.

So the machine verification here does not depend on `jsonschema`:

- every enum in a schema is compared against the enum in the code, so adding a
  `GateVerdict` member without publishing it fails;
- every fixture is checked against the schema's `required` and
  `additionalProperties` declarations, so a field the serializer emits but the
  schema omits fails;
- the `$id`/`const` envelope is compared against the constants the code uses.

When `jsonschema` *is* installed, a full validation runs on top of that. It is a
bonus layer, not the floor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici.domain.enums import (
    EvidenceLevel,
    GateVerdict,
    PublicationState,
    ScopeKind,
)
from ici.domain.events import EVENT_SCHEMA_ID, EVENT_SCHEMA_VERSION, EventType
from ici.domain.result import SCHEMA_ID, SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "src" / "ici" / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ici-next"

RUN_SCHEMA = SCHEMA_DIR / "ici-next-run-v1.schema.json"
EVENT_SCHEMA = SCHEMA_DIR / "ici-next-event-v1.schema.json"

RESULT_FIXTURES = (
    "run-success",
    "run-code-fail",
    "run-required-incomplete",
    "run-partial-selection",
    "run-cancelled",
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def walk(schema: dict, path: str = "") -> list[tuple[str, dict]]:
    """Yield every subschema with an ``enum``, so none is checked by luck."""

    found: list[tuple[str, dict]] = []
    if "enum" in schema:
        found.append((path or "<root>", schema))
    for key in ("properties", "$defs"):
        for name, child in schema.get(key, {}).items():
            if isinstance(child, dict):
                found.extend(walk(child, f"{path}.{name}" if path else name))
    items = schema.get("items")
    if isinstance(items, dict):
        found.extend(walk(items, f"{path}[]"))
    return found


class TestSchemasArePublished:
    @pytest.mark.parametrize("path", [RUN_SCHEMA, EVENT_SCHEMA], ids=lambda p: p.name)
    def test_schema_file_exists_and_parses(self, path: Path):
        schema = load(path)
        assert schema["$schema"].startswith("https://json-schema.org/")
        assert schema["title"]
        assert schema["description"], "a published schema has to say what it is for"

    def test_the_new_schema_id_is_distinct_from_the_v3_contract(self):
        """SPEC-04 section 1 forbids reusing the old version number.

        A v3 reader must not be able to mistake this document for one it
        understands.
        """

        run = load(RUN_SCHEMA)
        assert run["properties"]["schema_id"]["const"] == SCHEMA_ID == "ici.next.run"
        assert run["properties"]["schema_version"]["const"] == SCHEMA_VERSION == 1
        assert (SCHEMA_DIR / "ici-result-v3.schema.json").is_file(), (
            "the stable v3 schema must stay published alongside the new one"
        )

    def test_the_event_envelope_matches_the_code(self):
        event = load(EVENT_SCHEMA)
        assert event["properties"]["schema_id"]["const"] == EVENT_SCHEMA_ID
        assert event["properties"]["schema_version"]["const"] == EVENT_SCHEMA_VERSION


class TestEnumsMatchTheCode:
    """Adding a member without publishing it is the drift that matters most.

    A consumer validating against a stale schema would reject a document ici
    considers valid, which looks like ici produced a broken file.
    """

    @pytest.mark.parametrize(
        ("schema_path", "pointer", "enum_type"),
        [
            (RUN_SCHEMA, "gate.selected", GateVerdict),
            (RUN_SCHEMA, "gate.workspace", GateVerdict),
            (RUN_SCHEMA, "scope.kind", ScopeKind),
            (RUN_SCHEMA, "publication.state", PublicationState),
            (RUN_SCHEMA, "findings[].evidence", EvidenceLevel),
            (RUN_SCHEMA, "metrics[].evidence", EvidenceLevel),
            (EVENT_SCHEMA, "event_type", EventType),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_enum_values_are_identical(self, schema_path: Path, pointer: str, enum_type: type):
        published = dict(walk(load(schema_path)))
        assert pointer in published, f"{pointer} is not an enum in {schema_path.name}"
        assert sorted(published[pointer]["enum"]) == sorted(
            item.value
            for item in enum_type  # type: ignore[attr-defined]
        )

    def test_every_published_enum_is_covered_by_a_case_above(self):
        """Guards the parametrize list itself from going stale."""

        checked = {
            "gate.selected",
            "gate.workspace",
            "scope.kind",
            "publication.state",
            "findings[].evidence",
            "metrics[].evidence",
            "event_type",
        }
        published = set()
        for path in (RUN_SCHEMA, EVENT_SCHEMA):
            published.update(name for name, _ in walk(load(path)))
        assert published == checked


class TestFixturesSatisfyThePublishedSchema:
    """Structural validation that runs without `jsonschema` installed."""

    def check(self, schema: dict, payload: object, where: str) -> None:
        if not isinstance(payload, dict) or not isinstance(schema.get("properties"), dict):
            return
        missing = [key for key in schema.get("required", []) if key not in payload]
        assert not missing, f"{where}: fixture is missing required {missing}"
        if schema.get("additionalProperties") is False:
            extra = sorted(set(payload) - set(schema["properties"]))
            assert not extra, (
                f"{where}: the serializer emits {extra}, which the published schema "
                "does not declare"
            )
        for name, value in payload.items():
            child = schema["properties"].get(name)
            if not isinstance(child, dict):
                continue
            if "enum" in child:
                assert value in child["enum"], f"{where}.{name}: {value!r} is not published"
            if isinstance(value, list) and isinstance(child.get("items"), dict):
                for index, item in enumerate(value):
                    self.check(child["items"], item, f"{where}.{name}[{index}]")
            else:
                self.check(child, value, f"{where}.{name}")

    @pytest.mark.parametrize("name", RESULT_FIXTURES)
    def test_result_fixture_matches_the_schema(self, name: str):
        self.check(load(RUN_SCHEMA), load(FIXTURES / f"{name}.json"), name)

    def test_the_checker_actually_rejects_something(self):
        """A validator that passes everything proves nothing."""

        payload = load(FIXTURES / "run-success.json")
        payload["unexpected_key"] = 1
        with pytest.raises(AssertionError, match="does not declare"):
            self.check(load(RUN_SCHEMA), payload, "tampered")

        payload = load(FIXTURES / "run-success.json")
        del payload["run_id"]
        with pytest.raises(AssertionError, match="missing required"):
            self.check(load(RUN_SCHEMA), payload, "tampered")


class TestFullJsonSchemaValidation:
    """The bonus layer, when the optional dependency happens to be present."""

    @pytest.mark.parametrize("name", RESULT_FIXTURES)
    def test_fixture_validates(self, name: str):
        jsonschema = pytest.importorskip(
            "jsonschema", reason="jsonschema is optional; the structural check above always runs"
        )
        jsonschema.validate(load(FIXTURES / f"{name}.json"), load(RUN_SCHEMA))

    def test_a_tampered_fixture_is_rejected(self):
        jsonschema = pytest.importorskip("jsonschema")
        payload = load(FIXTURES / "run-success.json")
        payload["identity"]["policy_digest"] = "not-a-digest"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, load(RUN_SCHEMA))
