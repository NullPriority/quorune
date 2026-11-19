"""Fixed independent creature and land predicates over the same graveyard target."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_targeted_graveyard_exile as previous

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.compiler.graveyard_exile_type_outcome_templates import graveyard_exile_type_outcome_template
from quorune.rules.committed_quantity_followups import fixed_followup_capabilities
from quorune.rules.graveyard_exile_type_outcomes import (
    GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION,
    GraveyardExileTypeOutcomeSpec,
)


BODY = ('Exile target card from a graveyard. If it was a creature card, create a 2/2 black Rogue creature token. '
        'If it was a land card, create a Treasure token. Otherwise, you gain 3 life.')


class GraveyardExileTypeOutcomeTests(unittest.TestCase):
    def test_exact_three_clauses_keep_one_ordinary_target_and_both_token_definitions(self):
        _, (effect,), target, mechanics = graveyard_exile_type_outcome_template(BODY)
        self.assertEqual(GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION, effect['op'])
        self.assertEqual('$target.0', effect['card'])
        self.assertEqual({'zones': ['graveyard'], 'categories': ['card'], 'owner_relation': 'any', 'count': 1}, target)
        self.assertIn('cr-119-life', mechanics)
        spec = GraveyardExileTypeOutcomeSpec.from_dict(effect['result'])
        self.assertEqual('Rogue Token', spec.creature_token['name'])
        self.assertEqual('Treasure Token', spec.treasure_token['name'])
        self.assertEqual(('B',), spec.creature_token['characteristics']['colors'])
        self.assertEqual('tap_sac_any_color_mana_v1', spec.treasure_token['characteristics']['activated_ability_profile'])
        self.assertIn('mana.activated.fixed_output', fixed_followup_capabilities(spec.treasure_token))

    def test_land_creature_gets_both_tokens_in_order_and_only_neither_gets_life(self):
        _, (effect,), _, _ = graveyard_exile_type_outcome_template(BODY)
        spec = GraveyardExileTypeOutcomeSpec.from_dict(effect['result'])
        for creature, land, names in (
            (True, True, ['Rogue Token', 'Treasure Token']),
            (True, False, ['Rogue Token']),
            (False, True, ['Treasure Token']),
        ):
            with self.subTest(creature=creature, land=land):
                result = spec.followup(creature_card=creature, land_card=land)
                self.assertEqual(names, [effect['name'] for effect in result.effects])
        life, = spec.followup(creature_card=False, land_card=False).effects
        self.assertEqual({'op': 'life', 'player': '$controller', 'delta': 3}, life)
        with self.assertRaises(ValueError):
            spec.followup(creature_card=None, land_card=False)

    def test_rejects_open_or_altered_descriptors_and_retains_exact_roundtrip(self):
        _, (effect,), _, _ = graveyard_exile_type_outcome_template(BODY)
        payload = effect['result']
        spec = GraveyardExileTypeOutcomeSpec.from_dict(json.loads(json.dumps(payload)))
        self.assertEqual(payload, spec.to_dict())
        for replacement in (
            {'schema_version': True},
            {'otherwise': {'op': 'draw'}},
            {'treasure_token': payload['creature_token']},
            {'treasure_token': {**payload['treasure_token'], 'controller': '$target.0'}},
            {'creature_token': {**payload['creature_token'], 'quantity': 2}},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                GraveyardExileTypeOutcomeSpec.from_dict({**payload, **replacement})

    def test_nearby_optional_moves_and_extra_effects_do_not_use_this_model(self):
        for wording in (
            BODY.replace('Exile target', 'You may exile target'),
            BODY.replace('Exile target', 'Exile up to one target'),
            BODY.replace('If it was a land card', 'If it was a land permanent'),
            BODY.replace('create a Treasure token', 'create a Food token'),
            BODY.replace('Otherwise, you gain 3 life.', 'You draw a card.'),
            BODY + ' Draw a card.',
        ):
            with self.subTest(wording=wording):
                self.assertIsNone(graveyard_exile_type_outcome_template(wording))

    def test_type_outcome_uses_one_public_movement_and_reads_only_current_or_last_known_public_types(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from quorune.object_query import ObjectQueryResult
        from quorune.rules.graveyard_exile_type_outcomes import commit_graveyard_exile_type_outcomes
        from quorune.rules.single_object_zone_transition import (
            SingleObjectDestination, SingleObjectOrigin, SingleObjectZoneTransitionResult,
        )
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext
        from quorune.semantic_runtime.graveyard_exile_type_outcome_handlers import GraveyardExileTypeOutcomeHandler

        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Type outcome',
            seats=('A', 'B'), active_seats=('A', 'B'), apnap_order=('A', 'B'))
        _, (effect,), _, _ = graveyard_exile_type_outcome_template(BODY)
        for destination, current_types, expected in (
            ('exile', ('land', 'creature'), ['Rogue Token', 'Treasure Token']),
            ('exile', ('land',), ['Treasure Token']),
            ('hand', ('artifact',), ['Rogue Token', 'Treasure Token']),
        ):
            with self.subTest(destination=destination, current_types=current_types):
                target = SimpleNamespace(object_id='target', ref='graveyard-ref', logical_object_id='target@1',
                    zone='graveyard', types=('land', 'creature'), face_down=False, is_card_object=True)

                def public_card(card):
                    self.assertIn(card.zone, ('graveyard', 'exile'))
                    return ObjectQueryResult(object_id=card.object_id, ref=card.ref, printed_name='Fixture',
                        owner='B', controller='B', zone=card.zone, types=card.types, logical_object_id=card.logical_object_id)

                def move(host, ref, *, actor, **kwargs):
                    self.assertEqual(('A', target.ref), (actor, ref))
                    target.zone, target.logical_object_id, target.types = destination, 'target@2', current_types
                    return SingleObjectZoneTransitionResult(target.object_id, ref, 'B', 'B', SingleObjectDestination.EXILE,
                        destination, target.logical_object_id, SingleObjectOrigin.GRAVEYARD)

                host = SimpleNamespace(state=SimpleNamespace(cards={target.object_id: target}),
                    _require_seat=Mock(), _resolve_object=Mock(return_value=target),
                    _public_object_query_result=Mock(side_effect=public_card))
                intent, = GraveyardExileTypeOutcomeHandler().lower({**effect, 'card': target.ref}, context).intents
                with patch('quorune.public_zone_moves.exile_public_graveyard_card_transition', side_effect=move) as canonical:
                    result = commit_graveyard_exile_type_outcomes(host, intent)
                canonical.assert_called_once()
                self.assertEqual(expected, [token['name'] for token in result.effects])

    def test_raw_type_outcome_requires_ordinary_treasure_mana_life_and_target_owners(self):
        from types import SimpleNamespace

        from quorune.rules.graveyard_exile_type_outcomes import (
            GRAVEYARD_EXILE_TYPE_OUTCOME_BASE_DEPENDENCIES,
            graveyard_exile_type_outcome_node_capabilities,
            graveyard_exile_type_outcome_runtime_dependencies,
        )

        _, effects, target, _ = graveyard_exile_type_outcome_template(BODY)
        expected = {*GRAVEYARD_EXILE_TYPE_OUTCOME_BASE_DEPENDENCIES, 'life.change.effect',
            'mana.activated.fixed_output', 'token.creation.fixed_definition'}
        self.assertEqual(expected, set(graveyard_exile_type_outcome_node_capabilities(effects=effects, target_schema=target)))
        for wrong in ({'target_schema': {**target, 'count': True}}, {'handlers': effects}, {'effects': (*effects, {'op': 'life'})}):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                graveyard_exile_type_outcome_runtime_dependencies(SimpleNamespace(**{
                    **{'target_schema': target, 'effects': effects, 'handlers': ()}, **wrong,
                }))

    def test_composition_binds_the_target_but_preserves_both_child_controller_placeholders(self):
        from types import SimpleNamespace

        from quorune.compiler.effect_template_composition import reviewed_effect_template_composition
        from quorune.replacement_decisions import _quantity_followup_plan
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext
        from quorune.semantic_runtime.graveyard_exile_type_outcome_handlers import GraveyardExileTypeOutcomeHandler
        from quorune.semantic_runtime.values import resolve_semantic_value

        def outside_grammar(text):
            self.fail(f'Owned three-clause body reached a generic parser ({text})')

        compiled = reviewed_effect_template_composition(BODY, source_name='Generic source',
            compile_atomic=outside_grammar, compile_fixed=outside_grammar)
        self.assertEqual(graveyard_exile_type_outcome_template(BODY), compiled)
        _, effects, target, mechanics = compiled
        self.assertIn('mana.activated.fixed_output', capability_dependencies_for_node(
            effects=effects, target_schema=target, mechanic_ids=mechanics))
        item = SimpleNamespace(context={}, targets=['graveyard-card'], controller='A')
        bound = resolve_semantic_value(SimpleNamespace(), effects[0], item)
        self.assertEqual('graveyard-card', bound['card'])
        self.assertEqual('$controller', bound['result']['creature_token']['controller'])
        self.assertEqual('$controller', bound['result']['treasure_token']['controller'])
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Type outcome',
            seats=('A', 'B'), active_seats=('A', 'B'), apnap_order=('A', 'B'))
        self.assertTrue(_quantity_followup_plan(GraveyardExileTypeOutcomeHandler().lower(bound, context)))

    def test_classifying_a_land_creature_only_as_creature_breaks_the_two_result_witness(self):
        from unittest.mock import patch

        from quorune.rules import graveyard_exile_branches as owner

        original = owner._committed_exile_card_types

        def creature_only(host, receipt):
            types = original(host, receipt)
            return types - {'land'} if {'land', 'creature'} <= types else types

        witness = type(self)('test_type_outcome_uses_one_public_movement_and_reads_only_current_or_last_known_public_types')
        baseline = unittest.TestResult()
        witness.run(baseline)
        self.assertTrue(baseline.wasSuccessful(), (baseline.errors, baseline.failures))
        with patch.object(owner, '_committed_exile_card_types', creature_only):
            mutant_result = unittest.TestResult()
            witness.run(mutant_result)
        self.assertFalse(mutant_result.errors, mutant_result.errors)
        self.assertEqual(1, len(mutant_result.failures))
        self.assertIn('Treasure Token', mutant_result.failures[0][1])

class GraveyardExileTypeOutcomeNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = previous.TargetedGraveyardExileNativeTests.start
    reject = previous.TargetedGraveyardExileNativeTests.reject
    finish = previous.TargetedGraveyardExileNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        database = folder / 'graveyard-exile-type-outcome.sqlite3'
        build_fixture_database([
            basic,
            ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/graveyard-exile-type-outcome-cards.json',
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Overlapping graveyard exile types', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def cast_and_announce(self, session, source, target):
        self.prepare(session, mana={'C': 3, 'B': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.reject(session, 'A', {'action_id': 'choose', 'targets': []})
        self.target(session, 'A', [target.ref])

    @staticmethod
    def tokens(session):
        return sorted((card for card in session.state.cards.values()
            if card.zone == 'battlefield' and card.is_token and card.controller == 'A'),
            key=lambda card: card.zone_timestamp)

    def test_actual_paid_entry_uses_both_dryad_types_and_combat_damage_uses_the_life_fallback(self):
        session, private = self.start(742110)
        source = self.add(session.engine, 'Misfortune Teller', zone='hand')
        dryad = self.add(session.engine, 'Dryad Arbor', seat='D', zone='graveyard')
        neither = self.add(session.engine, 'Shock', seat='B', zone='graveyard')
        fervor = self.add(session.engine, 'Fervor')
        for card in (source, dryad, neither, fervor):
            self.assert_closed(session, card)
        self.cast_and_announce(session, source, dryad)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[dryad.object_id].zone)
        self.assertEqual(40, session.state.players['A'].life)
        self.assertEqual(['Rogue Token', 'Treasure Token'], [card.printed_name for card in self.tokens(session)])
        self.assertIn('deathtouch', session.engine._combat_keywords(session.state.cards[source.object_id]))

        self.attack(session, [(session.state.cards[source.object_id], 'B')])
        self.until(session, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == 'semantic.target')
        self.assertEqual(37, session.state.players['B'].life)
        self.target(session, 'A', [neither.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[neither.object_id].zone)
        self.assertEqual(43, session.state.players['A'].life)
        self.assertEqual(['Rogue Token', 'Treasure Token'], [card.printed_name for card in self.tokens(session)])
        self.finish(session, private)

    def test_actual_creature_only_and_land_only_each_create_their_own_token_without_life(self):
        for seed, name, expected in ((742120, 'Memnite', 'Rogue Token'), (742121, 'Forest', 'Treasure Token')):
            with self.subTest(name=name):
                session, private = self.start(seed)
                source = self.add(session.engine, 'Misfortune Teller', zone='hand')
                target = self.add(session.engine, name, seat='B', zone='graveyard')
                for card in (source, target):
                    self.assert_closed(session, card)
                self.cast_and_announce(session, source, target)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('exile', session.state.cards[target.object_id].zone)
                self.assertEqual([expected], [card.printed_name for card in self.tokens(session)])
                self.assertEqual(40, session.state.players['A'].life)
                self.finish(session, private)

    def test_actual_stale_land_creature_target_never_creates_tokens_or_falls_through_to_life(self):
        session, private = self.start(742130)
        source = self.add(session.engine, 'Misfortune Teller', zone='hand')
        target = self.add(session.engine, 'Dryad Arbor', seat='D', zone='graveyard')
        answer = self.add(session.engine, 'Cremate', seat='B', zone='hand')
        for card in (source, target, answer):
            self.assert_closed(session, card)
        session.state.players['B'].mana_pool['B'] = 1
        self.cast_and_announce(session, source, target)
        self.cast(session, 'B', answer, [target.ref])
        self.resolve_top(session)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[target.object_id].zone)
        self.assertEqual([], self.tokens(session))
        self.assertEqual(40, session.state.players['A'].life)
        self.finish(session, private)

    def test_actual_fault_after_second_token_restores_both_and_the_public_graveyard_then_replays(self):
        from unittest.mock import patch

        from quorune import token_creation as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash

        session, private = self.start(742140)
        source = self.add(session.engine, 'Misfortune Teller', zone='hand')
        dryad = self.add(session.engine, 'Dryad Arbor', seat='D', zone='graveyard')
        for card in (source, dryad):
            self.assert_closed(session, card)
        self.cast_and_announce(session, source, dryad)
        self.priority(session, 'D')
        before = authoritative_state_hash(session.state)
        observed = []
        original = owner._commit_token_object

        def fault(*args, **kwargs):
            committed_id = original(*args, **kwargs)
            card = session.state.cards[committed_id]
            observed.append((session.state.cards[dryad.object_id].zone, card.printed_name))
            if len(observed) == 2:
                raise GameRuleError('Injected after the second exile token')
            return committed_id

        command = {'action_id': 'pass'}
        with patch.object(owner, '_commit_token_object', side_effect=fault):
            result = session.act('pilot:D', command)
        self.assertFalse(result.ok)
        self.assertIn('Injected after the second exile token', result.summary)
        self.assertEqual([('exile', 'Rogue Token'), ('exile', 'Treasure Token')], observed)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual('graveyard', session.state.cards[dryad.object_id].zone)
        self.assertEqual([], self.tokens(session))
        session = self.replay(session, load=True)
        retry = session.act('pilot:D', command)
        self.assertTrue(retry.ok, retry.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['Rogue Token', 'Treasure Token'], [card.printed_name for card in self.tokens(session)])
        self.finish(session, private)

    def test_fresh_native_losing_the_land_type_breaks_the_dryad_two_token_witness(self):
        from unittest.mock import patch

        from quorune.rules import graveyard_exile_branches as owner

        original = owner._committed_exile_card_types

        def creature_only(host, receipt):
            types = original(host, receipt)
            return types - {'land'} if {'land', 'creature'} <= types else types

        with patch.object(owner, '_committed_exile_card_types', creature_only):
            with self.assertRaisesRegex(AssertionError, 'Treasure Token'):
                self.test_actual_paid_entry_uses_both_dryad_types_and_combat_damage_uses_the_life_fallback()

    def test_genuinely_verified_overlap_owners_and_treasure_mana_are_required_for_whole_misfortune(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.rules.graveyard_exile_type_outcomes import GRAVEYARD_EXILE_TYPE_OUTCOME_CAPABILITY
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        root = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        titles = set(discover_tests(ROOT).values())
        profile = 'commander_review'

        def verified(rows):
            registry = CapabilityRegistry(rows)
            evidence = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=titles)
            registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
            return registry

        def admitted(name, registry):
            card = self.db.lookup(name, fuzzy=False)
            semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (card,), capability_registry=registry, capability_profile=profile,
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
            program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=registry, capability_profile=profile)
            return bind_card_program_runtime(program, capability_registry=registry, profile=profile)['strict_capability_ready']

        donor = 'Misfortune Teller'
        control = 'Cremate'
        simple = 'Grizzly Bears'
        baseline = verified(deepcopy(root))
        for name in (donor, control, simple, 'Tomebound Lich'):
            self.assertTrue(admitted(name, baseline), name)
        self.assertFalse(admitted('Ghost Vacuum', baseline))

        owners = (GRAVEYARD_EXILE_TYPE_OUTCOME_CAPABILITY, 'card.exile.public_graveyard', 'target.revalidate_resolution',
            'life.change.effect', 'token.creation.fixed_definition', 'mana.activated.fixed_output', 'trigger.event.normalized_damage')
        for missing in owners:
            with self.subTest(withdrawn=missing):
                rows = deepcopy(root)
                next(row for row in rows['capabilities'] if row['id'] == missing).update(
                    status='blocked', blockers=['Exact overlapping exile owner withdrawn'])
                registry = verified(rows)
                closure = registry.closure((missing,), profile=profile)
                self.assertFalse(closure.trusted)
                self.assertIn('status:' + missing + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in value for value in closure.blockers))
                self.assertFalse(admitted(donor, registry))
                self.assertEqual(missing not in {'card.exile.public_graveyard', 'target.revalidate_resolution'},
                    admitted(control, registry))
                self.assertTrue(admitted(simple, registry))
