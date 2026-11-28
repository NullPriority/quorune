"""Pinned targeted-card conditional result: both material consequences remain."""
from dataclasses import FrozenInstanceError
import json
import unittest
from unittest.mock import patch

from quorune.graveyard_card_result_model import GraveyardCardCounterLifeSpec as Spec
from quorune.compiler.graveyard_card_result_templates import parse_graveyard_card_counter_life as parse
from quorune.target_forms import TargetCharacteristicForm
from quorune.graveyard_exile_result_model import GraveyardExileLifeResultSpec


TEXT = 'Exile target card from a graveyard. If it was a creature card, put a +1/+1 counter on this creature and you gain 1 life.'


class GraveyardCardCounterLifeContractTests(unittest.TestCase):
    def test_fixed_result_codec_is_immutable_strict_and_carries_both_counter_and_life(self):
        spec = Spec(); value = spec.to_dict(); self.assertEqual(spec, Spec.from_dict(json.loads(json.dumps(value))))
        self.assertEqual({'counter': '+1/+1', 'amount': 1}, value['source_counter']); self.assertEqual(1, value['life_gain'])
        with self.assertRaises(FrozenInstanceError):spec.schema_version = 2
        for version in (True, False, 0, 2, '1', None):
            with self.assertRaises(ValueError):Spec(version)
        for changes in ({'schema_version': True}, {'schema_version': 2}, {'card_type': 'artifact'}, {'life_gain': True},
                {'life_gain': 2}, {'life_gain': 0}, {'source_counter': {'counter': '+1/+1', 'amount': True}},
                {'source_counter': {'counter': '+1/+1', 'amount': 2}}, {'source_counter': {'counter': '-1/-1', 'amount': 1}},
                {'optional': True}, {'source_counter': None}, {'target_player': 'B'}):
            with self.assertRaises(ValueError):Spec.from_dict(value | changes)
        for key in value:
            missing = dict(value); del missing[key]
            with self.assertRaises(ValueError):Spec.from_dict(missing)

    def test_creature_card_condition_uses_the_existing_type_form_and_accepts_artifact_creatures(self):
        condition = Spec().card_form; self.assertEqual(TargetCharacteristicForm(types_all=('creature',)), condition)
        for types, subtypes, expected in (({'creature'}, set(), True), ({'artifact', 'creature'}, {'golem'}, True),
                ({'artifact'}, {'creature'}, False), ({'land'}, {'forest'}, False), ({'instant'}, set(), False)):
            self.assertEqual(expected, condition.matches(types=types, subtypes=subtypes, supertypes=set()))

    def test_exact_full_clause_requires_both_material_consequences_and_preserves_the_unrestricted_card_target(self):
        self.assertEqual(Spec(), parse(TEXT)); self.assertEqual(Spec(), parse(TEXT[:-1]))
        for text in ('Exile target card from a graveyard.',
                TEXT.replace('target card', 'target creature card'),
                TEXT.replace('Exile target', 'You may exile target'),
                TEXT.replace('If it was a creature card', 'If a creature card is exiled this way'),
                TEXT.replace('creature card', 'artifact card'),
                TEXT.replace('and you gain 1 life', 'and you draw a card'),
                TEXT.replace('a +1/+1 counter', 'two +1/+1 counters'),
                TEXT.replace('this creature', 'target creature'),
                TEXT.replace('1 life', '2 life'), TEXT + ' Draw a card.'):
            self.assertIsNone(parse(text), text)

    def test_existing_chosen_card_life_result_encoding_is_not_a_counter_life_recipe(self):
        old = GraveyardExileLifeResultSpec(1)
        self.assertEqual(old, GraveyardExileLifeResultSpec.from_dict(old.to_dict()))
        with self.assertRaises(ValueError):Spec.from_dict(old.to_dict())
        with self.assertRaises(ValueError):GraveyardExileLifeResultSpec.from_dict(Spec().to_dict())

    def test_fresh_life_consequence_omission_is_killed_after_the_positive_complete_result(self):
        def expected():
            value = parse(TEXT).to_dict(); self.assertEqual(1, value['life_gain'])
            self.assertEqual({'counter': '+1/+1', 'amount': 1}, value['source_counter'])
        expected(); original = Spec.to_dict
        def missing(spec):
            value = original(spec); del value['life_gain']; return value
        with patch.object(Spec, 'to_dict', missing):
            with self.assertRaises(KeyError):expected()


class GraveyardCardCounterLifeOwnerTests(unittest.TestCase):
    def fixture(self, types=('creature',)):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.object_query import ObjectQueryResult
        target = CardInstance('target', 'target-ref', 'target-oracle', 'Public target', 'B', 'B', 'graveyard')
        source = CardInstance('source', 'source-ref', 'source-oracle', 'Source', 'A', 'C', 'battlefield')
        host = SimpleNamespace(state=SimpleNamespace(cards={c.object_id: c for c in (source, target)}), types=types, observed=[])
        host._require_seat = lambda actor, **kw: self.assertEqual('C', actor)
        host._resolve_object = lambda actor, ref, **kw: target
        def facts(card):
            host.observed.append(('query', card.zone))
            return ObjectQueryResult(card.object_id, card.ref, card.printed_name, card.owner, card.controller,
                card.zone, types=host.types, logical_object_id=card.logical_object_id)
        host._public_object_query_result = facts
        return host, target, source

    def intent(self, target, source, **changes):
        from quorune.semantic_runtime.graveyard_card_result_intent import GraveyardCardCounterLifeIntent
        values = dict(actor='C', object_ref=target.ref, reason='Targeted creature result',
            source_object_id=source.object_id, source_logical_object_id=source.logical_object_id, result=Spec())
        return GraveyardCardCounterLifeIntent(**(values | changes))

    def context(self, source=True):
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext, ReadOnlyRulesQuery, SemanticSourceContext
        return ReadOnlyHandlerContext('C', 'Targeted card result', ReadOnlyRulesQuery(tuple('ABCD'), tuple('ABCD'), tuple('ABCD')),
            SemanticSourceContext('stack', object_id='source', logical_object_id='source@0', card_ref='source-ref') if source else None)

    def test_intent_and_replacement_identity_require_paired_original_source_and_exact_typed_result(self):
        from dataclasses import replace
        from quorune.semantic_runtime.graveyard_card_result_intent import validate_graveyard_card_counter_life_identity as decode
        host, target, source = self.fixture(); intent = self.intent(target, source)
        self.assertEqual(intent.identity(), decode(json.loads(json.dumps(intent.identity()))))
        self.assertIsNone(self.intent(target, source, source_object_id=None, source_logical_object_id=None).source_object_id)
        for change in ({'actor': True}, {'object_ref': 1}, {'reason': ''}, {'source_object_id': None},
                {'source_logical_object_id': None}, {'source_object_id': ''}, {'result': None}, {'result': Spec().to_dict()}):
            with self.assertRaises(ValueError):replace(intent, **change)
        for change in ({'extra': True}, {'result': Spec().to_dict() | {'life_gain': 2}}, {'source_object_id': None}):
            with self.assertRaises(ValueError):decode(intent.identity() | change)

    def test_canonical_targeted_move_precedes_current_public_type_condition_and_hidden_destinations_use_last_known(self):
        from quorune.rules.graveyard_card_results import commit_graveyard_card_counter_life as commit
        for before, after, zone, down, expected in (
                (('creature',), ('artifact',), 'exile', False, False),
                (('artifact',), ('artifact', 'creature'), 'exile', False, True),
                (('creature',), ('artifact',), 'hand', False, True),
                (('artifact',), ('creature',), 'hand', False, False),
                (('creature',), ('artifact',), 'exile', True, True),
                (('creature',), ('creature',), 'graveyard', False, True)):
            with self.subTest(before=before, after=after, zone=zone, down=down):
                host, target, source = self.fixture(before); intent = self.intent(target, source)
                def move(current, ref, **kwargs):
                    self.assertIs(host, current); self.assertEqual(target.ref, ref); self.assertEqual('C', kwargs['actor'])
                    self.assertNotIn('owned_only', kwargs); host.observed.append(('move', target.zone))
                    target.zone = zone; target.zone_change_counter += 1; target.face_down = down; host.types = after
                    return target.ref
                with patch('quorune.rules.graveyard_card_results.exile_public_graveyard_card', side_effect=move):result = commit(host, intent)
                self.assertEqual([('query', 'graveyard'), ('move', 'graveyard')], host.observed[:2])
                self.assertEqual(['place_counters', 'life'] if expected else [], [e['op'] for e in result.effects])
                if expected:
                    self.assertEqual('$source.zone_object', result.effects[0]['card'])
                    self.assertEqual({'op': 'life', 'player': '$controller', 'delta': 1}, dict(result.effects[1]))
                if zone == 'hand' or down:self.assertEqual(2, len(host.observed))

    def test_missing_phased_or_new_source_incarnation_skips_only_counter_and_control_change_does_not(self):
        from quorune.rules.graveyard_card_results import commit_graveyard_card_counter_life as commit
        for state, expected_counter in (('same', True), ('new_controller', True), ('blink', False),
                ('graveyard', False), ('phased', False), ('removed', False), ('no_source', False)):
            with self.subTest(state=state):
                host, target, source = self.fixture(); intent = self.intent(target, source)
                if state == 'new_controller':source.controller = 'B'
                elif state == 'blink':source.zone_change_counter += 1
                elif state == 'graveyard':source.zone = 'graveyard'
                elif state == 'phased':source.phased_out = True
                elif state == 'removed':del host.state.cards[source.object_id]
                elif state == 'no_source':intent = self.intent(target, source, source_object_id=None, source_logical_object_id=None)
                def move(*args, **kwargs):target.zone = 'exile'; target.zone_change_counter += 1; return target.ref
                with patch('quorune.rules.graveyard_card_results.exile_public_graveyard_card', side_effect=move):result = commit(host, intent)
                self.assertEqual(['place_counters', 'life'] if expected_counter else ['life'], [e['op'] for e in result.effects])
                self.assertEqual('$controller', result.effects[-1]['player'])

    def test_noncard_or_wrong_origin_rejects_before_the_canonical_exile_port(self):
        from quorune.errors import GameRuleError
        from quorune.rules.graveyard_card_results import commit_graveyard_card_counter_life as commit
        for noncard in (True, False):
            host, target, source = self.fixture(); intent = self.intent(target, source)
            if noncard:target.object_kind = 'token'; target.is_token = True
            else:target.zone = 'hand'
            with patch('quorune.rules.graveyard_card_results.exile_public_graveyard_card') as move:
                with self.assertRaises(GameRuleError):commit(host, intent)
                move.assert_not_called()

    def test_direct_handler_preserves_target_and_original_source_and_keeps_legacy_exile_shape_closed(self):
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.semantic_runtime.graveyard_card_result_handlers import GraveyardCardCounterLifeHandler as Handler
        from quorune.semantic_runtime.public_zone_move_handlers import ExilePublicGraveyardCardHandler as Old
        from quorune.semantic_runtime.graveyard_card_result_intent import GraveyardCardCounterLifeIntent
        effect = Spec().effect(card='target-ref'); handler = Handler(); plan = handler.lower(effect, self.context())
        intent, = plan.intents; self.assertIsInstance(intent, GraveyardCardCounterLifeIntent)
        self.assertEqual(('C', 'target-ref', 'source', 'source@0', Spec()),
            (intent.actor, intent.object_ref, intent.source_object_id, intent.source_logical_object_id, intent.result))
        for change in ({'optional': True}, {'result': None}, {'result': Spec().to_dict() | {'life_gain': True}}):
            with self.assertRaises(SemanticNodeError):handler.lower(effect | change, self.context())
        with self.assertRaises(SemanticNodeError):handler.lower(effect, self.context(False))
        old = Old(); legacy = {'op': old.operation, 'card': 'target-ref'}
        self.assertNotIsInstance(old.lower(legacy, self.context()).intents[0], GraveyardCardCounterLifeIntent)
        with self.assertRaises(SemanticNodeError):old.lower(legacy | {'result': Spec().to_dict()}, self.context())

    def test_raw_result_payload_declares_every_owner_without_coverage_and_rejects_changed_target_role(self):
        from quorune.card_programs.descriptor_requirements import _owned_operation_payload_dependencies
        from quorune.rules.graveyard_card_results import (graveyard_card_counter_life_payload_dependencies,
            GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES)
        from quorune.graveyard_card_result_model import GRAVEYARD_CARD_COUNTER_LIFE_CAPABILITY as cap
        self.assertEqual(Spec(), Spec.from_effect(Spec().effect()))
        self.assertEqual(GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES, graveyard_card_counter_life_payload_dependencies(Spec().effect()))
        self.assertEqual(GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES, _owned_operation_payload_dependencies(Spec().effect()))
        self.assertTrue({cap, 'card.exile.public_graveyard', 'counter.producer.fixed_effect', 'life.change.effect',
            'target.revalidate_resolution'} <= set(GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES))
        for change in ({'card': '$controller'}, {'card': '$target.1'}, {'unowned': True},
                {'result': Spec().to_dict() | {'card_type': 'artifact'}}):
            with self.assertRaises(ValueError):graveyard_card_counter_life_payload_dependencies(Spec().effect() | change)
            with self.assertRaises(ValueError):_owned_operation_payload_dependencies(Spec().effect() | change)
        for card in (True, 1, '', None):
            with self.assertRaises(ValueError):Spec().effect(card=card)


class GraveyardCardCounterLifeIntegrationTests(unittest.TestCase):
    fixture = GraveyardCardCounterLifeOwnerTests.fixture
    context = GraveyardCardCounterLifeOwnerTests.context

    def test_full_template_preserves_unrestricted_public_card_target_and_both_typed_result_dependencies(self):
        from dataclasses import replace
        from quorune.compiler.public_zone_move_templates import public_graveyard_card_exile_template
        from quorune.rules.public_zone_move_capability_shapes import public_graveyard_card_exile_node_capabilities
        from quorune.rules.graveyard_card_results import GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES
        from quorune.rules.graveyard_card_targets import PublicGraveyardCardTargetKind
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.compiler.activated_mana_nodes import _activated_effect_dependency_gate
        from quorune.rules.capabilities import load_default_capability_registry
        new = public_graveyard_card_exile_template(TEXT); self.assertIsNotNone(new)
        self.assertEqual('exile-target-graveyard-card-counter-life-result-v1', new.template_id)
        self.assertEqual((Spec().effect(),), new.effects)
        self.assertEqual({'zones': ['graveyard'], 'categories': ['card'], 'owner_relation': 'any', 'count': 1}, new.target_schema)
        self.assertEqual(GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES, public_graveyard_card_exile_node_capabilities(
            effects=new.effects, target_schema=new.target_schema, mechanic_ids=new.mechanics))
        self.assertEqual(set(GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES), set(capability_dependencies_for_node(
            effects=new.effects, target_schema=new.target_schema, mechanic_ids=new.mechanics)))
        gate = _activated_effect_dependency_gate(effects=new.effects, target_schema=new.target_schema,
            mechanics=new.mechanics, trusted_mechanics=frozenset(), active_zone='battlefield', cost_schema=None,
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual((), gate.blockers); self.assertEqual(set(GRAVEYARD_CARD_COUNTER_LIFE_DEPENDENCIES), set(gate.capabilities))
        for change in ({'count': True}, {'types_all': ['creature']}, {'owner_relation': 'you'}):
            self.assertEqual((), public_graveyard_card_exile_node_capabilities(effects=new.effects,
                target_schema=dict(new.target_schema) | change, mechanic_ids=new.mechanics))
        other = next(kind for kind in PublicGraveyardCardTargetKind if kind is not PublicGraveyardCardTargetKind.CARD)
        for change in ({'target': other}, {'owner_relation': 'you'}, {'result': Spec().to_dict()}):
            with self.assertRaises(ValueError):replace(new, **change)
        old = public_graveyard_card_exile_template('Exile target card from a graveyard.')
        self.assertIsNone(old.result); self.assertEqual(({'op': 'exile_public_graveyard_card', 'card': '$target.0'},), old.effects)

    def test_existing_executor_and_committed_followup_consumer_preserve_exile_counter_life_then_remaining_order(self):
        from quorune.semantic_runtime.graveyard_card_result_handlers import GraveyardCardCounterLifeHandler
        from quorune.semantic_runtime.public_zone_move_handlers import ExilePublicGraveyardCardHandler
        from quorune.replacement_decisions import _quantity_followup_plan, _execute_quantity_followup
        for creature, source_present in ((True, True), (True, False), (False, True)):
            with self.subTest(creature=creature, source_present=source_present):
                host, target, source = self.fixture(('creature',) if creature else ('land',))
                if not source_present:source.zone_change_counter += 1
                plan = GraveyardCardCounterLifeHandler().lower(Spec().effect(card=target.ref), self.context())
                self.assertTrue(_quantity_followup_plan(plan))
                remaining = ({'op': 'draw', 'player': '$controller', 'count': 1, 'private': True},)
                def move(current, ref, **kwargs):
                    self.assertIs(host, current); host.observed.append(('move', target.zone))
                    target.zone = 'exile'; target.zone_change_counter += 1; return target.ref
                with patch('quorune.rules.graveyard_card_results.exile_public_graveyard_card', side_effect=move), \
                        patch('quorune.semantic_runtime.executor._synchronize_instruction_observers'):
                    effects, waiting = _execute_quantity_followup(host, plan, remaining)
                expected = (['place_counters'] if source_present else []) + ['life', 'draw'] if creature else ['draw']
                self.assertEqual(expected, [e['op'] for e in effects]); self.assertFalse(waiting)
                self.assertEqual('exile', target.zone)
        old = ExilePublicGraveyardCardHandler()
        self.assertFalse(_quantity_followup_plan(old.lower({'op': old.operation, 'card': 'target-ref'}, self.context())))

    def test_new_operation_reuses_bound_target_identity_guard_instead_of_mutating_a_new_incarnation(self):
        from types import SimpleNamespace
        from quorune.semantic_runtime.direct_target_fields import bound_mutation_target_overrides
        effect = Spec().effect(); item = SimpleNamespace(context={'target_snapshots': {
            'target-ref': {'object_id': 'target', 'zone_change_counter': 0}}})
        with patch('quorune.semantic_runtime.direct_target_fields.target_identity_matches_snapshot', return_value=False) as guard:
            self.assertEqual({'card': None}, bound_mutation_target_overrides(object(), effect, item, lambda ref: 'target-ref'))
            guard.assert_called_once()
        with patch('quorune.semantic_runtime.direct_target_fields.target_identity_matches_snapshot', return_value=True):
            self.assertEqual({}, bound_mutation_target_overrides(object(), effect, item, lambda ref: 'target-ref'))


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_targeted_graveyard_exile as graveyard_native


class GraveyardCardCounterLifeNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = graveyard_native.TargetedGraveyardExileNativeTests.start
    reject = graveyard_native.TargetedGraveyardExileNativeTests.reject
    finish = graveyard_native.TargetedGraveyardExileNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [c for c in base['cards'] if c['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-result-cards.json',
            ROOT / 'tests/fixtures/graveyard-card-result-cards.json'], folder / 'targeted-result.sqlite3')
        cls.db = CardDatabase(folder / 'targeted-result.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Targeted card counter/life', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def activate_result(self, s, source, target, *, seat='A'):
        self.priority(s, seat); current = s.state.cards[source.object_id]
        action = next(a for a in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':'))
        command = {'action_id': action['id'], 'targets': [target], 'pay': 'auto'}
        self.reject(s, 'D' if seat != 'D' else 'B', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def test_complete_ooze_original_and_all_material_costs_bind_normally_without_removable_result_owners(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.graveyard_card_result_model import GRAVEYARD_CARD_COUNTER_LIFE_CAPABILITY as cap
        s, _ = self.start(738000); source = self.add(s.engine, 'Scavenging Ooze', zone='hand'); self.assert_closed(s, source)
        self.assertEqual('exact', compile_oracle_card(self.db.lookup('Scavenging Ooze'), capability_registry=self.registry,
            capability_profile='commander_review').status)
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        for missing in (cap, 'card.exile.public_graveyard', 'target.revalidate_resolution',
                'counter.producer.fixed_effect', 'life.change.effect'):
            dependencies = tuple(v for v in program.capability_dependencies if v != missing)
            raw = replace(program, coverage=(), capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(raw, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'], missing)
            self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])

    def test_actual_printed_one_green_cast_and_green_nontap_activation_resolve_creature_artifact_creature_and_noncreature_targets(self):
        for index, name in enumerate(('Grizzly Bears', 'Memnite', 'Forest')):
            with self.subTest(target=name):
                s, private = self.start(738010 + index); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
                target = self.add(s.engine, name, seat='B', zone='graveyard')
                self.assert_closed(s, source); self.assert_closed(s, target)
                self.prepare(s, mana={'G': 3}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
                self.assertEqual(1, s.state.players['A'].mana_pool.get('G', 0))
                self.assertEqual((2, 2), tuple(s.engine._numeric_stat(source.object_id, field) for field in ('power', 'toughness')))
                self.activate_result(s, source, target.ref); self.assertEqual(0, s.state.players['A'].mana_pool.get('G', 0))
                self.assertFalse(s.state.cards[source.object_id].tapped); s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s)); creature = name != 'Forest'
                self.assertEqual('exile', s.state.cards[target.object_id].zone)
                self.assertEqual(1 if creature else 0, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.assertEqual((3, 3) if creature else (2, 2),
                    tuple(s.engine._numeric_stat(source.object_id, field) for field in ('power', 'toughness')))
                self.assertEqual((41 if creature else 40, 40, 40, 40), tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s, private)

    def test_actual_empty_or_ground_sealed_graveyards_remove_the_activation_offer_and_forced_commands_cannot_pay(self):
        for index, sealed in enumerate((False, True)):
            with self.subTest(sealed=sealed):
                s, private = self.start(738020 + index); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
                if sealed:
                    target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
                    seal = self.add(s.engine, 'Ground Seal', seat='D')
                    self.assert_closed(s, target); self.assert_closed(s, seal)
                self.assert_closed(s, source); self.prepare(s, mana={'G': 3}); self.checkpoint(s)
                self.cast(s, 'A', source); self.assertIsNone(self.resolve(s)); self.priority(s, 'A')
                current = s.state.cards[source.object_id]; prefix = 'activate:' + current.ref + ':'
                self.assertFalse(any(a['id'].startswith(prefix) for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']))
                ability = s.engine._activated_abilities(current)[0]
                self.reject(s, 'A', {'action_id': prefix + ability.ability_id,
                    'targets': [target.ref if sealed else 'missing-public-graveyard-card'], 'pay': 'auto'})
                self.assertEqual(1, s.state.players['A'].mana_pool.get('G', 0)); self.assertFalse(current.tapped)
                self.assertEqual(40, s.state.players['A'].life); self.finish(s, private)


    def test_actual_legacy_cremate_printed_black_cost_exiles_then_draws_without_new_result_consequences(self):
        s, private = self.start(738030); source = self.add(s.engine, 'Cremate', zone='hand')
        target = self.add(s.engine, 'Memnite', seat='B', zone='graveyard')
        self.assert_closed(s, source); self.assert_closed(s, target)
        self.prepare(s, mana={'B': 1}); self.checkpoint(s); before = len(s.state.players['A'].zones['hand'])
        self.cast(s, 'A', source, [target.ref]); self.assertEqual(0, s.state.players['A'].mana_pool.get('B', 0))
        self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(before, len(s.state.players['A'].zones['hand']))
        self.assertEqual((40, 40, 40, 40), tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.finish(s, private)


    def test_actual_repeated_target_only_first_resolves_and_returning_card_is_a_new_incarnation_despite_player_hexproof(self):
        for index, returned in enumerate((False, True)):
            with self.subTest(returned=returned):
                s, private = self.start(738040 + index); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
                protection = self.add(s.engine, 'Leyline of Sanctity', seat='B')
                pull = self.add(s.engine, 'Pull from Eternity', zone='hand')
                for card in (source, target, protection, pull):self.assert_closed(s, card)
                original_identity = target.logical_object_id
                self.prepare(s, mana={'W': 2, 'G': 3}); self.checkpoint(s); self.cast(s, 'A', source)
                self.assertIsNone(self.resolve(s)); self.activate_result(s, source, target.ref)
                self.activate_result(s, source, target.ref); self.assertEqual(0, s.state.players['A'].mana_pool.get('G', 0))
                self.assertEqual(2, len(s.state.stack)); self.resolve_top(s)
                self.assertEqual(1, len(s.state.stack)); self.assertEqual('exile', s.state.cards[target.object_id].zone)
                self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1', 0)); self.assertEqual(41, s.state.players['A'].life)
                if returned:
                    self.cast(s, 'A', pull, [s.state.cards[target.object_id].ref]); self.resolve_top(s)
                    self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
                    self.assertNotEqual(original_identity, s.state.cards[target.object_id].logical_object_id)
                s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard' if returned else 'exile', s.state.cards[target.object_id].zone)
                self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.assertEqual((41, 40, 40, 40), tuple(s.state.players[seat].life for seat in 'ABCD'))
                self.finish(s, private)


    def test_actual_borrowed_source_dies_and_original_controller_still_gains_life_without_a_graveyard_counter(self):
        from quorune.control_effects import change_control
        s, private = self.start(738050); source = self.add(s.engine, 'Scavenging Ooze')
        change_control(s.engine, source.object_id, 'C', reason='Prepared borrowed creature source')
        target = self.add(s.engine, 'Memnite', seat='B', zone='graveyard')
        kill = self.add(s.engine, 'Doom Blade', zone='hand')
        for card in (source, target, kill):self.assert_closed(s, card)
        s.state.players['C'].mana_pool.update({'G': 1}); self.prepare(s, mana={'B': 2}); self.checkpoint(s)
        self.activate_result(s, source, target.ref, seat='C'); self.assertEqual(0, s.state.players['C'].mana_pool.get('G', 0))
        self.cast(s, 'A', kill, [source.ref]); self.resolve_top(s)
        self.assertEqual(('graveyard', 'A'), (s.state.cards[source.object_id].zone, s.state.cards[source.object_id].controller))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual(0, s.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertEqual((40, 40, 41, 40), tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.finish(s, private)

    def test_actual_cloudshift_new_source_incarnation_gets_no_counter_but_original_ability_still_gains_life(self):
        s, private = self.start(738051); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
        target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
        blink = self.add(s.engine, 'Cloudshift', zone='hand')
        for card in (source, target, blink):self.assert_closed(s, card)
        self.prepare(s, mana={'W': 2, 'G': 2}); self.checkpoint(s); self.cast(s, 'A', source)
        self.assertIsNone(self.resolve(s)); original = s.state.cards[source.object_id].logical_object_id
        self.activate_result(s, source, target.ref); self.cast(s, 'A', blink, [s.state.cards[source.object_id].ref])
        self.resolve_top(s); self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.assertNotEqual(original, s.state.cards[source.object_id].logical_object_id)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual(0, s.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertEqual((2, 2), tuple(s.engine._numeric_stat(source.object_id, field) for field in ('power', 'toughness')))
        self.assertEqual((41, 40, 40, 40), tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.finish(s, private)


    def test_actual_counter_and_life_replacements_and_counter_prohibition_preserve_independent_consequences(self):
        for index, (name, seat, counters, life) in enumerate((
                ('Hardened Scales', 'A', 2, 41), ('Rhox Faithmender', 'A', 1, 42), ('Solemnity', 'D', 0, 41))):
            with self.subTest(modifier=name):
                s, private = self.start(738060 + index); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
                modifier = self.add(s.engine, name, seat=seat)
                for card in (source, target, modifier):self.assert_closed(s, card)
                self.prepare(s, mana={'G': 3}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
                self.activate_result(s, source, target.ref); s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
                self.assertEqual('exile', s.state.cards[target.object_id].zone)
                self.assertEqual(counters, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.assertEqual((2 + counters, 2 + counters), tuple(s.engine._numeric_stat(source.object_id, f) for f in ('power', 'toughness')))
                self.assertEqual((life, 40, 40, 40), tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s, private)

    def test_actual_commander_exile_source_counter_and_life_finish_before_its_owner_command_zone_choice(self):
        s, private = self.start(738070); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
        commander_id = s.state.players['B'].zones['command'][0]; commander = s.state.cards[commander_id]
        s.engine.move_card(commander_id, 'graveyard', reason='Prepared targeted-card commander boundary')
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        self.assertTrue(s.engine._stabilize()); self.assertEqual('state.commander_zone', s.state.pending_decision.kind)
        result = s.act('pilot:B', {'action_id': 'remain'}); self.assertTrue(result.ok, result.summary)
        self.assert_closed(s, source); self.prepare(s, mana={'G': 3}); self.checkpoint(s)
        self.cast(s, 'A', source); self.assertIsNone(self.resolve(s)); self.activate_result(s, source, commander.ref)
        self.assertEqual('state.commander_zone', self.resolve(s).kind)
        self.assertEqual('exile', s.state.cards[commander_id].zone)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1', 0)); self.assertEqual(41, s.state.players['A'].life)
        self.assertEqual(('B',), tuple(s.state.pending_decision.actors)); s = self.replay(s, load=True)
        self.reject(s, 'A', {'action_id': 'command'}); result = s.act('pilot:B', {'action_id': 'command'})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual('command', s.state.cards[commander_id].zone); self.finish(s, private)

    def test_actual_post_life_fault_restores_exile_counter_life_priority_stack_and_private_state_then_loads_and_retries(self):
        import quorune.effect_runtime.life_effects as owner
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        s, private = self.start(738071); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
        target = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard')
        for card in (source, target):self.assert_closed(s, card)
        self.prepare(s, mana={'G': 3}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.activate_result(s, source, target.ref); self.priority(s, 'D')
        before = authoritative_state_hash(s.state); observed = []; original = owner._commit
        def fault(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append((s.state.cards[target.object_id].zone,
                s.state.cards[source.object_id].counters.get('+1/+1', 0), s.state.players['A'].life))
            raise GameRuleError('Injected after targeted exile counter and result life commit')
        with patch.object(owner, '_commit', side_effect=fault):result = s.act('pilot:D', {'action_id': 'pass'})
        self.assertFalse(result.ok); self.assertIn('Injected after targeted exile', result.summary)
        self.assertEqual([('exile', 1, 41)], observed); self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
        self.assertEqual(0, s.state.cards[source.object_id].counters.get('+1/+1', 0)); self.assertEqual(40, s.state.players['A'].life)
        self.assertEqual(0, s.state.players['A'].mana_pool.get('G', 0)); self.assertEqual(1, len(s.state.stack))
        s = self.replay(s, load=True); result = s.act('pilot:D', {'action_id': 'pass'}); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1', 0)); self.assertEqual(41, s.state.players['A'].life)
        self.finish(s, private)

    def noncreature_native_journey(self, seed):
        s, private = self.start(seed); source = self.add(s.engine, 'Scavenging Ooze', zone='hand')
        target = self.add(s.engine, 'Forest', seat='B', zone='graveyard')
        for card in (source, target):self.assert_closed(s, card)
        self.prepare(s, mana={'G': 3}); self.checkpoint(s); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.activate_result(s, source, target.ref); self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual(0, s.state.cards[source.object_id].counters.get('+1/+1', 0)); self.assertEqual(40, s.state.players['A'].life)
        self.finish(s, private)

    def test_fresh_native_land_for_creature_predicate_is_killed_after_positive_real_noncreature_journey(self):
        self.noncreature_native_journey(738072)
        with patch.object(Spec, 'card_form', property(lambda spec: TargetCharacteristicForm(types_all=('land',)))):
            with self.assertRaisesRegex(AssertionError, '0 != 1'):self.noncreature_native_journey(738073)

    def test_genuinely_verified_five_owner_withdrawals_block_complete_ooze_and_preserve_independent_controls(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.graveyard_card_result_model import GRAVEYARD_CARD_COUNTER_LIFE_CAPABILITY as cap
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'); known = set(discover_tests(ROOT).values())
        def ready(name, registry):
            card = self.db.lookup(name); sem = SemanticRegistry()
            register_generated_programs(self.db, sem, (card,), capability_registry=registry, capability_profile='commander_review',
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
            program = compile_best_available_card_program(self.db, card, semantic_registry=sem,
                capability_registry=registry, capability_profile='commander_review')
            return bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')['strict_capability_ready']
        baseline = CapabilityRegistry(deepcopy(raw)); index = build_index(registry_value=raw, declaration_source=doc, discovered_test_ids=known)
        baseline.mark_evidence_verified(validate_capability_evidence_index(index, registry=baseline))
        self.assertTrue(ready('Scavenging Ooze', baseline))
        for missing in (cap, 'card.exile.public_graveyard', 'counter.producer.fixed_effect', 'life.change.effect', 'target.revalidate_resolution'):
            changed = deepcopy(raw); next(row for row in changed['capabilities'] if row['id'] == missing).update(
                status='blocked', blockers=['Targeted-card result or one canonical owner withdrawn'])
            registry = CapabilityRegistry(changed); current = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(current, registry=registry))
            status = registry.closure((missing,), profile='commander_review'); self.assertFalse(status.trusted)
            self.assertIn('status:' + missing + ':blocked', status.blockers)
            self.assertFalse(any('unverified' in b for b in status.blockers)); self.assertFalse(ready('Scavenging Ooze', registry))
            self.assertEqual(missing not in {'card.exile.public_graveyard', 'target.revalidate_resolution'}, ready('Cremate', registry))
            self.assertEqual(missing not in {'life.change.effect', 'target.revalidate_resolution'}, ready('Graveyard Shovel', registry))
            for name in ('Grizzly Bears', 'Forest'):self.assertTrue(ready(name, registry), (missing, name))


class GraveyardPermanentCardCounterContractTests(unittest.TestCase):
    instruction = (
        'Exile target card from a graveyard. If it was a permanent card, '
        'put a +1/+1 counter on this permanent.'
    )

    def test_permanent_card_result_uses_exact_closed_grammar_and_no_life_capability(self):
        from quorune.compiler.graveyard_card_result_templates import parse_graveyard_permanent_card_counter
        from quorune.compiler.public_zone_move_templates import public_graveyard_card_exile_template
        from quorune.graveyard_card_result_model import (
            GRAVEYARD_PERMANENT_CARD_COUNTER_DEPENDENCIES, GraveyardPermanentCardCounterSpec,
        )
        from quorune.rules.graveyard_card_results import graveyard_card_counter_life_payload_dependencies

        spec = parse_graveyard_permanent_card_counter(self.instruction)
        self.assertEqual(GraveyardPermanentCardCounterSpec(), spec)
        self.assertEqual(spec, GraveyardPermanentCardCounterSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        self.assertEqual(GRAVEYARD_PERMANENT_CARD_COUNTER_DEPENDENCIES,
            graveyard_card_counter_life_payload_dependencies(spec.effect()))
        self.assertNotIn('life.change.effect', spec.dependencies)
        template = public_graveyard_card_exile_template(self.instruction)
        self.assertEqual(
            {'zones': ['graveyard'], 'categories': ['card'], 'owner_relation': 'any', 'count': 1},
            template.target_schema,
        )
        for candidate in (
            self.instruction.replace('target card', 'target permanent card'),
            self.instruction.replace('If it was', 'If you exiled'),
            self.instruction.replace('this permanent', 'this creature'),
            self.instruction.replace('a +1/+1 counter', 'two +1/+1 counters'),
            self.instruction + ' Draw a card.',
        ):
            self.assertIsNone(parse_graveyard_permanent_card_counter(candidate))
        for invalid in (
            {'schema_version': True}, {'card_type': 'creature'}, {'life_gain': 0},
            {'source_counter': {'counter': '+1/+1', 'amount': True}},
        ):
            with self.assertRaises(ValueError):
                GraveyardPermanentCardCounterSpec.from_dict({**spec.to_dict(), **invalid})
        self.assertEqual(Spec(), parse(TEXT))


class GraveyardPermanentCardCounterNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'graveyard-permanent-source-counter.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/graveyard-permanent-source-counter-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            'Targeted permanent card source counter',
            [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'), DeckEntry('Plains', 30)],
            ['Isamaru, Hound of Konda'],
        )

    def prepare_sash(self, seed, *, in_hand=False):
        game = self.session(seed)
        sash = self.add(game.engine, 'Lion Sash', zone='hand' if in_hand else 'battlefield')
        self.assert_closed(game, sash)
        return game, sash

    def reject(self, game, seat, command):
        from quorune.record import authoritative_state_hash

        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act('pilot:' + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def submit(self, game, seat, command):
        result = game.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def action_for(self, game, sash, *, reconfigure=None):
        self.priority(game, 'A')
        prefix = 'activate:' + game.state.cards[sash.object_id].ref + ':'
        rows = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        matching = [
            row for row in rows if row['id'].startswith(prefix)
            and (
                row['id'].endswith('-reconfigure-' + reconfigure) if reconfigure
                else 'graveyard' in (row.get('target_schema') or {}).get('zones', ())
            )
        ]
        self.assertEqual(1, len(matching), rows)
        return matching[0]

    def source_status(self, game, sash, target):
        source = game.state.cards[sash.object_id]
        data = game.engine._effective_card_data(source)
        return (
            'creature' in game.engine._type_parts(data['type_line'])[0],
            source.attached_to,
            source.counters.get('+1/+1', 0),
            tuple(game.engine._numeric_stat(target.object_id, field) for field in ('power', 'toughness')),
        )

    def test_lion_sash_paid_exile_distinguishes_permanent_cards_and_updates_equipped_stats(self):
        from quorune.oracle_ir import compile_oracle_card

        game, sash = self.prepare_sash(1104, in_hand=True)
        receiver = self.add(game.engine, 'Grizzly Bears')
        land = self.add(game.engine, 'Forest', seat='B', zone='graveyard')
        instant = self.add(game.engine, 'Counterspell', seat='B', zone='graveyard')
        artifact = self.add(game.engine, 'Sol Ring', seat='C', zone='graveyard')
        private = self.add(game.engine, 'Unsummon', seat='C', zone='hand')
        ir = compile_oracle_card(self.db.lookup('Lion Sash'), capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.prepare(game, mana={'C': 5, 'W': 4})
        self.checkpoint(game)
        self.cast_for(game, 'A', sash)
        self.assertIsNone(self.resolve(game))

        attach = self.action_for(game, sash, reconfigure='attach')
        self.reject(game, 'A', {'action_id': attach['id'], 'targets': [sash.ref], 'pay': 'auto'})
        self.submit(game, 'A', {
            'action_id': attach['id'], 'targets': [receiver.ref], 'pay': 'manual', 'payment': {'C': 2},
        })
        self.assertIsNone(self.resolve(game))
        self.assertEqual((False, receiver.object_id, 0, (2, 2)), self.source_status(game, sash, receiver))

        for target, counters in ((land, 1), (instant, 1), (artifact, 2)):
            action = self.action_for(game, sash)
            before = game.state.players['A'].mana_pool['W']
            command = {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}
            self.reject(game, 'D', command)
            self.reject(game, 'A', {**command, 'targets': [private.ref]})
            self.assertNotIn(private.ref, json.dumps(game.packet('pilot:A', full=True)))
            self.submit(game, 'A', command)
            self.assertEqual(before - 1, game.state.players['A'].mana_pool['W'])
            self.assertIsNone(self.resolve(game))
            self.assertEqual('exile', game.state.cards[target.object_id].zone)
            received = 2 + counters
            self.assertEqual((False, receiver.object_id, counters, (received, received)),
                self.source_status(game, sash, receiver))
            self.assertEqual((40, 40, 40, 40), tuple(game.state.players[p].life for p in 'ABCD'))

        detach = self.action_for(game, sash, reconfigure='unattach')
        self.submit(game, 'A', {'action_id': detach['id'], 'pay': 'manual', 'payment': {'C': 2}})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((True, None, 2, (2, 2)), self.source_status(game, sash, receiver))
        self.assertEqual((3, 3),
            tuple(game.engine._numeric_stat(sash.object_id, field) for field in ('power', 'toughness')))
        self.replay(game, load=True)

    def test_lion_sash_only_current_target_and_original_source_receive_the_result(self):
        game, sash = self.prepare_sash(1105)
        land = self.add(game.engine, 'Forest', seat='B', zone='graveyard')
        artifact = self.add(game.engine, 'Sol Ring', seat='C', zone='graveyard')
        bounce = self.add(game.engine, 'Unsummon', seat='B', zone='hand')
        self.prepare(game, mana={'W': 3})
        game.state.players['B'].mana_pool['U'] = 1
        self.checkpoint(game)
        for target in (land, land):
            action = self.action_for(game, sash)
            self.submit(game, 'A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertIsNone(self.resolve(game))
        self.assertEqual('exile', game.state.cards[land.object_id].zone)
        self.assertEqual(1, game.state.cards[sash.object_id].counters.get('+1/+1', 0))

        action = self.action_for(game, sash)
        self.submit(game, 'A', {'action_id': action['id'], 'targets': [artifact.ref], 'pay': 'auto'})
        self.cast_for(game, 'B', bounce, [sash.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual('exile', game.state.cards[artifact.object_id].zone)
        self.assertEqual('hand', game.state.cards[sash.object_id].zone)
        self.assertEqual(0, game.state.cards[sash.object_id].counters.get('+1/+1', 0))
        self.replay(game, load=True)

    def test_lion_sash_permanent_predicate_mutation_is_killed_by_land_result(self):
        from types import SimpleNamespace

        def witness():
            game, sash = self.prepare_sash(1106)
            land = self.add(game.engine, 'Forest', seat='B', zone='graveyard')
            self.prepare(game, mana={'W': 1})
            action = self.action_for(game, sash)
            self.submit(game, 'A', {'action_id': action['id'], 'targets': [land.ref], 'pay': 'auto'})
            self.assertIsNone(self.resolve(game))
            self.assertEqual('exile', game.state.cards[land.object_id].zone)
            self.assertEqual(1, game.state.cards[sash.object_id].counters.get('+1/+1', 0), 'land result')

        witness()
        mutant = SimpleNamespace(PERMANENT_CARD=SimpleNamespace(types_any=('creature',)))
        with patch('quorune.rules.graveyard_card_results.PublicGraveyardCardTargetKind', mutant):
            with self.assertRaisesRegex(AssertionError, 'land result'):
                witness()

    def test_lion_sash_untrusted_result_owners_leave_a_material_residual(self):
        from copy import deepcopy
        from quorune.graveyard_card_result_model import GRAVEYARD_PERMANENT_CARD_COUNTER_DEPENDENCIES
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        original = self.db.lookup('Lion Sash', fuzzy=False)
        control = self.db.lookup('Grizzly Bears', fuzzy=False)
        profile = 'commander_review'
        for missing in GRAVEYARD_PERMANENT_CARD_COUNTER_DEPENDENCIES:
            with self.subTest(capability=missing):
                changed = deepcopy(raw)
                row = next(row for row in changed['capabilities'] if row['id'] == missing)
                row.update(status='blocked', blockers=['Targeted source-counter owner withdrawn'])
                registry = CapabilityRegistry(changed)
                evidence = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
                closure = registry.closure((missing,), profile=profile)
                self.assertIn('status:' + missing + ':blocked', closure.blockers)
                self.assertNotEqual('exact', compile_oracle_card(
                    original, capability_registry=registry, capability_profile=profile,
                ).status)
                self.assertEqual('exact', compile_oracle_card(
                    control, capability_registry=registry, capability_profile=profile,
                ).status)


if __name__ == '__main__':
    unittest.main()
