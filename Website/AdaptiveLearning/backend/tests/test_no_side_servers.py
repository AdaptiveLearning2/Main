"""The backend is one FastAPI app on loopback: no module stands up a second web server."""
import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _modules():
    return [p for p in sorted(BACKEND.glob("*.py"))]


def test_no_backend_module_imports_a_web_framework_of_its_own():
    """A dead Flask app with a service-role client was one renamed function from serving anyone's data."""
    found = []
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            found += [f"{path.name}: {n}" for n in names if n.split(".")[0] in ("flask", "flask_cors")]
    assert found == []


def test_running_main_directly_binds_loopback():
    """With ENV unset `/docs` is on, so a LAN bind would publish it and the API to the network."""
    tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))
    hosts = [kw.value.value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run"
             and getattr(node.func.value, "id", None) == "uvicorn"
             for kw in node.keywords if kw.arg == "host"]
    assert hosts == ["127.0.0.1"]
