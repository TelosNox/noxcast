"""Run the Home Assistant tests on Windows: `python -m pytest -p tests.windows`.

Home Assistant does not support Windows, but its test harness gets there with three stand-ins, loaded before
the harness plugin (hence -p, not conftest.py):
- `fcntl`: homeassistant.runner imports it for the lock file of a running instance, which tests never take.
- `resource`: homeassistant.util.resource raises the open-file limit, which Windows does not have.
- sockets: asyncio on Windows wakes its event loop through a socket pair on 127.0.0.1, which the harness
  blocks (it allows only Unix sockets). Here sockets stay open; CI on Linux still blocks them.

On other systems this module does nothing. Bit-exact floats differ between Windows and Linux; tests compare
results with a tolerance (tests/snapshot.py), so the same results pass on both.
"""
import sys
import types

if sys.platform == "win32":
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX, fcntl.LOCK_NB, fcntl.LOCK_UN = 2, 4, 8
    fcntl.flock = lambda fd, op: None
    sys.modules.setdefault("fcntl", fcntl)

    resource = types.ModuleType("resource")
    resource.RLIMIT_NOFILE = 7
    resource.getrlimit = lambda which: (8192, 8192)
    resource.setrlimit = lambda which, limits: None
    sys.modules.setdefault("resource", resource)

    import pytest_socket

    pytest_socket.disable_socket = lambda *args, **kwargs: None
