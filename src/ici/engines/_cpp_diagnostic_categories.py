"""Stable category projection for normalized C++ tool diagnostics."""

from __future__ import annotations

from ici.core.models import FindingCategory
from ici.engines._cpp_diagnostics import CppDiagnostic

CPP_DIAGNOSTIC_CATEGORY_POLICY = "tool-rule-v1"

_CLANG_SECURITY_PREFIXES = (
    "clang-analyzer-security.",
    "clang-analyzer-alpha.security.",
    "clang-analyzer-optin.taint.",
)
_CLANG_TIDY_SECURITY_PREFIXES = ("cert-", "android-cloexec-")
_CLANG_TIDY_SECURITY_RULES = frozenset(
    {
        "bugprone-command-processor",
        "bugprone-signal-handler",
        "bugprone-unsafe-functions",
        "concurrency-mt-unsafe",
    }
)
_CLANG_ANALYZER_RESOURCE_RULES = frozenset(
    {
        "clang-analyzer-alpha.core.danglingptrderef",
        "clang-analyzer-alpha.core.useafterlifetimeend",
        "clang-analyzer-alpha.cplusplus.smartptr",
        "clang-analyzer-cplusplus.arraydelete",
        "clang-analyzer-cplusplus.innerpointer",
        "clang-analyzer-cplusplus.newdelete",
        "clang-analyzer-cplusplus.newdeleteleaks",
        "clang-analyzer-fuchsia.handlechecker",
        "clang-analyzer-osx.cocoa.retaincount",
        "clang-analyzer-osx.cocoa.runloopautoreleaseleak",
        "clang-analyzer-osx.corefoundation.cfretainrelease",
        "clang-analyzer-unix.malloc",
        "clang-analyzer-unix.mismatcheddeallocator",
        "clang-analyzer-unix.stream",
    }
)
_CLANG_ANALYZER_RESOURCE_PREFIXES = (
    "clang-analyzer-alpha.webkit.",
    "clang-analyzer-webkit.",
)
_CLANG_TIDY_RESOURCE_RULES = frozenset(
    {
        "bugprone-dangling-handle",
        "bugprone-dangling-reference",
        "bugprone-multiple-new-in-one-expression",
        "bugprone-shared-ptr-array-mismatch",
        "bugprone-suspicious-realloc-usage",
        "bugprone-unique-ptr-array-mismatch",
        "bugprone-unused-raii",
        "bugprone-use-after-move",
        "cppcoreguidelines-owning-memory",
        "misc-new-delete-overloads",
    }
)

_CLAZY_RESOURCE_STEMS = (
    "clazy-lifetime",
    "clazy-ownership",
    "clazy-parent-less",
    "clazy-qobject-cast",
)
_CLAZY_RESOURCE_RULES = frozenset(
    {
        "clazy-connect-3arg-lambda",
        "clazy-ctor-missing-parent-argument",
        "clazy-lambda-in-connect",
        "clazy-post-event",
        "clazy-returning-data-from-temporary",
        "clazy-temporary-iterator",
    }
)
_CLAZY_COMPATIBILITY_STEMS = (
    "clazy-qt6",
    "clazy-deprecated",
    "clazy-qstring-arg",
    "clazy-qt-keyword",
)
_CLAZY_COMPATIBILITY_RULES = frozenset(
    {
        "clazy-modernize-overloaded-connects",
        "clazy-no-module-include",
        "clazy-old-style-connect",
        "clazy-qenums",
        "clazy-qstring-ref",
        "clazy-use-chrono-in-qtimer",
    }
)
_CLAZY_CORRECTNESS_STEMS = (
    "clazy-qobject",
    "clazy-connect",
    "clazy-signal",
    "clazy-slot",
    "clazy-qevent-cast",
)
_CLAZY_CORRECTNESS_RULES = frozenset(
    {
        "clazy-assert-with-side-effects",
        "clazy-base-class-event",
        "clazy-child-event-qobject-cast",
        "clazy-const-signal-or-slot",
        "clazy-copyable-polymorphic",
        "clazy-ifndef-define-typo",
        "clazy-incorrect-emit",
        "clazy-install-event-filter",
        "clazy-jni-signatures",
        "clazy-lambda-unique-connection",
        "clazy-missing-qobject-macro",
        "clazy-missing-typeinfo",
        "clazy-mutable-container-key",
        "clazy-overloaded-signal",
        "clazy-overridden-signal",
        "clazy-qhash-with-char-pointer-key",
        "clazy-qproperty-type-mismatch",
        "clazy-qproperty-without-notify",
        "clazy-qstring-varargs",
        "clazy-rule-of-three",
        "clazy-rule-of-two-soft",
        "clazy-signal-with-return-value",
        "clazy-skipped-base-method",
        "clazy-thread-with-slots",
        "clazy-unexpected-flag-enumerator-value",
        "clazy-virtual-call-ctor",
        "clazy-virtual-signal",
        "clazy-writing-to-temporary",
        "clazy-wrong-qevent-cast",
    }
)


def _matches_rule_stem(rule: str, stems: tuple[str, ...]) -> bool:
    """Match a normalized rule stem without accepting arbitrary substrings."""

    return any(
        rule == stem or rule.startswith(f"{stem}-") or rule.startswith(f"{stem}.") for stem in stems
    )


def cpp_diagnostic_category(diagnostic: CppDiagnostic) -> FindingCategory:
    """Project a normalized tool rule into a stable v3 finding category."""

    family = diagnostic.family.casefold()
    rule = diagnostic.tool_rule_id.casefold()

    if family == "compiler":
        return FindingCategory.CORRECTNESS
    if family in {"clang-analyzer", "clang-tidy"}:
        if (family == "clang-analyzer" and rule.startswith(_CLANG_SECURITY_PREFIXES)) or (
            family == "clang-tidy"
            and (
                rule.startswith(_CLANG_TIDY_SECURITY_PREFIXES) or rule in _CLANG_TIDY_SECURITY_RULES
            )
        ):
            return FindingCategory.SECURITY
        if family == "clang-analyzer":
            if rule in _CLANG_ANALYZER_RESOURCE_RULES or rule.startswith(
                _CLANG_ANALYZER_RESOURCE_PREFIXES
            ):
                return FindingCategory.RESOURCE
            return FindingCategory.CORRECTNESS
        if rule in _CLANG_TIDY_RESOURCE_RULES:
            return FindingCategory.RESOURCE
        if rule.startswith("portability-") or rule == "modernize-deprecated-headers":
            return FindingCategory.COMPATIBILITY
        if rule.startswith("bugprone-") or rule.startswith("concurrency-"):
            return FindingCategory.CORRECTNESS
        return FindingCategory.MAINTAINABILITY

    if family != "clazy":
        return FindingCategory.CORRECTNESS
    if rule in _CLAZY_RESOURCE_RULES or _matches_rule_stem(rule, _CLAZY_RESOURCE_STEMS):
        return FindingCategory.RESOURCE
    if rule in _CLAZY_COMPATIBILITY_RULES or _matches_rule_stem(rule, _CLAZY_COMPATIBILITY_STEMS):
        return FindingCategory.COMPATIBILITY
    if rule in _CLAZY_CORRECTNESS_RULES or _matches_rule_stem(rule, _CLAZY_CORRECTNESS_STEMS):
        return FindingCategory.CORRECTNESS
    return FindingCategory.MAINTAINABILITY
