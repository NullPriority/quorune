"""Bind a closed literal continuation to actual destruction results."""
from __future__ import annotations

from . import regex_working_set as re

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..rules.announced_destruction_controller_tokens import (
    AnnouncedDestructionControllerTokens, announced_destruction_controller_token_dependencies,
)
from ..rules.artifact_destruction_treasure import ArtifactDestructionTreasure, artifact_destruction_treasure_dependencies
from ..rules.destruction_result_model import DESTRUCTION_RESULT_MECHANIC, DestructionFollowup
from ..rules.destruction_sized_token import DESTRUCTION_SIZED_TOKEN_SCOPE, DESTRUCTION_SIZED_TOKEN_VERSION
from ..object_predicate import ObjectQuerySpec
from ..rules.target_player_destruction_death_tokens import (
    TARGET_PLAYER_DEATH_TOKEN_SCOPE, TARGET_PLAYER_DEATH_TOKEN_VERSION,
)
from .closed_effect_programs import CLOSED_EFFECT_PROGRAM_MECHANIC

_SUBJECT = r"(?P<subject>(?:nontoken )?(?:permanent|creature|enchantment)(?: you controlled that was)?)"
_TERMINAL_COUNT = re.compile(
    r"(?P<effect>(?:Then )?(?:(?:You )?draw a card|create a [^.]+? token|"
    r"You (?:gain|lose) [12] life|put a \+1/\+1 counter on this creature))"
    rf" for each {_SUBJECT} destroyed this way\.$", re.I,
)
_CONTROLLED_LIFE = re.compile(
    r"You gain life equal to the number of (?P<subject>creatures you controlled that were) destroyed this way\.$", re.I,
)
_PREFIXED_TOKEN = re.compile(
    rf"For each {_SUBJECT} destroyed this way, you (?P<effect>create a [^.]+? token)\.$", re.I,
)


_CONTROLLER_TOKEN = re.compile(
    r"For each (?P<subject>permanent|creature) destroyed this way, its controller creates "
    r"(?P<token>a [^.]+? creature token(?: with [^.]+)?)\.$", re.I,
)


def _controller_token_followup(destruction, token, *, subject, target_schema):
    if target_schema is not None:
        raise ValueError("Controller token results require an untargeted mass destruction")
    scope = AffectedPermanentSetSpec.from_dict(destruction["set"])
    if (subject == "creature") != (scope.query.types_all == ("creature",)):
        raise ValueError("Controller tokens require the same destroyed object kind")
    return DestructionFollowup(scope, token, schema_version=2)


def _literal_followup(text):
    if match := _TERMINAL_COUNT.search(text):
        return match, match["effect"] + "."
    if match := _CONTROLLED_LIFE.search(text):
        return match, "You gain 1 life."
    if match := _PREFIXED_TOKEN.search(text):
        body = match["effect"]
        return match, body[0].upper() + body[1:] + "."
    return None


def committed_black_green_mana_template(text, *, compile_literal):
    match = re.fullmatch(
        r"(?P<destroy>Destroy each nonland permanent with mana value 2 or less\.) "
        r"Add \{B\} or \{G\} for each permanent destroyed this way\.",
        " ".join(text.split()), re.I,
    )
    if match is None:
        return None
    template, effects, target_schema, mechanics = compile_literal(match["destroy"])
    if template is None or target_schema is not None or len(effects) != 1 or effects[0].get("op") != "destroy_all":
        return None
    followup = DestructionFollowup.black_green_mana()
    try:
        followup.validate_destruction_set(AffectedPermanentSetSpec.from_dict(effects[0]["set"]))
    except (KeyError, TypeError, ValueError):
        return None
    return ("destroy-fixed-set-mana-allocation-v3", ({**effects[0], "followup": followup.to_dict()},),
            None, (*mechanics, DESTRUCTION_RESULT_MECHANIC))


_TARGET_PLAYER_DEATH_TOKENS = re.compile(
    r"(?P<destroy>Destroy all creatures target player controls\.) "
    r"For each creature that died this way, that player creates (?P<tokens>two [^.]+ creature tokens(?: with [^.]+)?)\.",
    re.I,
)


def target_player_death_token_template(text, *, compile_literal):
    match = _TARGET_PLAYER_DEATH_TOKENS.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    _, destroy, schema, destroy_mechanics = compile_literal(match["destroy"])
    _, create, create_schema, create_mechanics = compile_literal("Create " + match["tokens"] + ".")
    if (len(destroy) != 1 or destroy[0].get("op") != "destroy_all" or len(create) != 1
            or create_schema is not None or type(create[0].get("quantity")) is not int or create[0]["quantity"] != 2):
        return None
    try:
        followup = DestructionFollowup(TARGET_PLAYER_DEATH_TOKEN_SCOPE, {**create[0], "quantity": 1},
                                      schema_version=TARGET_PLAYER_DEATH_TOKEN_VERSION)
        followup.validate_destruction_set(AffectedPermanentSetSpec.from_dict(destroy[0]["set"]))
    except (KeyError, TypeError, ValueError):
        return None
    mechanics = (*destroy_mechanics, *create_mechanics, DESTRUCTION_RESULT_MECHANIC)
    return ("destroy-target-player-creature-death-tokens-v4", ({**destroy[0], "followup": followup.to_dict()},),
            schema, mechanics)


def announced_destruction_controller_token_template(text, match):
    destroy = text[:match.start()].strip()
    if destroy.casefold() != "destroy any number of target creatures." or match["subject"].casefold() != "creature":
        return None
    from .any_number_target_sets import any_number_target_set_template
    from .token_templates import fixed_token_creation_effect_template

    selection = any_number_target_set_template(destroy)
    token = fixed_token_creation_effect_template("Create " + match["token"] + ".")
    if selection is None or token is None:
        return None
    _, _, schema, mechanics = selection
    try:
        effect = AnnouncedDestructionControllerTokens(token.effect).effect()
        announced_destruction_controller_token_dependencies(effect, schema)
    except (TypeError, ValueError):
        return None
    return ("destroy-announced-creatures-controller-tokens-v1", (effect,), schema, (*mechanics, *token.mechanics))


def target_artifact_destruction_treasure_template(text, *, compile_literal):
    expected = "destroy target artifact or land. if an artifact is destroyed this way, create a treasure token."
    if " ".join(text.split()).casefold() != expected:
        return None
    from .token_templates import fixed_token_creation_effect_template

    _, destroy, schema, mechanics = compile_literal("Destroy target artifact or land.")
    if destroy != ({"op": "destroy", "card": "$target.0"},):
        return None
    token = fixed_token_creation_effect_template("Create a Treasure token.")
    if token is None:
        return None
    try:
        result = ArtifactDestructionTreasure(token.effect).effect()
        artifact_destruction_treasure_dependencies(result, schema)
    except (TypeError, ValueError):
        return None
    return ("destroy-target-artifact-treasure-v1", (result,), schema, (*mechanics, *token.mechanics))


_DESTRUCTION_SIZED_TOKEN = re.compile(
    r"Destroy all creatures, then create an X/X (?P<definition>colorless [^.,]+ artifact creature) token, "
    r"where X is the number of creatures destroyed this way\.",
    re.I,
)


def destruction_sized_token_template(text, *, compile_literal):
    match = _DESTRUCTION_SIZED_TOKEN.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    _, destroy, schema, destroy_mechanics = compile_literal("Destroy all creatures.")
    _, create, create_schema, create_mechanics = compile_literal("Create a 0/0 " + match["definition"] + " token.")
    if (schema is not None or create_schema is not None or len(destroy) != 1
            or destroy[0].get("op") != "destroy_all" or len(create) != 1):
        return None
    try:
        followup = DestructionFollowup(DESTRUCTION_SIZED_TOKEN_SCOPE, create[0], DESTRUCTION_SIZED_TOKEN_VERSION)
        followup.validate_destruction_set(AffectedPermanentSetSpec.from_dict(destroy[0]["set"]))
    except (KeyError, TypeError, ValueError):
        return None
    return ("destroy-fixed-set-result-v5", ({**destroy[0], "followup": followup.to_dict()},), None,
            (*destroy_mechanics, *create_mechanics, DESTRUCTION_RESULT_MECHANIC))


def _optional_graveyard_controller_tokens(text):
    match = re.fullmatch(
        r"You may destroy up to three target noncreature permanents\. "
        r"For each permanent put into a graveyard this way, its controller creates "
        r"(?P<token>a [^.]+ creature token(?: with [^.]+)?)\.",
        " ".join(text.split()), re.I,
    )
    if match is None:
        return None
    from ..rules.announced_destruction_controller_tokens import optional_graveyard_controller_token_dependencies
    from .fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template
    from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, OPTIONAL_EFFECT_OPERATION
    from .token_templates import fixed_token_creation_effect_template

    selection = fixed_homogeneous_target_set_effect_template("Destroy up to three target noncreature permanents.")
    token = fixed_token_creation_effect_template("Create " + match["token"] + ".")
    if selection is None or token is None:
        return None
    _, _, schema, mechanics = selection.compiled()
    try:
        child = AnnouncedDestructionControllerTokens(token.effect, schema_version=2).effect()
        optional_graveyard_controller_token_dependencies(child, schema)
    except (TypeError, ValueError):
        return None
    wrapper = {"op": OPTIONAL_EFFECT_OPERATION, "player": "$controller", "effects": [child]}
    return ("optional-graveyard-controller-tokens-v1", (wrapper,), schema,
            (*mechanics, *token.mechanics, FIXED_OPTIONAL_EFFECT_MECHANIC))


def _nonbasic_target_untap(text, compile_literal):
    if " ".join(text.split()).casefold() != "destroy target land. if that land was nonbasic, untap this creature.":
        return None
    from ..rules.nonbasic_land_source_untap import NonbasicLandSourceUntap, nonbasic_land_source_untap_dependencies

    _, effects, schema, mechanics = compile_literal("Destroy target land.")
    if effects != ({"op": "destroy", "card": "$target.0"},):
        return None
    followup = NonbasicLandSourceUntap().effect()
    try:
        nonbasic_land_source_untap_dependencies(followup, schema)
    except (TypeError, ValueError):
        return None
    return ("destroy-nonbasic-target-source-untap-v1", (followup,), schema, mechanics)


def _destroyed_creature_half_copy_template(text, *, compile_literal):
    if re.fullmatch(
        r"Destroy target creature\. If that creature dies this way, its controller creates two tokens that are copies of "
        r"that creature, except their (?:base )?power is half that creature['’]s power and their (?:base )?toughness is "
        r"half that creature['’]s toughness\. Round up each time\.", " ".join(text.split()), re.I,
    ) is None:
        return None
    from ..rules.destroyed_creature_copy import DestroyedCreatureCopy, destroyed_creature_copy_dependencies

    _, effects, schema, mechanics = compile_literal("Destroy target creature.")
    if effects != ({"op": "destroy", "card": "$target.0"},):
        return None
    outcome = DestroyedCreatureCopy().effect()
    try:
        destroyed_creature_copy_dependencies(outcome, schema)
    except (TypeError, ValueError):
        return None
    return "destroy-target-creature-death-two-half-copies-v1", (outcome,), schema, mechanics


def target_controller_library_reveal_template(text):
    expected = (
        "destroy target permanent an opponent controls. its controller reveals cards from the top of their library "
        "until they reveal a permanent card that shares a card type with that permanent. "
        "they put that card onto the battlefield and the rest on the bottom of their library in a random order."
    )
    if " ".join(text.split()).casefold() != expected:
        return None
    from ..rules.target_controller_library_reveal import TargetControllerLibraryReveal, target_controller_library_target

    return ("destroy-target-controller-library-reveal-v1", (TargetControllerLibraryReveal().effect(),),
            target_controller_library_target(), ("destroy", "cr-115-targets"))


def affected_artifact_life_template(text):
    normalized = " ".join(text.split()).casefold().replace("’", "'")
    expected = ("destroy all artifacts. they can't be regenerated. "
                "the controller of each of those artifacts gains life equal to its mana value.")
    if normalized != expected:
        return None
    from .destruction_templates import mass_destruction_effect_template

    destruction = mass_destruction_effect_template("Destroy all artifacts. They can't be regenerated.")
    if destruction is None:
        return None
    _, effects, schema, mechanics = destruction.compiled()
    followup = DestructionFollowup.affected_artifact_life()
    return ("affected-artifact-controller-mana-value-life-v1",
            ({**effects[0], "followup": followup.to_dict()},), schema, (*mechanics, DESTRUCTION_RESULT_MECHANIC))


def destruction_followup_template(text, *, compile_literal):
    normalized = text.strip()
    if life := affected_artifact_life_template(normalized):
        return life
    if copies := _destroyed_creature_half_copy_template(normalized, compile_literal=compile_literal):
        return copies
    if untap := _nonbasic_target_untap(normalized, compile_literal):
        return untap
    if optional := _optional_graveyard_controller_tokens(normalized):
        return optional
    if not normalized.casefold().startswith("destroy "):
        return None
    if "died this way" in normalized.casefold():
        return target_player_death_token_template(normalized, compile_literal=compile_literal)
    if "destroyed this way" not in normalized.casefold():
        return None
    if sized := destruction_sized_token_template(normalized, compile_literal=compile_literal):
        return sized
    if treasure := target_artifact_destruction_treasure_template(normalized, compile_literal=compile_literal):
        return treasure
    if allocation := committed_black_green_mana_template(normalized, compile_literal=compile_literal):
        return allocation
    distributed = _CONTROLLER_TOKEN.search(normalized)
    if distributed and (announced := announced_destruction_controller_token_template(normalized, distributed)):
        return announced
    literal = (distributed, "Create " + distributed["token"] + ".") if distributed else _literal_followup(normalized)
    if literal is None:
        return None
    match, replacement = literal
    _, effects, target_schema, mechanics = compile_literal(normalized[:match.start()] + replacement)
    if len(effects) != 2 or effects[0].get("op") != "destroy_all":
        return None
    subject = match["subject"].casefold()
    if subject == "creatures you controlled that were":
        subject = "creature you controlled that was"
    card_type = subject.removeprefix("nontoken ").removesuffix(" you controlled that was")
    controller = PermanentControllerRelation.ACTOR if "you controlled" in subject else PermanentControllerRelation.ANY
    try:
        if distributed:
            followup = _controller_token_followup(
                effects[0], effects[1], subject=subject, target_schema=target_schema,
            )
        else:
            followup = DestructionFollowup(
                AffectedPermanentSetSpec(
                    ObjectQuerySpec(
                        zones=("battlefield",), types_all=() if card_type == "permanent" else (card_type,),
                        token=False if subject.startswith("nontoken ") else None,
                    ),
                    controller_relation=controller,
                ),
                effects[1],
            )
    except (TypeError, ValueError):
        return None
    markers = tuple(
        mechanic for mechanic in mechanics
        if mechanic not in {"fixed-effect-clause-sequence", CLOSED_EFFECT_PROGRAM_MECHANIC, "bound-effect-program"}
    )
    return (
        f"destroy-fixed-set-result-v{followup.schema_version}",
        ({**effects[0], "followup": followup.to_dict()},),
        target_schema,
        (*markers, DESTRUCTION_RESULT_MECHANIC),
    )
