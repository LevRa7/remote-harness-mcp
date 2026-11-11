"""core/termux.py - compatibility alias for the Termux bridge.

The implementation moved to :mod:`core.platforms.android.termux` when the node
was split into per-platform packages: ``termux-*`` commands only ever exist on an
Android phone, so that is where the adapter belongs.

This file stays because ``core.termux`` is imported by ``core/mcp_tools.py``,
``core/device.py`` and the test suite.  It aliases the *same module object* into
``sys.modules`` instead of re-exporting names, so ``termux._run`` monkeypatched
by a test reaches the code that calls it - a plain ``from ... import *`` copy
would leave the real module untouched and silently defeat every such test.
"""

from __future__ import annotations

import sys

from core.platforms.android import termux as _impl

sys.modules[__name__] = _impl
