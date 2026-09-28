"""Lazy, entrypoint-safe handle to the ``execution_agent`` module.

The modular split of ``execution_agent`` (2026-09-27) left ten sibling modules
needing a back-reference to it — the running ``_tee``, the retention constants,
shared helpers — which they had been getting with a top-level
``import execution_agent as ea``.

That back-reference is a trap when ``execution_agent`` is the process entrypoint.
Run as ``python execution_agent.py`` it is registered in ``sys.modules`` as
``__main__``, NOT as ``execution_agent``. So a sibling's top-level
``import execution_agent`` fires while ``execution_agent`` is still executing its
own import block, re-enters it from the top, and fails on a name that is not
defined yet — e.g. ``ImportError: cannot import name 'TeeLogger'`` /
``'get_live_price'``. That is what crash-looped the container on 2026-09-27.

The fix: siblings do ``from execution_agent_ref import ea`` and reference
``ea.<name>``. Resolution is deferred to attribute-access time and goes through
``sys.modules``, so it always returns the ONE running instance:

  1. ``sys.modules['execution_agent']`` when it was imported normally
     (tests, other CLI tools, and the ``agent_entrypoint.py`` wrapper);
  2. ``__main__`` when ``execution_agent.py`` is run directly;
  3. failing both, it imports ``execution_agent`` — safe precisely because in
     that case it is NOT the entrypoint, so there is no duplicate-module hazard.

This preserves the old "execution_agent is available whenever these modules are
used" semantics while removing the load-time cycle.

See decisions/2026-09-27_startup-crash-shipping.md.
"""
from __future__ import annotations

import os
import sys


class _ExecutionAgentRef:
    """Attribute proxy that resolves the execution_agent module on each access."""

    @staticmethod
    def _module():
        mod = sys.modules.get("execution_agent")
        if mod is not None:
            return mod
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if main is not None and main_file and os.path.basename(main_file) == "execution_agent.py":
            return main
        # Not the entrypoint and not yet imported: importing it now is safe and
        # canonical (it will register under 'execution_agent', not '__main__').
        import execution_agent  # noqa: PLC0415 — deliberately deferred

        return execution_agent

    def __getattr__(self, name):
        return getattr(self._module(), name)

    def __setattr__(self, name, value):
        setattr(self._module(), name, value)


ea = _ExecutionAgentRef()
