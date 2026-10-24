# -*- coding: utf-8 -*-
"""A panel parameter, described once.

The page builds its controls from these (``as_dict``), and the values it
sends back go through `coerce`, which turns a JSON number into what the
control holds: inside its bounds, a float rounded to its decimals, an int a
whole number. That is what keeps the call text right: ``db=0.0`` and not
``db=0``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class Param:
    """One control: what it holds, its bounds, and what it says."""

    #: the key in a values dict; kudio's keyword where there is one
    name: str
    #: the form's label
    label: str
    #: float, int, choice, bool, file, or note (text, no value)
    kind: str
    default: Any = None
    min: float = 0.0
    max: float = 0.0
    step: float = 1.0
    #: decimals a float keeps; the number box on the page rounds to as many
    decimals: int = 2
    suffix: str = ""
    #: shown instead of the number at the minimum: "random", "None"
    special: str = ""
    choices: Tuple[str, ...] = ()
    #: a check box's own text, or a note's
    text: str = ""
    tip: str = ""
    #: the upper bound follows the clip's Nyquist frequency
    nyquist: bool = False
    #: for a file: the dialog's title, its types and the empty field's text
    title: str = ""
    types: Tuple[str, ...] = field(default_factory=tuple)
    placeholder: str = ""

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["choices"] = list(self.choices)
        out["types"] = list(self.types)
        return out

    def coerce(self, value: Any) -> Any:
        """*value* as its control holds it; the default when absent."""
        if value is None:
            value = self.default
        if self.kind == "float":
            v = min(max(float(value), self.min), self.max)
            return round(v, self.decimals)
        if self.kind == "int":
            return int(min(max(int(round(float(value))), self.min), self.max))
        if self.kind == "bool":
            return bool(value)
        if self.kind == "choice":
            value = str(value)
            if value not in self.choices:
                raise ValueError(f"{self.label} has to be one of {', '.join(self.choices)}")
            return value
        if self.kind == "file":
            return str(value or "")
        return value


def coerce_all(params: Sequence[Param], values: Mapping[str, Any]) -> Dict[str, Any]:
    """Every parameter of *params* from *values*, coerced, defaults filled."""
    return {p.name: p.coerce(values.get(p.name)) for p in params if p.kind != "note"}


__all__ = ["Param", "coerce_all"]
