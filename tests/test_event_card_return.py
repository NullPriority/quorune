from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_card_return import event_card_return_effect, event_card_return_intent
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class EventCardReturnCompilerTests(unittest.TestCase):
    def test_event_return_missing_dependency_and_incarnation_mutants_fail_closed(self):
        import json
        from test_fixed_optional_mana_payment_triggers import payment_record
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        record = payment_record("When this creature dies, return it to its owner's hand.")
        for id in ('zone.return.fixed_event_card', 'trigger.event.normalized_zone_change', 'zone.change.destination_replacement'):
            value = deepcopy(raw); cap = next(r for r in value['capabilities'] if r['id'] == id)
            cap.update(status='blocked', blockers=['Independent event return mutation'])
            with self.subTest(id=id): self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(value)).status)
    def test_attached_counter_and_event_return_siblings_bind_all_runtime_dependencies(self):
        from dataclasses import replace
        from test_fixed_optional_mana_payment_triggers import payment_record
        record = replace(payment_record(
            "Enchant creature\nEnchanted creature has infect.\n"
            "At the beginning of your upkeep, put a -1/-1 counter on enchanted creature.\n"
            "When this Aura is put into a graveyard from the battlefield, return it to its owner's hand.",
            type_line="Enchantment — Aura",
        ), keywords=("Enchant",))
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        counter = next(node for node in ir.faces[0].nodes if any(effect.get("op") == "place_counters" for effect in node.effects))
        self.assertIn("counter.producer.fixed_attached_effect", counter.capability_dependencies)
        self.assertIn("counter.producer.fixed_effect", counter.capability_dependencies)
        from quorune.card_programs.adapters import compile_card_program
        from quorune.card_programs import bind_card_program_runtime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dependency-binding.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/event-card-return.json"], path)
            with CardDatabase(path) as db:
                program = compile_card_program(db, record, capability_registry=registry, capability_profile="commander_review", trust_level="trusted")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def test_departed_return_effect_requires_exact_event_card_and_counter(self):
        intent = event_card_return_intent({**event_card_return_effect(), 'card': 'EVENT-CARD', 'expected_zone_change_counter': 2}, actor='A', reason='Witness')
        self.assertEqual('EVENT-CARD', intent.object_ref)
        self.assertEqual(2, intent.expected_zone_change_counter)
        self.assertEqual(('graveyard',), intent.expected_zones)
        self.assertTrue(intent.optional_if_missing); self.assertFalse(intent.owned_only)
        for mutant in ({'card': None}, {'expected_zone_change_counter': True}, {'expected_zone_change_counter': 0}, {'unknown': True}):
            with self.subTest(mutant=mutant), self.assertRaises(ValueError):
                event_card_return_intent({**event_card_return_effect(), 'card': 'EVENT-CARD', 'expected_zone_change_counter': 2, **mutant}, actor='A', reason='Witness')

    def test_event_return_source_and_attached_grammar_preserve_unrelated_siblings(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for text in ("When this creature dies, return it to its owner's hand.", "When this artifact is put into a graveyard from the battlefield, return it to its owner's hand.", "When enchanted creature dies, return that card to its owner's hand."):
            ir = compile_oracle_card(payment_record(text), capability_registry=registry, capability_profile='commander_review')
            node = ir.faces[0].nodes[0]
            self.assertEqual(event_card_return_effect(), dict(node.effects[0]))
            self.assertIn('zone.return.fixed_event_card', node.capability_dependencies)
        ir = compile_oracle_card(payment_record("When this creature dies, return it to its owner's hand.\nWhenever a player sneezes, draw a card."), capability_registry=registry)
        self.assertNotEqual('exact', ir.status)


class EventCardReturnActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'event-return.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json', ROOT / 'tests/fixtures/typed-grant-carrier-cards.json', ROOT / 'tests/fixtures/event-card-return.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Event return deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        session = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in 'ABCD'}, first_player='A', seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session); engine = session.engine
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        names = {'Mortus Strider', 'Demonic Vigor', "Squee's Embrace", "Altar's Reap", 'Naturalize', 'Murder', 'Generic Payment Commander', 'Generic Payment Plains'}
        records = tuple(record for record in self.db.iter_cards() if record.name in names and compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review').status == 'exact')
        register_generated_programs(self.db, engine.semantics, records, trust_level='trusted', capability_registry=self.registry, capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, *, ref, zone='battlefield', owner='A', controller=None):
        record = self.db.lookup(name); controller = controller or owner
        card = CardInstance(object_id='event-return:' + ref, ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=owner, controller=controller, zone=zone, zone_timestamp=engine._next_zone_timestamp(), known_to=[owner] if zone in {'hand', 'library'} else list(engine.seats), revealed_to=[] if zone in {'hand', 'library'} else list(engine.seats))
        engine.state.cards[card.object_id] = card; engine.state.players[controller if zone == 'battlefield' else owner].zones[zone].append(card.object_id)
        return card

    def ready(self, session, spell, mana):
        engine = session.engine; engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'precombat_main'; engine.state.step = 'main'
        engine.state.players['A'].mana_pool.update(mana)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine._grant_priority('A'); engine.pump()
        return next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + spell.ref)

    def checkpoint(self, session):
        session.initial_checkpoint = checkpoint_envelope(session.state); session.commands.clear(); session.decisions.clear()

    def resolve(self, session):
        for _ in range(24):
            if not session.state.stack: return
            if session.state.pending_decision and session.state.pending_decision.kind != 'priority': return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Event return did not finish')

    def replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'event-return-record'; session.save(path)
            self.assertEqual(expected, authoritative_state_hash(CommanderSession.load(self.db, path).state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result['ok'], {key: value for key, value in result.items() if key != 'events'})
        self.assertEqual(expected, result['final_state_hash'])

    def test_actual_sacrifice_returns_exact_card_to_owner_hand_with_pre_action_replay(self):
        session = self.session(4000701); engine = session.engine
        source = self.add(engine, 'Mortus Strider', ref='STRIDER', owner='B', controller='A')
        other = self.add(engine, 'Mortus Strider', ref='SAME-NAME')
        spell = self.add(engine, "Altar's Reap", ref='REAP', zone='hand')
        action = self.ready(session, spell, {'B': 1, 'C': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', 'sacrifice_cards': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertIn(source.object_id, session.state.players['B'].zones['hand'])
        self.assertEqual('battlefield', session.state.cards[other.object_id].zone)
        self.replay(session)

    def test_stale_graveyard_incarnation_is_not_returned_by_old_trigger(self):
        session = self.session(4000702); engine = session.engine
        source = self.add(engine, 'Mortus Strider', ref='STRIDER')
        spell = self.add(engine, "Altar's Reap", ref='REAP', zone='hand')
        action = self.ready(session, spell, {'B': 1, 'C': 1})
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', 'sacrifice_cards': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        previous_counter = source.zone_change_counter
        engine.move_card(source.object_id, 'exile', reason='Independent old-trigger counterexample')
        engine.move_card(source.object_id, 'graveyard', reason='Independent new graveyard incarnation')
        self.assertGreater(source.zone_change_counter, previous_counter)
        self.checkpoint(session); self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone); self.replay(session)

    def test_attachment_lki_returns_dead_creature_after_aura_leaves(self):
        session = self.session(4000703); engine = session.engine
        creature = self.add(engine, 'Generic Payment Commander', ref='CREATURE', owner='B', controller='A')
        aura = self.add(engine, 'Demonic Vigor', ref='VIGOR')
        aura.attached_to = creature.object_id; creature.attachments.append(aura.object_id)
        spell = self.add(engine, 'Murder', ref='MURDER', zone='hand')
        action = self.ready(session, spell, {'B': 2, 'C': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': [creature.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual('hand', session.state.cards[creature.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        self.assertIn(creature.object_id, session.state.players['B'].zones['hand'])
        self.replay(session)

    def test_return_incarnation_omission_mutant_is_killed(self):
        from dataclasses import replace
        from quorune import event_card_return as owner
        original = owner.event_card_return_intent
        def omit(effect, **kwargs):
            return replace(original(effect, **kwargs), expected_zone_change_counter=None)
        with mock.patch.object(owner, 'event_card_return_intent', omit):
            with self.assertRaises(AssertionError):
                self.test_stale_graveyard_incarnation_is_not_returned_by_old_trigger()

    def test_commander_command_choice_removes_card_before_old_return_trigger(self):
        session = self.session(4000704); engine = session.engine
        source = self.add(engine, 'Mortus Strider', ref='COMMANDER')
        source.is_commander = True
        source.commander_designation_id = 'event-return-commander-physical'
        spell = self.add(engine, "Altar's Reap", ref='REAP', zone='hand')
        action = self.ready(session, spell, {'B': 1, 'C': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', 'sacrifice_cards': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn('commander', session.state.pending_decision.kind)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending-commander-return'; session.save(path)
            session = CommanderSession.load(self.db, path)
        result = session.act('pilot:A', {'action_id': 'command'})
        self.assertTrue(result.ok, result.summary); self.resolve(session)
        self.assertEqual('command', session.state.cards[source.object_id].zone)
        self.replay(session)
