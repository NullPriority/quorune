"""Choose a real current owned-and-controlled creature for one meld payment."""

from ..meld_model import MELD_PAIR_FIELD, MeldPairSpec, meld_exile_effect
from ..replacement.immutable import FrozenMap
from .model import AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoicePreparation, SemanticChoiceRequest
from .model import SemanticChoiceError


MELD_CHOICE_CANDIDATES = "meld_choice_candidates"


def _choice_spec(effect):
    if set(effect) - {"_runtime_source", MELD_CHOICE_CANDIDATES} != {"op", MELD_PAIR_FIELD} or effect["op"] != "choose_objects":
        raise SemanticChoiceError("Meld selection needs its complete original ability")
    try:
        return MeldPairSpec.from_dict(effect[MELD_PAIR_FIELD])
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _partner_effect(spec, identity):
    object_id, logical_object_id = identity
    return meld_exile_effect(spec, object_id=object_id, logical_object_id=logical_object_id)


def prepare_meld_partner(effect, context):
    spec = _choice_spec(effect)
    if MELD_CHOICE_CANDIDATES in effect:
        raise SemanticChoiceError("Meld cannot reuse a completed partner selection")
    rows = tuple(context.query.object(ref) for ref in context.query.choice_candidate_refs())
    options = {row.ref: [row.object_id, row.logical_object_id] for row in rows if row is not None}
    if len(options) <= 1:
        effects = tuple(_partner_effect(spec, value) for value in options.values())
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="Meld partner eligibility is determined", prepend_effects=effects),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Choose a creature named {spec.partner_name} to exile for meld.",
            choice=ObjectChoice(field_name="objects", legal_refs=tuple(options), zones=("battlefield",), minimum=1, maximum=1),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "choose_objects",
                                      "objects": [{"id": ref, "name": spec.partner_name} for ref in options]}),
        ),
        continuation_effect=FrozenMap({**effect, MELD_CHOICE_CANDIDATES: options}),
    )


def complete_meld_partner(continuation, response, query):
    effect = continuation.effect
    spec = _choice_spec(effect)
    chosen = response.get("objects")
    if (set(response) - {"objects", "action", "choice_schema"}
            or response.get("action", "choose") != "choose" or not isinstance(chosen, (list, tuple)) or len(chosen) != 1):
        raise SemanticChoiceError("Choose exactly one of the current meld partners")
    ref, = chosen
    options = effect.get(MELD_CHOICE_CANDIDATES, {})
    if "choice_schema" in response:
        expected = ObjectChoice(field_name="objects", legal_refs=tuple(options), zones=("battlefield",), minimum=1, maximum=1).choice_schema()
        if FrozenMap(response["choice_schema"]) != FrozenMap(expected):
            raise SemanticChoiceError("Meld's original partner choices changed")
    row = query.object(ref)
    if row is None or ref not in options or tuple(options[ref]) != (row.object_id, row.logical_object_id):
        raise SemanticChoiceError("The original chosen meld partner is no longer available")
    return SemanticChoiceCompletion(prepend_effects=(_partner_effect(spec, options[ref]),))
