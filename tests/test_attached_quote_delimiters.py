"""Closed quote punctuation and recipient-owned attachment grants."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as controls
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry


CANDIDATES = {"Thief's Knife", "Black Mage's Rod", "Sage's Nouliths",
              "White Mage's Staff", "Red Mage's Rapier"}


class AttachedQuoteDelimiterModelTests(unittest.TestCase):
    def test_missing_outer_or_independent_child_authority_remains_residual(self):
        from copy import deepcopy
        from test_qualified_zone_event_queries import query_record
        from quorune.rules.capabilities import CapabilityRegistry
        text = 'Equipped creature gets +1/+1, has "Whenever this creature deals combat damage to a player, draw a card," and is a Rogue in addition to its other types.'
        card = replace(query_record(text), type_line='Artifact — Equipment')
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('continuous.attached.fixed_characteristics', 'zone.draw.library_to_hand'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['omitted attached grant authority'])
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=CapabilityRegistry(changed)).status)

    def test_delimiter_keeps_inner_commas_complete_outer_shell_and_original_span(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.continuous_templates import attached_quoted_ability_text
        text = 'Equipped creature gets +1/+1, has "Whenever this creature deals combat damage to a player, draw a card," and is a Rogue in addition to its other types.'
        quoted = attached_quoted_ability_text(text)
        self.assertEqual('Whenever this creature deals combat damage to a player, draw a card', quoted)
        card = replace(query_record(text), type_line='Artifact — Equipment')
        ir = compile_oracle_card(card, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status)
        self.assertEqual(text, ir.faces[0].nodes[0].text)
        more = text.replace('draw a card,', 'draw a card. Discard a card,')
        self.assertIn('draw a card. Discard a card', attached_quoted_ability_text(more))
        for changed in (text.replace('Rogue', 'Unrepresentedtype'), text + ' Destroy everything.',
                        text.replace('draw a card,', 'draw a card,,'), text.replace('draw a card,', 'perform an unrepresented instruction,')):
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=changed),
                capability_registry=load_default_capability_registry()).status)

    def test_ability_and_type_without_stats_keeps_the_independent_exact_child(self):
        from test_qualified_zone_event_queries import query_record
        text = 'Equipped creature has "Whenever you cast a noncreature spell, this creature gets +2/+0 until end of turn" and is a Wizard in addition to its other types.'
        card = replace(query_record(text), type_line='Artifact — Equipment')
        ir = compile_oracle_card(card, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status)
        for changed in (text.replace('Wizard', 'Unrepresentedtype'), text.replace('gets +2/+0 until end of turn', 'does something unknown')):
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=changed),
                capability_registry=load_default_capability_registry()).status)


class AttachedQuoteDelimiterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.JobSelectRuntimeTests.session
    add = controls.JobSelectRuntimeTests.add
    prepare = controls.JobSelectRuntimeTests.prepare
    cast = controls.JobSelectRuntimeTests.cast
    checkpoint = controls.JobSelectRuntimeTests.checkpoint
    resolve = controls.JobSelectRuntimeTests.resolve
    replay = controls.JobSelectRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'attached-quotes.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/job-select-cards.json',
            ROOT / 'tests/fixtures/ascend-city-blessing-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Attached quotes', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_candidates_have_complete_normal_binding(self):
        for name in sorted(CANDIDATES):
            program = compile_best_available_card_program(self.db, self.db.lookup(name),
                semantic_registry=SemanticRegistry(), capability_registry=self.registry,
                capability_profile='commander_review')
            with self.subTest(card=name):
                self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'])

    def fixture(self, seed, names):
        session = self.session(seed)
        engine = session.engine
        recipient = self.add(engine, 'Generic Bound Body', seat='B', ref='grant-recipient-B')
        recipient.acquired_control_turn_count = -1
        sources = []
        for index, name in enumerate(names):
            source = self.add(engine, name, ref='grant-equipment-A-' + str(index))
            attach_objects(engine.state.cards, source, recipient, source_timestamp=engine._next_zone_timestamp())
            sources.append(source)
        private = {seat: self.add(engine, 'Generic Bound Body', seat=seat, zone='hand',
            ref='quote-private-' + seat) for seat in 'ABCD'}
        self.prepare(session, actor='B', active='B', mana={'C':20, 'W':4, 'G':4})
        engine.state.players['A'].mana_pool.update({'G':4, 'C':4})
        engine.state.players['D'].mana_pool.update({'W':2, 'C':4})
        self.checkpoint(session)
        return session, sources, recipient, private

    def until(self, session, predicate, *, in_combat=False):
        for _ in range(80):
            session.engine.pump()
            if predicate(session):
                return
            if in_combat and session.state.phase_index >= 9:
                self.fail('No granted trigger before the combat ended')
            decision = session.state.pending_decision
            self.assertIsNotNone(decision)
            if decision.kind == 'priority':
                response = {'action_id':'pass'}
            elif decision.kind == 'combat.blockers':
                response = {'action_id':'block', 'blocks':{}}
            else:
                self.fail('Unexpected grant witness decision ' + decision.kind)
            principal = session.pending_principals()[0]
            session.packet(principal, full=True)
            result = session.act(principal, response)
            self.assertTrue(result.ok, result.summary)
        self.fail('Grant action did not reach the required state')

    def attack(self, session, recipient):
        self.until(session, lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
        session.packet('pilot:B', full=True)
        command = {'action_id':'attack', 'attackers':{recipient.ref:'C'}}
        unchanged = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', command).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        result = session.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_combat_draw_belongs_to_recipient_and_survives_equipment_removal(self):
        session, sources, recipient, private = self.fixture(109_005_001, ["Thief's Knife"])
        removal = self.add(session.engine, 'Disenchant', seat='D', zone='hand', ref='remove-grant-A')
        for index in range(2):
            self.add(session.engine, 'Generic Bound Plains', seat='D', ref='response-land-' + str(index))
        self.checkpoint(session)
        hands = {seat:set(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        life = session.state.players['C'].life
        self.attack(session, recipient)
        self.until(session, lambda s:bool(s.state.stack), in_combat=True)
        self.assertEqual(life - 3, session.state.players['C'].life)
        self.assertEqual('B', session.state.stack[-1].controller)
        self.assertEqual(recipient.object_id, session.state.stack[-1].source_object_id)
        session = self.replay(session, load=True)
        self.cast(session, 'D', removal, [sources[0].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[sources[0].object_id].zone)
        self.assertEqual(1, len(set(session.state.players['B'].zones['hand']) - hands['B']))
        for seat in 'AC':
            self.assertEqual(hands[seat], set(session.state.players[seat].zones['hand']))
        self.assertEqual(len(hands['D']) - 1, len(session.state.players['D'].zones['hand']))
        drawn = next(iter(set(session.state.players['B'].zones['hand']) - hands['B']))
        for viewer in 'ABCD':
            packet = json.dumps(session.packet('pilot:' + viewer, full=True))
            self.assertEqual(viewer == 'B', session.state.cards[drawn].ref in packet)
            for owner in 'ABCD':
                if viewer != owner:
                    self.assertNotIn(private[owner].ref, packet)
        self.assertNotIn('rogue', session.engine._type_parts(session.engine._effective_card_data(session.state.cards[recipient.object_id])['type_line'])[1])
        self.replay(session, load=True)

    def test_spell_cast_grants_use_recipient_controller_and_own_source(self):
        session, sources, recipient, private = self.fixture(109_005_002, ["Black Mage's Rod", "Red Mage's Rapier"])
        growth = self.add(session.engine, 'Generic Bound Growth', zone='hand', ref='A-negative-spell')
        other = self.add(session.engine, 'Generic Bound Body', ref='A-growth-target')
        dummy = self.add(session.engine, "Monk's Fist", ref='unrelated-disenchant-target')
        spell = self.add(session.engine, 'Disenchant', seat='B', zone='hand', ref='B-noncreature-spell')
        self.checkpoint(session)
        life = {seat:session.state.players[seat].life for seat in 'ABCD'}
        self.cast(session, 'A', growth, [other.ref])
        self.assertEqual(1, len(session.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life, {seat:session.state.players[seat].life for seat in 'ABCD'})
        self.cast(session, 'B', spell, [dummy.ref])
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        refs = [item.ref for batch in session.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2, len(refs))
        result = session.act('pilot:B', {'action_id':'order', 'triggers':refs})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({seat:value - (seat != 'B') for seat,value in life.items()},
            {seat:session.state.players[seat].life for seat in 'ABCD'})
        self.assertEqual(5, session.engine._numeric_stat(recipient.object_id, 'power'))
        self.replay(session, load=True)

    def test_attack_grants_gain_life_and_offer_only_attacking_untap_targets(self):
        session, sources, recipient, private = self.fixture(109_005_003, ["White Mage's Staff"])
        before = {seat:session.state.players[seat].life for seat in 'ABCD'}
        self.attack(session, recipient)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({seat:value + (seat == 'B') for seat,value in before.items()},
            {seat:session.state.players[seat].life for seat in 'ABCD'})
        self.replay(session, load=True)
        session, sources, recipient, private = self.fixture(109_005_004, ["Sage's Nouliths"])
        other = self.add(session.engine, 'Generic Bound Body', ref='not-attacking-target')
        self.checkpoint(session)
        self.attack(session, recipient)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        schema = session.packet('pilot:B', full=True)['decision']['ctx']['target_schema']
        self.assertIn(recipient.ref, schema['legal_refs'])
        self.assertNotIn(other.ref, schema['legal_refs'])
        session = self.replay(session, load=True)
        unchanged = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id':'choose','targets':[recipient.ref]}).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        result = session.act('pilot:B', {'action_id':'choose','targets':[recipient.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[recipient.object_id].tapped)
        self.assertEqual('C', session.state.cards[recipient.object_id].attacking)
        self.replay(session, load=True)

    def test_grant_fragment_omission_is_killed_by_native_recipient_consequences(self):
        import quorune.compiler.attached_granted_ability_nodes as owner
        method = 'test_actual_combat_draw_belongs_to_recipient_and_survives_equipment_removal'
        with patch.object(owner, 'attached_granted_ability_plan', return_value=None), self.assertRaises(AssertionError):
            getattr(type(self)(method), method)()
