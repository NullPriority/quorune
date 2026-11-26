"""Include protection at possible recipients of a finite redirection."""

from dataclasses import replace

from ..replacement.applicability import condition_matches
from ..replacement.operations import RedirectDamage
from .damage_snapshots import recipient_snapshot


def protection_proposals_for_redirection(host, proposals, effects):
    redirects = tuple((effect.conditions, operation) for effect in effects for operation in effect.operations
                      if isinstance(operation, RedirectDamage))
    if not any(operation.maximum_damage is not None for _, operation in redirects):
        return proposals
    return tuple(candidate for proposal in proposals for candidate in _reachable_recipients(host, proposal, redirects))


def _reachable_recipients(host, proposal, redirects):
    visited = {proposal.recipient.ref: proposal}
    pending = [proposal]
    found_finite = False
    while pending:
        current = pending.pop()
        event = current.event()
        for conditions, operation in redirects:
            if not condition_matches(conditions, event):
                continue
            found_finite |= operation.maximum_damage is not None
            if operation.target in visited:
                continue
            recipient = recipient_snapshot(host, operation.target, actor=operation.target_controller)
            candidate = replace(proposal, recipient=recipient)
            visited[recipient.ref] = candidate
            pending.append(candidate)
    return tuple(visited.values()) if found_finite else (proposal,)
