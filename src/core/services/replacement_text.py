"""Replacement value generation shared by document-specific engines."""

from src.core.models.replacement import ReplacementTarget


def _partial(value: str) -> str:
    visible = [index for index, char in enumerate(value) if not char.isspace()]
    keep = 4 if len(visible) > 8 else 2
    protected = set(visible[-keep:])
    return "".join(char if char.isspace() or index in protected else "*" for index, char in enumerate(value))


def _regex_dummy(value: str) -> str:
    generated: list[str] = []
    for char in value:
        if "a" <= char <= "z":
            generated.append(chr((ord(char) - ord("a") + 1) % 26 + ord("a")))
        elif "A" <= char <= "Z":
            generated.append(chr((ord(char) - ord("A") + 1) % 26 + ord("A")))
        elif "0" <= char <= "9":
            generated.append(str((int(char) + 1) % 10))
        else:
            generated.append(char)
    return "".join(generated)


def replacement_text(original: str, target: ReplacementTarget) -> str:
    """Build one replacement while retaining the source's whitespace/pattern."""
    if target.replacement_type == "PARTIAL":
        return _partial(original)
    if target.replacement_type == "STATIC":
        return f"[{target.static_text}]"
    return _regex_dummy(original)
