from __future__ import annotations

"""Full-consumption token-copy grammar over the shared target and copy model."""

from dataclasses import replace
import re

from ..creature_subtypes import canonical_creature_subtype
from ..fixed_token_production import FIXED_TOKEN_PRODUCTION_MECHANIC_ID, FixedTokenCreationTemplate
from ..rules.source_references import SourceReferenceSpec
from ..token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec
from .direct_target import direct_permanent_target_spec
from .fixed_numbers import fixed_number


_COPY = re.compile(r"^Create (?P<count>a|one|two|three|four|five|[1-5]) (?P<tapped>tapped )?tokens? that(?:'s| are) (?:a copy|copies) of (?P<body>.+?)\.?$", re.I)
_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": ""}


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
    addition = re.fullmatch(r"(?:an? )?(?P<description>.+?) in addition to (?:its|their) other types", value, re.I)
    if addition is not None:
        description = addition["description"].casefold()
        if description in {"artifact", "enchantment"}:
            return replace(exception, add_card_types=(description,))
        additive_base = re.fullmatch(r"(?P<power>\d+)/(?P<toughness>\d+) (?:(?P<color>white|blue|black|red|green) )?(?P<subtype>[a-z'-]+)(?: (?P<cardtype>artifact|enchantment))?(?: creature)?", description)
        if additive_base:
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


def token_copy_recipe_template(text: str, *, source_name: str,
                               source_is_permanent: bool | None = None,
                               event: str | None = None) -> FixedTokenCreationTemplate | None:
    normalized = " ".join(text.strip().split())
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
    exception = _fixed_exception(parts[1] if len(parts) == 2 else "")
    if exception is None:
        return None
    schema = None
    if subject.casefold().startswith(("target ", "another target ")):
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
    elif subject.casefold() in {"that creature", "that artifact", "that permanent"} and event in {
        "permanent.enter", "artifact.enter", "creature.enter", "creature.dies", "permanent.graveyard", "permanent.leave",
    }:
        origin = "event_object"
    else:
        return None
    recipe = TokenCopyRecipeSpec(origin=origin, exception=exception, cleanup=cleanup,
                                 temporary_keywords=temporary_keywords, keyword_duration=keyword_duration)
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
