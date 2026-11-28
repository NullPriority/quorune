"""A fixed mana result uses the payer's ordinary life replacement transaction."""

from collections.abc import Mapping


_CAUSE = "mana ability life gain"


def _event_source(event_id, payment_id):
    if not isinstance(payment_id, str) or not payment_id or not isinstance(event_id, str):
        return None
    prefix = f"life.mana:{payment_id}:"
    if not event_id.startswith(prefix) or not event_id.endswith(":0"):
        return None
    source, separator, ordinal = event_id[len(prefix):-2].rpartition(":")
    if (source and separator and ordinal.isascii() and ordinal.isdecimal()
            and (ordinal == "0" or not ordinal.startswith("0"))):
        return source
    return None


def mana_life_event_belongs_to_payment(event_id, payment_id):
    return _event_source(event_id, payment_id) is not None


def mana_life_replacement_event(batch, response: Mapping, seat):
    if len(batch.events) != 1:
        return None
    event = batch.events[0]
    payload = event.payload
    source = _event_source(event.event_id, response.get("_mana_payment_id"))
    if (source is not None and event.kind == "life.change" and event.affected_player == seat
            and event.affected_object is None and payload.get("player") == seat
            and payload.get("source_controller") == seat and payload.get("source") == source
            and payload.get("cause") == _CAUSE and payload.get("direction") == "gain"
            and type(payload.get("amount")) is int and 1 <= payload["amount"] <= 20
            and type(payload.get("requested_amount")) is int and payload["requested_amount"] == payload["amount"]):
        return event
    return None


def apply_mana_life_gain(host, seat, amount, index, *, source, payment_id, selections_by_event):
    from ..effect_runtime.life_effects import _apply_life
    from ..errors import GameRuleError

    if source is None:
        raise GameRuleError("Mana life gain requires its original source")
    prefix = f"life.mana:{payment_id}:{source.ref}:{index}" if payment_id else None
    effect = {"source": source.ref, "player": seat, "delta": amount, "cause": _CAUSE}
    if prefix is not None and isinstance(selections_by_event, Mapping):
        effect["_replacement_selections"] = selections_by_event.get(f"{prefix}:0", ())
    _apply_life(host, effect, actor=seat, operation="life", reason=_CAUSE, event_prefix=prefix)
