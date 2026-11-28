"""Fixed removal followups addressed to the same target's player information."""
from dataclasses import dataclass
from . import regex_working_set as re

from ..target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_MECHANIC, TARGET_OWNER_INFORMATION_MECHANIC,
    target_controller_reference, target_owner_reference,
)
from .bound_effect_programs import BoundEffectProgramTemplate, _program, _target_type_is_guaranteed
from .closed_effect_programs import _sentence_partition

TARGET_RECIPIENT_RESULT_CAPABILITY = 'resolution.effect_program.target_recipient_result'
TARGET_RECIPIENT_RESULT_MECHANIC = 'fixed-target-recipient-result'
TARGET_RECIPIENT_RESULT_TEMPLATE = 'bound-target-recipient-result-v1'
_TAIL = re.compile(
    r"(?:Then )?(?:(?:Its|That (?P<kind>permanent|artifact|battle|creature|enchantment|land|planeswalker)['’]s) "
    r"(?P<relation>controller|owner)|(?P<owner_player>That player)) "
    r"(?P<verb>creates|discards|gets|gains|loses|draws|manifests) (?P<body>.+)\.", re.I,
)


@dataclass(frozen=True, slots=True)
class TargetRecipientResultTemplate(BoundEffectProgramTemplate):
    @property
    def template_id(self): return TARGET_RECIPIENT_RESULT_TEMPLATE


def _target_sacrifice_power_token_template(text, *, compile_component):
    match = re.fullmatch(
        r"Target creature['’]s controller sacrifices it, then creates X (?P<definition>[^.,]+) creature tokens, "
        r"where X is that creature['’]s power[.]?",
        text.strip(), re.I,
    )
    if match is None:
        return None
    from .direct_target import direct_permanent_target_spec
    from ..rules.target_recipient_results import bind_fixed_recipient_result, target_sacrifice_power_effect
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec

    token_id, token_effects, token_schema, token_mechanics = compile_component(
        "Create a " + match["definition"] + " creature token.",
    )
    if token_id is None or token_schema is not None or len(token_effects) != 1:
        return None
    recipient = target_controller_reference()
    bound = bind_fixed_recipient_result(token_effects[0], None, recipient)
    if bound is None or bound.get("op") != "create_token":
        return None
    bound["quantity"] = ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET, characteristic="power").to_dict()
    schema = direct_permanent_target_spec("target creature").to_target_schema()
    components = (
        ("fixed-target-sacrifice-power-v1", (target_sacrifice_power_effect(),), schema, ("cr-115-targets",)),
        (token_id, (bound,), None, (*token_mechanics, TARGET_CONTROLLER_INFORMATION_MECHANIC,
                                   TARGET_RECIPIENT_RESULT_MECHANIC, SCALAR_AMOUNT_MECHANIC)),
    )
    program = _program(components, schema=schema)
    if program is None:
        return None
    return TargetRecipientResultTemplate(program.component_template_ids, program.component_effect_counts,
        program._effects, program._target_schema, program.mechanic_ids)


def _target_owner_shuffle_draw_template(text, *, compile_component):
    if not re.fullmatch(
        r"The owner of target nonland permanent shuffles it into their library, then draws two cards\.",
        " ".join(text.split()), re.I,
    ):
        return None

    from .fixed_owner_zone_move_templates import FixedOwnerZoneMoveReference, FixedOwnerZoneMoveTemplate
    from ..rules.target_recipient_results import bind_fixed_recipient_result, target_owner_shuffle_schema

    schema = target_owner_shuffle_schema()
    prefix = FixedOwnerZoneMoveTemplate(FixedOwnerZoneMoveReference.TARGET, "library",
        "nonland-permanent", target_schema=schema, shuffle=True).compiled()
    template, effects, targets, mechanics = compile_component("You draw two cards.")
    if template is None or len(effects) != 1:
        return None
    draw = bind_fixed_recipient_result(effects[0], targets, target_owner_reference())
    if draw is None:
        return None
    tail = (template, (draw,), None,
            (*mechanics, TARGET_OWNER_INFORMATION_MECHANIC, TARGET_RECIPIENT_RESULT_MECHANIC))
    program = _program((prefix, tail), schema=schema)
    if program is None:
        return None
    return TargetRecipientResultTemplate(program.component_template_ids, program.component_effect_counts,
        program._effects, program._target_schema, program.mechanic_ids)


_TARGET_DESTRUCTION_MANA_TOKENS = re.compile(
    r"Destroy target (?P<kind>artifact|enchantment)\.(?P<regeneration> It can['’]t be regenerated\.)? "
    r"Create X (?P<definition>[^.,]+) creature tokens, where X is that (?P=kind)['’]s mana value\.",
    re.I,
)


def target_destruction_mana_token_template(text, *, compile_component):
    match = _TARGET_DESTRUCTION_MANA_TOKENS.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    from ..rules.target_destruction_mana_value_tokens import (
        TARGET_DESTRUCTION_MANA_TOKEN_AMOUNT, target_destruction_mana_token_node_capabilities,
    )
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC

    kind = match["kind"].casefold()
    prefix = f"Destroy target {kind}."
    if match["regeneration"]:
        prefix += " It can't be regenerated."
    destruction = compile_component(prefix)
    token_id, token_effects, token_schema, token_mechanics = compile_component(
        "Create a " + match["definition"] + " creature token.",
    )
    if destruction[0] is None or destruction[2] is None or token_id is None or token_schema is not None or len(token_effects) != 1:
        return None
    token = {**token_effects[0], "quantity": TARGET_DESTRUCTION_MANA_TOKEN_AMOUNT.to_dict()}
    schema = destruction[2]
    components = (destruction, (token_id, (token,), None, (*token_mechanics, SCALAR_AMOUNT_MECHANIC)))
    program = _program(components, schema=schema)
    if program is None or not target_destruction_mana_token_node_capabilities(
        effects=program.effects, target_schema=schema,
    ):
        return None
    return program


_DESTROY_CONTROLLED_ARTIFACT_TOKENS = re.compile(
    r"Destroy target artifact\. If you controlled that artifact, "
    r"create three (?P<definition>[^.,]+) creature tokens\.",
    re.I,
)


def controlled_artifact_token_template(text, *, compile_component):
    match = _DESTROY_CONTROLLED_ARTIFACT_TOKENS.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC
    from ..rules.controlled_artifact_token_result import (
        controlled_artifact_token_branch, controlled_artifact_token_node_capabilities,
    )

    destroy = compile_component("Destroy target artifact.")
    token_id, token_effects, token_schema, token_mechanics = compile_component("Create three " + match["definition"] + " creature tokens.")
    if destroy[0] is None or destroy[2] is None or token_id is None or token_schema is not None or len(token_effects) != 1:
        return None
    branch = controlled_artifact_token_branch(token_effects[0])
    components = (destroy, (token_id, (branch,), None, (*token_mechanics, RESOLUTION_CONDITION_MECHANIC)))
    program = _program(components, schema=destroy[2])
    if program is None or not controlled_artifact_token_node_capabilities(effects=program.effects, target_schema=destroy[2]):
        return None
    return program


def controlled_artifact_draw_template(text, *, compile_component):
    if re.fullmatch(r"Destroy up to one target artifact\. If you controlled that (?:permanent|artifact), draw a card\.",
                    " ".join(text.split()), re.I) is None:
        return None
    from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC
    from ..rules.controlled_artifact_token_result import (
        controlled_artifact_draw_branch, controlled_artifact_draw_node_capabilities,
    )

    destruction = compile_component("Destroy up to one target artifact.")
    draw_id, effects, target, mechanics = compile_component("Draw a card.")
    if draw_id is None or target is not None or len(effects) != 1:
        return None
    branch = (draw_id, (controlled_artifact_draw_branch(effects[0]),), None, (*mechanics, RESOLUTION_CONDITION_MECHANIC))
    program = _program((destruction, branch), schema=destruction[2])
    if program is not None and controlled_artifact_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
    ):
        return program


def target_recipient_result_template(text, *, compile_component):
    if controlled_tokens := controlled_artifact_token_template(text, compile_component=compile_component):
        return controlled_tokens
    if mana_tokens := target_destruction_mana_token_template(text, compile_component=compile_component):
        return mana_tokens
    if shuffle := _target_owner_shuffle_draw_template(text, compile_component=compile_component):
        return shuffle
    if sacrifice := _target_sacrifice_power_token_template(text, compile_component=compile_component):
        return sacrifice
    clauses = _sentence_partition(text)
    if clauses is None or len(clauses) not in {2,3}: return None
    tail = _TAIL.fullmatch(clauses[-1])
    if tail is None: return None
    prefix = compile_component(' '.join(clauses[:-1]))
    schema = prefix[2]
    if (prefix[0] is None or schema is None or schema.get('count') != 1
            or type(schema.get('count')) is not int or schema.get('up_to') or len(prefix[1]) != 1): return None
    from ..rules.target_recipient_results import target_recipient_prefix_is_closed, bind_fixed_recipient_result
    relation = 'owner' if tail['owner_player'] else tail['relation'].casefold()
    if tail['owner_player'] and prefix[1][0].get('op')!='bounce':return None
    if not target_recipient_prefix_is_closed(prefix[1][0],schema,relation): return None
    if tail['kind'] and not _target_type_is_guaranteed(schema,tail['kind'].casefold()): return None
    verb = tail['verb'].casefold().removesuffix('s')
    if verb == 'manifest':
        if relation != 'controller' or tail['body'].casefold() != 'the top card of their library':
            return None
        result = compile_component('Manifest the top card of your library.')
    else:
        targeted = verb in {'discard','get'} or verb=='gain' and tail['body'].casefold().startswith('control of ')
        subject = 'Target player ' + tail['verb'] if targeted else 'Create' if verb=='create' else 'You ' + verb
        result = compile_component(subject + ' ' + tail['body'] + '.')
    if result[0] is None or len(result[1]) != 1: return None
    recipient = target_controller_reference() if relation == 'controller' else target_owner_reference()
    bound = bind_fixed_recipient_result(result[1][0],result[2],recipient)
    if bound is None: return None
    information = TARGET_CONTROLLER_INFORMATION_MECHANIC if relation == 'controller' else TARGET_OWNER_INFORMATION_MECHANIC
    component = (result[0],(bound,),None,(*result[3],information,TARGET_RECIPIENT_RESULT_MECHANIC))
    program = _program((prefix,component),schema=schema)
    if program is None:return None
    return TargetRecipientResultTemplate(program.component_template_ids,program.component_effect_counts,
        program._effects,program._target_schema,program.mechanic_ids)
