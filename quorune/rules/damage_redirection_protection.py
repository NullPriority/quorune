"""Include protection at recipients reachable from bounded redirections."""

from dataclasses import replace

from ..replacement.applicability import condition_matches
from ..replacement.operations import RedirectDamage
from .damage_snapshots import recipient_snapshot


def protection_proposals_for_redirection(host, proposals, effects, *, scoped_redirection_ids=frozenset()):
    redirects = tuple(
        (effect.conditions, operation, operation.maximum_damage is not None
         or effect.exclusive_damage_scope is not None or effect.effect_id in scoped_redirection_ids)
        for effect in effects for operation in effect.operations if isinstance(operation, RedirectDamage)
    )
    if not any(scoped for _, _, scoped in redirects):
        return proposals
    return tuple(candidate for proposal in proposals for candidate in _reachable_recipients(host, proposal, redirects))


def _reachable_recipients(host, proposal, redirects):
    visited = {proposal.recipient.ref: proposal}
    pending = [proposal]
    found_scoped = False
    while pending:
        current = pending.pop()
        event = current.event()
        for conditions, operation, scoped in redirects:
            if not condition_matches(conditions, event):
                continue
            found_scoped |= scoped
            if operation.target in visited:
                continue
            recipient = recipient_snapshot(host, operation.target, actor=operation.target_controller)
            candidate = replace(proposal, recipient=recipient)
            visited[recipient.ref] = candidate
            pending.append(candidate)
    return tuple(visited.values()) if found_scoped else (proposal,)
