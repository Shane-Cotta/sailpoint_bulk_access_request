"""A tiny stand-in for what the workflow engine and SailPoint's email service do with a send-email step, so the
tests (and reviewers) can see the emails `definitions.py` builds as they arrive.

* `resolve_context(attrs_context, state)` does the workflow engine's part: "key.$" JSONPath values and "{{...}}"
  templates read from the run's state; a one-item list arrives unwrapped; a missing path gives "" in a template
  and leaves a "key.$" variable unset (all verified live, docs/dev/CONTRACTS.md section 10).
* `render(template, context)` does the email service's part for the Velocity subset the emails use:
  "$!{a}" / "$!{a.b}", #if / #elseif / #else / #end with `"$!{a}" == "x"`, `"$!{a}" != ""`, `$a.b` and `true`,
  #foreach($x in $list), and #set($x = $y), #set($x = [$y]), #set($x = "text"). Anything else raises, so a test
  fails if the markup strays outside what was checked against the live tenant.
"""

from __future__ import annotations

import re
from typing import Any

_MISSING = object()


# ── the workflow engine's part ───────────────────────────────────────────────────────────────────────────────
def jsonpath(state: dict[str, Any], path: str) -> Any:
    """`$.a.b[0].c`, `$.a[1:]` and a trailing `.length()`; _MISSING when any step is absent."""
    assert path.startswith("$"), path
    length = path.endswith(".length()")
    if length:
        path = path[: -len(".length()")]
    cur: Any = state
    for name, index, sl in re.findall(r"\.([A-Za-z_][\w]*)|\[(-?\d+)\]|\[(-?\d*:-?\d*)\]", path[1:]):
        if name:
            if not isinstance(cur, dict) or name not in cur:
                return _MISSING
            cur = cur[name]
        elif index:
            if not isinstance(cur, list) or not -len(cur) <= int(index) < len(cur):
                return _MISSING
            cur = cur[int(index)]
        else:
            if not isinstance(cur, list):
                return _MISSING
            a, b = sl.split(":")
            cur = cur[int(a) if a else None: int(b) if b else None]
    if length:
        return len(cur) if isinstance(cur, (list, str)) else _MISSING
    return cur


def _unwrap(value: Any) -> Any:
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def template(state: dict[str, Any], text: str) -> str:
    def one(m: re.Match[str]) -> str:
        value = _unwrap(jsonpath(state, m.group(1)))
        return "" if value is _MISSING or value is None else str(value)
    return re.sub(r"\{\{(\$[^}]*)\}\}", one, text)


def resolve_context(context: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in context.items():
        if key.endswith(".$"):
            got = jsonpath(state, value)
            if got is _MISSING:
                out[key] = value          # left as it was: "$key" stays unset in the template
            else:
                out[key[:-2]] = _unwrap(got)
        else:
            out[key] = template(state, value) if isinstance(value, str) else value
    return out


# ── the email service's part (Velocity subset) ───────────────────────────────────────────────────────────────
_TOKEN = re.compile(r'#\{?(if|elseif|foreach|set)\}?\(|#\{?(else|end)\}?|\$!\{([A-Za-z_][\w.]*)\}')


def _lookup(ctx: dict[str, Any], dotted: str) -> Any:
    cur: Any = ctx
    for i, part in enumerate(dotted.split(".")):
        if i == 0:
            cur = ctx.get(part)
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _text(value: Any) -> str:
    return "" if value is None else ("true" if value is True else "false" if value is False else str(value))


def _args(src: str, pos: int) -> tuple[str, int]:
    """The text inside a directive's parentheses (quotes respected), and the position after ")"."""
    depth, i, quoted = 1, pos, False
    while i < len(src):
        c = src[i]
        if c == '"':
            quoted = not quoted
        elif not quoted and c == "(":
            depth += 1
        elif not quoted and c == ")":
            depth -= 1
            if depth == 0:
                return src[pos:i], i + 1
        i += 1
    raise ValueError(f"unclosed directive at {pos}")


def _parse(src: str) -> list[Any]:
    """Nodes: str | ("ref", name) | ("if", [(cond, body)], else_body) | ("foreach", var, list, body) | ("set", var, expr)."""
    pos = 0

    def block(stop: tuple[str, ...]) -> tuple[list[Any], str | None, str | None]:
        nonlocal pos
        nodes: list[Any] = []
        while True:
            m = _TOKEN.search(src, pos)
            if not m:
                nodes.append(src[pos:])
                pos = len(src)
                if stop:
                    raise ValueError("missing #end")
                return nodes, None, None
            nodes.append(src[pos:m.start()])
            pos = m.end()
            directive = m.group(1) or m.group(2)
            if m.group(3):
                nodes.append(("ref", m.group(3)))
            elif directive in ("else", "end", "elseif"):
                if directive not in stop:
                    raise ValueError(f"unexpected #{directive}")
                args = None
                if directive == "elseif":
                    args, pos = _args(src, pos)
                return nodes, directive, args
            elif directive == "if":
                cond, pos = _args(src, pos)
                branches, otherwise = [], []
                body, ended, args = block(("elseif", "else", "end"))
                branches.append((cond, body))
                while ended == "elseif":
                    cond = args
                    body, ended, args = block(("elseif", "else", "end"))
                    branches.append((cond, body))
                if ended == "else":
                    otherwise, _, _ = block(("end",))
                nodes.append(("if", branches, otherwise))
            elif directive == "foreach":
                spec, pos = _args(src, pos)
                fm = re.fullmatch(r"\s*\$(\w+)\s+in\s+\$(\w+)\s*", spec)
                if not fm:
                    raise ValueError(f"unsupported #foreach({spec})")
                body, _, _ = block(("end",))
                nodes.append(("foreach", fm.group(1), fm.group(2), body))
            elif directive == "set":
                spec, pos = _args(src, pos)
                sm = re.fullmatch(r'\s*\$(\w+)\s*=\s*(\[\$\w+\]|\$\w+|"[^"$]*")\s*', spec)
                if not sm:
                    raise ValueError(f"unsupported #set({spec})")
                nodes.append(("set", sm.group(1), sm.group(2)))

    nodes, _, _ = block(())
    return nodes


def _cond(expr: str, ctx: dict[str, Any]) -> bool:
    expr = expr.strip()
    if expr == "true":
        return True
    m = re.fullmatch(r'"\$!\{([\w.]+)\}"\s*(==|!=)\s*"([^"$]*)"', expr)
    if m:
        same = _text(_lookup(ctx, m.group(1))) == m.group(3)
        return same if m.group(2) == "==" else not same
    m = re.fullmatch(r"\$([\w]+(?:\.[\w]+)*)", expr)
    if m:
        value = _lookup(ctx, m.group(1))
        return value is not None and value is not False
    raise ValueError(f"unsupported condition: {expr}")


def _run(nodes: list[Any], ctx: dict[str, Any], out: list[str]) -> None:
    for node in nodes:
        if isinstance(node, str):
            if "$" in node or "#" in node:
                raise ValueError(f"stray $ or # in template text: {node[:60]!r}")
            out.append(node)
        elif node[0] == "ref":
            out.append(_text(_lookup(ctx, node[1])))
        elif node[0] == "if":
            for cond, body in node[1]:
                if _cond(cond, ctx):
                    _run(body, ctx, out)
                    break
            else:
                _run(node[2], ctx, out)
        elif node[0] == "foreach":
            seq = ctx.get(node[2])
            items = list(seq.values()) if isinstance(seq, dict) else list(seq or [])
            for item in items:
                ctx[node[1]] = item
                _run(node[3], ctx, out)
        elif node[0] == "set":
            expr = node[2]
            if expr.startswith("["):
                ctx[node[1]] = [ctx.get(expr[2:-1])]
            elif expr.startswith('"'):
                ctx[node[1]] = expr[1:-1]
            elif ctx.get(expr[1:]) is not None:   # Velocity 1.7 leaves the variable alone for a null value
                ctx[node[1]] = ctx.get(expr[1:])


def render(source: str, context: dict[str, Any]) -> str:
    out: list[str] = []
    _run(_parse(source), dict(context), out)
    return "".join(out)


def render_step(step: dict[str, Any], state: dict[str, Any]) -> tuple[str, str]:
    """(subject, body) of a send-email step as the recipient sees it, for the given workflow state."""
    attrs = step["attributes"]
    ctx = resolve_context(attrs.get("context") or {}, state)
    return template(state, attrs["subject"]), render(template(state, attrs["body"]), ctx)
