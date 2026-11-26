from __future__ import annotations

"""Immutable fixed copy recipes; this module owns no game-state mutation."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .ability_fragments import GrantedTriggeredAbilitySpec
from .characteristic_evaluation import render_layered_type_line, type_parts
from .creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from .keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .fixed_keyword_entry_counters import FixedKeywordEntryCounterSpec
from .replacement.immutable import FrozenMap, thaw_value


class TokenCopyRecipeError(ValueError):
    """A copy instruction exceeds its explicitly represented vocabulary."""


def _unique_words(values: Any, *, allowed: set[str] | frozenset[str], label: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise TokenCopyRecipeError(f"{label} must be an array")
    result = tuple(values)
    if any(type(value) is not str or value not in allowed for value in result) or len(set(result)) != len(result):
        raise TokenCopyRecipeError(f"{label} has an unsupported or duplicate value")
    return result


RETAINED_LEGEND_PARTNER_VERSION = 6
SIGNED_COPY_BASE_STATS_VERSION = 7


def retained_legend_rule_fragment():
    from .legend_rule_model import LegendRuleExemptionSpec
    from .object_predicate import ObjectQuerySpec

    return LegendRuleExemptionSpec("controller", ObjectQuerySpec(zones=("battlefield",)))


@dataclass(frozen=True, slots=True)
class TokenCopyExceptionSpec:
    remove_legendary: bool = False
    add_card_types: tuple[str, ...] = ()
    add_subtypes: tuple[str, ...] = ()
    creature_subtypes: tuple[str, ...] | None = None
    colors: tuple[str, ...] | None = None
    power: int | None = None
    toughness: int | None = None
    add_keywords: tuple[str, ...] = ()
    add_ability_fragments: tuple[Mapping[str, Any], ...] = ()
    schema_version: int = 1
    remove_mana_cost: bool = False
    name: str | None = None
    card_types: tuple[str, ...] | None = None
    retain_source_stats: bool = False
    add_supertypes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4, 5, RETAINED_LEGEND_PARTNER_VERSION, SIGNED_COPY_BASE_STATS_VERSION} or type(self.remove_legendary) is not bool or type(self.remove_mana_cost) is not bool or self.remove_mana_cost and self.schema_version not in {2,3}:
            raise TokenCopyRecipeError("Copy exception version or legendary flag is malformed")
        if type(self.retain_source_stats) is not bool or self.name is not None and (type(self.name) is not str or not self.name):
            raise TokenCopyRecipeError('Copy retained name/stats fields are malformed')
        if self.card_types is not None:
            object.__setattr__(self,'card_types',_unique_words(self.card_types,
                allowed=frozenset({'artifact','creature'}),label='replacement copy types'))
            if self.card_types!=('artifact',):raise TokenCopyRecipeError('Replacement copy types currently retain only artifact')
        if self.schema_version!=3 and (self.name is not None or self.card_types is not None or self.retain_source_stats):
            raise TokenCopyRecipeError('Extended copy values require version three')
        if self.retain_source_stats and self.power is not None:raise TokenCopyRecipeError('Retained and fixed copy stats conflict')
        signed_stats = self.schema_version == SIGNED_COPY_BASE_STATS_VERSION
        if (self.power is None) != (self.toughness is None) or any(
            value is not None and (type(value) is not int or (value < 0 and not signed_stats))
            for value in (self.power, self.toughness)
        ):
            raise TokenCopyRecipeError("Copy base stats require their closed integer pair")
        if signed_stats and (self.power is None or self.remove_legendary or self.add_card_types or self.add_subtypes
                or self.creature_subtypes is not None or self.colors is not None or self.add_keywords
                or self.add_ability_fragments or self.add_supertypes):
            raise TokenCopyRecipeError("Signed copy stats require only a fixed base power and toughness pair")
        allowed_subtypes = CREATURE_SUBTYPES | {"food", "equipment", "vehicle"}
        if self.schema_version == 3:
            allowed_subtypes = allowed_subtypes | {"cave"}
        added_keyword_scope = {
            5: {"Vanishing"},
            RETAINED_LEGEND_PARTNER_VERSION: {"Partner"},
        }
        for field_name, allowed in (
            ("add_card_types", frozenset({"artifact", "creature", "enchantment"})),
            ("add_subtypes", allowed_subtypes),
            ("add_keywords", FIXED_CHARACTERISTIC_KEYWORDS | added_keyword_scope.get(self.schema_version, set())),
        ):
            object.__setattr__(self, field_name, _unique_words(getattr(self, field_name), allowed=allowed, label=field_name))
        if self.creature_subtypes is not None:
            subtypes = _unique_words(self.creature_subtypes, allowed=CREATURE_SUBTYPES, label="creature_subtypes")
            if not subtypes or any(canonical_creature_subtype(value) != value for value in subtypes):
                raise TokenCopyRecipeError("A creature subtype replacement must be canonical and nonempty")
            object.__setattr__(self, "creature_subtypes", subtypes)
        if self.colors is not None:
            colors = _unique_words(self.colors, allowed=frozenset("WUBRG"), label="colors")
            if colors != tuple(color for color in "WUBRG" if color in colors):
                raise TokenCopyRecipeError("Copy colors must use canonical order")
            object.__setattr__(self, "colors", colors)
        supertypes = _unique_words(self.add_supertypes, allowed={"legendary", "snow"}, label="add_supertypes")
        if (self.schema_version == 4 and (supertypes != ("legendary", "snow") or self.remove_legendary)
                or self.schema_version != 4 and supertypes):
            raise TokenCopyRecipeError("Legendary and snow copy values require version four")
        object.__setattr__(self, "add_supertypes", supertypes)
        from .ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_to_dict, canonical_ability_fragments
        maximum_fragments = 3 if self.schema_version == 5 else 2
        if not isinstance(self.add_ability_fragments, (list, tuple)) or len(self.add_ability_fragments) > maximum_fragments:
            raise TokenCopyRecipeError("Copy grants require a bounded fragment array")
        fragments = canonical_ability_fragments(self.add_ability_fragments)
        if self.schema_version == 4:
            from .characteristic_fragments import AllCreatureTypesCharacteristicDefinitionSpec

            if (self.add_card_types or self.add_subtypes or self.creature_subtypes is not None
                    or self.colors is not None or self.power is not None or self.add_keywords
                    or fragments not in ((), (AllCreatureTypesCharacteristicDefinitionSpec(),))):
                raise TokenCopyRecipeError("Legendary snow copy values only add their supertypes and changeling")
        if self.schema_version == RETAINED_LEGEND_PARTNER_VERSION and (
            self.remove_legendary or self.add_card_types or self.add_subtypes or self.creature_subtypes is not None
            or self.colors is not None or self.power is not None or self.add_keywords != ("Partner",)
            or fragments != (retained_legend_rule_fragment(),)
        ):
            raise TokenCopyRecipeError("The retained ability exception requires exactly Partner and the controller's legend-rule exemption")
        keyword_entries = tuple(fragment for fragment in fragments if isinstance(fragment, FixedKeywordEntryCounterSpec))
        if self.schema_version == 5:
            triggers = tuple(fragment for fragment in fragments if isinstance(fragment, GrantedTriggeredAbilitySpec))
            if (self.remove_legendary or self.add_card_types or self.add_subtypes or self.creature_subtypes is not None
                    or self.colors is not None or self.power is not None or self.add_keywords != ("Vanishing",)
                    or keyword_entries != (FixedKeywordEntryCounterSpec("vanishing", 3),)
                    or len(triggers) != 2 or {fragment.event for fragment in triggers} != {"step.begin", "counter.removed"}
                    or any(fragment.schema_version != 1 for fragment in triggers)
                    or len({fragment.semantic_key for fragment in triggers}) != 2):
                raise TokenCopyRecipeError("The Vanishing copy exception requires its fixed entry and two distinct triggers")
        elif keyword_entries:
            raise TokenCopyRecipeError("Copied keyword entry counters require version five")
        object.__setattr__(self, "add_ability_fragments", tuple(FrozenMap(ability_fragment_to_dict(fragment)) for fragment in fragments))

    def to_dict(self) -> dict[str, Any]:
        value={"schema_version": self.schema_version, "remove_legendary": self.remove_legendary,
            "add_card_types": list(self.add_card_types), "add_subtypes": list(self.add_subtypes),
            "creature_subtypes": list(self.creature_subtypes) if self.creature_subtypes is not None else None,
            "colors": list(self.colors) if self.colors is not None else None,
            "power": self.power, "toughness": self.toughness, "add_keywords": list(self.add_keywords),
            "add_ability_fragments": [thaw_value(fragment) for fragment in self.add_ability_fragments]}
        if self.schema_version in {2,3}:value['remove_mana_cost']=self.remove_mana_cost
        if self.schema_version==3:value.update(name=self.name,card_types=list(self.card_types) if self.card_types is not None else None,retain_source_stats=self.retain_source_stats)
        if self.schema_version == 4:
            value["add_supertypes"] = list(self.add_supertypes)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenCopyExceptionSpec":
        fields = {"schema_version", "remove_legendary", "add_card_types", "add_subtypes", "creature_subtypes", "colors", "power", "toughness", "add_keywords", "add_ability_fragments"}
        if isinstance(value,Mapping) and value.get('schema_version') in {2,3}:fields=fields|{'remove_mana_cost'}
        if isinstance(value,Mapping) and value.get('schema_version')==3:fields=fields|{'name','card_types','retain_source_stats'}
        if isinstance(value, Mapping) and value.get("schema_version") == 4:
            fields.add("add_supertypes")
        if not isinstance(value, Mapping) or set(value) != fields:
            raise TokenCopyRecipeError("Copy exceptions have a closed complete schema")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise TokenCopyRecipeError('Copy exception fields are malformed') from exc


GRAVEYARD_TARGET_TOKEN_COPY_VERSION = 6
EQUIPPED_TOKEN_COPY_VERSION = 7
POST_ENTRY_COPY_GRANT_VERSION = 8
NAMED_ADDITIVE_COPY_VERSION = 9
EQUIPPED_TOKEN_COPY_CAPABILITY = "token.creation.equipped_nonlegendary_haste_copy"
GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN = "graveyard_target"


def graveyard_target_token_copy_schema():
    from .rules.graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec

    return PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CREATURE_CARD, "you").to_target_schema()


@dataclass(frozen=True, slots=True)
class TokenCopyRecipeSpec:
    origin: str
    exception: TokenCopyExceptionSpec = TokenCopyExceptionSpec()
    cleanup: str = "none"
    temporary_keywords: tuple[str, ...] = ()
    keyword_duration: str = "zone_object"
    schema_version: int = 1
    creation_origin: str | None = None
    attachment_kind: str | None = None
    post_entry_grant: GrantedTriggeredAbilitySpec | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4, 5, GRAVEYARD_TARGET_TOKEN_COPY_VERSION, EQUIPPED_TOKEN_COPY_VERSION, POST_ENTRY_COPY_GRANT_VERSION, NAMED_ADDITIVE_COPY_VERSION}:
            raise TokenCopyRecipeError("Copy recipe version is unsupported")
        if (self.creation_origin is not None and (self.schema_version!=2 or self.creation_origin!='embalm')
                or self.schema_version==2 and self.creation_origin is None):raise TokenCopyRecipeError('Versioned copy creation origin is closed')
        attached = self.origin == "source_attachment"
        if (self.schema_version in {3, EQUIPPED_TOKEN_COPY_VERSION}) != attached:
            raise TokenCopyRecipeError("Source attachment requires an explicit attachment recipe")
        if self.schema_version == EQUIPPED_TOKEN_COPY_VERSION:
            if (self.attachment_kind != "equipped" or self.cleanup != "none"
                    or self.exception != TokenCopyExceptionSpec(remove_legendary=True)
                    or tuple(self.temporary_keywords) != ("Haste",) or self.keyword_duration != "zone_object"):
                raise TokenCopyRecipeError("Equipped copy requires its exact nonlegendary recipe and separate haste")
        elif attached:
            if (self.attachment_kind != "enchanted" or self.cleanup != "none"
                    or self.temporary_keywords or self.keyword_duration != "zone_object"):
                raise TokenCopyRecipeError("Enchanted copy recipe has one closed intrinsic lifecycle")
        elif self.attachment_kind is not None:
            raise TokenCopyRecipeError("Historical copy recipes cannot carry an attachment relation")
        if self.origin not in {"source", "target", "event_object", "source_attachment", "event_spell",
                               "cast_target", GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN} or not isinstance(self.exception, TokenCopyExceptionSpec):
            raise TokenCopyRecipeError("Copy reference origin or exception is unsupported")
        if self.exception.schema_version in {4, 5, RETAINED_LEGEND_PARTNER_VERSION}:
            raise TokenCopyRecipeError("These copy values require the entry copy owner")
        if self.exception.schema_version == SIGNED_COPY_BASE_STATS_VERSION:
            raise TokenCopyRecipeError("Signed copy values require a committed copy outcome")
        cast_origin = {"event_spell": 4, "cast_target": 5}.get(self.origin)
        if cast_origin is not None or self.schema_version in {4, 5}:
            if self.schema_version != cast_origin:
                raise TokenCopyRecipeError("Cast copy references require their own recipe version")
            if (self.exception != TokenCopyExceptionSpec(remove_legendary=True)
                    or self.cleanup != "none" or self.temporary_keywords or self.keyword_duration != "zone_object"):
                raise TokenCopyRecipeError("Cast copy represents direct nonlegendary token creation")
        if self.cleanup not in {"none", "sacrifice_next_end_step", "exile_next_end_step"}:
            raise TokenCopyRecipeError("Copy cleanup is outside the represented lifecycle")
        if self.keyword_duration not in {"zone_object", "until_end_of_turn"}:
            raise TokenCopyRecipeError("Copy keyword duration is unsupported")
        object.__setattr__(self, "temporary_keywords", _unique_words(self.temporary_keywords,
            allowed=FIXED_CHARACTERISTIC_KEYWORDS, label="temporary_keywords"))
        from_graveyard = self.origin == GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN
        if (self.schema_version == GRAVEYARD_TARGET_TOKEN_COPY_VERSION) != from_graveyard:
            raise TokenCopyRecipeError("Graveyard copy references require their own recipe version")
        if from_graveyard and (
            self.exception != TokenCopyExceptionSpec(add_card_types=("artifact",))
            or self.cleanup != "sacrifice_next_end_step" or self.temporary_keywords != ("Haste",)
            or self.keyword_duration != "zone_object"
        ):
            raise TokenCopyRecipeError("Graveyard token copy requires artifact, granted haste and next-end sacrifice")
        if self.schema_version == POST_ENTRY_COPY_GRANT_VERSION:
            grant = self.post_entry_grant
            if (self.origin != "target" or self.exception != TokenCopyExceptionSpec()
                    or self.cleanup != "sacrifice_next_end_step" or self.temporary_keywords != ("Haste",)
                    or self.keyword_duration != "zone_object" or not isinstance(grant, GrantedTriggeredAbilitySpec)
                    or grant.schema_version != 1 or grant.event != "permanent.graveyard.self"
                    or ":token:" not in grant.ability_id or not grant.semantic_key.endswith(f":{grant.ability_id}")):
                raise TokenCopyRecipeError("Post-entry copy grants require the closed target and death-trigger lifecycle")
        elif self.post_entry_grant is not None:
            raise TokenCopyRecipeError("Post-entry grant data requires its own recipe version")
        if self.source_bound_cleanup:
            exception = self.exception
            if (self.origin != "target" or self.cleanup != "sacrifice_next_end_step"
                    or self.temporary_keywords != ("Haste",) or self.keyword_duration != "until_end_of_turn"
                    or not exception.name or exception.power is None
                    or exception.add_card_types != ("artifact", "creature")
                    or len(exception.add_subtypes) != 1 or exception.add_subtypes[0] not in CREATURE_SUBTYPES
                    or exception != TokenCopyExceptionSpec(
                        schema_version=3, name=exception.name, power=exception.power,
                        toughness=exception.toughness, add_card_types=exception.add_card_types,
                        add_subtypes=exception.add_subtypes,
                    )):
                raise TokenCopyRecipeError("Named additive copy requires its fixed copy values and source-bound lifecycle")

    @property
    def source_bound_cleanup(self) -> bool:
        return self.schema_version == NAMED_ADDITIVE_COPY_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = {"schema_version": self.schema_version, "origin": self.origin,
                "exception": self.exception.to_dict(), "cleanup": self.cleanup,
                "temporary_keywords": list(self.temporary_keywords), "keyword_duration": self.keyword_duration,**({"creation_origin":self.creation_origin} if self.schema_version==2 else {}), **({"attachment_kind":self.attachment_kind, "attachment_reference":self.attachment_reference()} if self.schema_version in {3, EQUIPPED_TOKEN_COPY_VERSION} else {})}
        if self.post_entry_grant is not None:
            result["post_entry_grant"] = self.post_entry_grant.to_dict()
        return result

    def attachment_reference(self) -> dict[str, Any]:
        if self.schema_version not in {3, EQUIPPED_TOKEN_COPY_VERSION}:
            raise TokenCopyRecipeError("Only an attachment copy recipe carries an attachment reference")
        from .attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
        relation = AttachmentReferenceKind.EQUIPPED if self.attachment_kind == "equipped" else AttachmentReferenceKind.ENCHANTED
        return AttachmentReferenceSpec(relation, "creature", schema_version=2).to_dict()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenCopyRecipeSpec":
        fields={"schema_version", "origin", "exception", "cleanup", "temporary_keywords", "keyword_duration"}
        if isinstance(value,Mapping) and value.get("schema_version")==2:fields.add("creation_origin")
        if isinstance(value, Mapping) and type(value.get("schema_version")) is int and value["schema_version"] in {3, EQUIPPED_TOKEN_COPY_VERSION}:
            fields.update({"attachment_kind", "attachment_reference"})
        if isinstance(value, Mapping) and value.get("schema_version") == POST_ENTRY_COPY_GRANT_VERSION:
            fields.add("post_entry_grant")
        if not isinstance(value, Mapping) or set(value) != fields:
            raise TokenCopyRecipeError("Copy recipes have a closed complete schema")
        recipe = cls(schema_version=value["schema_version"], origin=value["origin"],
                     exception=TokenCopyExceptionSpec.from_dict(value["exception"]), cleanup=value["cleanup"],
                     temporary_keywords=value["temporary_keywords"], keyword_duration=value["keyword_duration"],
                     creation_origin=value.get("creation_origin"), attachment_kind=value.get("attachment_kind"),
                     post_entry_grant=(GrantedTriggeredAbilitySpec.from_dict(value["post_entry_grant"])
                                       if "post_entry_grant" in value else None))
        if recipe.schema_version in {3, EQUIPPED_TOKEN_COPY_VERSION}:
            from .attachment_references import AttachmentReferenceSpec

            reference = AttachmentReferenceSpec.from_dict(value["attachment_reference"])
            if reference.to_dict() != recipe.attachment_reference():
                raise TokenCopyRecipeError("Attached recipe requires its exact version-two creature reference")
        return recipe


OPTIONAL_CONTROLLED_TOKEN_COPY_CAPABILITY = "token.copy.optional_controlled_set"
OPTIONAL_CONTROLLED_TOKEN_COPY_VERSION = 2


def optional_controlled_token_copy_effect(*, player="$controller"):
    return {"op": "copy_all_tokens", "schema_version": OPTIONAL_CONTROLLED_TOKEN_COPY_VERSION, "player": player}


def validate_optional_controlled_token_copy(effect, *, actor="$controller"):
    if type(effect.get("schema_version")) is not int or dict(effect) != optional_controlled_token_copy_effect(player=actor):
        raise TokenCopyRecipeError("Optional controlled-token copy requires its exact original controller")


def optional_controlled_token_copy_runtime_dependencies(program):
    if not any(effect.get("op") == "copy_all_tokens" and "schema_version" in effect for effect in program.effects):
        return ()
    from .replacement.immutable import thaw_value

    condition = {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "beginning_combat"},
    ]}
    if (len(program.effects) != 2 or program.effects[0].get("op") != "create_token"
            or type(program.effects[0].get("quantity")) is not int or program.effects[0]["quantity"] != 1
            or program.effects[0].get("controller") != "$controller"
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.destination is not None or program.trigger_limit is not None or program.requires_arbiter
            or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or thaw_value(program.event_condition) != condition):
        raise TokenCopyRecipeError("Optional controlled-token copy requires the complete original combat trigger")
    validate_optional_controlled_token_copy(program.effects[1])
    return (OPTIONAL_CONTROLLED_TOKEN_COPY_CAPABILITY,)


TOKEN_COPY_BATCH_FIELD = "copy_batch"
TOKEN_COPY_BATCH_CAPABILITY = "token.creation.current_battlefield_copy_batch"
ANNOUNCED_OPPONENT_COPY_BATCH_CAPABILITY = "token.creation.announced_opponent_copy_batch"
ANNOUNCED_HASTY_COPY_BATCH_CAPABILITY = "token.creation.announced_hasty_copy_batch"
TOKEN_COPY_BATCH_MECHANIC = "current-battlefield-token-copy-batch"


class TokenCopyBatchKind(str, Enum):
    TARGET_PLAYER_CREATURES = "target_player_creatures"
    CONTROLLER_TOKENS = "controller_tokens"
    CONTROLLER_NONTOKEN_CREATURES = "controller_nontoken_creatures"
    CONTROLLER_ARTIFACT_CREATURES = "controller_artifact_creatures"
    ANNOUNCED_OPPONENT_CREATURES = "announced_opponent_creatures"
    ANNOUNCED_HASTY_COPIES = "announced_hasty_copies"


@dataclass(frozen=True, slots=True)
class TokenCopyBatchSpec:
    """One current public or announced creature set for a single creator."""

    kind: TokenCopyBatchKind
    schema_version: int = 1

    def __post_init__(self) -> None:
        version = {TokenCopyBatchKind.ANNOUNCED_OPPONENT_CREATURES: 2,
                   TokenCopyBatchKind.ANNOUNCED_HASTY_COPIES: 3}.get(self.kind, 1)
        if not isinstance(self.kind, TokenCopyBatchKind) or type(self.schema_version) is not int or self.schema_version != version:
            raise TokenCopyRecipeError("Token copy batch kind and version are closed")

    @property
    def uses_announced_models(self) -> bool:
        return self.kind in {TokenCopyBatchKind.ANNOUNCED_OPPONENT_CREATURES, TokenCopyBatchKind.ANNOUNCED_HASTY_COPIES}

    @property
    def targets_player(self) -> bool:
        return self.kind is TokenCopyBatchKind.TARGET_PLAYER_CREATURES

    @property
    def target_schema(self) -> dict[str, Any] | None:
        if self.uses_announced_models:
            from .target_cardinality import AnnouncedTargetCardinality

            relation = ({"controller_relation": "opponent", "distinct_controllers": True, "retain_controller": True}
                        if self.kind is TokenCopyBatchKind.ANNOUNCED_OPPONENT_CREATURES else {"controller_relation": "you"})
            return {
                "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
                **relation,
                "count_parameter": AnnouncedTargetCardinality(
                    selection="up_to", parameter="any_number", schema_version=2,
                ).to_dict(),
            }
        if self.targets_player:
            return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
        return None

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        from .fixed_token_production import FIXED_TOKEN_COPY_CAPABILITY_ID

        result = (TOKEN_COPY_BATCH_CAPABILITY, FIXED_TOKEN_COPY_CAPABILITY_ID)
        if self.uses_announced_models:
            from .target_cardinality import ANY_NUMBER_TARGET_CAPABILITY

            if self.kind is TokenCopyBatchKind.ANNOUNCED_HASTY_COPIES:
                return (*result, ANNOUNCED_HASTY_COPY_BATCH_CAPABILITY, ANY_NUMBER_TARGET_CAPABILITY,
                        "target.revalidate_resolution", "activation.tap_untap_cost.haste", "combat.attack.haste",
                        "trigger.delayed.fixed_object_action", "zone.move.fixed_public_set")
            return (*result, ANNOUNCED_OPPONENT_COPY_BATCH_CAPABILITY, ANY_NUMBER_TARGET_CAPABILITY,
                    "target.set.one_per_player", "target.revalidate_resolution")
        return (*result, "target.revalidate_resolution") if self.targets_player else result

    @property
    def exception(self) -> TokenCopyExceptionSpec:
        if self.kind is TokenCopyBatchKind.ANNOUNCED_HASTY_COPIES:
            return TokenCopyExceptionSpec(add_keywords=("Haste",))
        return TokenCopyExceptionSpec(remove_legendary=self.kind is TokenCopyBatchKind.CONTROLLER_NONTOKEN_CREATURES)

    def model_query(self, player: str):
        from .object_predicate import ObjectQuerySpec

        if self.uses_announced_models:
            raise TokenCopyRecipeError("Announced copy models require their revalidated target slots")
        if self.kind is TokenCopyBatchKind.CONTROLLER_ARTIFACT_CREATURES:
            return ObjectQuerySpec(zones=("battlefield",), controller=player, types_all=("artifact", "creature"))
        if self.kind is TokenCopyBatchKind.CONTROLLER_TOKENS:
            return ObjectQuerySpec(zones=("battlefield",), controller=player, token=True)
        return ObjectQuerySpec(zones=("battlefield",), controller=player, types_all=("creature",),
                               token=None if self.targets_player else False)

    def to_effect(self) -> dict[str, Any]:
        return {"op": "create_token_batch", "controller": "$controller",
                TOKEN_COPY_BATCH_FIELD: {"schema_version": self.schema_version, "kind": self.kind.value}}

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any], *, actor: str | None = None) -> "TokenCopyBatchSpec":
        required = {"op", "controller", TOKEN_COPY_BATCH_FIELD}
        allowed = required if actor is None else required | {"_runtime_source", "_replacement_selections", "reason"}
        if (not isinstance(effect, Mapping) or not required <= set(effect) <= allowed
                or effect["op"] != "create_token_batch"
                or effect["controller"] != ("$controller" if actor is None else actor)):
            raise TokenCopyRecipeError("Token copy batch requires a closed creation instruction and its controller")
        descriptor = effect[TOKEN_COPY_BATCH_FIELD]
        if not isinstance(descriptor, Mapping) or set(descriptor) != {"schema_version", "kind"}:
            raise TokenCopyRecipeError("Token copy batch requires a complete versioned model selection")
        try:
            kind = TokenCopyBatchKind(descriptor["kind"])
        except (TypeError, ValueError) as exc:
            raise TokenCopyRecipeError("Token copy batch model selection is unknown") from exc
        return cls(kind, descriptor["schema_version"])


def apply_copy_exception(characteristics: Mapping[str, Any], exception: TokenCopyExceptionSpec) -> dict[str, Any]:
    """Apply CR 707.9 values before creation; never copy status or counters."""
    result = deepcopy(dict(characteristics))
    if exception.retain_source_stats:raise TokenCopyRecipeError('Entry source stats must be resolved before applying a copy exception')
    if exception.name is not None:result['name']=exception.name
    if exception.remove_mana_cost:
        result['mana_cost']=''
        result['mana_value']=0
    card_types, subtypes, supertypes = type_parts(str(result.get("type_line") or ""))
    if exception.remove_legendary:
        supertypes.discard("legendary")
    if exception.card_types is not None:
        card_types=set(exception.card_types)
        # These replacements retain only artifact subtypes. Creature, land,
        # enchantment and planeswalker subtypes cannot survive without their type.
        from .copy_value_types import ARTIFACT_SUBTYPES
        subtypes.intersection_update(ARTIFACT_SUBTYPES if 'artifact' in card_types else ())
    card_types.update(exception.add_card_types)
    if exception.creature_subtypes is not None:
        subtypes.difference_update(CREATURE_SUBTYPES)
        subtypes.update(exception.creature_subtypes)
    subtypes.update(value for value in exception.add_subtypes if value != "cave" or "land" in card_types)
    if exception.add_supertypes:
        supertypes.update(exception.add_supertypes)
        result["type_line"] = render_layered_type_line({
            "card_types": card_types, "subtypes": sorted(subtypes), "supertypes": supertypes,
        })
    elif exception.card_types is not None or exception.remove_legendary or exception.add_card_types or exception.add_subtypes or exception.creature_subtypes is not None:
        ordered_types = [value for value in ("kindred", "artifact", "battle", "creature", "enchantment", "land", "planeswalker") if value in card_types]
        left = " ".join(value.title() for value in (*sorted(supertypes), *ordered_types))
        result["type_line"] = left + (" — " + " ".join(value.title() for value in sorted(subtypes)) if subtypes else "")
    if exception.power is not None:
        result["power"], result["toughness"] = str(exception.power), str(exception.toughness)
    if exception.colors is not None:
        result["colors"] = list(exception.colors)
        result.pop("color_indicator", None)
    fragments = result.get("ability_fragments", ())
    if not isinstance(fragments, (list, tuple)) or any(not isinstance(value, Mapping) for value in fragments):
        raise TokenCopyRecipeError("Copied ability fragments are unavailable")
    result["ability_fragments"] = [deepcopy(dict(fragment)) for fragment in fragments if not (
        exception.power is not None and fragment.get("kind") in {"query_power_toughness_definition","dynamic_power_toughness"}
        or exception.colors is not None and fragment.get("kind") == "colorless_characteristic_definition"
        or (exception.creature_subtypes is not None or exception.card_types is not None) and fragment.get("kind") == "all_creature_types_characteristic_definition"
    )]
    result["ability_fragments"].extend(thaw_value(fragment) for fragment in exception.add_ability_fragments)
    keywords = list(result.get("keywords", ()))
    if exception.colors is not None:
        keywords = [value for value in keywords if str(value).casefold() != "devoid"]
    if exception.creature_subtypes is not None or exception.card_types is not None:
        keywords = [value for value in keywords if str(value).casefold() != "changeling"]
    if (exception.schema_version == 4 and exception.add_ability_fragments
            and not any(str(value).casefold() == "changeling" for value in keywords)):
        keywords.append("Changeling")
    result["keywords"] = list(dict.fromkeys((*keywords, *exception.add_keywords)))
    from .shared_type_doors import SHARED_TYPE_COPY_EXCEPTIONS, SHARED_TYPE_COPY_FIELD
    if SHARED_TYPE_COPY_FIELD in result:
        previous = result.get(SHARED_TYPE_COPY_EXCEPTIONS, ())
        if not isinstance(previous, (list, tuple)):
            raise TokenCopyRecipeError("Shared type copy exceptions must be an array")
        result[SHARED_TYPE_COPY_EXCEPTIONS] = [*deepcopy(previous), exception.to_dict()]
    from .defined_token_faces import DEFINED_TOKEN_FACES_FIELD, DEFINED_TOKEN_COPY_EXCEPTIONS_FIELD
    if DEFINED_TOKEN_FACES_FIELD in result:
        previous = result.get(DEFINED_TOKEN_COPY_EXCEPTIONS_FIELD, ())
        if not isinstance(previous, (list, tuple)):
            raise TokenCopyRecipeError('Defined token copy exceptions must be an array')
        result[DEFINED_TOKEN_COPY_EXCEPTIONS_FIELD] = [*deepcopy(previous), exception.to_dict()]
    return result


def executable_copy_effects(effects: Sequence[Mapping[str, Any]]):
    """Visit executable wrappers, never copied future ability metadata."""
    from .resolution_conditions import CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION
    for effect in effects:
        if effect.get("op") == "create_token" and "copy_spec" in effect:
            yield effect
        for field in ("effects", "then_effects"):
            children = effect.get(field)
            if effect.get("op") in {"offer_optional_effect", "offer_optional_mana_payment"} and isinstance(children, (list, tuple)):
                yield from executable_copy_effects(children)
        if (effect.get("op") == "apply_if_public_condition"
                and type(effect.get("schema_version")) is int
                and effect["schema_version"] == CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION):
            for field in ("effects", "otherwise_effects"):
                children = effect.get(field)
                if isinstance(children, (list, tuple)):
                    yield from executable_copy_effects(children)


def copy_recipes(effects: Sequence[Mapping[str, Any]]) -> tuple[TokenCopyRecipeSpec, ...]:
    return tuple(TokenCopyRecipeSpec.from_dict(effect['copy_spec']) for effect in executable_copy_effects(effects))
