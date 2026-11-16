"""Local dashboard: Starlette app serving the packaged Vue build and the /api/ endpoints.

Import the submodules (``web.app``, ``web.server``) directly; the package
re-exports nothing so that importing a light submodule does not load Starlette and uvicorn.
"""
