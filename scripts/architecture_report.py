"""Architecture report and ratchet for the MainWindow decomposition (docs/mainwindow_decomposition_plan.md).

Everything here is static (AST only, no Qt import), so it runs in a second and cannot be fooled by test stubs.

  python scripts/architecture_report.py            print the current numbers next to the recorded ones
  python scripts/architecture_report.py --check    exit 1 if any number got worse, or a hard rule is broken
  python scripts/architecture_report.py --update   lower the recorded numbers to the current ones (never raises one)

The recorded numbers live in docs/architecture_ratchet.json. A change may lower a number and may not raise one.

Hard rules checked (plan section 3.2):
  R1  no module other than window.py may read or write ``<window>._private`` (counted per module; the list only shrinks)
  R3  modules listed under "ui_free_modules" may not import PySide6.QtWidgets
  and, because the first remediation left dangling references behind:
  - every ``self.<name>`` read inside MainWindow and the extracted controllers resolves to something the class defines
  - every ``from <repo module> import <name>`` resolves to a name that module defines
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "image_triage"
WINDOW_PY = PACKAGE / "window.py"
RATCHET_PATH = REPO / "docs" / "architecture_ratchet.json"

def discover_controllers() -> dict[str, str]:
    """``*_controller.py`` modules -> the class whose ``__init__`` takes the window (the back-reference controllers)."""
    found: dict[str, str] = {}
    for path in sorted(PACKAGE.glob("*_controller.py")):
        for node in _parse(path).body:
            if isinstance(node, ast.ClassDef):
                init = next((b for b in node.body if isinstance(b, ast.FunctionDef) and b.name == "__init__"), None)
                if init is not None and any(a.arg == "window" for a in init.args.args):
                    found[path.name] = node.name
                    break
    return found


# Window-typed receivers in the controllers and the action table.
_WINDOW_RECEIVERS = {"window", "self._window", "host", "self._host"}

_WINDOW_REACH = re.compile(r"(?:\bwindow|self\._window|\bhost|self\._host)\.(_[A-Za-z]\w*)")

# Names Qt gives every QMainWindow; resolved lazily so the report still runs without PySide6.
_QT_BASES_CACHE: set[str] | None = None


def _qt_main_window_names() -> set[str]:
    global _QT_BASES_CACHE
    if _QT_BASES_CACHE is None:
        try:
            from PySide6.QtWidgets import QMainWindow

            _QT_BASES_CACHE = set(dir(QMainWindow))
        except ImportError:  # the static metrics do not need it
            _QT_BASES_CACHE = set()
    return _QT_BASES_CACHE


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8", errors="replace"))


def _self_stores(node: ast.AST) -> set[str]:
    return {
        n.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute)
        and isinstance(n.ctx, ast.Store)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    }


def _state_attributes(window: ast.ClassDef) -> set[str]:
    """The window's attributes, not counting controller handles (``self._x = SomeController(self)``): a handle is wiring,
    and every extracted domain needs one, so counting it would punish the very change the ratchet is meant to encourage."""
    handles = {
        target.attr
        for n in ast.walk(window)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
        and n.value.func.id.endswith("Controller")
        for target in n.targets
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"
    }
    return _self_stores(window) - handles


def _is_delegate(fn: ast.FunctionDef) -> bool:
    """``def _x(self, a, b): return self._owner.x(a, b)``: a body that only forwards its own parameters."""
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
    if len(body) != 1:
        return False
    stmt = body[0]
    call = stmt.value if isinstance(stmt, (ast.Return, ast.Expr)) else None
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
        return False
    target = call.func.value
    if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"):
        return False
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs} - {"self"}
    used: set[str] = set()
    for arg in list(call.args) + [k.value for k in call.keywords]:
        arg = arg.value if isinstance(arg, ast.Starred) else arg
        if isinstance(arg, ast.Name) and arg.id in params:
            used.add(arg.id)
        elif isinstance(arg, ast.Constant):
            continue
        else:
            return False
    return True


def collect_metrics() -> dict:
    src = WINDOW_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    window = next(c for c in classes if c.name == "MainWindow")
    methods = [b for b in window.body if isinstance(b, ast.FunctionDef)]

    private_by_module: dict[str, int] = {}
    distinct_total = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts or path == WINDOW_PY:
            continue
        found = _WINDOW_REACH.findall(path.read_text(encoding="utf-8", errors="replace"))
        if found:
            private_by_module[path.relative_to(PACKAGE).as_posix()] = len(found)
            distinct_total += len(set(found))

    return {
        "metrics": {
            "main_window_lines": window.end_lineno - window.lineno + 1,
            "main_window_methods": len(methods),
            "main_window_attributes": len(_state_attributes(window)),
            "window_py_other_classes": len(classes) - 1,
            "forwarding_delegates": sum(1 for m in methods if _is_delegate(m)),
            "private_accesses_total": sum(private_by_module.values()),
            # How many *different* window privates each module needs, summed. Unlike the access count this cannot be
            # lowered by funnelling many accesses through one wrapper: only moving or removing the dependency does.
            "private_attributes_distinct_total": distinct_total,
        },
        "private_accesses_by_module": private_by_module,
    }


# ------------------------------------------------------------------ hard rules


def _imports_qtwidgets(path: Path) -> bool:
    for n in ast.walk(_parse(path)):
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[:2] == ["PySide6", "QtWidgets"]:
            return True
        if isinstance(n, ast.Import) and any(a.name.split(".")[:2] == ["PySide6", "QtWidgets"] for a in n.names):
            return True
    return False


def check_ui_free(modules: list[str]) -> list[str]:
    problems = []
    for rel in modules:
        path = PACKAGE / rel
        if not path.exists():
            problems.append(f"ui_free_modules lists {rel}, which does not exist")
        elif _imports_qtwidgets(path):
            problems.append(f"{rel} is declared UI-free (R3) but imports PySide6.QtWidgets")
    return problems


def _class_members(cls: ast.ClassDef) -> set[str]:
    members = _self_stores(cls)
    for b in cls.body:
        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            members.add(b.name)
        elif isinstance(b, ast.Assign):
            members.update(x.id for t in b.targets for x in ast.walk(t) if isinstance(x, ast.Name))
        elif isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
            members.add(b.target.id)
    return members


def _guarded_names(cls: ast.ClassDef) -> set[str]:
    """Names the class itself probes with hasattr/getattr(self, "name"): an absent one is handled, not a bug."""
    out: set[str] = set()
    for n in ast.walk(cls):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id in ("hasattr", "getattr")
            and len(n.args) >= 2
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id == "self"
            and isinstance(n.args[1], ast.Constant)
            and isinstance(n.args[1].value, str)
        ):
            out.add(n.args[1].value)
    return out


def check_self_members() -> list[str]:
    """``self.<name>`` reads that no definition in MainWindow (or Qt) supplies: the AttributeError the audit found."""
    problems = []
    targets = [(WINDOW_PY, "MainWindow", _qt_main_window_names())]
    targets += [(PACKAGE / f, c, set()) for f, c in discover_controllers().items()]
    for path, class_name, inherited in targets:
        cls = next((n for n in _parse(path).body if isinstance(n, ast.ClassDef) and n.name == class_name), None)
        if cls is None:
            problems.append(f"{path.relative_to(REPO).as_posix()}: class {class_name} not found")
            continue
        known = _class_members(cls) | inherited | _guarded_names(cls)
        seen: dict[str, int] = {}
        for n in ast.walk(cls):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.ctx, ast.Load)
                and isinstance(n.value, ast.Name)
                and n.value.id in ("self", "cls")
                and n.attr not in known
                and not n.attr.startswith("__")
            ):
                seen.setdefault(n.attr, n.lineno)
        for attr, line in sorted(seen.items(), key=lambda kv: kv[1]):
            problems.append(f"{path.relative_to(REPO).as_posix()}:{line}: {class_name} reads self.{attr} (or cls.{attr}), which is never defined")
    return problems


def check_window_refs() -> list[str]:
    """``window.<name>`` in a controller or the action table must name something ``MainWindow`` defines.

    The back-reference controllers reach the window through ``window`` / ``self._window``; a typo, or a member that was
    deleted or moved, is an AttributeError at the moment that code path runs. Qt's own names count as defined.
    """
    window_cls = next(n for n in _parse(WINDOW_PY).body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    known = _class_members(window_cls) | _qt_main_window_names() | _guarded_names(window_cls)
    files = [PACKAGE / name for name in discover_controllers()] + [PACKAGE / "ui" / "actions.py"]
    problems = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and not node.attr.startswith("__") and ast.unparse(node.value) in _WINDOW_RECEIVERS:
                if node.attr not in known:
                    problems.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}: {ast.unparse(node)} is not defined on MainWindow")
    return problems


def _qobject_names() -> set[str]:
    try:
        from PySide6.QtCore import QObject
    except ImportError:  # the report runs without PySide6; inherited names then cannot be listed
        return set()
    return set(dir(QObject))


def check_controller_calls() -> list[str]:
    """``<window>.<handle>.<name>`` must name something the controller behind that handle defines.

    A slice moves methods into a controller and rewrites its callers; a caller the rewrite missed (a path through another
    object, a name that was only a forwarder) is an AttributeError when that code runs. ``<handle>`` is any ``self._x``
    that ``MainWindow`` sets to ``SomeController(self)``.
    """
    window_tree = _parse(WINDOW_PY)
    handles: dict[str, str] = {}
    for node in ast.walk(window_tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id.endswith("Controller")
            and any(isinstance(arg, ast.Name) and arg.id == "self" for arg in node.value.args)
        ):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    handles[target.attr] = node.value.func.id
    classes: dict[str, ast.ClassDef] = {}
    for path in PACKAGE.rglob("*.py"):
        for node in _parse(path).body:
            if isinstance(node, ast.ClassDef) and node.name in handles.values():
                classes[node.name] = node
    inherited = _qobject_names()
    members = {handle: _class_members(classes[cls]) | inherited for handle, cls in handles.items() if cls in classes}
    receivers = _WINDOW_RECEIVERS | {"self", "main_window", "w", "win"}
    problems = []
    for path in sorted(PACKAGE.rglob("*.py")) + sorted((REPO / "tests").glob("*.py")):
        if "__pycache__" in path.parts:
            continue
        in_tests = "tests" in path.parts
        for node in ast.walk(_parse(path)):
            if not (isinstance(node, ast.Attribute) and not node.attr.startswith("__") and isinstance(node.value, ast.Attribute)):
                continue
            inner = node.value
            if inner.attr not in members or ast.unparse(inner.value) not in receivers:
                continue
            if ast.unparse(inner.value) == "self" and path != WINDOW_PY:
                continue
            if in_tests and ast.unparse(inner.value) == "self":
                continue
            if node.attr not in members[inner.attr]:
                problems.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}: {ast.unparse(node)} is not defined on {handles[inner.attr]}")
    return problems


_REPO_ROOTS = ("image_triage", "aiculler", "packaging", "scripts", "benchmarks", "sandboxes", "tests")


def _module_path(dotted: str) -> Path | None:
    base = REPO.joinpath(*dotted.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    if base.is_dir():  # namespace package (tests/, scripts/): names imported from it are submodules
        return base
    return None


def _defined_names(path: Path) -> tuple[set[str], bool]:
    if path.is_dir():
        return set(), False
    names: set[str] = set()
    star = False
    for n in ast.walk(_parse(path)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names.update(x.id for t in n.targets for x in ast.walk(t) if isinstance(x, ast.Name))
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                star = star or a.name == "*"
                names.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in n.names)
    return names, star or "__getattr__" in names


def check_internal_imports() -> list[str]:
    """Every ``from <repo module> import name`` (module level or inside a function) resolves."""
    files = [p for r in _REPO_ROOTS for p in (REPO / r).rglob("*.py") if "__pycache__" not in p.parts]
    files += [p for p in (REPO / "setup_msi.py", REPO / "freeze_support.py") if p.exists()]
    cache: dict[Path, tuple[set[str], bool]] = {}
    problems = []
    for f in files:
        try:
            tree = _parse(f)
        except SyntaxError as exc:
            problems.append(f"{f.relative_to(REPO).as_posix()}: syntax error: {exc}")
            continue
        parts = list(f.relative_to(REPO).with_suffix("").parts)
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom):
                continue
            if n.level:
                base = parts[:-1]
                base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                target = ".".join(base + ([n.module] if n.module else []))
            else:
                target = n.module or ""
            if target.split(".")[0] not in _REPO_ROOTS:
                continue
            mp = _module_path(target)
            if mp is None:
                problems.append(f"{f.relative_to(REPO).as_posix()}:{n.lineno}: module {target} not found")
                continue
            names, star = cache.setdefault(mp, _defined_names(mp))
            for a in n.names:
                if a.name != "*" and not star and a.name not in names and _module_path(f"{target}.{a.name}") is None:
                    problems.append(f"{f.relative_to(REPO).as_posix()}:{n.lineno}: {target} has no name {a.name}")
    return problems


# ------------------------------------------------------------------ ratchet


def load_ratchet() -> dict:
    return json.loads(RATCHET_PATH.read_text(encoding="utf-8"))


# Reported, not enforced: moving code out of MainWindow into a back-reference controller turns invisible intra-window
# coupling into visible ``window._x`` accesses, so these totals rise when a cluster is extracted. They are bounded
# per module instead (below), and a new controller must be registered deliberately with ``--accept-new-modules``.
INFORMATIONAL_METRICS = {"private_accesses_total", "private_attributes_distinct_total"}


def check_ratchet(current: dict, recorded: dict) -> list[str]:
    problems = []
    for key, value in current["metrics"].items():
        limit = recorded["metrics"].get(key)
        if key not in INFORMATIONAL_METRICS and limit is not None and value > limit:
            problems.append(f"{key} rose from {limit} to {value} (the ratchet only allows it to fall)")
    allowed = recorded.get("private_accesses_by_module", {})
    for module, hits in current["private_accesses_by_module"].items():
        if hits > allowed.get(module, 0):
            problems.append(
                f"{module} reaches into the window's privates {hits} times (allowed {allowed.get(module, 0)}); R1 forbids new reaches"
            )
    return problems


def updated_ratchet(current: dict, recorded: dict, *, accept_new_modules: bool = False, accept_growth: frozenset[str] = frozenset()) -> dict:
    """Recorded values drop to the current ones; an enforced number is never raised.

    ``accept_new_modules`` registers modules that did not exist in the record (a freshly extracted controller) at their
    current count; the informational totals are simply refreshed.
    """
    out = json.loads(json.dumps(recorded))
    for key, value in current["metrics"].items():
        if key in INFORMATIONAL_METRICS:
            out["metrics"][key] = value
        else:
            out["metrics"][key] = min(value, out["metrics"].get(key, value))
    known = recorded.get("private_accesses_by_module", {})
    mods = {}
    for module, hits in current["private_accesses_by_module"].items():
        if module in accept_growth:
            mods[module] = hits  # a deliberate, named exception (an attribute moved behind a controller: window._x -> window._owner.x)
        elif module in known:
            mods[module] = min(hits, known[module])
        elif accept_new_modules:
            mods[module] = hits
    out["private_accesses_by_module"] = mods
    return out


def run_all_checks(current: dict, recorded: dict) -> list[str]:
    return (
        check_ratchet(current, recorded)
        + check_ui_free(recorded.get("ui_free_modules", []))
        + check_self_members()
        + check_window_refs()
        + check_controller_calls()
        + check_internal_imports()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if a number got worse or a rule is broken")
    parser.add_argument("--update", action="store_true", help="lower the recorded numbers to the current ones")
    parser.add_argument("--accept-growth", action="append", default=[], metavar="MODULE", help="with --update: allow this named module's access count to rise (state it in the plan)")
    parser.add_argument("--accept-new-modules", action="store_true", help="with --update: register new controller modules at their current access count")
    args = parser.parse_args(argv)

    current = collect_metrics()
    if not RATCHET_PATH.exists():
        print(f"{RATCHET_PATH} does not exist; create it with the seed values below and re-run.")
        print(json.dumps({"schema": 1, **current, "ui_free_modules": []}, indent=2))
        return 1
    recorded = load_ratchet()

    if args.update:
        problems = check_ratchet(current, recorded)
        if args.accept_new_modules:
            known = recorded.get("private_accesses_by_module", {})
            problems = [p for p in problems if not any(p.startswith(m + " reaches") and m not in known for m in current["private_accesses_by_module"])]
        problems = [p for p in problems if not any(p.startswith(m + " reaches") for m in args.accept_growth)]
        if problems:
            print("Refusing to update: " + "; ".join(problems))
            return 1
        RATCHET_PATH.write_text(
            json.dumps(updated_ratchet(current, recorded, accept_new_modules=args.accept_new_modules, accept_growth=frozenset(args.accept_growth)), indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {RATCHET_PATH.relative_to(REPO)}")
        return 0

    width = max(len(k) for k in current["metrics"])
    print("metric".ljust(width), "  now  recorded")
    for key, value in current["metrics"].items():
        print(key.ljust(width), f"{value:6d} {recorded['metrics'].get(key, '-')!s:>9}")
    if args.check:
        problems = run_all_checks(current, recorded)
        for p in problems:
            print("FAIL:", p)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
