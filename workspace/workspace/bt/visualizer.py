"""Inspection helpers — pretty-print BT structure and live state.

Two surfaces:

* :func:`ascii_tree` — render the structure (no status) of a tree as
  indented ASCII. Useful for "what shape did the builder produce?"
  debugging.
* :func:`ascii_status` — render the tree with each node's most-recent
  status. Pair with the engine: call after every tick to get an
  operator-visible snapshot.
* :func:`dot` — Graphviz DOT export. py_trees ships this natively but
  we re-expose it here so projects don't have to remember the import.

The renderers stay print-friendly (no terminal colors by default —
trivial to add later if operators want a TUI).
"""

from __future__ import annotations

from typing import Any

import py_trees


STATUS_GLYPH = {
    py_trees.common.Status.INVALID: "·",
    py_trees.common.Status.RUNNING: "●",
    py_trees.common.Status.SUCCESS: "✓",
    py_trees.common.Status.FAILURE: "✗",
}


def ascii_tree(root: py_trees.behaviour.Behaviour, indent: str = "  ") -> str:
    """Structural ASCII rendering: type + name per node, indented.

    Reads at-a-glance — what builder.py produced before any tick fires.
    """
    lines = []

    def _walk(node, depth: int):
        kind = type(node).__name__
        lines.append(f"{indent * depth}{kind}({node.name})")
        for child in getattr(node, "children", []) or []:
            _walk(child, depth + 1)

    _walk(root, 0)
    return "\n".join(lines)


def ascii_status(root: py_trees.behaviour.Behaviour, indent: str = "  ") -> str:
    """Same shape as :func:`ascii_tree` but with each node's latest status."""
    lines = []

    def _walk(node, depth: int):
        glyph = STATUS_GLYPH.get(node.status, "?")
        kind = type(node).__name__
        lines.append(f"{indent * depth}{glyph} {kind}({node.name}) [{node.status.name}]")
        for child in getattr(node, "children", []) or []:
            _walk(child, depth + 1)

    _walk(root, 0)
    return "\n".join(lines)


def dot(root: py_trees.behaviour.Behaviour) -> str:
    """Graphviz DOT export — feed to ``dot -Tpng`` to render a tree image.

    Wraps py_trees' built-in display helper so projects don't have to
    import multiple py_trees submodules.
    """
    try:
        # py_trees >= 2.x
        graph = py_trees.display.dot_tree(root)  # type: ignore[attr-defined]
        return graph.to_string()
    except Exception:
        # Older versions or non-pydot environments — fall back to a
        # minimal hand-rolled DOT.
        lines = ["digraph BT {", "  node [shape=box, fontname=monospace];"]
        seq = {"n": 0}
        ids = {}

        def _id(node):
            if id(node) not in ids:
                seq["n"] += 1
                ids[id(node)] = f"n{seq['n']}"
            return ids[id(node)]

        def _walk(node, parent_id=None):
            nid = _id(node)
            label = f"{type(node).__name__}\\n{node.name}"
            lines.append(f'  {nid} [label="{label}"];')
            if parent_id is not None:
                lines.append(f"  {parent_id} -> {nid};")
            for c in getattr(node, "children", []) or []:
                _walk(c, nid)

        _walk(root)
        lines.append("}")
        return "\n".join(lines)
