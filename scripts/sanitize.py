# -*- coding: utf-8 -*-
"""Minimal allowlist HTML sanitizer for release notes (defense against XSS).

Keeps only a small set of formatting tags, drops ALL attributes (so no href/src/
style/on*-handlers survive), and removes <script>/<style> blocks entirely. Output is
safe to render as HTML; disallowed tags are removed but their text is kept.
"""
import re

_ALLOWED = {"br", "p", "b", "strong", "i", "em", "u", "ul", "ol", "li"}
_BLOCK = re.compile(r"<(script|style|iframe|object|embed)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_OPEN_BLOCK = re.compile(r"<(script|style|iframe|object|embed)\b[^>]*>", re.I)
_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z0-9]+)[^>]*>")


def sanitize_html(s, cap=4000):
    if not s:
        return ""
    s = _BLOCK.sub(" ", s)
    s = _OPEN_BLOCK.sub(" ", s)  # any unclosed dangerous opener

    def repl(m):
        close, name = m.group(1), m.group(2).lower()
        if name in _ALLOWED:
            return f"<{close}{name}>"  # bare tag, attributes dropped
        return ""  # remove disallowed tag, keep inner text

    s = _TAG.sub(repl, s)
    return s[:cap].strip()
