"""Represent the normalized output of the public event binding grammar."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class FixedPublicEventBindingSpec:
    """One parser-owned public occurrence subscription."""

    event: str
    variant: str
    body: str
    template_id: str
    mechanic: str
    condition: Mapping[str, Any] | None = None
    capabilities: tuple[str, ...] = ()
    active_zone: str = "battlefield"

    def __post_init__(self) -> None:
        for field in ("event", "variant", "body", "template_id", "mechanic"):
            if not isinstance(getattr(self, field), str) or not getattr(
                self, field
            ):
                raise ValueError("Public event binding identities must be nonempty")
        if self.active_zone not in {"battlefield", "hand", "graveyard"}:
            raise ValueError("Public event bindings require a closed active zone")
        if not isinstance(self.capabilities, tuple) or any(
            type(value) is not str or not value
            for value in self.capabilities
        ):
            raise ValueError(
                "Public event binding capabilities must be nonempty strings"
            )
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Public event binding capabilities must be unique")


def public_event_binding(
    event: str,
    variant: str,
    body: str,
    template_id: str,
    mechanic: str,
    *,
    condition: Mapping[str, Any] | None = None,
    capabilities: tuple[str, ...] = (),
    active_zone: str = "battlefield",
) -> FixedPublicEventBindingSpec:
    return FixedPublicEventBindingSpec(
        event=event,
        variant=variant,
        body=body,
        template_id=template_id,
        mechanic=mechanic,
        condition=condition,
        capabilities=capabilities,
        active_zone=active_zone,
    )
