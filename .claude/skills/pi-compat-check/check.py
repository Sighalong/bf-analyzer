#!/usr/bin/env python3
"""Static check that the project runs on a Raspberry Pi 1B (armv6, 512 MB, Python 3.7+).

Usage: python3 -I check.py [ROOT]   (needs Python 3.10+ to run the checker itself)
Prints only problems, then a one-line summary. Exit code 1 if any problem.
"""
import ast
import os
import sys

MIN = (3, 7)
# Modules added to the stdlib after 3.7, or that are not in the stdlib at all but often assumed.
NEWER_STDLIB = {"zoneinfo": (3, 9), "graphlib": (3, 9), "tomllib": (3, 11)}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".claude", "node_modules"}


def py_files(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(d, f)


def local_modules(root):
    mods = set()
    for p in py_files(root):
        rel = os.path.relpath(p, root)
        mods.add(rel.split(os.sep)[0].replace(".py", ""))           # packages / top-level modules
        mods.add(os.path.splitext(os.path.basename(p))[0])           # sibling imports, e.g. tests/_helpers
    return mods


def check_file(path, local):
    problems = []
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src, feature_version=MIN)
    except SyntaxError as e:
        return ["{}:{}: syntax newer than Python {}.{}: {}".format(path, e.lineno, *MIN, e.msg)]
    future_ann = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                     and any(a.name == "annotations" for a in n.names) for n in tree.body)

    def ann_problem(node):
        if node is None or future_ann:
            return None
        for sub in ast.walk(node):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                return "'X | Y' annotation needs 3.10 (use Optional/Union)"
            if isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) \
                    and sub.value.id in {"list", "dict", "set", "tuple", "type"}:
                return "'{}[...]' annotation needs 3.9 (use typing.List etc.)".format(sub.value.id)
        return None

    for n in ast.walk(tree):
        anns = []
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns = [a.annotation for a in n.args.args + n.args.kwonlyargs] + [n.returns]
        elif isinstance(n, ast.AnnAssign):
            anns = [n.annotation]
        for a in anns:
            msg = ann_problem(a)
            if msg:
                problems.append("{}:{}: {}".format(path, getattr(a, "lineno", n.lineno), msg))
        if isinstance(n, ast.JoinedStr):
            seg = ast.get_source_segment(src, n) or ""
            if "=}" in seg or "=!" in seg or "=:" in seg:
                problems.append("{}:{}: f-string '=' specifier needs 3.8".format(path, n.lineno))
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
            if isinstance(n, ast.ImportFrom) and n.level:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in NEWER_STDLIB and NEWER_STDLIB[top] > MIN:
                    problems.append("{}:{}: '{}' needs Python {}.{} (guard with try/except)".format(
                        path, n.lineno, top, *NEWER_STDLIB[top]))
                elif top not in sys.stdlib_module_names and top not in local and top != "__future__":
                    problems.append("{}:{}: third-party import '{}' (Pi 1B: stdlib only)".format(
                        path, n.lineno, top))
    return problems


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    if sys.version_info < (3, 10):
        print("run the checker with Python 3.10+ (it needs sys.stdlib_module_names)")
        return 2
    local = local_modules(root)
    problems, n = [], 0
    for p in py_files(root):
        n += 1
        problems += check_file(p, local)
    req = os.path.join(root, "requirements.txt")
    if os.path.exists(req):
        deps = [l.strip() for l in open(req) if l.strip() and not l.startswith("#")]
        if deps:
            problems.append("requirements.txt lists {} (Pi 1B install must not need them): {}".format(
                len(deps), ", ".join(deps)))
    for p in problems:
        print(p)
    print("checked {} files: {} problem(s)".format(n, len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
