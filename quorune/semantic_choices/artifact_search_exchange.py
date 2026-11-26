"""Private resolution search and settlement for one selected artifact."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..library_order_model import fixed_shuffle_effect
from ..replacement.immutable import FrozenMap
from ..public_quantity_reductions import public_mana_value
from ..rules.artifact_search_exchange import (
    ARTIFACT_SEARCH_EXCHANGE_CAPABILITY,
    ARTIFACT_SEARCH_EXCHANGE_OPERATION,
    artifact_search_exchange_instruction,
)
from ..rules.mana_pool_payment import MANA_POOL_PAYMENT_FIELD
from ..scalar_effect_amount_model import PaidSacrificeManaValueSnapshot
from ..semantic_runtime.intents import PayManaCostIntent, ZoneMoveIntent
from ..zone_trigger_events import ZoneTransitionKind
from .model import (
    AutoContinue, ObjectChoice, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def scoped_exchange_search(effect):
    return effect.get("op") == ARTIFACT_SEARCH_EXCHANGE_OPERATION and effect.get("phase") == "search"


def _eligible_sacrifice(row, actor):
    return row.controller == actor and "artifact" in row.types and not row.phased_out and not row.sacrifice_prohibited


def _locked_move(row, actor, destination, reason, *, sacrifice=False, optional_if_missing=False):
    physical, separator, incarnation = row.logical_object_id.rpartition("@")
    if not separator or physical != row.object_id or not incarnation.isdecimal():
        raise SemanticChoiceError("Artifact exchange requires a valid object incarnation")
    return ZoneMoveIntent(
        actor=actor, object_ref=row.ref, expected_zones=(row.zone,), destination=destination,
        reason=reason, required_types=("artifact",), controlled_only=sacrifice, owned_only=not sacrifice,
        new_controller=actor if destination == "battlefield" else None,
        expected_zone_change_counter=int(incarnation), optional_if_missing=optional_if_missing,
        transition_kind=ZoneTransitionKind.SACRIFICE if sacrifice else ZoneTransitionKind.ORDINARY,
    )


@dataclass(frozen=True, slots=True)
class ArtifactSearchExchangeChoiceHandler:
    operation: str = ARTIFACT_SEARCH_EXCHANGE_OPERATION
    handler_id: str = "choice.library.artifact-exchange.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("118.11", "118.12", "400.7", "608.2c", "608.2g", "701.23b", "701.24a")
    capability_dependencies: tuple[str, ...] = (ARTIFACT_SEARCH_EXCHANGE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = (
        "schema_version", "phase", "actor", "stack", "paid", "chosen", "chosen_incarnation",
        "_allowed", "_requirements",
    )
    private_data: tuple[str, ...] = ("qualified library candidates", "selected library incarnation")
    projected_fields: tuple[str, ...] = ("objects", "search_cards", "cost", "legal_actions")
    mutation_path: tuple[str, ...] = ("ZoneMoveIntent", "PayManaCostIntent", "fixed_shuffle_effect")
    replay_fixture: str = "resolution-artifact-search-exchange"
    test_modules: tuple[str, ...] = ("tests.test_exact_mishra_closure",)

    @staticmethod
    def _shuffle(actor):
        return FrozenMap(fixed_shuffle_effect(actor))

    def _skip(self, effect, actor, reason):
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason, prepend_effects=(self._shuffle(actor),)),
        )

    @staticmethod
    def _prepare_objects(effect, rows, actor, *, library):
        rows = tuple(sorted(rows, key=lambda row: row.ref))
        refs = tuple(row.ref for row in rows)
        context = (
            {"search_cards": tuple({"id": row.ref, "name": row.printed_name} for row in rows)}
            if library else {"objects": refs}
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=("Choose an artifact card or fail to find." if library
                        else "Choose one artifact to sacrifice."),
                choice=ObjectChoice(
                    field_name="card", legal_refs=refs, zones=("library" if library else "battlefield",),
                    minimum=0 if library else 1, maximum=len(refs) and 1, optional=library,
                    visibility="actor_private" if library else "public",
                    owner_relation="actor" if library else "any",
                    controller_relation="any" if library else "actor",
                    predicates=FrozenMap({"types": ["artifact"]}),
                    schema_extras=FrozenMap({"rules_may_fail_to_find": True}) if library else FrozenMap(),
                ),
                public_context=FrozenMap({**context, "stack": effect["stack"]}),
            ),
            continuation_effect=FrozenMap({**dict(effect), "_allowed": {
                row.ref: row.logical_object_id for row in rows
            }}),
        )

    def prepare(self, effect, context):
        actor = context.actor
        if actor != context.stack_controller:
            raise SemanticChoiceError("Artifact exchange belongs to the resolving controller")
        phase = effect.get("phase")
        if phase is None:
            if dict(effect) != artifact_search_exchange_instruction():
                raise SemanticChoiceError("Unknown artifact exchange descriptor")
            effect = {**dict(effect), "phase": "sacrifice", "actor": actor, "stack": context.stack_ref}
            rows = tuple(
                row for row in context.query.objects(zones=("battlefield",), controller=actor)
                if _eligible_sacrifice(row, actor)
            )
            if not rows:
                return self._skip(effect, actor, "No legal artifact to sacrifice")
            return self._prepare_objects(effect, rows, actor, library=False)
        if effect.get("actor") != actor or effect.get("stack") != context.stack_ref:
            raise SemanticChoiceError("Artifact exchange lost its resolving stack authority")
        if phase == "search":
            PaidSacrificeManaValueSnapshot.from_dict(effect["paid"])
            if not context.query.library_search_allowed(actor):
                return self._skip(effect, actor, "Library search is prohibited")
            scope = set(context.query.library_search_refs(actor))
            rows = tuple(
                row for row in context.query.objects(zones=("library",), owner=actor)
                if row.ref in scope and "artifact" in row.types
            )
            return self._prepare_objects(effect, rows, actor, library=True)
        if phase != "pay":
            raise SemanticChoiceError("Unknown artifact exchange continuation")
        row, requirements = self._payment(effect, context.query, actor)
        if not context.query.cost_is_affordable(actor, requirements):
            settlement = self._settle(row, actor, False, requirements, None)
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                preparation_intents=settlement.intents,
                auto_continue=AutoContinue(
                    "Artifact exchange payment is unavailable", prepend_effects=settlement.prepend_effects,
                ),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Pay the generic mana difference or decline.",
                choice=ScalarChoice(field_name="pay", legal_values=(True, False)),
                public_context=FrozenMap({"stack": effect["stack"], "cost": requirements}),
            ),
            continuation_effect=FrozenMap(effect),
        )

    @staticmethod
    def _payment(effect, query, actor):
        row = query.object(effect["chosen"], zones=("library",))
        if row is None or row.owner != actor or row.logical_object_id != effect["chosen_incarnation"]                 or "artifact" not in row.types:
            raise SemanticChoiceError("The selected artifact lost its library incarnation")
        paid = PaidSacrificeManaValueSnapshot.from_dict(effect["paid"])
        difference = public_mana_value({"mana_value": row.mana_value}) - paid.mana_value
        requirements = {"GENERIC": difference}
        if difference <= 0 or dict(effect["_requirements"]) != requirements:
            raise SemanticChoiceError("Artifact exchange lost its exact mana difference")
        return row, requirements

    def _settle(self, row, actor, pay, requirements, pool_payment):
        intents = []
        if pay:
            intents.append(PayManaCostIntent(
                actor=actor, player=actor, requirements=FrozenMap(requirements),
                reason="Artifact search exchange", event_code="artifact.exchange.pay",
                message="Paid the artifact exchange mana difference.",
                details=FrozenMap({"cost": requirements}), pool_payment=pool_payment,
            ))
        intents.append(_locked_move(
            row, actor, "battlefield" if pay else "graveyard", "Artifact search exchange",
            optional_if_missing=pay,
        ))
        return SemanticChoiceCompletion(intents=tuple(intents), prepend_effects=(self._shuffle(actor),))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = effect["actor"]
        if effect["stack"] != continuation.stack_ref or actor != continuation.semantic_frame.controller:
            raise SemanticChoiceError("Artifact exchange lost its original resolution frame")
        phase = effect["phase"]
        if phase == "pay":
            row, requirements = self._payment(effect, query, actor)
            pay = response.get("pay", False)
            pool_payment = response.get(MANA_POOL_PAYMENT_FIELD)
            if type(pay) is not bool or (pool_payment is not None and (not pay or not isinstance(pool_payment, Mapping))):
                raise SemanticChoiceError("Invalid artifact exchange payment choice")
            if pay and not query.cost_is_affordable(actor, requirements):
                raise SemanticChoiceError("Artifact exchange payment is no longer payable")
            return self._settle(row, actor, pay, requirements, pool_payment)
        selected = response.get("card")
        if phase == "search" and not query.library_search_allowed(actor):
            raise SemanticChoiceError("Library search is prohibited")
        if phase == "search" and selected in (None, ""):
            return SemanticChoiceCompletion(prepend_effects=(self._shuffle(actor),))
        zone = {"sacrifice": "battlefield", "search": "library"}.get(phase)
        if zone is None or not isinstance(selected, str):
            raise SemanticChoiceError("Invalid artifact exchange selection")
        row = query.object(selected, zones=(zone,))
        if row is None or effect["_allowed"].get(selected) != row.logical_object_id or "artifact" not in row.types:
            raise SemanticChoiceError("Artifact exchange candidate is stale or ineligible")
        remaining = {key: value for key, value in effect.items() if key != "_allowed"}
        value = public_mana_value({"mana_value": row.mana_value})
        if phase == "sacrifice":
            if not _eligible_sacrifice(row, actor):
                raise SemanticChoiceError("The selected artifact cannot be sacrificed")
            paid = PaidSacrificeManaValueSnapshot(row.logical_object_id, value)
            return SemanticChoiceCompletion(
                intents=(_locked_move(row, actor, "graveyard", "Artifact search exchange", sacrifice=True),),
                prepend_effects=(FrozenMap({**remaining, "phase": "search", "paid": paid.to_dict()}),),
            )
        if row.owner != actor or selected not in query.library_search_refs(actor):
            raise SemanticChoiceError("Artifact exchange candidate is outside the permitted library scope")
        paid = PaidSacrificeManaValueSnapshot.from_dict(effect["paid"])
        if value <= paid.mana_value:
            return SemanticChoiceCompletion(
                intents=(_locked_move(row, actor, "battlefield", "Artifact search exchange"),),
                prepend_effects=(self._shuffle(actor),),
            )
        return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
            **remaining, "phase": "pay", "chosen": row.ref, "chosen_incarnation": row.logical_object_id,
            "_requirements": {"GENERIC": value - paid.mana_value},
        }),))
