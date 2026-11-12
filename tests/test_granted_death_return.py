"""Granted death-return triggers retain their holder and departure contract."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class GrantedDeathReturnCompilerTests(unittest.TestCase):
    def test_static_and_conditional_grants_use_permanent_departure_and_exact_counter(self):
        registry = load_default_capability_registry()
        for text, counter in (
            ('Creatures you control have persist.', '-1/-1'),
            ('Artifacts you control have persist.', '-1/-1'),
            ('Enchanted creature has undying.', '+1/+1'),
            ('This creature has persist as long as you control a black creature.', '-1/-1'),
            ('Other non-Human creatures you control have undying.', '+1/+1'),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                nodes = [node for face in ir.faces for node in face.nodes]
                child = next(node for node in nodes if node.kind == 'granted_triggered_ability')
                outer = next(node for node in nodes if node.kind == 'static_ability')
                self.assertEqual('permanent.graveyard.self', child.event)
                self.assertEqual({'field': 'death_return_departed_without_counter',
                    'counter': counter, 'op': 'truthy'}, child.event_condition)
                self.assertEqual('death_return_with_counter', child.effects[0]['op'])
                self.assertEqual(counter, child.effects[0]['entry_counter'])
                self.assertEqual('$source', child.effects[0]['card'])
                self.assertEqual(outer.span, child.span)
                self.assertIn('continuous.ability.fixed_keyword_event_grant', outer.capability_dependencies)

    def test_parameters_unknown_siblings_and_two_keywords_do_not_gain_support(self):
        registry = load_default_capability_registry()
        for text in ('Creatures you control have persist 2.',
                     'Creatures you control have undying X.',
                     'Creatures you control have persist and undying.',
                     'Creatures you control have persist and remember the prophecy.',
                     'Other non-Human creatures you control get +1/+1 and have undying.',
                     'Creatures you control have persist.\nThe remembered prophecy becomes true.'):
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry)
                self.assertNotEqual('exact', ir.status)
                self.assertTrue(ir.material_residuals)
        for text in ('Target creature gains undying 2 until end of turn.',
                     'Target creature gains persist until your next turn.',
                     'Target creature gains undying until end of turn.\nThe remembered prophecy becomes true.'):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(query_record(text), type_line='Instant'), capability_registry=registry)
                self.assertNotEqual('exact', ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_temporary_spell_grant_keeps_target_and_duration(self):
        record = replace(query_record('Target creature gains undying until end of turn.'), type_line='Instant')
        ir = compile_oracle_card(record, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        outer, child = ir.faces[0].nodes
        self.assertEqual('spell_ability', outer.kind)
        self.assertEqual('granted_triggered_ability', child.kind)
        self.assertEqual('permanent.graveyard.self', child.event)
        self.assertEqual('apply_source_characteristics_until_end_of_turn', outer.effects[0]['op'])
        self.assertEqual('$target.0', outer.effects[0]['card'])
        self.assertEqual(['creature'], outer.target_schema['types_any'])
        self.assertEqual(1, len(outer.effects[0]['ability_fragments']))
        ir = compile_oracle_card(replace(query_record('Creatures you control gain persist until end of turn.'),
            type_line='Instant'), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        outer, child = ir.faces[0].nodes
        self.assertIsNone(outer.target_schema)
        self.assertEqual(8, outer.effects[0]['schema_version'])
        self.assertEqual('$controller', outer.effects[0]['predicate']['controller'])
        self.assertEqual('permanent.graveyard.self', child.event)

    def test_death_return_lowering_omission_is_killed(self):
        registry = load_default_capability_registry()
        for card in (query_record('Creatures you control have persist.'),
                     replace(query_record('Target creature gains undying until end of turn.'), type_line='Instant')):
            self.assertEqual('exact', compile_oracle_card(card, capability_registry=registry).status)
            with patch('quorune.compiler.keyword_nodes.death_return_keyword_node', return_value=None):
                self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=registry).status)

    def test_keyword_and_grant_authorities_fail_closed_independently(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        for omitted in ('counter.producer.persist', 'counter.producer.undying',
                        'continuous.ability.fixed_keyword_event_grant',
                        'continuous.ability.temporary_quoted_grant', 'variant.monarch.designate'):
            value = deepcopy(raw)
            next(row for row in value['capabilities'] if row['id'] == omitted).update(
                status='blocked', blockers=['tested keyword or grant authority withdrawal'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            for keyword in ('persist', 'undying'):
                with self.subTest(omitted=omitted, keyword=keyword):
                    ir = compile_oracle_card(query_record('Creatures you control have ' + keyword + '.'),
                        capability_registry=registry)
                    blocked = omitted in {'counter.producer.' + keyword, 'continuous.ability.fixed_keyword_event_grant'}
                    self.assertEqual(not blocked, ir.status == 'exact')
            card = replace(query_record('Target creature gains undying until end of turn.'), type_line='Instant')
            ir = compile_oracle_card(card, capability_registry=registry)
            self.assertEqual(omitted not in {'counter.producer.undying', 'continuous.ability.temporary_quoted_grant'},
                ir.status == 'exact')


class GrantedDeathReturnRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    resume = native.AbilitySourceCounterRuntimeTests.resume
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'granted-death-return.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/granted-death-return-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Granted death returns', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def return_trigger(self, session, source):
        for _ in range(64):
            matches = [item for item in session.state.stack if item.kind == 'triggered_ability'
                and item.source_object_id == source.object_id]
            if matches:
                self.assertEqual(1, len(matches))
                return matches[0]
            decision = session.state.pending_decision
            if decision is None:
                session.engine.pump()
                continue
            self.assertEqual('priority', decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('The granted death-return ability did not trigger')

    def test_actual_scarecrow_condition_uses_departing_controller_then_owner_return_and_counter(self):
        for black in (True, False):
            with self.subTest(black_creature_controlled_by_recipient=black):
                session = self.session(983_100 + black)
                engine = session.engine
                source = self.add(engine, 'Rattleblaze Scarecrow', zone='hand')
                engine.move_card(source.object_id, 'battlefield', controller='B', log=False)
                self.add(engine, 'Tormented Soul', ref='owner-black-creature')
                self.add(engine, 'Tormented Soul' if black else 'Grizzly Bears', seat='B', ref='controller-condition-creature')
                first = self.add(engine, 'Lightning Bolt', seat='D', zone='hand', ref='first-scarecrow-bolt')
                second = self.add(engine, 'Lightning Bolt', seat='D', zone='hand', ref='second-scarecrow-bolt')
                private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand')
                self.assertTrue(self.binding(session, source)['strict_capability_ready'])
                self.prepare(session, actor='B', active='B')
                session.state.players['D'].mana_pool.update(R=2)
                self.checkpoint(session)
                self.cast(session, first, (source.ref,), seat='D')
                if not black:
                    self.assertIsNone(self.settle(session))
                    self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                    self.private_replay(session, private)
                    continue
                trigger = self.return_trigger(session, source)
                self.assertEqual('B', trigger.controller)
                self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                session = self.resume(session)
                self.assertIsNone(self.settle(session))
                returned = session.state.cards[source.object_id]
                self.assertEqual(('battlefield', 'A', 'A'), (returned.zone, returned.owner, returned.controller))
                self.assertEqual({'-1/-1': 1}, returned.counters)
                self.assertEqual((4, 2), (session.engine._numeric_stat(returned.object_id, 'power'),
                    session.engine._numeric_stat(returned.object_id, 'toughness')))
                self.cast(session, second, (returned.ref,), seat='D')
                self.assertIsNone(self.settle(session))
                self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
                self.private_replay(session, private)

    def test_actual_undying_evil_returns_a_noncreature_recipient_after_pending_reload(self):
        session = self.session(983_200)
        engine = session.engine
        spell = self.add(engine, 'Undying Evil', zone='hand')
        change = self.add(engine, 'One with the Stars', zone='hand')
        bearer = self.add(engine, 'Grizzly Bears', seat='B')
        removal = self.add(engine, 'Naturalize', seat='D', zone='hand')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand')
        for card in (spell, change, bearer, removal):
            self.assertTrue(self.binding(session, card)['strict_capability_ready'], card.printed_name)
        self.prepare(session, mana={'B': 1, 'U': 1, 'C': 3})
        session.state.players['D'].mana_pool.update(G=1, C=1)
        self.checkpoint(session)
        self.cast(session, spell, (bearer.ref,))
        self.assertIsNone(self.settle(session))
        self.assertEqual('graveyard', session.state.cards[spell.object_id].zone)
        self.cast(session, change, (bearer.ref,))
        self.assertIsNone(self.settle(session))
        self.assertNotIn('Creature', session.engine._effective_card_data(session.state.cards[bearer.object_id])['type_line'])
        self.cast(session, removal, (bearer.ref,), seat='D')
        trigger = self.return_trigger(session, bearer)
        self.assertEqual('B', trigger.controller)
        self.assertEqual('graveyard', session.state.cards[bearer.object_id].zone)
        session = self.resume(session)
        self.assertIsNone(self.settle(session))
        returned = session.state.cards[bearer.object_id]
        self.assertEqual(('battlefield', 'B'), (returned.zone, returned.controller))
        self.assertEqual({'+1/+1': 1}, returned.counters)
        self.assertEqual((3, 3), (session.engine._numeric_stat(returned.object_id, 'power'),
            session.engine._numeric_stat(returned.object_id, 'toughness')))
        self.assertEqual('graveyard', session.state.cards[change.object_id].zone)
        self.assertFalse(any(fragment.get('kind') == 'granted_triggered' for fragment in
            session.engine._effective_card_data(returned).get('ability_fragments', ())))
        self.private_replay(session, private)
