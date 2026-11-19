"""Pinned end-step, current Decayed and intervening-if contracts."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.controller_creature_keyword_absence import (
    CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE, ControllerCreatureKeywordAbsence,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


JADAR = 'Jadar, Ghoulcaller of Nephalia'
CONDITION_CAPABILITY = 'trigger.condition.fixed_public_state'
DECAYED_CAPABILITY = 'combat.trigger.decayed'
PROFILE = 'commander_review'
FIXTURES = ROOT / 'tests' / 'fixtures'


def register_card(db, registry, name):
    card = db.lookup(name, fuzzy=False)
    semantics = SemanticRegistry()
    register_generated_programs(
        db, semantics, (card,), capability_registry=registry, capability_profile=PROFILE,
        promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
        promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
    )
    program = compile_best_available_card_program(
        db, card, semantic_registry=semantics, capability_registry=registry, capability_profile=PROFILE,
    )
    return bind_card_program_runtime(program, capability_registry=registry, profile=PROFILE), semantics


class ControllerCreatureKeywordAbsenceTests(unittest.TestCase):
    def test_complete_pinned_jadar_preserves_schedule_live_condition_and_decayed_result(self):
        registry = load_default_capability_registry()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'keyword-absence.sqlite3'
            build_fixture_database([
                FIXTURES / 'upkeep-creature-token-absence-cards.json',
                FIXTURES / 'controller-creature-keyword-absence-cards.json',
                FIXTURES / 'death-keyword-absence-cards.json',
            ], path)
            with CardDatabase(path) as db:
                card = db.lookup(JADAR, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertEqual((), ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual('step.begin', node.event)
                self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
                self.assertTrue({CONDITION_CAPABILITY, DECAYED_CAPABILITY} <= set(node.capability_dependencies))
                schedule, predicate = node.event_condition['all']
                self.assertEqual({'all': [
                    {'field': 'player', 'op': 'eq', 'value': '$source.controller'},
                    {'field': 'step', 'op': 'eq', 'value': 'end_step'},
                ]}, schedule)
                self.assertEqual(ControllerCreatureKeywordAbsence('decayed'),
                    ControllerCreatureKeywordAbsence.from_dict(predicate['condition']))
                token, = node.effects
                self.assertEqual(('create_token', 1, 'Zombie Token', '$controller'),
                    (token['op'], token['quantity'], token['name'], token['controller']))
                self.assertEqual({'combat_keyword_trigger', 'declaration_restriction'},
                    {fragment['kind'] for fragment in token['characteristics']['ability_fragments']})

                admitted, semantics = register_card(db, registry, JADAR)
                self.assertTrue(admitted['strict_capability_ready'], admitted['blockers'])
                program, = [p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == 'step.begin']
                self.assertIn(CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE, program.coverage)

                horde, _ = register_card(db, registry, 'Diregraf Horde')
                self.assertTrue(horde['strict_capability_ready'], horde['blockers'])
                for original, excluded in (
                    ('your end step', 'each end step'), ('your end step', 'your upkeep'),
                    ('with decayed', 'with flying'), ('no creatures', 'no other creatures'),
                    ('with decayed,', 'with decayed or haste,'),
                ):
                    with self.subTest(excluded=excluded):
                        changed = replace(card, oracle_text=card.oracle_text.replace(original, excluded, 1))
                        rejected = compile_oracle_card(changed, capability_registry=registry, capability_profile=PROFILE)
                        self.assertNotEqual('exact', rejected.status)

    def test_condition_codec_rejects_unknown_qualifiers_and_versions(self):
        spec = ControllerCreatureKeywordAbsence('decayed')
        self.assertEqual(spec, ControllerCreatureKeywordAbsence.from_dict(spec.to_dict()))
        for changes in ({'keyword': 'flying'}, {'schema_version': True}, {'schema_version': 2},
                {'scope': 'opponent'}, {'kind': 'decayed_at_departure'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ControllerCreatureKeywordAbsence.from_dict({**spec.to_dict(), **changes})


class ControllerCreatureKeywordAbsenceNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / 'bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'keyword-absence-native.sqlite3'
        build_fixture_database([
            basic, FIXTURES / 'targeted-graveyard-exile-cards.json',
            FIXTURES / 'upkeep-creature-token-absence-cards.json',
            FIXTURES / 'controller-creature-keyword-absence-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Controller end-step keyword absence', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def end_step(self, session, seat='A'):
        self.until(session, lambda game: game.state.step == 'end_step' and game.state.active_player == seat)

    @staticmethod
    def zombies(session, seat='A'):
        return [card for card in session.state.cards.values() if card.zone == 'battlefield'
            and card.controller == seat and card.is_token and card.printed_name == 'Zombie Token']

    def pay_for_jadar(self, session, source):
        self.assert_closed(session, source)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)

    def assert_private_hand_replays(self, session, private):
        for seat in session.engine.active_seats:
            if seat == private.owner:
                continue
            packet = json.dumps(session.packet('pilot:' + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(session, load=True)

    def test_paid_jadar_never_triggers_on_another_seats_end_step(self):
        session = self.session(702147709)
        source = self.add(session.engine, JADAR, zone='hand')
        timing = self.add(session.engine, 'Vedalken Orrery')
        lands = [self.add(session.engine, 'Swamp', ref=f'foreign-step-jadar-payment-{n}') for n in range(2)]
        self.assert_closed(session, timing)
        self.prepare(session, active='D', actor='D')
        self.checkpoint(session)
        self.pay_for_jadar(session, source)
        self.assertTrue(all(session.state.cards[land.object_id].tapped for land in lands))

        self.end_step(session, 'D')
        self.assertEqual(0, self.trigger_count(session, source))
        self.assertEqual([], self.zombies(session))
        self.replay(session, load=True)

    def test_paid_controller_end_step_ignores_foreign_decayed_and_survives_source_removal(self):
        session = self.session(702147710)
        source = self.add(session.engine, JADAR, zone='hand')
        opponent_token = self.add(session.engine, 'Rotten Reunion', seat='B', zone='hand')
        removal = self.add(session.engine, 'Shock', seat='B', zone='hand')
        private = self.add(session.engine, 'Humble', seat='C', zone='hand', ref='decayed-other-seat-secret')
        self.add(session.engine, 'Swamp', seat='B')
        self.add(session.engine, 'Mountain', seat='B')
        self.add(session.engine, 'Grizzly Bears')
        for spell in (opponent_token, removal):
            self.assert_closed(session, spell)
        self.prepare(session, mana={'B': 1, 'C': 1})
        self.checkpoint(session)
        self.pay_for_jadar(session, source)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.cast(session, 'B', opponent_token, [])
        self.assertIsNone(self.resolve(session))
        foreign, = self.zombies(session, 'B')
        self.assertIn('decayed', session.engine._combat_keywords(foreign))

        self.end_step(session)
        trigger, = session.state.stack
        self.assertEqual((source.object_id, 'A', 'A'), (
            trigger.source_object_id, trigger.controller, trigger.context['intervening_condition_controller'],
        ))
        self.cast(session, 'B', removal, [session.state.cards[source.object_id].ref])
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.resolve_top(session)
        zombie, = self.zombies(session)
        effective = session.engine._effective_card_data(zombie)
        self.assertEqual(('2', '2', ['B']), (str(effective['power']), str(effective['toughness']), effective['colors']))
        self.assertIn('decayed', session.engine._combat_keywords(zombie))
        self.assertEqual(1, len(self.zombies(session, 'B')))
        self.assert_private_hand_replays(session, private)

    def test_paid_existing_decayed_suppresses_occurrence_until_ability_is_removed(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                session = self.session(702147720 + remove)
                source = self.add(session.engine, JADAR, zone='hand')
                maker = self.add(session.engine, 'Rotten Reunion', zone='hand')
                self.assert_closed(session, maker)
                if remove:
                    layer_removal = self.add(session.engine, 'Humble', seat='B', zone='hand')
                    self.add(session.engine, 'Plains', seat='B', ref='keyword-absence-humble-first')
                    self.add(session.engine, 'Plains', seat='B', ref='keyword-absence-humble-second')
                    self.assert_closed(session, layer_removal)
                self.prepare(session, mana={'B': 2, 'C': 1})
                self.checkpoint(session)
                self.pay_for_jadar(session, source)
                self.cast(session, 'A', maker, [])
                self.assertIsNone(self.resolve(session))
                existing, = self.zombies(session)
                self.assertIn('decayed', session.engine._combat_keywords(existing))
                if remove:
                    self.cast(session, 'B', layer_removal, [existing.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertNotIn('decayed', session.engine._combat_keywords(session.state.cards[existing.object_id]))
                self.end_step(session)
                self.assertEqual(int(remove), self.trigger_count(session, source))
                if remove:
                    self.resolve_top(session)
                self.assertEqual(1 + int(remove), len(self.zombies(session)))
                self.replay(session, load=True)

    def test_paid_owned_decayed_response_suppresses_queued_result_after_replay(self):
        session = self.session(702147730)
        source = self.add(session.engine, JADAR, zone='hand')
        response = self.add(session.engine, 'Rotten Reunion', zone='hand')
        land = self.add(session.engine, 'Swamp')
        private = self.add(session.engine, 'Humble', seat='D', zone='hand', ref='decayed-response-secret')
        self.assert_closed(session, response)
        self.prepare(session, mana={'B': 1, 'C': 1})
        self.checkpoint(session)
        self.pay_for_jadar(session, source)
        self.end_step(session)
        self.assertEqual(1, self.trigger_count(session, source))
        self.assertEqual([], self.zombies(session))
        self.cast(session, 'A', response, [])
        self.resolve_top(session)
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.assertEqual(1, self.trigger_count(session, source))
        existing, = self.zombies(session)
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertEqual([existing.object_id], [card.object_id for card in self.zombies(session)])
        self.assert_private_hand_replays(session, private)

    def test_omitting_live_resolution_check_is_detected_by_paid_response(self):
        from quorune.rules import event_subscriptions

        original = event_subscriptions._controller_keyword_absence_holds

        def accept_recheck(host, source, context, condition):
            if 'intervening_condition_controller' in context:
                return True
            return original(host, source, context, condition)

        with patch.object(event_subscriptions, '_controller_keyword_absence_holds', accept_recheck):
            with self.assertRaisesRegex(AssertionError, 'Lists differ'):
                self.test_paid_owned_decayed_response_suppresses_queued_result_after_replay()

    def test_raw_program_requires_exact_occurrence_coverage_and_condition_capability(self):
        session = self.session(702147740)
        source = self.add(session.engine, JADAR)
        self.assert_closed(session, source)
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event == 'step.begin']
        schedule, predicate = program.event_condition['all']
        for changed in (
            replace(program, active_zone='graveyard'),
            replace(program, event_condition={'any': [schedule, predicate]}),
            replace(program, event_condition={'all': [{'all': [schedule['all'][1]]}, predicate]}),
            replace(program, event_condition=schedule),
            replace(program, coverage=[word for word in program.coverage if word != CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE]),
        ):
            with self.subTest(changed=changed.event_condition, zone=changed.active_zone, coverage=changed.coverage):
                binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding['strict'])
                self.assertTrue(any('runtime_event_subscriptions:invalid:' in value for value in binding['blockers']),
                    binding['blockers'])
        deps = [cap for cap in program.capability_dependencies if cap != CONDITION_CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + CONDITION_CAPABILITY, binding['blockers'])

    def test_verified_condition_and_decayed_withdrawals_require_complete_jadar(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        original, response, independent = JADAR, 'Rotten Reunion', 'Grizzly Bears'
        baseline = verified(deepcopy(source))
        for name in (original, response, independent):
            outcome, _ = register_card(self.db, baseline, name)
            self.assertTrue(outcome['strict_capability_ready'], outcome['blockers'])

        for missing in (CONDITION_CAPABILITY, DECAYED_CAPABILITY):
            with self.subTest(withdrawn=missing):
                rows = deepcopy(source)
                next(row for row in rows['capabilities'] if row['id'] == missing).update(
                    status='blocked', blockers=['Exact end-step candidate owner withdrawn'])
                registry = verified(rows)
                closure = registry.closure((missing,), profile=PROFILE)
                self.assertFalse(closure.trusted)
                self.assertIn('status:' + missing + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in value for value in closure.blockers))
                for name in (original, response, independent):
                    outcome, _ = register_card(self.db, registry, name)
                    ready = name == independent or name == response and missing == CONDITION_CAPABILITY
                    self.assertEqual(ready, outcome['strict_capability_ready'], (name, outcome['blockers']))
