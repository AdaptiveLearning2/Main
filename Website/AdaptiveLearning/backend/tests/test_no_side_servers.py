"""The backend is one FastAPI app on loopback: no module stands up a second web server."""
import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Anything that serves HTTP by being imported and run; a client library is not one.
_SERVERS = {"flask", "flask_cors", "django", "bottle", "tornado", "cherrypy", "sanic", "quart",
            "falcon", "waitress", "gunicorn", "http.server", "socketserver", "wsgiref", "aiohttp.web"}
# Constructing one of these, or calling `uvicorn.run`, is standing up an app.
_APP_CLASSES = {"FastAPI", "Starlette", "Flask", "APIRouter"}


def _modules():
    modules = sorted(BACKEND.glob("*.py"))
    # A floor, so a moved directory cannot make every check below pass over nothing.
    assert len(modules) >= 10 and "main.py" in {p.name for p in modules}, modules
    return [(p, ast.parse(p.read_text(encoding="utf-8-sig"))) for p in modules]


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            yield from (f"{node.module}.{a.name}" for a in node.names)


def test_no_backend_module_imports_a_web_server_of_its_own():
    """A dead Flask app with a service-role client was one renamed function from serving anyone's data."""
    found = [f"{path.name}: {name}" for path, tree in _modules() for name in _imports(tree)
             if name in _SERVERS or name.split(".")[0] in _SERVERS]
    assert found == []


def test_only_main_builds_an_app_or_runs_a_server():
    found = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            is_run = name == "run" and getattr(node.func.value, "id", None) == "uvicorn" \
                if isinstance(node.func, ast.Attribute) else False
            if (name in _APP_CLASSES or is_run) and path.name != "main.py":
                found.append(f"{path.name}:{node.lineno}")
    assert found == []


def test_running_main_directly_binds_loopback():
    """With ENV unset `/docs` is on, so a LAN bind would publish it and the API to the network."""
    tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))
    hosts = [kw.value.value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run"
             and getattr(node.func.value, "id", None) == "uvicorn"
             for kw in node.keywords if kw.arg == "host"]
    assert hosts == ["127.0.0.1"]
