"""Stable adapter contracts. Dicts cross boundaries; dataclasses validate shape."""

from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
	name: str
	description: str
	parameters: dict[str, Any]
	handler: Callable[..., Any]
	kind: str = "read"
	permission_doctype: str | None = None

	def function_spec(self):
		return {"type": "function", "function": {"name": self.name, "description": self.description,
			"parameters": self.parameters or {"type": "object", "properties": {}}}}


@dataclass
class ReferenceBundle:
	adapter: str
	facts: dict[str, Any]
	references: dict[str, Any]
	memories: dict[str, Any] = field(default_factory=dict)
	warnings: list[str] = field(default_factory=list)

	def as_dict(self):
		return asdict(self)


@dataclass
class Decision:
	status: str
	confidence: float
	values: dict[str, Any]
	reasons: list[str]
	handoff_reasons: list[str] = field(default_factory=list)
	memory_sources: list[str] = field(default_factory=list)

	@classmethod
	def from_dict(cls, value):
		value = value if isinstance(value, dict) else {}
		status = value.get("status") if value.get("status") in {"review", "handoff"} else "handoff"
		return cls(status=status, confidence=max(0.0, min(float(value.get("confidence") or 0), 1.0)),
			values=value.get("values") if isinstance(value.get("values"), dict) else {},
			reasons=[str(x) for x in value.get("reasons") or []],
			handoff_reasons=[str(x) for x in value.get("handoff_reasons") or []],
			memory_sources=[str(x) for x in value.get("memory_sources") or []])

	def as_dict(self):
		return asdict(self)


@dataclass
class ActionPlan:
	target_doctype: str
	values: dict[str, Any]
	children: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
	requires_human: bool = True
	blockers: list[str] = field(default_factory=list)

	def as_dict(self):
		return asdict(self)
