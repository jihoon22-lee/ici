"""Development-only oracle capture from the frozen Python product; never shipped."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))
from ici.analysis._cpp_cognitive import cpp_cognitive_metric  # noqa: E402
from ici.analysis._exception_rules import analyze_cpp_exceptions  # noqa: E402
from ici.analysis.cpp_complexity import _cpp_function_inventory  # noqa: E402

CASES = {
    "simple": "int plain(int x) { return x + 1; }\n",
    "two_functions": "void first() {}\nint second() { if (true) return 1; return 0; }\n",
    "if_else_chain": "int choose(int x) { if (x == 1) { return 1; } else if(x == 2) { return 2; } else { return 3; } }\n",
    "nested_loops": "int loops(int x) { for(int i=0;i<x;i++) { while(x>0) { if(x%2) {x--;} else {break;} } } return x; }\n",
    "unbraced": "int flow(int x) { if(x) for(int i=0;i<x;i++) while(x>0) --x; return x; }\n",
    "logical": "bool logic(bool a,bool b,bool c) { return a && b && c || a; }\n",
    "alternative_logical": "bool logic(bool a,bool b,bool c) { return a and b and c or a; }\n",
    "ternary": "int choice(int x) { return x ? 1 : 2; }\n",
    "switch": "int dispatch(int x) { switch(x) { case 1: return 2; case 2: break; default:return 0; } return 1; }\n",
    "do_while": "int loop(int x) { do { --x; if(x==2) continue; } while(x); return x; }\n",
    "try_catch": "void fragile() { try { work(); } catch(int x) { throw; } catch(...) { log(); } }\n",
    "lambda_exclusion": "int host(int x) { auto callback=[](int y){if(y) return 2;return 0;}; return x ? callback(x) : 0; }\n",
    "nested_lambda": "int host() { auto cb=[](){auto next=[](){if(true)return 1;return 0;};if(next())return 1;return 0;}; return cb(); }\n",
    "raw_strings": 'int safe() { auto text=R"tag(if(true){return 9;} // fake)tag"; /* if(0){} */ return 0; }\n',
    "multiline": "int\nlong_name(\n int x\n)\n{\n if(x) {return x;}\n return 0;\n}\n",
    "operator": "struct X { int operator()(int n) { if(n)return n;return 0; } bool operator==(const X&)const{return true;} };\n",
    "constructor": "struct X { int v; X(int n):v(n) { if(n<0) v=0; } ~X() noexcept {} };\n",
    "template": "template<class T> T maximum(T a,T b) { return a>b ? a : b; }\n",
    "preprocessor": "int conditional(int x) {\n#if FEATURE\n if(x) return 1;\n#endif\n return 0;\n}\n",
    "unicode": "// 😀\nint café(int x) { if(x)return x;return 0; }\n",
    "destructor_throw": "struct X { ~X() { throw 1; } };\n",
    "empty_catch": "void work() { try { call(); } catch(...) { /* intentionally silent */ } }\n",
    "nonempty_catch": "void work() { try { call(); } catch(...) { log(); throw; } }\n",
    "exception_literals": 'void text(){ auto value=R"x(~X(){ throw 1; } catch(...){})x"; }\n',
    "no_functions": "struct Data { int value; };\n",
}
rows = []
for name, text in CASES.items():
    spans, _ = _cpp_function_inventory(text.splitlines())
    functions = []
    for span in spans:
        lines = text.splitlines()[span.body_start_line - 1 : span.end_line]
        if span.body_start_line == span.end_line:
            body = lines[0][span.body_start_column - 1 : span.end_column]
        else:
            lines[0] = lines[0][span.body_start_column - 1 :]
            lines[-1] = lines[-1][: span.end_column]
            body = "\n".join(lines)
        metric = cpp_cognitive_metric(body)
        functions.append(
            {
                "name": span.name,
                "start_line": span.start_line,
                "end_line": span.end_line,
                "complexity": span.complexity,
                "nesting": span.max_nesting,
                "cognitive": metric.cognitive,
                "control_nesting": metric.max_nesting,
                "excluded_lambdas": metric.excluded_lambdas,
            }
        )
    exceptions = analyze_cpp_exceptions("case.cpp", text)
    rows.append(
        {
            "name": name,
            "source": text,
            "functions": functions,
            "exceptions": [
                {"name": t.target_name, "line": t.start_line, "status": t.status.value}
                for t in exceptions.targets
            ],
        }
    )
Path(__file__).with_name("source-oracle.json").write_text(
    json.dumps({"source": EXPECTED, "cases": rows}, indent=2, ensure_ascii=False) + "\n"
)

from ici.analysis._cpp_dup_tokenization import cpp_directive_lines, tokenize_cpp_lines  # noqa: E402

LEXICAL = [
    *CASES.values(),
    "int add_left(int first, int second) { return first + second + 7; }\n",
    "auto value = 0xAABB; auto other=0x1.fp-3; auto binary=0b1010;\n",
    "auto value = 1'000ULL; auto other=1.0e-5f; auto unit=10_km;\n",
    "auto value = u8\"text\"_tag; auto other=L'x';\n",
    "result = renderValue /* retained call identity */ (input);\n",
    "case diskmap::NodeIssue::DepthLimitReached: renderIssue();\n",
    "Q_OBJECT\nQ_GADGET\nsignals:\nslots:\n",
    "foreach (const auto &item, values) { consume(item); }\nforever { processEvents(); }\n",
    'const char *ordinary="// not a comment /* still text */";\n',
    'const char *payload=R"TAG(\n// raw line\n)NOT_THE_END"\n/* raw marker */\n)TAG";\nint after=1;\n',
    "/*c*/ #define VALUE 1\nint after=1;\n",
    "#define VALUE one \\\ncontinued_one \\\ncontinued_two\nint after=1;\n",
    "#define VALUE one \\   \nint retained=2;\n",
    "int visible=1; // hidden comment \\\nint hidden=2;\nint after=visible+1;\n",
    "int result=first + \\\nsecond;\nint after=result+1;\n",
    "int identifier_spli\\\nced=1;\n",
    "auto value=1\\\n000;\n",
    'const char *text="left\\\n# still string";\n',
    "int value=1; /* unterminated comment\nint after=2;\n",
    'const char *value="unterminated string\nint after=2;\n',
    'auto value=R"tag(unterminated raw\nint after=2;\n',
    "int value<:2:> = <%1,2%>;\n",
    "ns::" + "long_name_" * 30 + "();\n",
]
for ending in ["\n", "\r\n", "\r"]:
    for backslashes in [1, 2, 3]:
        prefix = 'const char *value="left' + "\\" * backslashes + ending
        source = (
            prefix
            + ('";' + ending if backslashes % 2 else '";\\' + ending + '";' + ending)
            + "int after=1;"
            + ending
        )
        LEXICAL.append(source)
lexical = [
    {
        "source": source,
        "indexed": tokenize_cpp_lines(source),
        "directive_lines": sorted(cpp_directive_lines(source)),
    }
    for source in LEXICAL
]
Path(__file__).with_name("lexical-oracle.json").write_text(
    json.dumps({"source": EXPECTED, "cases": lexical}, indent=2, ensure_ascii=False) + "\n"
)
