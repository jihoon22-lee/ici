"""Adversarial tests for exception-safety evidence."""

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines.exception import ExceptionSafetyEngine


def test_exception_engine_reports_lost_traceback(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "try:\n    work()\nexcept Exception as exc:\n    raise exc\n", encoding="utf-8"
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    target = next(target for target in result.targets if target.target_name == "LostTraceback")
    assert target.file_path == "src/mod.py"
    assert target.start_line == 4
    assert target.status == EngineStatus.WARN


def test_exception_engine_distinguishes_bare_and_other_variable_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        """try:
    work()
except Exception as exc:
    raise RuntimeError("wrapped")

try:
    work()
except Exception as exc:
    raise
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "LostTraceback" for target in result.targets)
    assert result.status == EngineStatus.PASS


def test_exception_engine_ignores_raise_in_nested_function_scope(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        """def make_handler():
    try:
        work()
    except Exception as exc:
        def later():
            raise exc
        return later
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "LostTraceback" for target in result.targets)


def test_exception_engine_ignores_cpp_comments_and_strings(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "safe.cpp").write_text(
        """// catch(...) {}
const char* text = "catch(...) {} throw boom";
/* ~Type() { throw bad; } */
void f() { try { work(); } catch (...) { log(); } }
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(
        target.target_name in {"CatchAllSwallowed", "DestructorThrow"} for target in result.targets
    )


def test_exception_engine_reports_syntax_errors_as_not_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.py").write_text("try:\n    pass\nexcept (:\n    pass\n", encoding="utf-8")

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(target.target_name == "SyntaxError" for target in result.targets)


def test_exception_engine_without_sources_is_not_applicable(tmp_path):
    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert result.targets[0].status == EngineStatus.SKIP


def test_exception_engine_returns_pass_target_for_clean_python_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.py").write_text("def work():\n    return 1\n", encoding="utf-8")

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert any(
        target.file_path == "src/clean.py" and target.status == EngineStatus.PASS
        for target in result.targets
    )


def test_exception_engine_supports_multiline_cpp_bodies(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "unsafe.cpp").write_text(
        """class Type {
public:
    ~Type()
    {
        throw 1;
    }
};

void run() {
    try {
        work();
    }
    catch (...)
    {
    }
}
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "DestructorThrow" for target in result.targets)
    assert any(target.target_name == "CatchAllSwallowed" for target in result.targets)


def test_exception_engine_does_not_call_conditional_brace_catch_empty(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "conditional.cpp").write_text(
        "void run() {\n    try { work(); }\n    catch (...) { if (ready) {} }\n}\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "CatchAllSwallowed" for target in result.targets)


def test_exception_engine_ignores_cpp_raw_string_false_positive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "raw.cpp").write_text(
        'const char* text = R"tag(catch (...) { } ~Type() { throw 1; })tag";\n',
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(
        target.target_name in {"CatchAllSwallowed", "DestructorThrow"} for target in result.targets
    )


@pytest.mark.parametrize("prefix", ["u8R", "uR", "UR", "LR"])
def test_exception_engine_ignores_cpp_prefixed_raw_string_false_positive(tmp_path, prefix):
    src = tmp_path / "src"
    src.mkdir()
    (src / "raw.cpp").write_text(
        f'const char* text = {prefix}"tag(catch (...) {{ }} ~Type() {{ throw 1; }})tag";\n',
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(
        target.target_name in {"CatchAllSwallowed", "DestructorThrow"} for target in result.targets
    )


def test_exception_engine_mask_cpp_literals_preserves_lines_and_following_code():
    content = (
        "// catch(...) {}\n"
        'const char* quoted = "escaped \\" catch(...) {}";\n'
        'const char* raw = u8R"tag(\n'
        "catch (...) {}\n"
        ')tag";\n'
        "void f() { catch (...) {} }\n"
    )

    masked = ExceptionSafetyEngine._mask_cpp_literals(content)

    assert masked.count("\n") == content.count("\n")
    assert "catch" not in "\n".join(masked.splitlines()[:-1])
    assert "catch (...) {}" in masked.splitlines()[-1]


def test_exception_default_mode_matches_pass_fail_policy(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text(
        "try:\n    work()\nexcept Exception as exc:\n    raise exc\n", encoding="utf-8"
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL


def test_exception_engine_does_not_flag_explicit_raise_cause(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "wrapped.py").write_text(
        """try:
    work()
except Exception as exc:
    cause = RuntimeError("cause")
    raise exc from cause
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "LostTraceback" for target in result.targets)


@pytest.mark.parametrize(
    "handler",
    [
        "except BaseException:",
        "except (ValueError, BaseException):",
        "except builtins.BaseException:",
    ],
)
def test_exception_engine_flags_base_exception_handlers_with_location(tmp_path, handler):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        f"import builtins\n\ntry:\n    work()\n{handler}\n    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    target = next(target for target in result.targets if target.target_name == "BaseException")
    assert target.file_path == "src/mod.py"
    assert target.start_line == 5
    assert target.status == EngineStatus.FAIL
    assert result.status == EngineStatus.FAIL


def test_exception_engine_flags_module_level_base_exception_aliases(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "aliases.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

try:
    work()
except BE:
    log()

try:
    work()
except b.BaseException:
    log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (6, EngineStatus.FAIL),
        (11, EngineStatus.FAIL),
    ]


def test_exception_engine_does_not_flag_function_local_base_exception_alias_shadowing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "shadowed.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

def handle(BE, b):
    try:
        work()
    except BE:
        log()
    try:
        work()
    except b.BaseException:
        log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_flags_function_scope_base_exception_aliases(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "function_aliases.py").write_text(
        """def handle():
    from builtins import BaseException as BE
    import builtins as b

    try:
        work()
    except BE:
        log()

    try:
        work()
    except b.BaseException:
        log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (7, EngineStatus.FAIL),
        (12, EngineStatus.FAIL),
    ]


def test_exception_engine_flags_class_scope_base_exception_aliases(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "class_aliases.py").write_text(
        """class Handler:
    from builtins import BaseException as CBE
    import builtins as cb

    try:
        work()
    except CBE:
        log()

    try:
        work()
    except cb.BaseException:
        log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (7, EngineStatus.FAIL),
        (12, EngineStatus.FAIL),
    ]


def test_exception_engine_does_not_leak_nested_alias_to_outer_or_sibling_scope(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "scopes.py").write_text(
        """def nested():
    from builtins import BaseException as BE
    try:
        work()
    except BE:
        log()

def sibling():
    try:
        work()
    except BE:
        log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [(5, EngineStatus.FAIL)]


def test_exception_engine_respects_assignment_and_import_shadowing_of_scoped_aliases(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "shadowed_scopes.py").write_text(
        """def handle():
    from builtins import BaseException as BE
    import builtins as b
    BE = ValueError
    import math as b
    try:
        work()
    except BE:
        log()
    try:
        work()
    except b.BaseException:
        log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_keeps_aliases_possible_across_conditional_handler_paths(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "conditional_handlers.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

try:
    work()
except ValueError:
    BE = ValueError
    b = object()
except BE:
    log()

try:
    work()
except ValueError:
    BaseException = ValueError
except BaseException:
    log()

try:
    work()
except ValueError:
    b = object()
except b.BaseException:
    log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (9, EngineStatus.FAIL),
        (16, EngineStatus.FAIL),
        (23, EngineStatus.FAIL),
    ]


def test_exception_engine_treats_control_flow_bindings_as_possible(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "conditional_bindings.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

if flag:
    BE = ValueError
    b = object()
try:
    work()
except BE:
    log()
try:
    work()
except b.BaseException:
    log()

for item in items:
    BE = ValueError
    b = object()
try:
    work()
except BE:
    log()
try:
    work()
except b.BaseException:
    log()

while flag:
    BE = ValueError
    b = object()
try:
    work()
except BE:
    log()
try:
    work()
except b.BaseException:
    log()

try:
    BE = ValueError
except ValueError:
    log()
try:
    work()
except BE:
    log()

try:
    work()
except ValueError:
    BE = ValueError
try:
    work()
except BE:
    log()

CBE = ValueError
cb = object()
with manager() as BE:
    log()
try:
    work()
except BE:
    log()
with manager() as b:
    log()
try:
    work()
except b.BaseException:
    log()
if first:
    from builtins import BaseException as CBE
    import builtins as cb
if second:
    CBE = ValueError
    cb = object()
try:
    work()
except CBE:
    log()
try:
    work()
except cb.BaseException:
    log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (9, EngineStatus.FAIL),
        (13, EngineStatus.FAIL),
        (21, EngineStatus.FAIL),
        (25, EngineStatus.FAIL),
        (33, EngineStatus.FAIL),
        (37, EngineStatus.FAIL),
        (46, EngineStatus.FAIL),
        (55, EngineStatus.FAIL),
        (64, EngineStatus.FAIL),
        (70, EngineStatus.FAIL),
        (80, EngineStatus.FAIL),
        (84, EngineStatus.FAIL),
    ]


def test_exception_engine_does_not_leak_comprehension_targets(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "comprehensions.py").write_text(
        """from builtins import BaseException as BE
[BE for BE in values]
{BE for BE in values}
{BE: BE for BE in values}
(BE for BE in values)
try:
    work()
except BE:
    log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [(8, EngineStatus.FAIL)]


def test_exception_engine_treats_loop_targets_as_conditional_bindings(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "loop_target.py").write_text(
        """from builtins import BaseException as BE
for BE in values:
    pass
try:
    work()
except BE:
    log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [(6, EngineStatus.FAIL)]


def test_exception_engine_uses_last_alias_binding_before_handler(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ordered_aliases.py").write_text(
        """def handle():
    BE = ValueError
    from builtins import BaseException as BE
    b = object()
    import builtins as b
    try:
        work()
    except BE:
        log()
    try:
        work()
    except b.BaseException:
        log()

def later():
    try:
        work()
    except BE:
        log()
    from builtins import BaseException as BE
    try:
        work()
    except b.BaseException:
        log()
    import builtins as b
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (8, EngineStatus.FAIL),
        (12, EngineStatus.FAIL),
    ]


def test_exception_engine_resolves_module_aliases_at_function_execution_time(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "module_runtime.py").write_text(
        """def handle():
    try:
        work()
    except BE:
        log()
    try:
        work()
    except b.BaseException:
        log()

from builtins import BaseException as BE
import builtins as b
handle()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (4, EngineStatus.FAIL),
        (8, EngineStatus.FAIL),
    ]


def test_exception_engine_resolves_outer_aliases_after_nested_definition(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "nested_runtime.py").write_text(
        """def outer():
    def inner():
        try:
            work()
        except BE:
            log()
        try:
            work()
        except b.BaseException:
            log()
    from builtins import BaseException as BE
    import builtins as b
    inner()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (5, EngineStatus.FAIL),
        (9, EngineStatus.FAIL),
    ]


def test_exception_engine_keeps_enclosing_aliases_possible_after_child_call(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "post_call.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

def handle():
    try:
        work()
    except BE:
        log()
    try:
        work()
    except b.BaseException:
        log()

handle()
BE = ValueError
b = object()

def direct():
    try:
        work()
    except BaseException:
        log()

direct()
BaseException = ValueError

BaseException = ValueError
def stable_shadow():
    try:
        work()
    except BaseException:
        log()
stable_shadow()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (7, EngineStatus.FAIL),
        (11, EngineStatus.FAIL),
        (21, EngineStatus.FAIL),
    ]


def test_exception_engine_keeps_outer_aliases_possible_after_nested_call(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "nested_post_call.py").write_text(
        """def outer():
    from builtins import BaseException as BE
    import builtins as b
    def inner():
        nonlocal BE
        try:
            work()
        except BE:
            log()
        try:
            work()
        except b.BaseException:
            log()
    inner()
    BE = ValueError
    b = object()

def outer_direct():
    def inner():
        try:
            work()
        except BaseException:
            log()
    inner()
    BaseException = ValueError

outer()
outer_direct()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (8, EngineStatus.FAIL),
        (12, EngineStatus.FAIL),
        (22, EngineStatus.FAIL),
    ]


def test_exception_engine_local_bindings_after_handler_shadow_outer_aliases(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "local_runtime.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

def local_import():
    try:
        work()
    except BE:
        log()
    from builtins import BaseException as BE

def local_assignment():
    try:
        work()
    except b.BaseException:
        log()
    b = object()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_evaluates_handler_type_before_handler_name_binding(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "handler_names.py").write_text(
        """import builtins

def handle():
    try:
        work()
    except BaseException as BaseException:
        log()
    try:
        work()
    except builtins.BaseException as builtins:
        log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (6, EngineStatus.FAIL),
        (10, EngineStatus.FAIL),
    ]


def test_exception_engine_does_not_apply_future_module_or_class_bindings(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "future_bindings.py").write_text(
        """import builtins

try:
    work()
except BaseException:
    log()
try:
    work()
except builtins.BaseException:
    log()

class Handler:
    import builtins
    try:
        work()
    except BaseException:
        log()
    BaseException = ValueError
    builtins = object()
    try:
        work()
    except BaseException:
        log()
    try:
        work()
    except builtins.BaseException:
        log()
BaseException = ValueError
builtins = object()
try:
    work()
except BaseException:
    log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (5, EngineStatus.FAIL),
        (9, EngineStatus.FAIL),
        (16, EngineStatus.FAIL),
    ]


def test_exception_engine_global_assignment_does_not_shadow_module_alias(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "global_binding.py").write_text(
        """from builtins import BaseException as BE

def handle():
    global BE
    try:
        work()
    except BE:
        log()
    BE = ValueError
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [(7, EngineStatus.FAIL)]


def test_exception_engine_treats_handler_targets_as_transient_bindings(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "transient_handlers.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

try:
    work()
except ValueError as BE:
    try:
        work()
    except BE:
        log()
except BE:
    log()

try:
    work()
except ValueError as b:
    try:
        work()
    except b.BaseException:
        log()
except b.BaseException:
    log()

try:
    work()
except ValueError as BE:
    log()
try:
    work()
except BE:
    log()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (11, EngineStatus.FAIL),
        (21, EngineStatus.FAIL),
        (30, EngineStatus.FAIL),
    ]


def test_exception_engine_allows_alias_rebind_after_transient_handler(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "transient_rebind.py").write_text(
        """from builtins import BaseException as BE
import builtins as b

def direct():
    try:
        work()
    except ValueError as BE:
        try:
            work()
        except BE:
            log()
        from builtins import BaseException as BE
        try:
            work()
        except BE:
            log()
    try:
        work()
    except ValueError as b:
        try:
            work()
        except b.BaseException:
            log()
        import builtins as b
        try:
            work()
        except b.BaseException:
            log()

def closure():
    try:
        work()
    except ValueError as BE:
        def inner():
            try:
                work()
            except BE:
                log()
        from builtins import BaseException as BE
        inner()
    try:
        work()
    except ValueError as b:
        def inner_b():
            try:
                work()
            except b.BaseException:
                log()
        import builtins as b
        inner_b()

direct()
closure()
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    targets = [target for target in result.targets if target.target_name == "BaseException"]
    assert [(target.start_line, target.status) for target in targets] == [
        (15, EngineStatus.FAIL),
        (27, EngineStatus.FAIL),
        (37, EngineStatus.FAIL),
        (47, EngineStatus.FAIL),
    ]


def test_exception_engine_cancels_pending_destructor_at_declaration_semicolon(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "unsafe.cpp").write_text(
        """class Type {
public:
    ~Type()
        noexcept;
};

void run() {
    throw 1;
}
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "DestructorThrow" for target in result.targets)


def test_exception_engine_calculates_empty_catch_once_for_pass_target(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.cpp").write_text(
        "void run() { try { work(); } catch (...) { log(); } }\n", encoding="utf-8"
    )
    calls = []
    original = ExceptionSafetyEngine._empty_catch_all_lines

    def counted(masked):
        calls.append(masked)
        return original(masked)

    monkeypatch.setattr(ExceptionSafetyEngine, "_empty_catch_all_lines", staticmethod(counted))

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert len(calls) == 1


def test_exception_engine_does_not_assume_unimported_builtins_module_alias(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "try:\n    work()\nexcept builtins.BaseException:\n    log()\n", encoding="utf-8"
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_resolves_direct_builtin_after_delete(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "BaseException = ValueError\ndel BaseException\n\n"
        "try:\n    work()\nexcept BaseException:\n    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_keeps_deleted_exception_alias_shadowed(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "from builtins import BaseException as BE\ndel BE\n\n"
        "try:\n    work()\nexcept BE:\n    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_keeps_alias_possible_after_boolop_walrus(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "from builtins import BaseException as BE\n"
        "False and (BE := ValueError)\n\n"
        "try:\n    work()\nexcept BE:\n    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_keeps_alias_possible_after_ifexp_walrus(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "from builtins import BaseException as BE\n"
        "value = (BE := ValueError) if condition else None\n\n"
        "try:\n    work()\nexcept BE:\n    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "BaseException" for target in result.targets)


@pytest.mark.parametrize("scope", ["module", "function"])
def test_exception_engine_match_capture_does_not_resolve_as_builtin(tmp_path, scope):
    src = tmp_path / "src"
    src.mkdir()
    prefix = "" if scope == "module" else "def handle(value):\n"
    indent = "" if scope == "module" else "    "
    body = (
        f"{prefix}{indent}match value:\n"
        f"{indent}    case BaseException:\n"
        f"{indent}        pass\n"
        f"{indent}try:\n"
        f"{indent}    work()\n"
        f"{indent}except BaseException:\n"
        f"{indent}    log()\n"
    )
    (src / "mod.py").write_text(body, encoding="utf-8")

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "BaseException" for target in result.targets)


@pytest.mark.parametrize("scope", ["module", "class"])
def test_exception_engine_preserves_builtin_after_non_irrefutable_match_capture(tmp_path, scope):
    src = tmp_path / "src"
    src.mkdir()
    if scope == "module":
        prefix = ""
        indent = ""
    else:
        prefix = "class Handler:\n"
        indent = "    "
    body = (
        f"{prefix}{indent}match value:\n"
        f"{indent}    case BaseException if condition:\n"
        f"{indent}        pass\n"
        f"{indent}    case _:\n"
        f"{indent}        pass\n"
        f"{indent}try:\n"
        f"{indent}    work()\n"
        f"{indent}except BaseException:\n"
        f"{indent}    log()\n"
    )
    (src / "mod.py").write_text(body, encoding="utf-8")

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "BaseException" for target in result.targets)


@pytest.mark.parametrize("scope", ["module", "class"])
def test_exception_engine_keeps_builtin_possible_after_conditional_delete(tmp_path, scope):
    src = tmp_path / "src"
    src.mkdir()
    if scope == "module":
        prefix = ""
        indent = ""
    else:
        prefix = "class Handler:\n"
        indent = "    "
    (src / "mod.py").write_text(
        f"{prefix}{indent}BaseException = ValueError\n"
        f"{indent}if condition:\n"
        f"{indent}    del BaseException\n"
        f"{indent}try:\n"
        f"{indent}    work()\n"
        f"{indent}except BaseException:\n"
        f"{indent}    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "BaseException" for target in result.targets)


def test_exception_engine_second_with_context_is_conditional(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "from builtins import BaseException as BE\n"
        "with first(), (BE := second()):\n"
        "    pass\n\n"
        "try:\n    work()\nexcept BE:\n    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "BaseException" for target in result.targets)


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_exception_engine_masks_cpp_line_comment_splice(tmp_path, line_ending):
    src = tmp_path / "src"
    src.mkdir()
    (src / "spliced.cpp").write_text(
        "// comment "
        + "\\"
        + line_ending
        + "catch (...) { }"
        + line_ending
        + "void f() {"
        + line_ending
        + "    try { work(); } catch (...) { log(); }"
        + line_ending
        + "}"
        + line_ending,
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "CatchAllSwallowed" for target in result.targets)
