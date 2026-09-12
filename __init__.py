"""code-review-sage builtin app — GitHub PR review (KiroCrew OSS port)."""
# ``register_routes`` is the app's one public entrypoint when the gateway loads
# this directory as a package (``kiro_crew.apps.builtins.code_review_sage``).
# In a standalone checkout the same directory is imported as the top-level
# ``__init__`` with no parent package, so the package-relative import below has
# nothing to resolve against. Guard it the same way the rest of the codebase
# guards its ``kiro_crew`` imports: expose the symbol when the package context
# exists, and stay importable (for pytest, which walks this file) when it does
# not. Tests import ``backend.routes`` directly and never need this re-export.
try:
    from .backend.routes import register_routes
except ImportError:  # pragma: no cover - standalone checkout / no parent package
    register_routes = None  # type: ignore[assignment]

__all__ = ["register_routes"]
