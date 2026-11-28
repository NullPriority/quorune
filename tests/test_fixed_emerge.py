from __future__ import annotations

"""Fixed Emerge reduces total generic cost after increases (CR 702.119)."""

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.emerge_model import EMERGE_CAPABILITY, FixedManaEmergeSpec
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.semantics import SemanticRegistry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_fixed_copy_type_exceptions as copies
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-emerge-cards.json'


class FixedEmergeModelTests(unittest.TestCase):
    def test_closed_emerge_cost_reduces_the_total_generic_amount_and_preserves_other_obligations(self):
        vector = mana_cost_to_vector('{5}{U}')[0]
        spec = FixedManaEmergeSpec('front:emerge:line:1', vector)
        vector['GENERIC'] = 99
        self.assertEqual(5, spec.requirements['GENERIC'])
        self.assertEqual(spec, FixedManaEmergeSpec.from_dict(spec.to_dict()))
        for total, mana_value, expected in (('{6}{U}', 0, '{6}{U}'), ('{6}{U}', 5, '{1}{U}'),
                ('{6}{U}', 15, '{U}'), ('{6}{U}{C}', 15, '{U}{C}'), ('{U}{U}', 15, '{U}{U}')):
            with self.subTest(total=total, mana_value=mana_value):
                original = mana_cost_to_vector(total)[0]
                before = deepcopy(original)
                self.assertEqual(mana_cost_to_vector(expected)[0], spec.reduce_total(original, mana_value))
                self.assertEqual(before, original)
        for value in (-1, True, '5', None):
            with self.assertRaises(ValueError):
                spec.reduce_total(mana_cost_to_vector('{6}{U}')[0], value)
        for change in ({'unknown': 1}, {'schema_version': True}, {'instance_id': ''},
                {'requirements': {'GENERIC': 5}}, {'requirements': {**dict(spec.requirements), 'U': True}}):
            with self.assertRaises(ValueError):
                FixedManaEmergeSpec.from_dict({**spec.to_dict(), **change})


class FixedEmergeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text='Emerge {5}{U}\nFlying', registry=None, **changes):
        record = CardRecord(oracle_id='fixture:emerge-boundary', name='Emerge Boundary', mana_cost='{7}',
            mana_value=7, type_line='Creature — Eldrazi', oracle_text=text, power='3', toughness='4',
            loyalty=None, defense=None, colors=(), color_identity=('U',), keywords=('Emerge', 'Flying'),
            produced_mana=(), layout='normal', released_at='2026-01-01', legalities={'commander': 'legal'}, faces=(), raw={})
        return compile_oracle_card(replace(record, **changes), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_emerge_cost_node_has_original_span_closed_handler_and_complete_card_boundary(self):
        ir = self.compile()
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual('Emerge {5}{U}', node.text)
        self.assertEqual((0, len(node.text)), (node.span.start, node.span.end))
        self.assertIn(EMERGE_CAPABILITY, node.capability_dependencies)
        from quorune.semantic_runtime.emerge import FixedManaEmergeHandler
        descriptor, = node.handlers
        spec = FixedManaEmergeHandler().validate(descriptor)
        self.assertEqual(mana_cost_to_vector('{5}{U}')[0], dict(spec.requirements))
        for change in ({'extra': True}, {'schema_version': True}, {REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: False}):
            with self.assertRaises(ValueError):
                FixedManaEmergeHandler().validate({**descriptor, **change})

    def test_emerge_unknown_costs_forms_siblings_and_withdrawn_owners_fail_closed(self):
        for text in ('Emerge {X}{U}', 'Emerge {W/U}', 'Emerge from artifacts {5}{U}',
                'Emerge {5}{U}\nEmerge {4}{U}', 'Emerge {5}{U}\nRewrite history.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(text).status)
        self.assertNotEqual('exact', self.compile(type_line='Land').status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (EMERGE_CAPABILITY, 'casting.additional_cost.fixed_sacrifice'):
            value = deepcopy(raw)
            next(r for r in value['capabilities'] if r['id'] == capability).update(status='blocked', blockers=['owner omitted'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(value)).status)


class FixedEmergeRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    choose_copy = copies.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'emerge.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Emerge witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_emerge_uses_sacrificed_mana_value_after_cost_increases_and_casts_normally(self):
        for name, generic in (('Autochthon Wurm', 0), ('Glass Golem', 1)):
            with self.subTest(sacrificed=name):
                session = self.session(70211901 + generic)
                engine = session.engine
                source = self.add(engine, 'Wretched Gryff', zone='hand', ref='emerge-original')
                sacrifice = self.add(engine, name, ref='emerge-sacrifice')
                foreign = self.add(engine, 'Memnite', seat='B', ref='emerge-foreign-creature')
                wrong_type = self.add(engine, 'Great Furnace', ref='emerge-noncreature')
                wrong_type.tapped = True
                self.add(engine, 'Sphere of Resistance', ref='emerge-cost-increase')
                private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='emerge-private-card')
                self.assert_closed(session, source)
                self.assert_closed(session, sacrifice)
                self.prepare(session, mana={'U': 1, 'C': generic})
                offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    if row['id'] == 'cast:' + source.ref)
                option = next(row for row in offered['cost_options'] if row['id'].startswith('emerge:'))
                self.checkpoint(session)
                self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
                self.reject(session, offered['id'], 'D', cost_option=option['id'], sacrifice_cards=[sacrifice.ref], pay='auto')
                for refs in ([], [foreign.ref], [wrong_type.ref], [sacrifice.ref, sacrifice.ref]):
                    self.reject(session, offered['id'], cost_option=option['id'], sacrifice_cards=refs, pay='auto')
                draws = len(session.state.players['A'].draw_history)
                hand = set(session.state.players['A'].zones['hand'])
                result = session.act('pilot:A', {'action_id': offered['id'], 'cost_option': option['id'],
                    'sacrifice_cards': [sacrifice.ref], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual('graveyard', session.state.cards[sacrifice.object_id].zone)
                self.assertEqual('battlefield', session.state.cards[foreign.object_id].zone)
                self.assertEqual(0, session.state.players['A'].mana_pool.get('U', 0))
                self.assertEqual(0, session.state.players['A'].mana_pool.get('C', 0))
                self.assertEqual(2, len(session.state.stack))
                session = self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                self.assertEqual(draws + 1, len(session.state.players['A'].draw_history))
                added = set(session.state.players['A'].zones['hand']) - hand
                self.assertEqual(1, len(added))
                drawn = session.state.cards[next(iter(added))]
                for seat in 'BCD':
                    self.assertNotIn(drawn.ref, json.dumps(session.packet('pilot:' + seat, full=True)))
                self.replay(session)

    def test_actual_ordinary_cast_pays_printed_cost_and_keeps_the_creature(self):
        session = self.session(70211903)
        engine = session.engine
        source = self.add(engine, 'Wretched Gryff', zone='hand', ref='ordinary-emerge-card')
        creature = self.add(engine, 'Glass Golem', ref='ordinary-unsacrificed-creature')
        self.add(engine, 'Sphere of Resistance', ref='ordinary-emerge-increase')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 8})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'cost_option': 'normal', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('battlefield', session.state.cards[creature.object_id].zone)
        self.assertEqual(0, session.state.players['A'].mana_pool.get('C', 0))
        self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[creature.object_id].zone)
        self.replay(session, load=True)

    def test_actual_chosen_creature_can_tap_for_mana_then_be_sacrificed_to_its_owner(self):
        session = self.session(70211904)
        engine = session.engine
        source = self.add(engine, 'It of the Horrid Swarm', zone='hand', ref='emerge-green-spell')
        creature = self.add(engine, 'Llanowar Elves', ref='emerge-green-mana-creature')
        # A lawful pre-state: B owns the creature, A has controlled it since
        # before this turn. The following cast pays both costs through owners.
        creature.owner = 'B'
        creature.acquired_control_turn_count = -1
        self.add(engine, 'Sphere of Resistance', ref='emerge-green-increase')
        self.assert_closed(session, source)
        self.assert_closed(session, creature)
        self.prepare(session, mana={'C': 6})
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        option = next(row for row in action['cost_options'] if row['id'].startswith('emerge:'))
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'],
            'sacrifice_cards': [creature.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[creature.object_id]
        self.assertEqual(('B', 'graveyard'), (current.owner, current.zone))
        self.assertIn(current.object_id, session.state.players['B'].zones['graveyard'])
        self.assertEqual(0, session.state.players['A'].mana_pool.get('C', 0))
        self.assertEqual(0, session.state.players['A'].mana_pool.get('G', 0))
        self.assertEqual(2, len(session.state.stack))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        tokens = [card for card in session.state.cards.values()
            if card.is_token and card.zone == 'battlefield' and card.controller == 'A']
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertEqual((1, 1), (session.engine._numeric_stat(token.object_id, 'power'),
                session.engine._numeric_stat(token.object_id, 'toughness')))
            self.assertIn('Insect', session.engine._effective_card_data(token)['type_line'])
        self.replay(session)

    def test_actual_copied_creature_uses_current_mana_value_and_binds_the_chosen_incarnation(self):
        session = self.session(70211905)
        engine = session.engine
        source = self.add(engine, 'Wretched Gryff', zone='hand', ref='copied-emerge-spell')
        template = self.add(engine, 'Glass Golem', ref='copied-emerge-template')
        clone = self.add(engine, 'Clone', zone='hand', ref='copied-emerge-creature')
        self.add(engine, 'Sphere of Resistance', ref='copied-emerge-increase')
        self.assert_closed(session, source)
        self.assert_closed(session, clone)
        self.prepare(session, mana={'C': 5, 'U': 2})
        self.checkpoint(session)
        self.cast(session, 'A', clone)
        session = self.choose_copy(session, template)
        current = session.state.cards[clone.object_id]
        self.assertEqual(5, session.engine._effective_card_data(current)['mana_value'])
        self.assertEqual('{5}', session.engine._effective_card_data(current)['mana_cost'])
        self.assertEqual(1, session.state.players['A'].mana_pool.get('C', 0))
        self.assertEqual(1, session.state.players['A'].mana_pool.get('U', 0))
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        option = next(row for row in action['cost_options']
            if row['id'].startswith('emerge:') and row['id'].endswith(f':{current.ref}:{current.zone_change_counter}'))
        self.reject(session, action['id'], cost_option=option['id'], sacrifice_cards=[template.ref], pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'],
            'cost_cards': [current.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[clone.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[template.object_id].zone)
        self.resolve(session)
        self.assertEqual(7, session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        self.replay(session, load=True)

    def test_omitted_emerge_reduction_is_detected_without_changing_source_admission(self):
        from quorune.rules.casting import costs
        result = unittest.TestResult()
        witness = type(self)('test_actual_emerge_uses_sacrificed_mana_value_after_cost_increases_and_casts_normally')
        with mock.patch.object(costs, 'reduce_emerge_total', return_value=None):
            witness.run(result)
        self.assertEqual(1, result.testsRun)
        self.assertTrue(result.failures or result.errors)
        self.assertNotIn('self.assert_closed', '\n'.join(trace for _, trace in result.failures + result.errors))

    def test_original_emerge_cards_need_complete_result_bodies_and_normal_registration(self):
        expected = {'Vexing Scuttler', 'Abundant Maw', 'Elder Deep-Fiend', 'Mockery of Nature',
            'Decimator of the Provinces', 'Wretched Gryff', 'Lashweed Lurker', 'Drownyard Behemoth',
            'It of the Horrid Swarm', 'Twisted Riddlekeeper'}
        for raw in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']:
            if 'Emerge' not in raw['keywords']:
                continue
            with self.subTest(card=raw['name']):
                record = self.db.lookup(raw['name'])
                semantics = SemanticRegistry(include_builtin_packs=False)
                program = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(record.name in expected, bound['strict_capability_ready'], program.residuals)
                register_generated_programs(self.db, semantics, (record,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                if record.name in expected:
                    programs = tuple(p for p in semantics.programs_for_oracle(record.oracle_id)
                        if 'generated_oracle_ir' in p.coverage)
                    self.assertTrue(programs)
                    self.assertTrue(all(p.trust_level == 'trusted' for p in programs))

    def test_original_riddlekeeper_emerge_cast_trigger_taps_and_stuns_selected_permanents(self):
        # Printed Oracle: emerge {5}{C}{U}, then a cast trigger with up to two
        # targets. CR 601.2/603.3: the cast trigger resolves before the creature.
        session=self.session(70211980)
        source=self.add(session.engine,'Twisted Riddlekeeper',zone='hand',ref='emerge-riddlekeeper')
        sacrifice=self.add(session.engine,'Glass Golem',ref='riddlekeeper-price')
        first=self.add(session.engine,'Generic Bound Body',seat='B',ref='riddlekeeper-first')
        second=self.add(session.engine,'Great Furnace',seat='C',ref='riddlekeeper-second')
        untouched=self.add(session.engine,'Memnite',seat='D',ref='riddlekeeper-unselected')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='riddlekeeper-private')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':1,'U':1})
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='cast:'+source.ref)
        option=next(row for row in offered['cost_options'] if row['id'].startswith('emerge:'))
        self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_option':option['id'],
            'sacrifice_cards':[sacrifice.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[sacrifice.object_id].zone)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        self.assertEqual(['pilot:A'],session.pending_principals())
        self.reject(session,'choose','D',targets=[first.ref,second.ref])
        self.reject(session,'choose',targets=[first.ref,first.ref])
        for card in (first,second,untouched):
            self.assertFalse(session.state.cards[card.object_id].tapped)
            self.assertEqual(0,session.state.cards[card.object_id].counters.get('stun',0))
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'choose','targets':[first.ref,second.ref]})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        for card in (first,second):
            self.assertTrue(session.state.cards[card.object_id].tapped)
            self.assertEqual(1,session.state.cards[card.object_id].counters.get('stun',0))
        self.assertFalse(session.state.cards[untouched.object_id].tapped)
        self.assertFalse(session.state.cards[untouched.object_id].counters)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[source.object_id]))
        for viewer in 'ABD':
            self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)


from inherited_composition_fixture import InheritedNativeFixture

PATRON = "Patron of the Kitsune"


class OfferingNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        directory = ROOT / "tests/fixtures"
        database = Path(cls.temporary.name) / "offering.sqlite3"
        build_fixture_database([
            directory / name for name in (
                "bound-effect-program-cards.json", "fixed-emerge-cards.json",
                "temporary-target-interaction-cards.json", "life-gain-counted-result-cards.json",
                "additional-conditional-modifier-cards.json",
            )
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Offering", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_patron_exact_source_and_offering_mana_reduction_boundaries(self):
        from quorune.offering_model import (
            OFFERING_CAPABILITY, OFFERING_HANDLER, offering_reduction_vectors, reduce_offering_total,
        )
        from quorune.semantic_runtime.offering import OfferingCostHandler

        profile = "commander_review"
        card = self.db.lookup(PATRON, fuzzy=False)
        opts = {"capability_registry": self.registry, "capability_profile": profile}
        ir = compile_oracle_card(card, **opts)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(2, len(ir.faces[0].nodes))
        node, = [node for node in ir.faces[0].nodes if node.event == "cast.cost"]
        self.assertIn(OFFERING_CAPABILITY, node.capability_dependencies)
        handler, = node.handlers
        self.assertEqual(OFFERING_HANDLER, handler["handler_id"])
        self.assertEqual("fox", OfferingCostHandler().validate(handler).subtype)
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **opts)
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile=profile)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        for text in ("NotASubtype offering", "Fox offering\nRabbit offering", "Fox offering once each turn"):
            narrowed = compile_oracle_card(replace(card, oracle_text=text), **opts)
            self.assertTrue(narrowed.material_residuals, text)
        with mock.patch("quorune.compiler.offering_nodes.offering_static_node", return_value=None):
            omitted = compile_oracle_card(card, **opts)
        self.assertTrue(omitted.material_residuals)

        total = mana_cost_to_vector("{4}{W}{W}")[0]
        for printed, expected in [
            ("", ("{4}{W}{W}",)), ("{W}{W}{W}", ("{3}",)),
            ("{R}{W}", ("{3}{W}",)), ("{C}{C}", ("{2}{W}{W}",)),
            ("{2/W}", ("{2}{W}{W}", "{4}{W}")),
            ("{W/P}{S}", ("{3}{W}",)), ("{1}{X}", ("{3}{W}{W}",)),
        ]:
            with self.subTest(printed=printed):
                remaining = [reduce_offering_total(total, credit) for credit in offering_reduction_vectors(printed)]
                self.assertCountEqual([mana_cost_to_vector(cost)[0] for cost in expected], remaining)
        for malformed in ("{Q}", "{1} trailing cost", "W", "{2/P}"):
            with self.assertRaises(ValueError):
                offering_reduction_vectors(malformed)

    def cast_options(self, game, actor, source):
        self.priority(game, actor)
        actions = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row["cost_options"] for row in actions if row["id"] == "cast:" + source.ref)

    def reject_offer(self, game, actor, command):
        from quorune.record import authoritative_state_hash

        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_patron_off_turn_offering_locks_color_reduction_and_precedes_counter_response(self):
        game = self.session(70248001)
        source = self.add(game.engine, PATRON, zone="hand")
        twin = self.add(game.engine, "Eight-and-a-Half-Tails", ref="two-white-fox")
        mixed = self.add(game.engine, "Golden-Tail Disciple", ref="generic-and-white-fox")
        foreign = self.add(game.engine, "Golden-Tail Disciple", seat="B", ref="foreign-fox")
        other = self.add(game.engine, "Memnite", ref="not-a-fox")
        draw_spell = self.add(game.engine, "Divination", seat="B", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        tax = self.add(game.engine, "Sphere of Resistance")
        for card in (source, draw_spell, counter, tax):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"U": 8, "C": 20})
        game.state.players["A"].mana_pool.update({"W": 4, "C": 20})
        self.checkpoint(game)
        self.cast_for(game, "B", draw_spell)
        options = self.cast_options(game, "A", source)
        self.assertEqual(2, len(options), options)
        paid_twin, = [row for row in options if ":" + twin.ref + ":" in row["id"]]
        paid_mixed, = [row for row in options if ":" + mixed.ref + ":" in row["id"]]
        self.assertEqual(mana_cost_to_vector("{5}")[0], paid_twin["requirements"])
        self.assertEqual(mana_cost_to_vector("{3}{W}")[0], paid_mixed["requirements"])
        action = "cast:" + source.ref
        command = {"action_id": action, "cost_option": paid_twin["id"],
                   "sacrifice_cards": [twin.ref], "reserve": {"W": 4}, "pay": "auto"}
        self.reject_offer(game, "D", command)
        self.reject_offer(game, "A", {"action_id": action, "cost_option": "normal", "pay": "auto"})
        for wrong in ([], [mixed.ref], [foreign.ref], [other.ref], [twin.ref, mixed.ref]):
            self.reject_offer(game, "A", {**command, "sacrifice_cards": wrong})
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[twin.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[mixed.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        self.assertEqual(4, game.state.players["A"].mana_pool["W"])
        self.assertEqual(15, game.state.players["A"].mana_pool["C"])
        self.assertEqual("stack", game.state.cards[source.object_id].zone)
        stacked, = [item for item in game.state.stack if item.card_object_id == source.object_id]
        game = self.replay(game, load=True)
        self.cast_for(game, "B", counter, [stacked.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[twin.object_id].zone)
        self.assertEqual(15, game.state.players["A"].mana_pool["C"])
        self.resolve(game)
        self.replay(game, load=True)

    def test_patron_normal_main_phase_cast_keeps_the_fox(self):
        game = self.session(70248002)
        source = self.add(game.engine, PATRON, zone="hand")
        fox = self.add(game.engine, "Golden-Tail Disciple")
        self.assert_closed(game, source)
        self.prepare(game, mana={"W": 2, "C": 4})
        self.checkpoint(game)
        price, = [row for row in self.cast_options(game, "A", source) if row["id"] == "normal"]
        self.assertEqual(mana_cost_to_vector("{4}{W}{W}")[0], price["requirements"])
        response = game.act("pilot:A", {"action_id": "cast:" + source.ref, "cost_option": "normal", "pay": "auto"})
        self.assertTrue(response.ok, response.summary)
        self.resolve(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[fox.object_id].zone)
        self.replay(game, load=True)
