"""Source-bound token cleanup through canonical creation and public movement."""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from .created_token_cleanup_model import CreatedTokenCleanupSpec
from .replacement.immutable import FrozenMap, thaw_value
from .semantic_runtime.context import SemanticSourceContext


@dataclass(frozen=True, slots=True)
class CreatedTokenCleanupPlan:
    spec: CreatedTokenCleanupSpec
    source: SemanticSourceContext
    source_binding: FrozenMap | None = None
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.spec, CreatedTokenCleanupSpec)
                or not isinstance(self.source, SemanticSourceContext)
                or type(self.source.stack_ref) is not str or not self.source.stack_ref):
            raise ValueError('Created-token cleanup requires its typed source and instruction')
        object.__setattr__(self, 'source', SemanticSourceContext(**self.source.to_dict()))
        if self.source_binding is not None:
            from .target_source import TargetingSourceBinding
            if (not isinstance(self.source_binding, Mapping)
                    or type(self.source_binding.get('schema_version')) is not int
                    or self.source_binding.get('schema_version') != 1):
                raise ValueError('Created-token cleanup source binding has an invalid version')
            binding = TargetingSourceBinding.from_dict(thaw_value(FrozenMap(self.source_binding)))
            if (binding.object_id != self.source.object_id or binding.logical_object_id != self.source.logical_object_id
                    or self.source.card_ref not in {None, binding.object_ref}):
                raise ValueError('Created-token cleanup source binding changed incarnation')
            object.__setattr__(self, 'source_binding', FrozenMap(binding.to_dict()))

    def to_dict(self):
        return {'kind': 'created_token_cleanup_plan', 'schema_version': self.schema_version,
            'spec': self.spec.to_dict(), 'source': self.source.to_dict(),
            'source_binding': thaw_value(self.source_binding)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'spec', 'source', 'source_binding'}
                or value.get('kind') != 'created_token_cleanup_plan'
                or not isinstance(value.get('source'), Mapping)
                or set(value['source']) != {'stack_ref', 'object_id', 'logical_object_id', 'card_ref'}):
            raise ValueError('Created-token cleanup plan has unknown or missing fields')
        return cls(CreatedTokenCleanupSpec.from_dict(value['spec']), SemanticSourceContext(**dict(value['source'])),
            value['source_binding'], value['schema_version'])


def capture_created_token_cleanup(host, plan, *, controller):
    from .target_source import TARGETING_SOURCE_CONTEXT, capture_targeting_source
    from .semantic_runtime.context import semantic_source_context
    if not isinstance(plan, CreatedTokenCleanupPlan):
        raise ValueError('Token cleanup must be a typed plan')
    item = next((item for item in host.state.stack if item.ref == plan.source.stack_ref), None)
    if item is None or item.controller != controller:
        raise ValueError('Token cleanup requires its authoritative resolving stack object')
    if semantic_source_context(item, host.state.cards).to_dict() != plan.source.to_dict():
        raise ValueError('Token cleanup source differs from its resolving stack object')
    card = host.state.cards.get(plan.source.object_id)
    if card is not None and card.logical_object_id == plan.source.logical_object_id:
        binding = capture_targeting_source(host, card).to_dict()
    else:
        binding = item.context.get(TARGETING_SOURCE_CONTEXT, plan.source_binding)
    return replace(plan, source_binding=FrozenMap(binding) if binding is not None else None)


def schedule_created_token_cleanup(host, controller, cards, spec, *, label, source=None, source_binding=None):
    from .trigger_processing import schedule_delayed_trigger
    from .target_source import TARGETING_SOURCE_CONTEXT
    if not isinstance(spec, CreatedTokenCleanupSpec):
        raise ValueError('Created-token cleanup instruction must be typed')
    cards = tuple(cards)
    if not cards and source is None:
        return None
    movement = {'op': 'move_if_in_zone', 'cards': [{'card': card.ref,
        'expected_zone_change_counter': card.zone_change_counter,
        'expected_object_identity': card.logical_object_id} for card in cards],
        'from': 'battlefield', 'destination': 'graveyard' if spec.action == 'sacrifice' else 'exile',
        **({'transition_kind': 'sacrifice', 'required_controller': controller} if spec.action == 'sacrifice' else {})}
    context = {'dynamic_effects': [movement] if cards else []}
    if source is not None and source.logical_object_id is not None:
        context['source_logical_object_id'] = source.logical_object_id
    if source_binding is not None:
        context[TARGETING_SOURCE_CONTEXT] = thaw_value(source_binding)
    return schedule_delayed_trigger(host, controller=controller, label=label, event_kind='step.begin',
        condition=spec.condition(), stack_template={'label': label, 'context': context},
        source_object_id=source.object_id if source is not None else None,
        source_logical_object_id=source.logical_object_id if source is not None else None,
        referred_object_ids=tuple(card.object_id for card in cards), once=True)


def apply_fixed_token_cleanup_creation(host, effect, *, actor, reason):
    from .rules.created_token_cleanup_shapes import created_token_cleanup_closure
    from .token_creation import create_tokens
    public = {key: value for key, value in effect.items() if key not in {'_runtime_source', '_replacement_selections'}}
    quantity = public.get('quantity')
    if type(quantity) is not int or quantity < 0 or public.get('controller') != actor:
        raise ValueError('Temporary token creation has invalid quantity or controller')
    validation = {**public, 'controller': '$controller', 'quantity': max(1, quantity)}
    if isinstance(validation.get('characteristics'), Mapping):
        validation['characteristics'] = {key: str(value) if key in {'power', 'toughness'} and type(value) is int else value
            for key, value in validation['characteristics'].items()}
    created_token_cleanup_closure(validation)
    raw_source = effect.get('_runtime_source')
    if not isinstance(raw_source, Mapping) or set(raw_source) != {'stack_ref', 'object_id', 'logical_object_id', 'card_ref'}:
        raise ValueError('Temporary token creation requires its captured source')
    plan = CreatedTokenCleanupPlan(CreatedTokenCleanupSpec.from_dict(public['token_cleanup']),
        SemanticSourceContext(**dict(raw_source)))
    return create_tokens(host, actor, name=public['name'], quantity=quantity,
        characteristics=public['characteristics'], tapped=public.get('tapped', False),
        token_cleanup=plan, reason=reason, replacement_selections=tuple(effect.get('_replacement_selections', ())))
