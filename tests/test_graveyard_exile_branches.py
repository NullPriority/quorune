"""Independent assertions for the pinned exclusive exile grammar and outcomes."""

from dataclasses import FrozenInstanceError
from functools import partial
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_targeted_graveyard_exile as graveyard_native

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.compiler.graveyard_exile_branch_templates import parse_graveyard_exile_branch
from quorune.errors import GameRuleError
from quorune.graveyard_exile_branches_model import (
    GRAVEYARD_EXILE_BRANCH_BASE_DEPENDENCIES,
    GraveyardExileBranchRecipe,
    GraveyardExileBranchSpec,
    GraveyardExileConsequence,
)
from quorune.object_query import ObjectQueryResult
from quorune.rules.graveyard_exile_branches import commit_graveyard_exile_branch
from quorune.rules.single_object_zone_transition import SingleObjectDestination, SingleObjectOrigin, SingleObjectZoneTransitionResult
from quorune.semantic_runtime.graveyard_exile_branch_intent import GraveyardExileBranchIntent


MANDATORY = (
    'Exile target card from a graveyard. If it was a creature card, you gain 3 life. Otherwise, you draw a card.'
)
OPTIONAL = (
    'You may exile target card from a graveyard. If a creature card is exiled this way, you gain 2 life. '
    'If a noncreature card is exiled this way, this creature gets +1/+1 until end of turn.'
)


class GraveyardExileBranchContractTests(unittest.TestCase):
    def test_exact_grammar_keeps_the_required_target_and_distinguishes_resolution_optional_movement(self):
        mandatory = parse_graveyard_exile_branch(MANDATORY)
        optional = parse_graveyard_exile_branch(OPTIONAL)
        self.assertEqual(GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW, mandatory.recipe)
        self.assertEqual(GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST, optional.recipe)
        self.assertFalse(mandatory.optional)
        self.assertTrue(optional.optional)
        for text in (
            MANDATORY + ' Draw a card.',
            MANDATORY.replace('target card', 'up to one target card'),
            MANDATORY.replace('a creature card', 'an artifact card'),
            MANDATORY.replace('Otherwise, you draw a card.', 'You may draw a card.'),
            OPTIONAL.replace('You may exile', 'Exile'),
            OPTIONAL.replace('this creature', 'target creature'),
            OPTIONAL.replace('until end of turn', 'until your next turn'),
            OPTIONAL.replace('a noncreature card', 'an artifact creature card'),
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_graveyard_exile_branch(text))

    def test_mandatory_creature_and_noncreature_classifications_each_supply_one_exclusive_consequence(self):
        spec = GraveyardExileBranchSpec(GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW)
        for committed in (True, False):
            with self.subTest(committed=committed):
                self.assertIs(GraveyardExileConsequence.GAIN_THREE_LIFE,
                    spec.consequence(exile_committed=committed, creature_card=True))
                self.assertIs(GraveyardExileConsequence.DRAW_ONE,
                    spec.consequence(exile_committed=committed, creature_card=False))

    def test_optional_result_requires_committed_exile_and_never_treats_an_unknown_as_noncreature(self):
        spec = GraveyardExileBranchSpec(GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST)
        for classification in (True, False, None):
            self.assertIsNone(spec.consequence(exile_committed=False, creature_card=classification))
        self.assertIs(GraveyardExileConsequence.GAIN_TWO_LIFE,
            spec.consequence(exile_committed=True, creature_card=True))
        self.assertIs(GraveyardExileConsequence.SOURCE_PLUS_ONE_UNTIL_END_OF_TURN,
            spec.consequence(exile_committed=True, creature_card=False))
        with self.assertRaises(ValueError):
            spec.consequence(exile_committed=True, creature_card=None)
        with self.assertRaises(ValueError):
            spec.consequence(exile_committed=1, creature_card=True)

    def test_versioned_codec_rejects_recipe_substitution_and_unknown_outcome_fields(self):
        spec = GraveyardExileBranchSpec(GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW)
        data = json.loads(json.dumps(spec.to_dict()))
        self.assertEqual(spec, GraveyardExileBranchSpec.from_dict(data))
        with self.assertRaises(FrozenInstanceError):
            spec.recipe = GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST
        for change in ({'schema_version': True}, {'schema_version': 2}, {'recipe': 'choose_both'},
            {'recipe': []}, {'amount': 3}, {'target': '$controller'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                GraveyardExileBranchSpec.from_dict(data | change)


class GraveyardExileBranchReceiptTests(unittest.TestCase):
    def fixture(self, recipe, *, initial_types=('artifact', 'creature'), source_zone='battlefield', source_identity='source@1'):
        target = SimpleNamespace(object_id='target', ref='public-target', zone='graveyard',
            logical_object_id='target@1', face_down=False, is_card_object=True, types=initial_types)
        source = SimpleNamespace(object_id='source', zone=source_zone, logical_object_id=source_identity, phased_out=False)
        query = Mock(side_effect=lambda card: ObjectQueryResult(object_id=card.object_id, ref=card.ref,
            printed_name='Public card', owner='B', controller='B', zone=card.zone, types=card.types,
            logical_object_id=card.logical_object_id))
        host = SimpleNamespace(state=SimpleNamespace(cards={'target': target, 'source': source}),
            _require_seat=Mock(), _resolve_object=Mock(return_value=target), _public_object_query_result=query)
        intent = GraveyardExileBranchIntent(actor='A', object_ref=target.ref, reason='Exclusive exile',
            source_object_id='source', source_logical_object_id='source@1', result=GraveyardExileBranchSpec(recipe))
        return host, target, intent

    def commit(self, host, target, intent, *, zone='exile', types=('creature',), receipt_id='target'):
        def move(moving_host, ref, **kwargs):
            self.assertIs(host, moving_host)
            self.assertEqual((target.ref, 'A'), (ref, kwargs['actor']))
            target.zone = zone
            target.types = types
            target.logical_object_id = 'target@2'
            return SingleObjectZoneTransitionResult(receipt_id, ref, 'B', 'B', SingleObjectDestination.EXILE,
                zone, target.logical_object_id, SingleObjectOrigin.GRAVEYARD)

        with patch('quorune.public_zone_moves.exile_public_graveyard_card_transition', side_effect=move) as called:
            followup = commit_graveyard_exile_branch(host, intent)
        called.assert_called_once()
        return [dict(effect) for effect in followup.effects]

    def test_current_exile_type_wins_and_redirected_mandatory_uses_last_known_without_reading_hand(self):
        recipe = GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW
        host, target, intent = self.fixture(recipe)
        self.assertEqual([{'op':'draw', 'player':'$controller', 'count':1, 'private':True}],
            self.commit(host, target, intent, types=('land',)))
        self.assertEqual(2, host._public_object_query_result.call_count)

        host, target, intent = self.fixture(recipe)
        self.assertEqual([{'op':'life', 'player':'$controller', 'delta':3}],
            self.commit(host, target, intent, zone='hand', types=('land',)))
        host._public_object_query_result.assert_called_once()

    def test_optional_committed_result_reads_arrival_and_boosts_only_original_source(self):
        recipe = GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST
        host, target, intent = self.fixture(recipe)
        self.assertEqual([], self.commit(host, target, intent, zone='hand'))
        host._public_object_query_result.assert_not_called()

        host, target, intent = self.fixture(recipe, source_zone='graveyard')
        self.assertEqual([{'op':'life', 'player':'$controller', 'delta':2}],
            self.commit(host, target, intent, types=('artifact', 'creature')))
        host._public_object_query_result.assert_called_once()

        host, target, intent = self.fixture(recipe, source_identity='source@2')
        self.assertEqual([], self.commit(host, target, intent, types=('land',)))
        host, target, intent = self.fixture(recipe)
        self.assertEqual([{'op':'modify_stats_until_end_of_turn', 'card':'$source.zone_object', 'power':1, 'toughness':1}],
            self.commit(host, target, intent, types=('land',)))

    def test_mismatched_movement_receipt_cannot_supply_a_branch(self):
        host, target, intent = self.fixture(GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW)
        with self.assertRaisesRegex(GameRuleError, 'different card'):
            self.commit(host, target, intent, receipt_id='other')

    def test_committed_classification_mutation_is_killed_by_the_draw_branch_witness(self):
        def witness():
            host, target, intent = self.fixture(GraveyardExileBranchRecipe.CREATURE_LIFE_OR_DRAW)
            effect, = self.commit(host, target, intent, types=('land',))
            self.assertEqual('draw', effect['op'], 'A noncreature exit must cause private draw')

        witness()
        with patch('quorune.rules.graveyard_exile_branches._committed_creature_card', return_value=True):
            with self.assertRaisesRegex(AssertionError, 'must cause private draw'):
                witness()


class GraveyardExileBranchCompilerTests(unittest.TestCase):
    def test_spell_promotion_requires_the_direct_closed_recipe_and_all_runtime_owners(self):
        from quorune.compiler.graveyard_exile_branch_templates import graveyard_exile_branch_template
        from quorune.rules.graveyard_exile_branches import (
            graveyard_exile_branch_runtime_dependencies,
            is_closed_graveyard_exile_branch_spell,
        )

        _, effects, schema, _ = graveyard_exile_branch_template(MANDATORY)
        base = dict(event='resolve', effects=effects, target_schema=schema, handlers=())
        required = graveyard_exile_branch_runtime_dependencies(SimpleNamespace(**base))
        complete = {**base, 'capability_dependencies':required}
        self.assertTrue(is_closed_graveyard_exile_branch_spell(SimpleNamespace(**complete)))
        for changed in ({'target_schema':{**schema, 'count':True}}, {'effects':effects+({'op':'draw'},)},
                {'handlers':({'handler_id':'foreign'},)}, {'event':'activate'},
                {'capability_dependencies':tuple(key for key in required if key != 'zone.draw.library_to_hand')}):
            with self.subTest(changed=changed):
                self.assertFalse(is_closed_graveyard_exile_branch_spell(SimpleNamespace(**(complete|changed))))

    def test_composition_and_raw_minima_require_the_precise_target_and_may_envelope(self):
        from quorune.compiler.effect_template_composition import reviewed_effect_template_composition
        from quorune.oracle_ir import _effect_template
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.rules.graveyard_exile_branches import (
            graveyard_exile_branch_payload_dependencies,
            graveyard_exile_branch_runtime_dependencies,
        )

        atomic = partial(_effect_template, card_name='Neutral source')
        for text, optional in ((MANDATORY, False), (OPTIONAL, True)):
            with self.subTest(optional=optional):
                template, effects, schema, mechanics = reviewed_effect_template_composition(
                    text, source_name='Neutral source', compile_atomic=atomic, compile_fixed=atomic,
                )
                self.assertIsNotNone(template)
                self.assertEqual((1, 'any', ['graveyard'], ['card']),
                    (schema['count'], schema['owner_relation'], schema['zones'], schema['categories']))
                self.assertNotIn('up_to', schema)
                effect, = effects
                leaf = effect['effects'][0] if optional else effect
                self.assertEqual('$target.0', leaf['card'])
                dependent = ('continuous.resolution.fixed_characteristics_until_end_of_turn' if optional
                    else 'zone.draw.library_to_hand')
                expected = {*GRAVEYARD_EXILE_BRANCH_BASE_DEPENDENCIES, 'life.change.effect', dependent}
                self.assertEqual(expected, set(graveyard_exile_branch_payload_dependencies(leaf)))
                if optional:
                    self.assertEqual(('offer_optional_effect', '$controller'), (effect['op'], effect['player']))
                    expected.add('effect.choice.optional_fixed')
                self.assertEqual(expected, set(capability_dependencies_for_node(
                    effects=effects, target_schema=schema, mechanic_ids=mechanics)))
                program = SimpleNamespace(effects=effects, target_schema=schema, handlers=(), coverage=())
                self.assertEqual(expected, set(graveyard_exile_branch_runtime_dependencies(program)))
                with self.assertRaises(ValueError):
                    graveyard_exile_branch_runtime_dependencies(SimpleNamespace(effects=effects,
                        target_schema={**schema, 'count':True}, handlers=()))
                malformed = (leaf,) if optional else ({'op':'offer_optional_effect', 'player':'$controller', 'effects':[leaf]},)
                with self.assertRaises(ValueError):
                    graveyard_exile_branch_runtime_dependencies(SimpleNamespace(effects=malformed, target_schema=schema, handlers=()))

    def test_exile_branch_handler_builds_typed_followup_plan_and_ordinary_exile_retains_its_plan(self):
        from quorune.replacement_decisions import _quantity_followup_plan
        from quorune.semantic_runtime import default_semantic_interpreter
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticSourceContext
        from quorune.semantic_runtime.graveyard_exile_branch_handlers import GraveyardExileBranchHandler

        interpreter = default_semantic_interpreter()
        options = dict(actor='A', default_reason='Choice', seats=('A','B'), active_seats=('A','B'), apnap_order=('A','B'))
        recipe = GraveyardExileBranchSpec(GraveyardExileBranchRecipe.OPTIONAL_CREATURE_LIFE_OR_SOURCE_BOOST)
        effect = {**recipe.effect(), 'card':'public-target'}
        source = SemanticSourceContext(stack_ref='stack', object_id='source', logical_object_id='source@1')
        context = ReadOnlyHandlerContext.from_sequences(source=source, **options)
        plan = GraveyardExileBranchHandler().lower(effect, context)
        intent, = plan.intents
        self.assertIsInstance(intent, GraveyardExileBranchIntent)
        self.assertEqual('source@1', intent.source_logical_object_id)
        self.assertTrue(_quantity_followup_plan(plan))
        plain = interpreter.lower_for_seats({'op':'exile_public_graveyard_card', 'card':'public-target'}, **options)
        self.assertFalse(_quantity_followup_plan(plain))


class GraveyardExileBranchNativeTests(InheritedNativeFixture, unittest.TestCase):
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
        db_path = folder / 'exclusive-exile.sqlite3'
        build_fixture_database([
            basic,
            ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/graveyard-card-result-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-branch-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-token-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-type-outcome-cards.json',
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Exclusive graveyard exile', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def choose_optional(self, session, answer):
        self.assertEqual('semantic.choice', session.state.pending_decision.kind)
        self.assertEqual(('A',), tuple(session.state.pending_decision.actors))
        command = {'action_id': 'choose', 'choice': answer}
        self.reject(session, 'B', command)
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)

    @staticmethod
    def hand_objects(session, seat='A'):
        return set(session.state.players[seat].zones['hand'])

    def test_actual_cling_pays_black_and_resolves_exclusive_life_or_private_draw(self):
        session, private = self.start(739110)
        first = self.add(session.engine, 'Cling to Dust', zone='hand', ref='life-branch-cling')
        second = self.add(session.engine, 'Cling to Dust', zone='hand', ref='draw-branch-cling')
        creature = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
        land = self.add(session.engine, 'Forest', seat='D', zone='graveyard')
        for card in (first, second, creature, land):
            self.assert_closed(session, card)
        self.prepare(session, mana={'B':2})
        self.checkpoint(session)

        hand = self.hand_objects(session)
        self.cast(session, 'A', first, [creature.ref])
        self.assertEqual(1, sum(session.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[creature.object_id].zone)
        self.assertEqual(43, session.state.players['A'].life)
        self.assertEqual(hand - {first.object_id}, self.hand_objects(session))

        hand = self.hand_objects(session)
        self.cast(session, 'A', second, [land.ref])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[land.object_id].zone)
        self.assertEqual(43, session.state.players['A'].life)
        drawn_id, = self.hand_objects(session) - hand
        self.assertEqual((hand - {second.object_id}) | {drawn_id}, self.hand_objects(session))
        drawn = session.state.cards[drawn_id]
        for seat in 'BCD':
            self.assertNotIn(drawn.ref, json.dumps(session.packet(f'pilot:{seat}', full=True)))
        self.finish(session, private)

    def test_actual_deathgorge_declines_entry_then_accepts_noncreature_attack_exile_for_source_boost(self):
        session, private = self.start(739120)
        source = self.add(session.engine, 'Deathgorge Scavenger', zone='hand')
        fervor = self.add(session.engine, 'Fervor')
        creature = self.add(session.engine, 'Memnite', seat='B', zone='graveyard')
        land = self.add(session.engine, 'Forest', seat='D', zone='graveyard')
        for card in (source, fervor, creature, land):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C':2,'G':1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.reject(session, 'A', {'action_id':'choose','targets':[]})
        self.target(session, 'A', [creature.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        self.choose_optional(session, 'decline')
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[creature.object_id].zone)
        self.assertEqual(40, session.state.players['A'].life)
        self.assertEqual((3,2), tuple(session.engine._numeric_stat(source.object_id, stat) for stat in ('power','toughness')))

        self.attack(session, [(session.state.cards[source.object_id], 'B')])
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [land.ref])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.choose_optional(session, 'apply')
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[land.object_id].zone)
        self.assertEqual(40, session.state.players['A'].life)
        self.assertEqual((4,3), tuple(session.engine._numeric_stat(source.object_id, stat) for stat in ('power','toughness')))
        self.finish(session, private)

    def test_actual_deathgorge_creature_branch_survives_source_departure(self):
        session, private = self.start(739130)
        source = self.add(session.engine, 'Deathgorge Scavenger', zone='hand')
        creature = self.add(session.engine, 'Memnite', seat='D', zone='graveyard')
        removal = self.add(session.engine, 'Doom Blade', seat='B', zone='hand')
        for card in (source, creature, removal):
            self.assert_closed(session, card)
        session.state.players['B'].mana_pool.update({'C':1,'B':1})
        self.prepare(session, mana={'C':2,'G':1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [creature.ref])
        self.cast(session, 'B', removal, [source.ref])
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        session = self.replay(session, load=True)
        self.choose_optional(session, 'apply')
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[creature.object_id].zone)
        self.assertEqual(42, session.state.players['A'].life)
        self.finish(session, private)

    def test_actual_escape_pays_five_other_cards_and_allows_target_payment_overlap_to_fizzle(self):
        for index, overlap in enumerate((False, True)):
            with self.subTest(overlap=overlap):
                session, private = self.start(739140+index)
                source = self.add(session.engine, 'Cling to Dust', zone='graveyard')
                payment = [self.add(session.engine, 'Memnite', zone='graveyard', ref='escape-paid-creature')]
                for slot in range(4):
                    payment.append(self.add(session.engine, 'Forest', zone='graveyard', ref=f'escape-branch-payment-{slot}'))
                other = self.add(session.engine, 'Grizzly Bears', seat='B', zone='graveyard')
                for card in (source, other, *payment):
                    self.assert_closed(session, card)
                self.prepare(session, mana={'C':3,'B':1})
                self.checkpoint(session)
                self.priority(session, 'A')
                offered = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                action = next(row for row in offered if row.get('card') == source.ref)
                option = next(row for row in action['cost_options'] if row['id'] == 'escape')
                schema = option['choice_schema']['exile_cards']
                self.assertEqual(5, schema['count'])
                self.assertNotIn(source.ref, schema['legal_refs'])
                target = payment[0] if overlap else other
                command = {'action_id':action['id'], 'cost_option':'escape', 'targets':[target.ref],
                    'exile_cards':[card.ref for card in payment], 'pay':'auto'}
                self.reject(session, 'D', command)
                accepted = session.act('pilot:A', command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual('stack', session.state.cards[source.object_id].zone)
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                self.assertTrue(all(session.state.cards[card.object_id].zone == 'exile' for card in payment))
                hand = self.hand_objects(session)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(40 if overlap else 43, session.state.players['A'].life)
                self.assertEqual(hand, self.hand_objects(session))
                self.assertEqual('graveyard' if overlap else 'exile', session.state.cards[other.object_id].zone)
                self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                self.finish(session, private)

    def test_actual_deathgorge_target_lost_to_cremate_never_offers_the_optional_action(self):
        session, private = self.start(739150)
        source = self.add(session.engine, 'Deathgorge Scavenger', zone='hand')
        target = self.add(session.engine, 'Memnite', seat='D', zone='graveyard')
        answer = self.add(session.engine, 'Cremate', seat='B', zone='hand')
        for card in (source, target, answer):
            self.assert_closed(session, card)
        session.state.players['B'].mana_pool['B'] = 1
        self.prepare(session, mana={'C': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [target.ref])
        self.cast(session, 'B', answer, [target.ref])
        self.resolve_top(session)
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        hand = self.hand_objects(session)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(40, session.state.players['A'].life)
        self.assertEqual(hand, self.hand_objects(session))
        self.assertEqual((3, 2), tuple(session.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.finish(session, private)

    def test_actual_cling_fault_after_private_draw_restores_movement_priority_and_replay(self):
        from quorune.drawing import transaction as draw_owner
        from quorune.record import authoritative_state_hash

        session, private = self.start(739160)
        source = self.add(session.engine, 'Cling to Dust', zone='hand')
        land = self.add(session.engine, 'Forest', seat='D', zone='graveyard')
        for card in (source, land):
            self.assert_closed(session, card)
        self.prepare(session, mana={'B': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source, [land.ref])
        self.priority(session, 'D')
        hand = self.hand_objects(session)
        library = tuple(session.state.players['A'].zones['library'])
        top_card = library[-1]
        before = authoritative_state_hash(session.state)
        original_draw = draw_owner._commit_ordinary_draw
        observed = []

        def fault(*args, **kwargs):
            result = original_draw(*args, **kwargs)
            observed.append((session.state.cards[land.object_id].zone, self.hand_objects(session) - hand))
            self.assertEqual('draw', result.kind)
            raise GameRuleError('Injected after the committed private draw')

        command = {'action_id': 'pass'}
        with patch.object(draw_owner, '_commit_ordinary_draw', side_effect=fault):
            result = session.act('pilot:D', command)
        self.assertFalse(result.ok)
        self.assertIn('Injected after the committed private draw', result.summary)
        self.assertEqual([('exile', {top_card})], observed)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual('graveyard', session.state.cards[land.object_id].zone)
        self.assertEqual(hand, self.hand_objects(session))
        self.assertEqual(library, tuple(session.state.players['A'].zones['library']))
        self.assertEqual(1, len(session.state.stack))

        session = self.replay(session, load=True)
        result = session.act('pilot:D', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[land.object_id].zone)
        self.assertEqual(hand | {top_card}, self.hand_objects(session))
        self.assertEqual(40, session.state.players['A'].life)
        self.finish(session, private)

    def test_fresh_native_noncreature_classification_mutation_breaks_the_positive_paid_witness(self):
        from quorune.rules import graveyard_exile_branches as owner

        def trial(seed):
            session, private = self.start(seed)
            source = self.add(session.engine, 'Cling to Dust', zone='hand')
            target = self.add(session.engine, 'Forest', seat='B', zone='graveyard')
            for card in (source, target):
                self.assert_closed(session, card)
            self.prepare(session, mana={'B': 1})
            self.checkpoint(session)
            self.cast(session, 'A', source, [target.ref])
            hand = self.hand_objects(session)
            self.assertIsNone(self.resolve(session))
            self.assertEqual('exile', session.state.cards[target.object_id].zone)
            self.assertEqual(40, session.state.players['A'].life)
            self.assertEqual(1, len(self.hand_objects(session) - hand))
            self.finish(session, private)

        trial(739170)
        with patch.object(owner, '_committed_creature_card', return_value=True):
            with self.assertRaisesRegex(AssertionError, '40 != 43'):
                trial(739171)

    def test_genuinely_verified_branch_owner_withdrawals_block_the_affected_complete_programs(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.graveyard_exile_branches_model import GRAVEYARD_EXILE_BRANCH_CAPABILITY
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known_tests = set(discover_tests(ROOT).values())
        profile = 'commander_review'

        def verified(registry_value):
            registry = CapabilityRegistry(registry_value)
            index = build_index(registry_value=registry_value, declaration_source=declarations, discovered_test_ids=known_tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        def ready(name, registry):
            card = self.db.lookup(name)
            semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (card,), capability_registry=registry, capability_profile=profile,
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
            program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=registry, capability_profile=profile)
            return bind_card_program_runtime(program, capability_registry=registry, profile=profile)['strict_capability_ready']

        cling, deathgorge, cremate = 'Cling to Dust', 'Deathgorge Scavenger', 'Cremate'
        both = {cling, deathgorge}
        withdrawals = {
            GRAVEYARD_EXILE_BRANCH_CAPABILITY: both,
            'card.exile.public_graveyard': both | {cremate},
            'target.revalidate_resolution': both | {cremate},
            'life.change.effect': both,
            'effect.choice.optional_fixed': {deathgorge},
            'continuous.resolution.fixed_characteristics_until_end_of_turn': {deathgorge},
            'zone.draw.library_to_hand': {cling, cremate},
            'casting.lifecycle.fixed_zone_cast': {cling},
        }
        controls = (cling, deathgorge, cremate, 'Grizzly Bears')
        registry = verified(deepcopy(raw))
        for name in controls:
            self.assertTrue(ready(name, registry), name)
        self.assertFalse(ready('Ghost Vacuum', registry))

        for missing, affected in withdrawals.items():
            with self.subTest(withdrawn=missing):
                changed = deepcopy(raw)
                entry = next(row for row in changed['capabilities'] if row['id'] == missing)
                entry.update(status='blocked', blockers=['Canonical exclusive graveyard exile owner withdrawn'])
                registry = verified(changed)
                status = registry.closure((missing,), profile=profile)
                self.assertFalse(status.trusted)
                self.assertIn('status:' + missing + ':blocked', status.blockers)
                self.assertFalse(any('unverified' in blocker for blocker in status.blockers))
                for name in controls:
                    self.assertEqual(name not in affected, ready(name, registry), name)
