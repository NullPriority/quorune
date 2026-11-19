"""Pinned CR 603.4 contracts for live subtype creature token absence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_scheduled_player_triggers as scheduled
import test_targeted_graveyard_exile as previous

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.characteristic_fragments import CharacteristicQuantityScope
from quorune.compiler.public_trigger_conditions import _each_upkeep_creature_token_absence
from quorune.continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from quorune.object_predicate import ObjectQuerySpec


UPKEEP = 'At the beginning of each upkeep'
PEST_ABSENCE = "you don't control a Pest creature token"


class UpkeepCreatureTokenAbsenceTests(unittest.TestCase):
    def test_each_upkeep_counts_only_live_creature_tokens_of_the_qualified_subtype(self):
        for wording, subtypes in (
            (PEST_ABSENCE, ('pest',)),
            ("you don’t control a Time Lord creature token", ('time lord',)),
        ):
            with self.subTest(wording=wording):
                condition = _each_upkeep_creature_token_absence(UPKEEP, wording)
                self.assertIsNotNone(condition)
                query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), subtypes_all=subtypes, token=True)
                self.assertEqual(FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST, condition.kind)
                self.assertEqual(0, condition.amount)
                self.assertEqual(CharacteristicQuantityScope.CONTROLLER_ZONE, condition.quantity.scope)
                self.assertFalse(condition.quantity.exclude_source)
                self.assertEqual(query, condition.quantity.query)
                self.assertEqual(condition, FixedPublicStateConditionSpec.from_dict(condition.to_dict()))

    def test_subtype_token_absence_stays_inside_its_upkeep_and_fixed_subtype_envelope(self):
        for prefix, condition in (
            ('At the beginning of your end step', PEST_ABSENCE),
            (UPKEEP, "you don't control a Pest permanent"),
            (UPKEEP, "you don't control another Pest creature token"),
            (UPKEEP, "you don't control a Pest or Zombie creature token"),
            (UPKEEP, "you don't control a Madeup creature token"),
            (UPKEEP, "you don't control a Pest creature token with flying"),
            (UPKEEP, "you control no creatures with decayed"),
        ):
            with self.subTest(prefix=prefix, condition=condition):
                self.assertIsNone(_each_upkeep_creature_token_absence(prefix, condition))

    def test_pinned_original_keeps_the_step_token_intrinsic_and_life_replacement(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from quorune.carddb import CardDatabase
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        fixture = Path(__file__).with_name('fixtures') / 'upkeep-creature-token-absence-cards.json'
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'upkeep-token-absence.sqlite3'
            build_fixture_database(fixture, path)
            with CardDatabase(path) as db:
                registry = load_default_capability_registry()
                def ir(name):
                    return compile_oracle_card(db.lookup(name, fuzzy=False), capability_registry=registry,
                        capability_profile='commander_review')

                original = ir('Pest Rescuer')
                control = ir('Beledros Witherbloom')

        self.assertEqual('exact', original.status, original.material_residuals)
        self.assertEqual('exact', control.status, control.material_residuals)
        trigger, intrinsic, replacement = original.faces[0].nodes
        self.assertEqual(('step.begin', 'creature.dies.self', 'life.change'),
            (trigger.event, intrinsic.event, replacement.event))
        self.assertIn('trigger.condition.fixed_public_state', trigger.capability_dependencies)
        self.assertIn('token.creation.typed_ability_definition', trigger.capability_dependencies)
        self.assertIn('life.gain.replacement.static_addition', replacement.capability_dependencies)
        schedule, predicate = trigger.event_condition['all']
        self.assertEqual({'all': [{'field': 'step', 'op': 'eq', 'value': 'upkeep'}]}, schedule)
        self.assertEqual('fixed_public_state_condition', predicate['field'])
        self.assertEqual(_each_upkeep_creature_token_absence(UPKEEP, PEST_ABSENCE),
            FixedPublicStateConditionSpec.from_dict(predicate['condition']))
        token, = trigger.effects
        self.assertEqual('Pest Token', token['name'])
        self.assertEqual(['B', 'G'], token['characteristics']['colors'])
        self.assertEqual(1, len(token['characteristics']['ability_fragments']))
        self.assertEqual([{'op': 'life', 'player': '$controller', 'delta': 1}], list(intrinsic.effects))

class UpkeepCreatureTokenAbsenceNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = previous.TargetedGraveyardExileNativeTests.start
    finish = previous.TargetedGraveyardExileNativeTests.finish
    enter_upkeep = scheduled.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        database = folder / 'upkeep-creature-token-absence.sqlite3'
        build_fixture_database([
            basic,
            ROOT / 'tests/fixtures/targeted-graveyard-exile-cards.json',
            ROOT / 'tests/fixtures/upkeep-creature-token-absence-cards.json',
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Each upkeep creature-token absence', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def resolved_rescuer(self, session, source):
        self.prepare(session, mana={'C': 2, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))

    @staticmethod
    def owned_pests(session, seat='A'):
        return [card for card in session.state.cards.values()
            if card.zone == 'battlefield' and card.controller == seat
            and card.is_token and card.printed_name == 'Pest Token']

    def seed_pest(self, session, seat):
        session.engine.create_token(seat, name='Pest Token', reason='Fixture public predicate member',
            characteristics={'type_line': 'Token Creature — Pest', 'colors': ['B', 'G'], 'power': '1', 'toughness': '1'})
        pest, = self.owned_pests(session, seat)
        return pest

    def test_actual_each_opponent_upkeep_ignores_foreign_tokens_and_nontoken_pests_then_gains_two(self):
        session, private = self.start(741110)
        source = self.add(session.engine, 'Pest Rescuer', zone='hand')
        changeling = self.add(session.engine, 'Universal Automaton')
        answer = self.add(session.engine, 'Shock', seat='B', zone='hand')
        mountain = self.add(session.engine, 'Mountain', seat='B')
        for card in (source, changeling, answer, mountain):
            self.assert_closed(session, card)
        self.seed_pest(session, 'D')
        self.resolved_rescuer(session, source)
        row = session.engine._public_object_query_result(session.state.cards[changeling.object_id])
        self.assertIn('pest', row.subtypes)
        self.assertFalse(row.token)
        self.enter_upkeep(session, 'B')
        triggers = [item for item in session.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(1, len(triggers))
        self.assertEqual(('A', 'B'), (triggers[0].controller, triggers[0].context['player']))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        pest, = self.owned_pests(session)
        self.assertEqual(1, len(self.owned_pests(session, 'D')))
        self.cast(session, 'B', answer, [pest.ref])
        self.until(session, lambda game: any(item.kind == 'triggered_ability' and item.source_object_id == pest.object_id
            for item in game.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.owned_pests(session))
        self.assertEqual((42, 40, 40, 40), tuple(session.state.players[seat].life for seat in 'ABCD'))
        self.finish(session, private)

    def test_actual_existing_owned_creature_token_prevents_queueing_at_upkeep(self):
        session, private = self.start(741120)
        source = self.add(session.engine, 'Pest Rescuer', zone='hand')
        answer = self.add(session.engine, 'Shock', seat='B', zone='hand')
        mountain = self.add(session.engine, 'Mountain', seat='B')
        for card in (source, answer, mountain):
            self.assert_closed(session, card)
        pest = self.seed_pest(session, 'A')
        self.resolved_rescuer(session, source)
        self.enter_upkeep(session, 'B')
        self.assertEqual(0, self.trigger_count(session, source))
        self.cast(session, 'B', answer, [pest.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.owned_pests(session))
        self.assertEqual(0, self.trigger_count(session, source))
        self.finish(session, private)

    def test_actual_new_owned_pest_in_response_prevents_the_queued_result_after_load(self):
        session, private = self.start(741130)
        source = self.add(session.engine, 'Pest Rescuer', zone='hand')
        response = self.add(session.engine, 'Professor of Zoomancy', zone='hand')
        orrery = self.add(session.engine, 'Vedalken Orrery')
        lands = [self.add(session.engine, 'Forest', ref=f'upkeep-response-forest-{i}') for i in range(4)]
        for card in (source, response, orrery, *lands):
            self.assert_closed(session, card)
        self.resolved_rescuer(session, source)
        self.enter_upkeep(session, 'B')
        trigger, = [item for item in session.state.stack if item.source_object_id == source.object_id]
        self.cast(session, 'A', response)
        self.until(session, lambda game: bool(self.owned_pests(game)) and len(game.state.stack) == 1)
        pest, = self.owned_pests(session)
        self.assertEqual(trigger.ref, session.state.stack[0].ref)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([pest.object_id], [card.object_id for card in self.owned_pests(session)])
        self.assertEqual(40, session.state.players['A'].life)
        self.finish(session, private)

    def test_fresh_native_missing_token_qualifier_is_killed_by_the_nontoken_pest_witness(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.card_programs.runtime import _FixedPublicStateSnapshotResolver as Resolver

        original = Resolver._condition_quantity

        def unqualified(host, source, condition):
            quantity = condition.quantity
            if quantity is not None and quantity.query.token is True and quantity.query.subtypes_all == ('pest',):
                condition = replace(condition, quantity=replace(quantity, query=replace(quantity.query, token=None)))
            return original(host, source, condition)

        with patch.object(Resolver, '_condition_quantity', unqualified):
            with self.assertRaisesRegex(AssertionError, '1 != 0'):
                self.test_actual_each_opponent_upkeep_ignores_foreign_tokens_and_nontoken_pests_then_gains_two()

    def test_genuinely_verified_condition_intrinsic_and_life_owner_withdrawals_validate_whole_pest(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        initial = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declaration = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        profile = 'commander_review'

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declaration, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        def admitted(name, registry):
            record = self.db.lookup(name, fuzzy=False)
            semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (record,), capability_registry=registry, capability_profile=profile,
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
            program = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                capability_registry=registry, capability_profile=profile)
            return bind_card_program_runtime(program, capability_registry=registry, profile=profile)['strict_capability_ready']

        pest, dragon, body = 'Pest Rescuer', 'Beledros Witherbloom', 'Universal Automaton'
        baseline = verified(deepcopy(initial))
        for name in (pest, dragon, body):
            self.assertTrue(admitted(name, baseline))

        affected = {
            'trigger.condition.fixed_public_state': {pest},
            'token.creation.typed_ability_definition': {pest, dragon},
            'life.gain.replacement.static_addition': {pest},
        }
        for missing, blocked in affected.items():
            with self.subTest(withdrawn=missing):
                rows = deepcopy(initial)
                next(row for row in rows['capabilities'] if row['id'] == missing).update(
                    status='blocked', blockers=['Exact intervening Pest owner withdrawn'])
                registry = verified(rows)
                report = registry.closure((missing,), profile=profile)
                self.assertFalse(report.trusted)
                self.assertIn('status:' + missing + ':blocked', report.blockers)
                self.assertFalse(any('unverified' in value for value in report.blockers))
                for name in (pest, dragon, body):
                    self.assertEqual(name not in blocked, admitted(name, registry), name)
