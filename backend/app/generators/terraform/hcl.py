"""A tiny HCL2 writer.

Terraform output is built from real block objects rather than string templates.
That buys three things worth having in a code generator: values are escaped in
exactly one place, indentation cannot drift, and the tests can assert on
structure instead of on whitespace.
"""

from __future__ import annotations

from typing import Any

INDENT = "  "


class Raw:
    """An HCL expression that must be emitted verbatim.

    Use for references and function calls -- ``Raw("aws_vpc.main.id")`` renders
    as ``aws_vpc.main.id``, where the plain string would render as a quoted
    literal.
    """

    __slots__ = ("expr",)

    def __init__(self, expr: str) -> None:
        self.expr = expr

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Raw({self.expr!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Raw) and other.expr == self.expr

    def __hash__(self) -> int:
        return hash(self.expr)


def var(name: str) -> Raw:
    return Raw(f"var.{name}")


def ref(*parts: str) -> Raw:
    return Raw(".".join(parts))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_value(value: Any, indent: int = 0) -> str:
    pad = INDENT * indent
    inner = INDENT * (indent + 1)

    if isinstance(value, Raw):
        return value.expr
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f'"{_escape(value)}"'
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = ",\n".join(f"{inner}{render_value(v, indent + 1)}" for v in value)
        return f"[\n{items},\n{pad}]" if len(value) > 3 else (
            "[" + ", ".join(render_value(v, indent) for v in value) + "]"
        )
    if isinstance(value, dict):
        if not value:
            return "{}"
        width = max(len(str(k)) for k in value)
        lines = "\n".join(
            f"{inner}{str(k).ljust(width)} = {render_value(v, indent + 1)}"
            for k, v in value.items()
        )
        return "{\n" + lines + f"\n{pad}}}"
    raise TypeError(f"cannot render {type(value).__name__} as HCL")


class Block:
    """One HCL block: ``type "label" "label" { ... }``."""

    def __init__(self, block_type: str, *labels: str, comment: str | None = None) -> None:
        self.block_type = block_type
        self.labels = labels
        self.comment = comment
        self._entries: list[tuple[str, Any] | Block] = []

    def set(self, key: str, value: Any) -> Block:
        self._entries.append((key, value))
        return self

    def set_if(self, condition: bool, key: str, value: Any) -> Block:
        if condition:
            self.set(key, value)
        return self

    def set_all(self, values: dict[str, Any]) -> Block:
        for key, value in values.items():
            self.set(key, value)
        return self

    def block(self, block_type: str, *labels: str) -> Block:
        child = Block(block_type, *labels)
        self._entries.append(child)
        return child

    def add(self, child: Block) -> Block:
        self._entries.append(child)
        return self

    def render(self, indent: int = 0) -> str:
        pad = INDENT * indent
        inner_indent = indent + 1
        inner = INDENT * inner_indent

        header = self.block_type
        if self.labels:
            header += " " + " ".join(f'"{label}"' for label in self.labels)

        lines: list[str] = []
        if self.comment:
            lines += [f"{pad}# {line}" for line in self.comment.splitlines()]
        lines.append(f"{pad}{header} {{")
        header_index = len(lines)

        # Align the `=` of consecutive simple attributes, the way `terraform fmt` does.
        run: list[tuple[str, Any]] = []

        def flush() -> None:
            if not run:
                return
            width = max(len(k) for k, _ in run)
            for key, value in run:
                lines.append(f"{inner}{key.ljust(width)} = {render_value(value, inner_indent)}")
            run.clear()

        for entry in self._entries:
            if isinstance(entry, Block):
                flush()
                if len(lines) > header_index:  # no blank line right after the header
                    lines.append("")
                lines.append(entry.render(inner_indent))
            else:
                run.append(entry)
        flush()

        lines.append(f"{pad}}}")
        return "\n".join(lines)


class HclFile:
    """An ordered collection of blocks rendered into one ``.tf`` file."""

    def __init__(self, header: str | None = None) -> None:
        self.header = header
        self.blocks: list[Block] = []

    def add(self, block: Block) -> Block:
        self.blocks.append(block)
        return block

    def resource(self, tf_type: str, name: str, comment: str | None = None) -> Block:
        return self.add(Block("resource", tf_type, name, comment=comment))

    def data(self, tf_type: str, name: str, comment: str | None = None) -> Block:
        return self.add(Block("data", tf_type, name, comment=comment))

    def variable(self, name: str, comment: str | None = None) -> Block:
        return self.add(Block("variable", name, comment=comment))

    def output(self, name: str, comment: str | None = None) -> Block:
        return self.add(Block("output", name, comment=comment))

    def __bool__(self) -> bool:
        return bool(self.blocks)

    def render(self) -> str:
        parts: list[str] = []
        if self.header:
            parts.append("\n".join(f"# {line}" for line in self.header.splitlines()))
        for block in self.blocks:
            parts.append(block.render())
        return "\n\n".join(parts).rstrip() + "\n"
