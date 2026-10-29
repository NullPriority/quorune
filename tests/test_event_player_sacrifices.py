"""CR701.21/603/608: occurrence chooser, source controller and owner differ."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_ring_native_actions as traversal
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


DAMAGE_TEXT = 'Whenever this creature deals combat damage to a player, that player sacrifices a creature of their choice.'


class EventPlayerSacrificeCompilerTests(unittest.TestCase):
    def test_missing_choice_dependencies_fail_closed_with_an_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        record = replace(query_record(DAMAGE_TEXT), type_line='Creature — Human', keywords=())
        self.assertEqual('exact', compile_oracle_card(record,
            capability_registry=load_default_capability_registry()).status)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        for capability in ('resolution.event_player.fixed_sacrifice', 'choice.affected_player.fixed_sacrifice',
                           'zone.change.destination_replacement', 'trigger.placement.apnap', 'variant.monarch.designate'):
            value = deepcopy(raw)
            next(row for row in value['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['omitted tested owner'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations,
                                discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            with self.subTest(capability=capability):
                self.assertEqual(capability == 'variant.monarch.designate',
                    compile_oracle_card(record, capability_registry=registry).status == 'exact')

    def test_source_subject_matrix_preserves_player_meaning_and_rejects_ambiguous_reads(self):
        registry = load_default_capability_registry()
        for text, expected in (
            (DAMAGE_TEXT, '$context.target'),
            ('Whenever this creature dies, that creature\'s controller sacrifices a creature of their choice.', '$context.previous_controller'),
            ('Whenever this creature attacks, defending player sacrifices a permanent of their choice.', '$context.defending_player'),
        ):
            with self.subTest(text=text):
                record = replace(query_record(text), type_line='Creature — Human', keywords=())
                ir = compile_oracle_card(record, capability_registry=registry)
                self.assertEqual('exact', ir.status)
                node = next(node for face in ir.faces for node in face.nodes if node.kind == 'triggered_ability')
                self.assertIsNone(node.target_schema)
                self.assertEqual([expected], node.effects[0]['players'])
                self.assertNotIn('target', node.effects[0])
        for text in (
            DAMAGE_TEXT.replace('that player', "that creature's controller"),
            DAMAGE_TEXT.replace('to a player', 'to a creature'),
            DAMAGE_TEXT.replace('that player', 'that permanent\'s owner'),
            DAMAGE_TEXT.replace('a creature of their choice', 'an unrepresented permanent kind'),
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(
                    replace(query_record(text), type_line='Creature — Human', keywords=()),
                    capability_registry=registry).status)

    def test_shape_rejects_targets_literal_players_and_changed_origin(self):
        from quorune.compiler.affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template
        from quorune.compiler.event_player_choice_templates import EVENT_PLAYER_CHOICE_MECHANIC
        from quorune.rules.event_player_choice_shapes import event_player_sacrifice_node_capabilities
        template = fixed_affected_player_sacrifice_effect_template('Sacrifice a creature.')
        effect = {**template.effects[0], 'players': ['$context.target']}
        args = {'effects': (effect,), 'target_schema': None,
                'mechanic_ids': {*template.mechanics, EVENT_PLAYER_CHOICE_MECHANIC}}
        self.assertIn('resolution.event_player.fixed_sacrifice', event_player_sacrifice_node_capabilities(**args))
        for change in ({'players': ['B']}, {'players': ['$target.0']}, {'players': ['$context.unknown']},
                       {'actor': '$context.target'}, {'target': '$context.target'}, {'count': True}):
            self.assertEqual((), event_player_sacrifice_node_capabilities(**{**args, 'effects': ({**effect, **change},)}))
        self.assertEqual((), event_player_sacrifice_node_capabilities(**{**args, 'target_schema': {'count': 1}}))


class EventPlayerSacrificeActionTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.JobSelectRuntimeTests.session
    add = actions.JobSelectRuntimeTests.add
    prepare = actions.JobSelectRuntimeTests.prepare
    cast = actions.JobSelectRuntimeTests.cast
    checkpoint = actions.JobSelectRuntimeTests.checkpoint
    resolve = actions.JobSelectRuntimeTests.resolve
    replay = actions.JobSelectRuntimeTests.replay
    until = traversal.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'event-player-sacrifice.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/event-player-sacrifice-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Event player choices', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 24)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def assert_closed(self, name):
        card = self.db.lookup(name)
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'], name)

    def fixture(self, seed, *, removal=False, victim=True, attack=False):
        name = 'Generic Defending Player Sacrifice Aura' if attack else 'Generic Event Sacrifice Aura'
        self.assert_closed(name)
        session = self.session(seed)
        aura = self.add(session.engine, name, zone='hand', ref='event-choice-provider')
        attacker = self.add(session.engine, 'Generic Bound Body', seat='B', ref='event-choice-damager')
        chosen = self.add(session.engine, 'Generic Bound Body', seat='C', ref='event-choice-owned-by-C') if victim else None
        if chosen is not None:
            session.engine.change_control(chosen.object_id, 'D', reason='pre-action victim control fixture')
            self.add(session.engine, 'Generic Bound Body', seat='D', ref='event-choice-other-eligible')
        response = self.add(session.engine, 'Disenchant', zone='hand', ref='event-choice-remove-grant') if removal else None
        if removal:
            starting_card = session.state.players['A'].zones['hand'][0]
            session.engine.move_card(starting_card, 'graveyard', reason='pre-action hand-size fixture')
        for index in range(4):
            self.add(session.engine, 'Generic Bound Plains', ref='event-choice-land-' + str(index))
        self.prepare(session, mana={'U': 4, 'C': 20})
        self.checkpoint(session)
        self.cast(session, 'A', aura, [attacker.ref])
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda current: current.state.active_player == 'B'
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == 'combat.attackers')
        result = session.act('pilot:B', {'action_id': 'attack', 'attackers': {attacker.ref: 'D'}})
        self.assertTrue(result.ok, result.summary)
        self.until(session, lambda current: any(item.source_object_id == attacker.object_id
                   and item.context.get('event') == ('creature.attacks' if attack else 'damage.dealt')
                   for item in current.state.stack))
        if removal:
            self.cast(session, 'A', response, [aura.ref])
        return session, aura, attacker, chosen

    def test_granted_damage_chooses_damaged_player_and_sacrifices_borrowed_permanent(self):
        session, aura, attacker, chosen = self.fixture(701_021_101)
        decision = self.resolve(session)
        self.assertEqual('choice.apnap', decision.kind)
        self.assertEqual(['D'], decision.actors)
        self.assertEqual(38, session.state.players['D'].life)
        self.assertEqual('B', session.state.stack[-1].controller)
        self.assertEqual(attacker.object_id, session.state.stack[-1].source_object_id)
        session = self.replay(session, load=True)
        unchanged = authoritative_state_hash(session.state)
        command = {'action_id': 'choose', 'cards': [chosen.ref]}
        for wrong in ('A', 'B', 'C'):
            self.assertFalse(session.act('pilot:' + wrong, command).ok)
            self.assertEqual(unchanged, authoritative_state_hash(session.state))
        result = session.act('pilot:D', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[chosen.object_id].zone)
        self.assertIn(chosen.object_id, session.state.players['C'].zones['graveyard'])
        self.assertEqual('battlefield', session.state.cards[attacker.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[aura.object_id].zone)
        self.replay(session, load=True)

    def test_pending_granted_sacrifice_survives_provider_removal(self):
        session, aura, attacker, chosen = self.fixture(701_021_102, removal=True)
        decision = self.resolve(session)
        self.assertEqual('choice.apnap', decision.kind)
        self.assertEqual(['D'], decision.actors)
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        session = self.replay(session, load=True)
        result = session.act('pilot:D', {'action_id': 'choose', 'cards': [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[chosen.object_id].zone)
        self.replay(session, load=True)

    def test_original_demon_has_complete_normal_capability_binding(self):
        self.assert_closed('Demon of Wailing Agonies')

    def test_no_eligible_permanent_completes_without_an_impossible_choice(self):
        session, _aura, _attacker, _chosen = self.fixture(701_021_103, victim=False)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(38, session.state.players['D'].life)
        self.assertEqual([], session.state.players['D'].zones['graveyard'])
        self.replay(session, load=True)

    def test_attack_defender_makes_the_untargeted_choice_before_damage(self):
        session, _aura, attacker, chosen = self.fixture(701_021_104, attack=True)
        decision = self.resolve(session)
        self.assertEqual('choice.apnap', decision.kind)
        self.assertEqual(['D'], decision.actors)
        self.assertEqual(40, session.state.players['D'].life)
        self.assertEqual('B', session.state.stack[-1].controller)
        self.assertEqual(attacker.object_id, session.state.stack[-1].source_object_id)
        session = self.replay(session, load=True)
        result = session.act('pilot:D', {'action_id':'choose', 'cards':[chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[chosen.object_id].zone)
        self.assertEqual(40, session.state.players['D'].life)
        self.replay(session, load=True)

    def test_sacrifice_player_binding_mutant_is_killed(self):
        from quorune.compiler import event_player_choice_templates as compiler
        witness = EventPlayerSacrificeCompilerTests()
        witness.test_source_subject_matrix_preserves_player_meaning_and_rejects_ambiguous_reads()
        with patch.object(compiler, '_event_player', return_value='$controller'), self.assertRaises(AssertionError):
            witness.test_source_subject_matrix_preserves_player_meaning_and_rejects_ambiguous_reads()
