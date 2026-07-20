"""Shared fixed-width text-table renderer for the cost-report CLIs.

Extracted from ``cost_ledger_report`` / ``fire_log_report`` so the two (and any
future report script) render tables through one implementation. The
row-normalisation here was a real bug fix — the original ``zip(*rows)`` truncated
to the shortest tuple and silently dropped a header column on any row-length
mismatch — so a single canonical copy avoids that fix drifting between copies.

Imported as ``from _report_table import table``; both report scripts live in the
same ``lib/`` directory, so the bare import resolves when they are run as
``python lib/<report>.py`` (the script's own directory is on ``sys.path``).
"""
from __future__ import annotations


def table(headers, rows, aligns=None):
    """Render a fixed-width text table.

    ``aligns`` is a list of ``'<'``/``'>'`` per column (default all left). Rows
    are normalised to the header count — short rows padded, long rows truncated —
    so a mismatched row can never silently drop a header column.
    """
    n = len(headers)
    norm = [list(r)[:n] + [""] * (n - len(r)) for r in rows]
    aligns = aligns or ["<"] * n
    widths = [
        max([len(str(headers[i]))] + [len(str(r[i])) for r in norm])
        for i in range(n)
    ]

    def render(cells):
        return "  ".join(f"{str(c):{a}{w}}" for c, w, a in zip(cells, widths, aligns))

    out = [render(headers), render(["-" * w for w in widths])]
    out.extend(render(r) for r in norm)
    return "\n".join(out)
