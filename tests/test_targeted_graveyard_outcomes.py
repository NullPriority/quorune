"""Independent pinned target scope and creature exile outcome assertions."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.targeted_graveyard_outcome_templates import parse_targeted_graveyard_exile_outcome
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.object_query import ObjectQueryResult
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.targeted_graveyard_outcomes import (
    commit_targeted_graveyard_exiles,
    targeted_graveyard_outcome_effect,
    targeted_graveyard_outcome_schema,
)
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError
from quorune.semantic_runtime.targeted_graveyard_outcome_handlers import lower_targeted_graveyard_outcome
from quorune.targeted_graveyard_outcome_model import (
    CreatureExileOutcome,
    GraveyardExileTargetScope,
    TargetedGraveyardExileOutcomeSpec,
)
from scripts.build_test_database import build_fixture_database
import test_targeted_graveyard_exile as graveyard_native


class TargetedGraveyardExileOutcomeContractTests(unittest.TestCase):
    def test_pinned_outcome_clauses_keep_full_target_scope_and_consequence(self):
        cases = (
            ('Exile up to one target card from a graveyard. If a creature card was exiled this way, '
             'each opponent loses 2 life and you gain 2 life.',
             GraveyardExileTargetScope.UP_TO_ONE, CreatureExileOutcome.DRAIN_TWO_ONCE),
            ('Exile up to two target cards from a single graveyard. If at least one creature card was exiled this way, '
             'each opponent loses 2 life and you gain 2 life.',
             GraveyardExileTargetScope.UP_TO_TWO_SAME_GRAVEYARD, CreatureExileOutcome.DRAIN_TWO_ONCE),
            ('Exile up to two target cards from graveyards. You gain 1 life for each creature card exiled this way.',
             GraveyardExileTargetScope.UP_TO_TWO, CreatureExileOutcome.GAIN_ONE_PER_CREATURE),
            ('Exile up to two target cards from graveyards. For each creature card exiled this way, you gain 1 life.',
             GraveyardExileTargetScope.UP_TO_TWO, CreatureExileOutcome.GAIN_ONE_PER_CREATURE),
            ('Exile up to one target card from a graveyard. If a creature card is exiled this way, create a Clue token. '
             '(It\'s an artifact with "{2}, Sacrifice this token: Draw a card.")',
             GraveyardExileTargetScope.UP_TO_ONE, CreatureExileOutcome.CREATE_ONE_CLUE),
            ('Exile target card from a graveyard. If a creature card was exiled this way, create a Food token.',
             GraveyardExileTargetScope.ONE, CreatureExileOutcome.CREATE_ONE_FOOD),
        )
        for text, scope, outcome in cases:
            with self.subTest(scope=scope, outcome=outcome):
                self.assertEqual(TargetedGraveyardExileOutcomeSpec(scope, outcome),
                    parse_targeted_graveyard_exile_outcome(text))

    def test_reminders_cannot_mask_material_unknown_or_optional_resolution_branches(self):
        clue = 'Exile up to one target card from a graveyard. If a creature card is exiled this way, create a Clue token.'
        self.assertEqual(CreatureExileOutcome.CREATE_ONE_CLUE, parse_targeted_graveyard_exile_outcome(clue).outcome)
        unsupported = (
            clue + ' Draw a card.',
            clue + ' (Then draw.)',
            'You may exile target card from a graveyard. If a creature card is exiled this way, you gain 2 life. '
            'If a noncreature card is exiled this way, this creature gets +1/+1 until end of turn.',
            'Exile up to two target cards from graveyards. For each creature card exiled this way, '
            'each opponent loses 1 life and you gain 1 life.',
            'Exile up to two target cards from a single graveyard. For each creature card exiled this way, you gain 1 life.',
            clue.replace('a creature card', 'an artifact card'),
            clue.replace('a Clue token', 'two Clue tokens'),
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(parse_targeted_graveyard_exile_outcome(text))

    def test_scope_distinguishes_required_target_optional_target_and_single_graveyard(self):
        self.assertEqual((1, 1, False), self.scope_fields(GraveyardExileTargetScope.ONE))
        self.assertEqual((0, 1, False), self.scope_fields(GraveyardExileTargetScope.UP_TO_ONE))
        self.assertEqual((0, 2, False), self.scope_fields(GraveyardExileTargetScope.UP_TO_TWO))
        self.assertEqual((0, 2, True), self.scope_fields(GraveyardExileTargetScope.UP_TO_TWO_SAME_GRAVEYARD))

    @staticmethod
    def scope_fields(scope):
        return scope.minimum, scope.maximum, scope.same_graveyard

    def test_descriptor_codec_preserves_only_owned_scope_and_outcome_pairs(self):
        spec = TargetedGraveyardExileOutcomeSpec(
            GraveyardExileTargetScope.UP_TO_TWO_SAME_GRAVEYARD, CreatureExileOutcome.DRAIN_TWO_ONCE,
        )
        encoded = json.loads(json.dumps(spec.to_dict()))
        self.assertEqual(spec, TargetedGraveyardExileOutcomeSpec.from_dict(encoded))
        with self.assertRaises(FrozenInstanceError):
            spec.outcome = CreatureExileOutcome.CREATE_ONE_CLUE
        for invalid in (
            {'schema_version': True}, {'schema_version': 2}, {'scope': []}, {'scope': 'up_to_three'},
            {'outcome': 'gain_one_per_creature'}, {'outcome': 'create_one_food'}, {'multiplier': 1},
        ):
            with self.subTest(changed=invalid), self.assertRaises(ValueError):
                TargetedGraveyardExileOutcomeSpec.from_dict(encoded | invalid)


class TargetedGraveyardExileOutcomeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.context = ReadOnlyHandlerContext.from_sequences(
            actor='A', default_reason='Creature exile outcome', seats='ABC', active_seats='ABC', apnap_order='ABC',
        )
        self.counted = TargetedGraveyardExileOutcomeSpec(
            GraveyardExileTargetScope.UP_TO_TWO, CreatureExileOutcome.GAIN_ONE_PER_CREATURE,
        )
        self.one_graveyard = TargetedGraveyardExileOutcomeSpec(
            GraveyardExileTargetScope.UP_TO_TWO_SAME_GRAVEYARD, CreatureExileOutcome.DRAIN_TWO_ONCE,
        )
        self.food = TargetedGraveyardExileOutcomeSpec(
            GraveyardExileTargetScope.ONE, CreatureExileOutcome.CREATE_ONE_FOOD,
        )

    def lower(self, spec, refs, **changes):
        effect = targeted_graveyard_outcome_effect(spec) | {'cards': refs} | changes
        plan = lower_targeted_graveyard_outcome(effect, self.context, handler_id='graveyard-exile-target-set')
        intent, = plan.intents
        return intent

    def fixture(self):
        cards = {
            ref: SimpleNamespace(ref=ref, object_id=ref, logical_object_id=f'{ref}@1', owner=owner,
                zone='graveyard', face_down=False, is_card_object=True, types=('land',))
            for ref, owner in (('first', 'B'), ('second', 'C'))
        }

        def public_query(card):
            self.assertEqual('exile', card.zone)
            self.assertFalse(card.face_down)
            return ObjectQueryResult(
                object_id=card.object_id, ref=card.ref, printed_name='Public card', owner=card.owner,
                controller=card.owner, zone=card.zone, types=card.types, logical_object_id=card.logical_object_id,
            )

        host = SimpleNamespace(
            _require_seat=Mock(),
            _resolve_object=Mock(side_effect=lambda actor, ref, *, zones: cards[ref]),
            _move_cards_simultaneously=Mock(),
            _public_object_query_result=Mock(side_effect=public_query),
        )
        return host, cards

    def test_lowering_reuses_fixed_target_fields_and_keeps_mandatory_one_distinct_from_zero(self):
        self.assertEqual({'zones': ['graveyard'], 'categories': ['card'], 'owner_relation': 'any', 'count': 1},
            targeted_graveyard_outcome_schema(self.food))
        self.assertEqual({'zones': ['graveyard'], 'categories': ['card'], 'owner_relation': 'any', 'up_to': 2, 'same_owner': True},
            targeted_graveyard_outcome_schema(self.one_graveyard))
        self.assertEqual((), self.lower(self.counted, []).object_refs)
        self.assertEqual(('first', 'second'), self.lower(self.counted, ['first', 'second']).object_refs)
        self.assertEqual(('first',), self.lower(self.food, ['first']).object_refs)
        for spec, refs, changes in (
            (self.food, [], {}),
            (self.counted, ['first', 'first'], {}),
            (self.counted, ['first', 'second', 'third'], {}),
            (self.counted, ['first'], {'maximum_targets': True}),
            (self.counted, ['first'], {'announced_target_count': True}),
            (self.counted, ['first'], {'followup': {'schema_version': 1, 'scope': 'up_to_two'}}),
        ):
            with self.subTest(spec=spec, refs=refs, changes=changes), self.assertRaises(SemanticNodeError):
                self.lower(spec, refs, **changes)

    def test_receipt_uses_post_move_public_types_and_counts_each_actual_incarnation_once(self):
        host, cards = self.fixture()
        intent = self.lower(self.counted, ['first', 'second'])

        def move(changes, **kwargs):
            self.assertEqual((('first', 'exile'), ('second', 'exile')), changes)
            self.assertEqual((), kwargs['replacement_selections'])
            for card in cards.values():
                card.zone = 'exile'
                card.types = ('creature', 'artifact')
                card.logical_object_id = f'{card.ref}@2'
            return [cards['first'], cards['second'], cards['first']]

        host._move_cards_simultaneously.side_effect = move
        self.assertEqual(2, commit_targeted_graveyard_exiles(host, intent))
        host._move_cards_simultaneously.assert_called_once()

    def test_redirected_face_down_and_stayed_cards_do_not_read_private_type_or_count(self):
        for destination, face_down, changed in (('hand', False, True), ('exile', True, True), ('graveyard', False, False)):
            with self.subTest(destination=destination, face_down=face_down):
                host, cards = self.fixture()
                intent = self.lower(self.counted, ['first'])

                def move(changes, **kwargs):
                    card = cards['first']
                    card.zone = destination
                    card.face_down = face_down
                    card.types = ('creature',)
                    if changed:
                        card.logical_object_id = 'first@2'
                    return [card]

                host._move_cards_simultaneously.side_effect = move
                self.assertEqual(0, commit_targeted_graveyard_exiles(host, intent))
                host._public_object_query_result.assert_not_called()

    def test_zero_targets_and_rejected_sets_do_not_call_the_movement_port(self):
        host, cards = self.fixture()
        self.assertEqual(0, commit_targeted_graveyard_exiles(host, self.lower(self.counted, [])))
        host._resolve_object.assert_not_called()
        with self.assertRaisesRegex(GameRuleError, 'one graveyard'):
            commit_targeted_graveyard_exiles(host, self.lower(self.one_graveyard, ['first', 'second']))
        cards['first'].is_card_object = False
        with self.assertRaisesRegex(GameRuleError, 'physical graveyard cards'):
            commit_targeted_graveyard_exiles(host, self.lower(self.counted, ['first']))
        host._move_cards_simultaneously.assert_not_called()

    def test_fresh_count_requested_targets_mutation_fails_when_the_only_move_is_redirected(self):
        def assert_no_creature_exiled():
            host, cards = self.fixture()
            intent = self.lower(self.counted, ['first'])

            def move(changes, **kwargs):
                card = cards['first']
                card.zone = 'hand'
                card.logical_object_id = 'first@2'
                card.types = ('creature',)
                return [card]

            host._move_cards_simultaneously.side_effect = move
            self.assertEqual(0, commit_targeted_graveyard_exiles(host, intent))

        assert_no_creature_exiled()
        with patch('quorune.rules.targeted_graveyard_outcomes._partition_exiled_cards', return_value=(1, 0)):
            with self.assertRaisesRegex(AssertionError, '0 != 1'):
                assert_no_creature_exiled()


class TargetedGraveyardExileOutcomeIntegrationTests(unittest.TestCase):
    def test_normal_effect_compiler_and_raw_minimum_require_the_result_and_exact_target_schema(self):
        from functools import partial

        from quorune.card_programs.descriptor_requirements import _owned_operation_payload_dependencies
        from quorune.compiler.effect_template_composition import reviewed_effect_template_composition
        from quorune.oracle_ir import _effect_template
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.rules.targeted_graveyard_outcomes import (
            TARGETED_GRAVEYARD_OUTCOME_CAPABILITY,
            targeted_graveyard_outcome_runtime_dependencies,
        )

        body = ('Exile up to two target cards from a single graveyard. If at least one creature card was exiled this way, '
                'each opponent loses 2 life and you gain 2 life.')
        atomic = partial(_effect_template, card_name='Neutral source')
        self.assertIsNone(atomic(body)[0])
        template, effects, schema, mechanics = reviewed_effect_template_composition(
            body, source_name='Neutral source', compile_atomic=atomic, compile_fixed=atomic,
        )
        self.assertEqual('targeted-graveyard-exile-outcome-v1', template)
        effect, = effects
        owners = {
            TARGETED_GRAVEYARD_OUTCOME_CAPABILITY, 'card.exile.public_graveyard',
            'resolution.effect.fixed_homogeneous_target_set', 'target.revalidate_resolution', 'life.change.effect',
        }
        self.assertEqual(owners, set(capability_dependencies_for_node(
            effects=effects, target_schema=schema, mechanic_ids=mechanics)))
        self.assertEqual(owners, set(_owned_operation_payload_dependencies(effect)))
        program = SimpleNamespace(effects=effects, target_schema=schema, handlers=(), coverage=())
        self.assertEqual(owners, set(targeted_graveyard_outcome_runtime_dependencies(program)))
        for change in ({'up_to': True}, {'same_owner': False}, {'type_any': ['creature']}):
            with self.subTest(changed=change), self.assertRaises(ValueError):
                altered = SimpleNamespace(effects=effects, handlers=(), target_schema=schema | change)
                targeted_graveyard_outcome_runtime_dependencies(altered)
        nested = SimpleNamespace(effects=({'op': 'other', 'effects': effects},), handlers=(), target_schema=schema)
        with self.assertRaises(ValueError):
            targeted_graveyard_outcome_runtime_dependencies(nested)

    def test_predefined_token_outcomes_keep_the_ordinary_food_and_clue_definitions(self):
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        from quorune.replacement.immutable import thaw_value
        from quorune.rules.targeted_graveyard_outcomes import creature_exile_followup

        for scope, outcome, name, profile in (
            (GraveyardExileTargetScope.ONE, CreatureExileOutcome.CREATE_ONE_FOOD, 'Food', 'two_tap_sac_gain_three_life_v1'),
            (GraveyardExileTargetScope.UP_TO_ONE, CreatureExileOutcome.CREATE_ONE_CLUE, 'Clue', 'two_sac_draw_card_v1'),
        ):
            with self.subTest(token=name):
                recipe = TargetedGraveyardExileOutcomeSpec(scope, outcome)
                self.assertEqual((), creature_exile_followup(recipe, 0).effects)
                effect, = creature_exile_followup(recipe, 1).effects
                self.assertEqual(f'{name} Token', effect['name'])
                self.assertEqual(1, effect['quantity'])
                self.assertEqual(profile, effect['characteristics']['activated_ability_profile'])
                self.assertEqual(fixed_token_creation_effect_template(f'Create a {name} token.').effect, thaw_value(effect))

    def test_fixed_drain_and_counted_gain_are_distinct_single_followup_instructions(self):
        from quorune.rules.targeted_graveyard_outcomes import creature_exile_followup

        fixed = TargetedGraveyardExileOutcomeSpec(
            GraveyardExileTargetScope.UP_TO_TWO_SAME_GRAVEYARD, CreatureExileOutcome.DRAIN_TWO_ONCE,
        )
        counted = TargetedGraveyardExileOutcomeSpec(
            GraveyardExileTargetScope.UP_TO_TWO, CreatureExileOutcome.GAIN_ONE_PER_CREATURE,
        )
        for count in (1, 2):
            self.assertEqual({'op': 'drain_each_opponent', 'amount': 2},
                dict(creature_exile_followup(fixed, count).effects[0]))
            life, = creature_exile_followup(counted, count).effects
            self.assertEqual({'op': 'life', 'player': '$controller', 'delta': count}, dict(life))
        self.assertEqual((), creature_exile_followup(fixed, 0).effects)
        self.assertEqual((), creature_exile_followup(counted, 0).effects)
        for bad in (-1, 3, True):
            with self.assertRaises(ValueError):
                creature_exile_followup(counted, bad)

    def test_existing_handler_routes_outcome_to_committed_consumer_and_leaves_plain_exile_separate(self):
        from quorune.replacement_decisions import _quantity_followup_plan
        from quorune.semantic_runtime.fixed_target_set_handlers import FIXED_TARGET_SET_HANDLERS
        from quorune.semantic_runtime.targeted_graveyard_outcome_intent import TargetedGraveyardExileOutcomeIntent

        context = ReadOnlyHandlerContext.from_sequences(
            actor='A', default_reason='Targeted exile', seats='ABC', active_seats='ABC', apnap_order='ABC',
        )
        recipe = TargetedGraveyardExileOutcomeSpec(
            GraveyardExileTargetScope.UP_TO_TWO, CreatureExileOutcome.GAIN_ONE_PER_CREATURE,
        )
        outcome = targeted_graveyard_outcome_effect(recipe) | {'cards': ['card']}
        handler = next(handler for handler in FIXED_TARGET_SET_HANDLERS if handler.operation == outcome['op'])
        plan = handler.lower(outcome, context)
        intent, = plan.intents
        self.assertIsInstance(intent, TargetedGraveyardExileOutcomeIntent)
        self.assertTrue(_quantity_followup_plan(plan))
        plain = {key: value for key, value in outcome.items() if key != 'followup'}
        old_plan = handler.lower(plain, context)
        self.assertFalse(_quantity_followup_plan(old_plan))
        self.assertNotIsInstance(old_plan.intents[0], TargetedGraveyardExileOutcomeIntent)


class TargetedGraveyardExileOutcomeNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = graveyard_native.TargetedGraveyardExileNativeTests.start
    reject = graveyard_native.TargetedGraveyardExileNativeTests.reject
    finish = graveyard_native.TargetedGraveyardExileNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        db_path = folder / 'targeted-graveyard-outcomes.sqlite3'
        build_fixture_database([
            basic,
            ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/targeted-graveyard-outcome-cards.json',
            ROOT / 'tests/fixtures/transforming-saga-return-cards.json',
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Targeted graveyard exile outcomes', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def activate(self, session, source, targets, *, seat='A'):
        self.priority(session, seat)
        card = session.state.cards[source.object_id]
        actions = session.packet(f'pilot:{seat}', full=True)['decision']['ctx']['legal']['actions']
        action = next(action for action in actions if action['id'].startswith(f'activate:{card.ref}:'))
        command = {'action_id': action['id'], 'targets': list(targets), 'pay': 'auto'}
        self.reject(session, 'D' if seat != 'D' else 'B', command)
        result = session.act(f'pilot:{seat}', command)
        self.assertTrue(result.ok, result.summary)

    def assert_life(self, session, totals):
        self.assertEqual(totals, tuple(session.state.players[seat].life for seat in 'ABCD'))

    def test_actual_diregraf_entry_drain_applies_once_to_opponents_only_after_a_creature_exiles(self):
        for index, chosen in enumerate(('creature', 'noncreature', 'none')):
            with self.subTest(chosen=chosen):
                session, private = self.start(739010 + index)
                source = self.add(session.engine, 'Diregraf Scavenger', zone='hand')
                creature = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
                land = self.add(session.engine, 'Forest', seat='D', zone='graveyard')
                for card in (source, creature, land):
                    self.assert_closed(session, card)
                self.prepare(session, mana={'C': 3, 'B': 1})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.assertEqual('semantic.target', self.resolve(session).kind)
                self.assertEqual(('A',), tuple(session.state.pending_decision.actors))
                session = self.replay(session, load=True)
                options = {'creature': [creature.ref], 'noncreature': [land.ref], 'none': []}
                self.target(session, 'A', options[chosen])
                self.assertIsNone(self.resolve(session))
                self.assertEqual('exile' if chosen == 'creature' else 'graveyard', session.state.cards[creature.object_id].zone)
                self.assertEqual('exile' if chosen == 'noncreature' else 'graveyard', session.state.cards[land.object_id].zone)
                self.assert_life(session, (42, 38, 38, 38) if chosen == 'creature' else (40, 40, 40, 40))
                self.finish(session, private)

    def test_actual_soul_shackled_rejects_cross_graveyard_pair_and_drains_two_once_for_two_creatures(self):
        session, private = self.start(739020)
        source = self.add(session.engine, 'Soul-Shackled Zombie', zone='hand')
        first = self.add(session.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='first-zombie-target')
        second = self.add(session.engine, 'Memnite', seat='B', zone='graveyard', ref='second-zombie-target')
        other = self.add(session.engine, 'Grizzly Bears', seat='D', zone='graveyard', ref='other-zombie-target')
        for card in (source, first, second, other):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 3, 'B': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        decision = self.resolve(session)
        self.assertEqual('semantic.target', decision.kind)
        self.reject(session, 'A', {'action_id': 'choose', 'targets': [first.ref, other.ref]})
        self.reject(session, 'A', {'action_id': 'choose', 'targets': [first.ref, first.ref]})
        self.target(session, 'A', [first.ref, second.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('exile', 'exile', 'graveyard'), tuple(
            session.state.cards[card.object_id].zone for card in (first, second, other)))
        self.assert_life(session, (42, 38, 38, 38))
        self.finish(session, private)

    def test_actual_tymaret_pays_printed_activation_and_counts_surviving_creatures_across_graveyards(self):
        for index, remove_first in enumerate((False, True)):
            with self.subTest(remove_first=remove_first):
                session, private = self.start(739030 + index)
                source = self.add(session.engine, 'Tymaret, Chosen from Death', zone='hand')
                first = self.add(session.engine, 'Grizzly Bears', seat='B', zone='graveyard')
                second = self.add(session.engine, 'Memnite', seat='D', zone='graveyard')
                response = self.add(session.engine, 'Cremate', seat='B', zone='hand')
                for card in (source, first, second, response):
                    self.assert_closed(session, card)
                session.state.players['B'].mana_pool['B'] = 1
                self.prepare(session, mana={'C': 1, 'B': 3})
                self.checkpoint(session)
                self.cast(session, 'A', source)
                self.assertIsNone(self.resolve(session))
                self.assertEqual({'C': 1, 'B': 1}, {k: v for k, v in session.state.players['A'].mana_pool.items() if v})
                self.assertEqual(2, session.engine._numeric_stat(source.object_id, 'toughness'))
                self.activate(session, source, [first.ref, second.ref])
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.assertFalse(session.state.cards[source.object_id].tapped)
                if remove_first:
                    self.cast(session, 'B', response, [first.ref])
                    self.resolve_top(session)
                    self.assertEqual('exile', session.state.cards[first.object_id].zone)
                    self.assert_life(session, (40, 40, 40, 40))
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(('exile', 'exile'), tuple(session.state.cards[card.object_id].zone for card in (first, second)))
                self.assert_life(session, (41 if remove_first else 42, 40, 40, 40))
                self.finish(session, private)

    def proliferate_saga(self, session, spell, saga):
        self.cast(session, 'A', spell)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        command = {'action_id': 'choose', 'objects': [saga.ref], 'plan': 'PROLIFERATE'}
        self.reject(session, 'D', command)
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_binding_preserves_all_three_saga_chapters_and_counts_its_chapter_two_creatures(self):
        session, private = self.start(739040)
        saga = self.add(session.engine, 'The Binding of the Titans', zone='hand')
        first = self.add(session.engine, 'Contentious Plan', zone='hand', ref='advance-to-chapter-two')
        second = self.add(session.engine, 'Contentious Plan', zone='hand', ref='advance-to-chapter-three')
        creature = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
        double_face = self.add(session.engine, 'Kazandu Mammoth // Kazandu Valley', seat='D', zone='graveyard')
        land = self.add(session.engine, 'Forest', zone='graveyard', ref='return-in-chapter-three')
        for card in (saga, first, second, creature, double_face, land):
            self.assert_closed(session, card)
        self.prepare(session, mana={'G': 1, 'U': 5})
        self.checkpoint(session)
        before = tuple(len(session.state.players[seat].zones['library']) for seat in 'ABCD')
        self.cast(session, 'A', saga)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[saga.object_id].counters['lore'])
        self.assertEqual(tuple(size - 3 for size in before), tuple(
            len(session.state.players[seat].zones['library']) for seat in 'ABCD'))

        self.proliferate_saga(session, first, saga)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.assertEqual(2, session.state.cards[saga.object_id].counters['lore'])
        session = self.replay(session, load=True)
        self.target(session, 'A', [creature.ref, double_face.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('exile', 'exile'), tuple(
            session.state.cards[card.object_id].zone for card in (creature, double_face)))
        self.assert_life(session, (42, 40, 40, 40))

        self.proliferate_saga(session, second, saga)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [land.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[land.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[saga.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.finish(session, private)

    @staticmethod
    def tokens(session, name):
        return [card for card in session.state.cards.values()
            if card.is_token and card.zone == 'battlefield' and card.controller == 'A' and card.printed_name == name]

    def test_actual_raven_entry_and_attack_create_clues_whose_draws_trigger_second_draw_drain(self):
        session, private = self.start(739050)
        raven = self.add(session.engine, 'Raven Eagle', zone='hand')
        first = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
        second = self.add(session.engine, 'Grizzly Bears', seat='D', zone='graveyard')
        fervor = self.add(session.engine, 'Fervor')
        mana_sources = [self.add(session.engine, 'Plains', ref=f'clue-mana-{index}') for index in range(4)]
        for card in (raven, first, second, fervor, *mana_sources):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 2, 'B': 1})
        self.checkpoint(session)
        key = str(session.state.turn_sequence)
        drawn = session.state.players['A'].stats.get('cards_drawn_by_turn', {}).get(key, 0)
        self.assertEqual(0, drawn)
        self.cast(session, 'A', raven)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [first.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[first.object_id].zone)
        self.assertEqual(1, len(self.tokens(session, 'Clue Token')))

        self.attack(session, [(raven, 'B')])
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [second.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[second.object_id].zone)
        self.assertEqual(2, len(self.tokens(session, 'Clue Token')))
        for count in (1, 2):
            clue = self.tokens(session, 'Clue Token')[0]
            self.activate(session, clue, [])
            self.assertIsNone(self.resolve(session))
            self.assertEqual(count, session.state.players['A'].stats['cards_drawn_by_turn'][key])
            self.assertEqual(2 - count, len(self.tokens(session, 'Clue Token')))
            self.assert_life(session, (40, 40, 40, 40) if count == 1 else (41, 39, 39, 39))
        self.assertTrue(all(session.state.cards[land.object_id].tapped for land in mana_sources))
        self.finish(session, private)

    def test_actual_ignis_keeps_entry_selection_private_and_pays_tap_for_one_food_from_a_creature(self):
        session, private = self.start(739060)
        ignis = self.add(session.engine, 'Ignis Scientia', zone='hand')
        target = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
        fervor = self.add(session.engine, 'Fervor')
        for card in (ignis, target, fervor):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 5, 'G': 4, 'U': 4})
        self.checkpoint(session)
        before = sum(session.state.players['A'].mana_pool.values())
        self.cast(session, 'A', ignis)
        self.assertEqual(before - 3, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual(('A',), tuple(session.state.pending_decision.actors))
        offered = session.packet('pilot:A', full=True)['decision']['ctx']['objects']
        self.assertEqual(6, len(offered))
        selected = next(row['id'] for row in offered if row['name'] == 'Generic Bound Plains')
        self.assertNotIn(selected, json.dumps(session.packet('pilot:B', full=True)))
        land = next(card for card in session.state.cards.values() if card.ref == selected)
        session = self.replay(session, load=True)
        command = {'action_id': 'choose', 'cards': [selected]}
        self.reject(session, 'B', command)
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[land.object_id].zone)
        self.assertTrue(session.state.cards[land.object_id].tapped)

        self.activate(session, ignis, [target.ref])
        self.assertEqual(before - 6, sum(session.state.players['A'].mana_pool.values()))
        self.assertTrue(session.state.cards[ignis.object_id].tapped)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        food, = self.tokens(session, 'Food Token')
        self.activate(session, food, [])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before - 8, sum(session.state.players['A'].mana_pool.values()))
        self.assertFalse(self.tokens(session, 'Food Token'))
        self.assert_life(session, (43, 40, 40, 40))
        self.finish(session, private)

    def test_actual_commander_creature_counts_before_its_owner_command_zone_decision(self):
        session, private = self.start(739070)
        source = self.add(session.engine, 'Tymaret, Chosen from Death', zone='hand')
        commander_id = session.state.players['B'].zones['command'][0]
        commander = session.state.cards[commander_id]
        session.engine.move_card(commander_id, 'graveyard', reason='Prepare an owner-kept graveyard commander')
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        self.assertTrue(session.engine._stabilize())
        self.assertEqual('state.commander_zone', session.state.pending_decision.kind)
        result = session.act('pilot:B', {'action_id': 'remain'})
        self.assertTrue(result.ok, result.summary)
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'B': 3})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))

        self.activate(session, source, [commander.ref])
        self.assertEqual('state.commander_zone', self.resolve(session).kind)
        self.assertEqual('exile', session.state.cards[commander_id].zone)
        self.assert_life(session, (41, 40, 40, 40))
        self.assertEqual(('B',), tuple(session.state.pending_decision.actors))
        session = self.replay(session, load=True)
        self.reject(session, 'A', {'action_id': 'command'})
        result = session.act('pilot:B', {'action_id': 'command'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('command', session.state.cards[commander_id].zone)
        self.finish(session, private)

    def test_actual_fault_after_two_exiles_and_life_restores_state_then_loads_and_retries(self):
        import quorune.effect_runtime.life_effects as life_owner
        from quorune.record import authoritative_state_hash

        session, private = self.start(739071)
        source = self.add(session.engine, 'Tymaret, Chosen from Death', zone='hand')
        targets = [
            self.add(session.engine, 'Grizzly Bears', seat='B', zone='graveyard'),
            self.add(session.engine, 'Memnite', seat='D', zone='graveyard'),
        ]
        for card in (source, *targets):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 1, 'B': 3})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.activate(session, source, [card.ref for card in targets])
        self.priority(session, 'D')
        before = authoritative_state_hash(session.state)
        observed = []
        original = life_owner._commit

        def fault(*args, **kwargs):
            original(*args, **kwargs)
            observed.append((tuple(session.state.cards[card.object_id].zone for card in targets),
                session.state.players['A'].life))
            raise GameRuleError('Injected after committed exile outcome')

        with patch.object(life_owner, '_commit', side_effect=fault):
            rejected = session.act('pilot:D', {'action_id': 'pass'})
        self.assertFalse(rejected.ok)
        self.assertIn('Injected after committed exile outcome', rejected.summary)
        self.assertEqual([(('exile', 'exile'), 42)], observed)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))

        session = self.replay(session, load=True)
        result = session.act('pilot:D', {'action_id': 'pass'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('exile', 'exile'), tuple(session.state.cards[card.object_id].zone for card in targets))
        self.assert_life(session, (42, 40, 40, 40))
        self.finish(session, private)

    def test_fresh_native_count_requested_cards_mutation_fails_after_noncreature_exile_control(self):
        def journey(seed):
            session, private = self.start(seed)
            source = self.add(session.engine, 'Tymaret, Chosen from Death', zone='hand')
            noncreature = self.add(session.engine, 'Forest', seat='B', zone='graveyard')
            for card in (source, noncreature):
                self.assert_closed(session, card)
            self.prepare(session, mana={'C': 1, 'B': 3})
            self.checkpoint(session)
            self.cast(session, 'A', source)
            self.assertIsNone(self.resolve(session))
            self.activate(session, source, [noncreature.ref])
            self.assertIsNone(self.resolve(session))
            self.assertEqual('exile', session.state.cards[noncreature.object_id].zone)
            self.assertEqual(40, session.state.players['A'].life, 'A noncreature counts as zero')
            self.finish(session, private)

        journey(739072)
        with patch('quorune.rules.targeted_graveyard_outcomes._partition_exiled_cards', return_value=(1, 0)):
            with self.assertRaisesRegex(AssertionError, 'A noncreature counts as zero'):
                journey(739073)

    def test_genuinely_verified_withdrawals_reject_only_the_obligated_whole_originals(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.rules.targeted_graveyard_outcomes import TARGETED_GRAVEYARD_OUTCOME_CAPABILITY
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        originals = {
            'Diregraf Scavenger', 'Soul-Shackled Zombie', 'Tymaret, Chosen from Death',
            'The Binding of the Titans', 'Raven Eagle', 'Ignis Scientia',
        }
        registry_value = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())

        def verified(value):
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        def admits(name, registry):
            card = self.db.lookup(name, fuzzy=False)
            semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (card,), capability_registry=registry,
                capability_profile='commander_review', promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True)
            parent = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=registry, capability_profile='commander_review')
            return bind_card_program_runtime(parent, capability_registry=registry,
                profile='commander_review')['strict_capability_ready']

        baseline = verified(deepcopy(registry_value))
        self.assertEqual(originals, {name for name in originals if admits(name, baseline)})
        expectations = {
            TARGETED_GRAVEYARD_OUTCOME_CAPABILITY: originals,
            'card.exile.public_graveyard': originals,
            'resolution.effect.fixed_homogeneous_target_set': originals,
            'target.revalidate_resolution': originals,
            'life.change.effect': originals,
            'token.creation.fixed_definition': {'Raven Eagle', 'Ignis Scientia'},
            'zone.draw.library_to_hand': {'Raven Eagle'},
        }
        for missing, rejected in expectations.items():
            with self.subTest(withdrawn=missing):
                changed = deepcopy(registry_value)
                removed = next(row for row in changed['capabilities'] if row['id'] == missing)
                removed.update(status='blocked', blockers=['Scoped committed exile outcome withdrawal'])
                registry = verified(changed)
                owner = registry.closure((missing,), profile='commander_review')
                self.assertFalse(owner.trusted)
                self.assertIn(f'status:{missing}:blocked', owner.blockers)
                self.assertFalse(any('unverified' in reason for reason in owner.blockers))
                self.assertEqual(originals - rejected, {name for name in originals if admits(name, registry)})
                self.assertTrue(admits('Grizzly Bears', registry))
                self.assertEqual(missing not in {'card.exile.public_graveyard',
                    'target.revalidate_resolution', 'zone.draw.library_to_hand'}, admits('Cremate', registry))
