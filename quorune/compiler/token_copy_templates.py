from __future__ import annotations

"""Full-consumption token-copy grammar over the shared target and copy model."""

from dataclasses import replace
from . import regex_working_set as re

from ..creature_subtypes import canonical_creature_subtype
from ..fixed_token_production import FIXED_TOKEN_PRODUCTION_MECHANIC_ID, FixedTokenCreationTemplate
from ..rules.source_references import SourceReferenceSpec
from ..token_copy_recipes import (
    GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN, GRAVEYARD_TARGET_TOKEN_COPY_VERSION, NAMED_ADDITIVE_COPY_VERSION,
    TokenCopyExceptionSpec, TokenCopyRecipeSpec, graveyard_target_token_copy_schema,
)
from ..token_copy_recipient_model import OTHER_NONTOKEN_CREATURE_ENTRY
from .direct_target import direct_permanent_target_spec
from .fixed_numbers import fixed_number


_COPY = re.compile(r"^Create (?P<count>a|one|two|three|four|five|[1-5]) (?P<tapped>tapped )?tokens? that(?:'s| are) (?:a copy|copies) of (?P<body>.+?)\.?$", re.I)
_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": ""}
_NAMED_ADDITIVE_COPY = re.compile(
    r"^its name is (?P<name>[A-Za-z][A-Za-z0-9'\u2019-]*(?: [A-Za-z0-9'\u2019-]+)*) and "
    r"(?P<addition>it's a [0-9]+/[0-9]+ [A-Za-z'-]+ artifact creature in addition to its other types)$", re.I,
)
_NONCREATURE_ARTIFACT_COPY_SUBJECT = "target noncreature artifact you control"


def _fixed_exception(text: str) -> TokenCopyExceptionSpec | None:
    value = text.strip().rstrip(".")
    keyword_tail = re.search(r" and (?:it has|the token has) (?P<keywords>flying(?: and haste)?|haste)$", value, re.I)
    keywords = tuple(word.title() for word in keyword_tail["keywords"].casefold().split(" and ")) if keyword_tail else ()
    if keyword_tail:
        value = value[:keyword_tail.start()]
    if not value:
        return TokenCopyExceptionSpec()
    remove = re.match(r"^(?:it's not|it isn't|they aren't|they're not|the token isn't|the token is not|the tokens aren't) legendary(?: and )?", value, re.I)
    exception = TokenCopyExceptionSpec(remove_legendary=remove is not None, add_keywords=keywords)
    if remove is not None:
        value = value[remove.end():].strip()
        if not value:
            return exception
    value = re.sub(r"^(?:it's|it is|the token is) ", "", value, flags=re.I)
    addition = re.fullmatch(r"(?:an? )?(?P<description>.+?) in addition to (?:its|their) other (?P<creature_only>creature )?types", value, re.I)
    if addition is not None:
        description = addition["description"].casefold()
        if description in {"artifact", "enchantment"}:
            if addition['creature_only']:return None
            return replace(exception, add_card_types=(description,))
        typed = re.fullmatch(r"(?:(?P<subtypes>[a-z'-]+(?: [a-z'-]+)*) )?(?P<cardtype>artifact|enchantment) creature",description)
        if typed is not None:
            if addition['creature_only']:return None
            words=(typed['subtypes'] or '').split()
            subtypes=tuple(canonical_creature_subtype(word) for word in words)
            if any(value is None for value in subtypes) or len(set(subtypes))!=len(subtypes):return None
            return replace(exception,add_card_types=(typed['cardtype'],'creature'),add_subtypes=subtypes)
        additive_base = re.fullmatch(r"(?P<power>\d+)/(?P<toughness>\d+) (?:(?P<color>white|blue|black|red|green) )?(?P<subtype>[a-z'-]+)(?: (?P<cardtype>artifact|enchantment))?(?: creature)?", description)
        if additive_base:
            if addition['creature_only'] and additive_base['cardtype']:return None
            subtype = canonical_creature_subtype(additive_base["subtype"])
            if subtype is None:
                return None
            return replace(exception, power=int(additive_base["power"]), toughness=int(additive_base["toughness"]),
                colors=(_COLORS[additive_base["color"]],) if additive_base["color"] else None,
                add_card_types=tuple(value for value in (additive_base["cardtype"], "creature") if value), add_subtypes=(subtype,))
        words = description.split()
        subtypes = tuple(canonical_creature_subtype(word) for word in words)
        if words and all(subtypes):
            return replace(exception, add_subtypes=subtypes)
        return None
    haste = re.fullmatch(r"(?:it has|the token has) (?P<keywords>flying|haste)(?: and (?:it isn't|it's not) legendary)?", value, re.I)
    if haste is not None:
        return replace(exception, add_keywords=(haste["keywords"].title(),), remove_legendary=exception.remove_legendary or "legendary" in value)
    base = re.fullmatch(r"(?:a )?(?P<power>\d+)/(?P<toughness>\d+)(?: (?P<color>white|blue|black|red|green|colorless))?(?: (?P<subtype>[A-Za-z][A-Za-z'-]*))?", value, re.I)
    if base is None:
        return None
    subtype = canonical_creature_subtype(base["subtype"]) if base["subtype"] else None
    if base["subtype"] and subtype is None:
        return None
    color = _COLORS.get(base["color"].casefold()) if base["color"] else None
    return replace(exception, power=int(base["power"]), toughness=int(base["toughness"]),
        colors=(tuple(color) if color is not None else None),
        creature_subtypes=(subtype,) if subtype is not None else None)


def _token_copy_batch_template(text: str) -> FixedTokenCreationTemplate | None:
    from ..token_copy_recipes import TOKEN_COPY_BATCH_MECHANIC, TokenCopyBatchKind, TokenCopyBatchSpec

    kinds = {
        "for each creature target player controls, create a token that's a copy of that creature.":
            TokenCopyBatchKind.TARGET_PLAYER_CREATURES,
        "for each token you control, create a token that's a copy of that permanent.":
            TokenCopyBatchKind.CONTROLLER_TOKENS,
        "for each nontoken creature you control, create a token that's a copy of that creature, except it isn't legendary.":
            TokenCopyBatchKind.CONTROLLER_NONTOKEN_CREATURES,
        ("choose each artifact creature you control. for each creature chosen this way, "
         "create a token that's a copy of it."):
            TokenCopyBatchKind.CONTROLLER_ARTIFACT_CREATURES,
        "for each opponent, you create a token that's a copy of up to one target creature that player controls.":
            TokenCopyBatchKind.ANNOUNCED_OPPONENT_CREATURES,
        ("choose any number of target creatures you control. for each of them, create a token that's a copy of "
         "that creature, except it has haste. exile those tokens at the beginning of the next end step."):
            TokenCopyBatchKind.ANNOUNCED_HASTY_COPIES,
    }
    kind = kinds.get(text.casefold())
    if kind is None:
        return None
    version = {TokenCopyBatchKind.ANNOUNCED_OPPONENT_CREATURES: 2,
               TokenCopyBatchKind.ANNOUNCED_HASTY_COPIES: 3}.get(kind, 1)
    spec = TokenCopyBatchSpec(kind, schema_version=version)
    mechanics = (TOKEN_COPY_BATCH_MECHANIC, FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy",
                 "cr-111-tokens", "cr-707-copying-objects")
    if spec.uses_announced_models:
        from .any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
        from .per_player_target_sets import PER_PLAYER_TARGET_MECHANIC

        mechanics = (*mechanics, "cr-115-targets", ANY_NUMBER_TARGET_MECHANIC)
        if kind is TokenCopyBatchKind.ANNOUNCED_OPPONENT_CREATURES:
            mechanics = (*mechanics, PER_PLAYER_TARGET_MECHANIC)
    elif spec.targets_player:
        mechanics = (*mechanics, "cr-115-targets")
    return FixedTokenCreationTemplate(
        template_id="create-current-battlefield-copy-batch-v1", effect=spec.to_effect(),
        mechanics=mechanics, target_schema=spec.target_schema,
    )


def chosen_entry_token_copy_trigger_template(binding, body: str) -> FixedTokenCreationTemplate | None:
    from ..controller_creature_keyword_absence import is_controller_end_step_schedule
    from ..token_copy_recipes import (
        CHOSEN_ENTRY_TOKEN_COPY_TEMPLATE, TOKEN_COPY_BATCH_MECHANIC, TokenCopyBatchKind, TokenCopyBatchSpec,
    )

    if ((binding.event.value, binding.active_zone) != ("step.begin", "battlefield")
            or not is_controller_end_step_schedule(binding.event_condition)
            or " ".join(body.casefold().split()) !=
            "for each token you control of the chosen type that entered this turn, create a token that's a copy of it."):
        return None
    spec = TokenCopyBatchSpec(TokenCopyBatchKind.CONTROLLER_ENTERED_CHOSEN_TOKENS, schema_version=4)
    return FixedTokenCreationTemplate(
        template_id=CHOSEN_ENTRY_TOKEN_COPY_TEMPLATE, effect=spec.to_effect(), target_schema=None,
        mechanics=(TOKEN_COPY_BATCH_MECHANIC, FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
                   "fixed-token-copy", "cr-111-tokens", "cr-707-copying-objects"),
    )


def _occurrence_controller_copy_template(text: str) -> FixedTokenCreationTemplate | None:
    if text.casefold() != "that creature's controller creates a token that's a copy of that creature.":
        return None
    from ..token_copy_recipient_model import OCCURRENCE_COPY_RECIPIENT_MECHANIC
    from ..rules.token_copy_recipients import occurrence_controller_copy_effect

    return FixedTokenCreationTemplate(
        template_id="create-occurrence-controller-copy-v1", effect=occurrence_controller_copy_effect(),
        mechanics=(OCCURRENCE_COPY_RECIPIENT_MECHANIC, FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
                   "fixed-token-copy", "cr-111-tokens", "cr-707-copying-objects"),
    )


def token_copy_recipe_template(text: str, *, source_name: str,
                               source_is_permanent: bool | None = None,
                               event: str | None = None,
                               event_variant: str | None = None,
                               source_attachment_relation=None) -> FixedTokenCreationTemplate | None:
    normalized = " ".join(text.strip().split())
    if normalized.casefold() == (
        "choose target artifact creature you control. for each creature chosen this way, "
        "create a token that's a copy of it."
    ):
        normalized = "Create a token that's a copy of target artifact creature you control."
    if event == "creature.enter" and event_variant == OTHER_NONTOKEN_CREATURE_ENTRY:
        occurrence = _occurrence_controller_copy_template(normalized)
        if occurrence is not None:
            return occurrence
    batch = _token_copy_batch_template(normalized)
    if batch is not None:
        return batch
    recipient = _target_player_copy_template(normalized, source_name=source_name)
    if recipient is not None:return recipient
    if normalized.casefold().rstrip(".") == (
        "populate. the token created this way gains haste. sacrifice it at the beginning of the next end step"
    ):
        from ..fixed_token_production import source_bound_populate_effect

        if event != "step.begin":
            return None
        return FixedTokenCreationTemplate(template_id="populate-source-bound-haste-and-sacrifice-v1",
            effect=source_bound_populate_effect(), target_schema=None,
            mechanics=(FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy", "cr-111-tokens",
                       "cr-707-copying-objects", "haste"))
    if normalized.casefold().rstrip(".") == "populate":
        return FixedTokenCreationTemplate(template_id="populate-copiable-creature-token-v1",
            effect={"op":"populate"}, target_schema=None,
            mechanics=(FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy", "cr-111-tokens", "cr-707-copying-objects"))
    cleanup = "none"
    temporary_keywords: tuple[str, ...] = ()
    keyword_duration = "zone_object"
    cleanup_match = re.search(r"\. (?P<move>Exile|Sacrifice) (?:it|them|that token|those tokens) at the beginning of the next end step\.?$", normalized, re.I)
    if cleanup_match is not None:
        cleanup = cleanup_match["move"].casefold() + "_next_end_step"
        normalized = normalized[:cleanup_match.start()].rstrip(".") + "."
    haste = re.search(r"\. (?:It|They|That token|Those tokens) gains? haste(?P<duration> until end of turn)?\.?$", normalized, re.I)
    if haste is not None:
        temporary_keywords = ("Haste",)
        keyword_duration = "until_end_of_turn" if haste["duration"] else "zone_object"
        normalized = normalized[:haste.start()].rstrip(".") + "."
    match = _COPY.fullmatch(normalized)
    if match is None:
        return None
    body = match["body"].rstrip(".")
    parts = re.split(r", except ", body, maxsplit=1, flags=re.I)
    subject = parts[0]
    exception_text = parts[1] if len(parts) == 2 else ""
    named = _NAMED_ADDITIVE_COPY.fullmatch(exception_text)
    if named is not None and (
        subject.casefold() != _NONCREATURE_ARTIFACT_COPY_SUBJECT
        or fixed_number(match["count"]) != 1 or match["tapped"]
        or cleanup != "sacrifice_next_end_step" or temporary_keywords != ("Haste",)
        or keyword_duration != "until_end_of_turn"
    ):
        return None
    exception = _fixed_exception(named["addition"] if named is not None else exception_text)
    if exception is None:
        return None
    if named is not None:
        exception = replace(exception, schema_version=3, name=named["name"])
    schema = None
    if subject.casefold() == "target creature card in your graveyard":
        if (fixed_number(match["count"]) != 1 or match["tapped"]
                or exception != TokenCopyExceptionSpec(add_card_types=("artifact",))
                or cleanup != "sacrifice_next_end_step" or temporary_keywords != ("Haste",)
                or keyword_duration != "zone_object"):
            return None
        schema = graveyard_target_token_copy_schema()
        origin = GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN
    elif subject.casefold().startswith(("target ", "another target ")):
        target = direct_permanent_target_spec(subject)
        if target is None:
            return None
        schema = target.to_target_schema()
        origin = "target"
    elif (re.fullmatch(r"this (?:creature|artifact|permanent|Equipment)", subject, re.I)
          or SourceReferenceSpec(source_name).matches(subject)):
        if source_is_permanent is not True:
            return None
        origin = "source"
    elif subject.casefold() == "equipped creature":
        from ..attachment_references import AttachmentReferenceKind
        if (source_is_permanent is not True or source_attachment_relation is not AttachmentReferenceKind.EQUIPPED
                or fixed_number(match["count"]) != 1 or match["tapped"]
                or exception != TokenCopyExceptionSpec(remove_legendary=True) or cleanup != "none"
                or temporary_keywords != ("Haste",) or keyword_duration != "zone_object"):
            return None
        origin = "source_attachment"
    elif subject.casefold() == "enchanted creature":
        from ..attachment_references import AttachmentReferenceKind
        if (source_is_permanent is not True
                or source_attachment_relation is not AttachmentReferenceKind.ENCHANTED
                or cleanup != "none" or temporary_keywords):
            return None
        origin = "source_attachment"
    elif subject.casefold() in {"that creature", "that artifact", "that permanent", "it"} and event in {
        "permanent.enter", "artifact.enter", "creature.enter", "creature.dies", "permanent.graveyard", "permanent.leave",
    }:
        origin = "event_object"
    else:
        return None
    from ..token_copy_recipes import EQUIPPED_TOKEN_COPY_VERSION

    attachment_version = EQUIPPED_TOKEN_COPY_VERSION if subject.casefold() == "equipped creature" else 3
    attachment_kind = "equipped" if attachment_version == EQUIPPED_TOKEN_COPY_VERSION else "enchanted"
    recipe = TokenCopyRecipeSpec(origin=origin, exception=exception, cleanup=cleanup,
                                 temporary_keywords=temporary_keywords, keyword_duration=keyword_duration,
                                 **({"schema_version": attachment_version, "attachment_kind": attachment_kind}
                                    if origin == "source_attachment"
                                    else {"schema_version": GRAVEYARD_TARGET_TOKEN_COPY_VERSION}
                                    if origin == GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN
                                    else {"schema_version": NAMED_ADDITIVE_COPY_VERSION} if named is not None else {}))
    mechanics = {FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy", "cr-111-tokens", "cr-707-copying-objects"}
    if schema is not None:
        mechanics.add("cr-115-targets")
    if cleanup != "none":
        mechanics.add("cr-603-handling-triggered-abilities")
    mechanics.update(keyword.casefold() for keyword in (*exception.add_keywords, *temporary_keywords))
    return FixedTokenCreationTemplate(
        template_id="create-copiable-token-recipe-v1",
        effect={"op": "create_token", "controller": "$controller", "quantity": fixed_number(match["count"]),
                "copy_spec": recipe.to_dict(), **({"tapped": True} if match["tapped"] else {})},
        target_schema=schema, mechanics=tuple(sorted(mechanics)),
    )


def _target_player_copy_template(text, *, source_name):
    match = re.fullmatch(r"Target player creates (?P<body>a token that's a copy of target creature you control)\.", text, re.I)
    if match is None:return None
    from ..token_copy_recipient_model import COPY_RECIPIENT_FIELD, COPY_RECIPIENT_MECHANIC, TokenCopyRecipientBinding
    from ..rules.token_copy_recipients import COPY_TARGET_SCHEMA
    from copy import deepcopy
    base = token_copy_recipe_template('Create ' + match['body'] + '.', source_name=source_name)
    if base is None:return None
    return FixedTokenCreationTemplate(template_id='target-player-creature-copy-v1',
        effect={**base.effect, 'controller': '$target.0', COPY_RECIPIENT_FIELD: TokenCopyRecipientBinding().to_dict()},
        target_schema=deepcopy(COPY_TARGET_SCHEMA), mechanics=(*base.mechanics, COPY_RECIPIENT_MECHANIC))
