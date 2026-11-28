from __future__ import annotations

"""CR118.3/602.2b: a selected controlled creature pays one +1/+1 counter."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import CostChoice
from quorune.carddb import CardDatabase
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.selected_counter_costs import SELECTED_PLUS_COUNTER_COST_KIND, SELECTED_COUNTER_COST_CAPABILITY
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls

NAMES = ('Ray Fillet, Man Ray', 'Shapers of Nature', 'Spike Rogue', 'Bolrac-Clan Crusher',
         'Sunstar Chaplain', 'Ghave, Guru of Spores', 'Korozda Gorgon')


class SelectedCounterCostValueTests(unittest.TestCase):
    def test_fixed_choice_roundtrip_and_malformed_payment_shapes_reject(self):
        choice = CostChoice(SELECTED_PLUS_COUNTER_COST_KIND, card_type='creature')
        self.assertEqual(choice, CostChoice.from_dict(choice.to_dict()))
        self.assertEqual({'k': SELECTED_PLUS_COUNTER_COST_KIND, 'n': 1, 'z': 'battlefield', 't': 'creature'}, choice.compact())
        for changed in ({'count': 2}, {'count': True}, {'zone': 'hand'}, {'card_type': 'artifact'},
                        {'another': True}, {'predicate': {}}, {'unknown': True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                CostChoice.from_dict({**choice.to_dict(), **changed})


class SelectedCounterCostRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.CompoundDamageRuntimeTests.session
    add = controls.CompoundDamageRuntimeTests.add
    prepare = controls.CompoundDamageRuntimeTests.prepare
    checkpoint = controls.CompoundDamageRuntimeTests.checkpoint
    cast = controls.CompoundDamageRuntimeTests.cast
    resolve = controls.CompoundDamageRuntimeTests.resolve
    replay = controls.CompoundDamageRuntimeTests.replay
    binding = controls.CompoundDamageRuntimeTests.binding
    actions = controls.CompoundDamageRuntimeTests.actions
    private_replay = controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'selected-counters.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/controlled-counter-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Selected counter cost witnesses',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def selected_action(self, session, source):
        source = session.state.cards[source.object_id]
        return next(row for row in self.actions(session) if row['id'].startswith('activate:' + source.ref + ':')
            and any(choice.get('k') == SELECTED_PLUS_COUNTER_COST_KIND for choice in row['cost_summary'].get('choose_cost', ())))

    def test_seven_originals_have_whole_admission_and_authoritative_payment_forms(self):
        from quorune.choice_forms import build_action_form
        for index, name in enumerate(NAMES):
            session = self.session(734000 + index)
            source = self.add(session.engine, name)
            source.counters['+1/+1'] = 2
            self.prepare(session, mana={'C': 10, 'U': 3, 'G': 3})
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            self.assertTrue(self.binding(session, source)['strict_capability_ready'], name)
            status = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', status['status'], (name, status['unresolved']))
            abilities = compile_activated_ability_catalog(card)['front']
            self.assertTrue(any(any(choice.kind == SELECTED_PLUS_COUNTER_COST_KIND for choice in ability.choices) for ability in abilities))
            action = self.selected_action(session, source)
            self.assertEqual([source.ref], action['choice_schema']['cost_cards']['legal_refs'])
            form = build_action_form(action, decision_kind='priority',
                context=session.packet('pilot:A', full=True)['decision']['ctx'])
            self.assertIsNotNone(form)
            self.assertTrue(any(field['name'] == 'cost_cards' for field in form['fields']))

    def test_actual_shapers_uses_tapped_controlled_payer_and_rolls_back_bad_or_unfunded_selections(self):
        session = self.session(734020)
        source = self.add(session.engine, 'Shapers of Nature')
        payer = self.add(session.engine, 'Generic Bound Body', ref='selected-counter-payer')
        payer.counters['+1/+1'] = 1
        payer.tapped = True
        foreign = self.add(session.engine, 'Generic Bound Body', seat='B', ref='selected-counter-foreign')
        foreign.counters['+1/+1'] = 3
        empty = self.add(session.engine, 'Generic Bound Body', ref='selected-counter-empty')
        wrong_kind = self.add(session.engine, 'Hardened Scales')
        wrong_kind.counters['+1/+1'] = 1
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.prepare(session, mana={'C': 2, 'U': 1})
        self.checkpoint(session)
        action = self.selected_action(session, source)
        self.assertEqual([payer.ref], action['choice_schema']['cost_cards']['legal_refs'])
        before = authoritative_state_hash(session.state)
        good = {'action_id': action['id'], 'cost_cards': [payer.ref], 'pay': 'auto'}
        for actor, command in (
            ('pilot:B', good), ('pilot:A', {**good, 'cost_cards': []}),
            ('pilot:A', {**good, 'cost_cards': [payer.ref, payer.ref]}),
            ('pilot:A', {**good, 'cost_cards': [foreign.ref]}),
            ('pilot:A', {**good, 'cost_cards': [empty.ref]}),
            ('pilot:A', {**good, 'cost_cards': [wrong_kind.ref]}),
            ('pilot:A', {**good, 'pay': 'manual', 'payment': {'C': 2}}),
        ):
            result = session.act(actor, command)
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        hand = len(session.state.players['A'].zones['hand'])
        result = session.act('pilot:A', good)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertEqual(('battlefield', True), (session.state.cards[payer.object_id].zone, session.state.cards[payer.object_id].tapped))
        self.assertEqual(3, session.state.cards[foreign.object_id].counters['+1/+1'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_fresh_shapers_witness_kills_omitted_counter_payment(self):
        method = 'test_actual_shapers_uses_tapped_controlled_payer_and_rolls_back_bad_or_unfunded_selections'
        with patch('quorune.counter_removal.commit_counter_removals', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def pay_activation(self, session, source, payer, targets=()):
        action = self.selected_action(session, source)
        result = session.act('pilot:A', {'action_id': action['id'],
            'cost_cards': [session.state.cards[payer.object_id].ref], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)

    def test_actual_bolrac_source_can_pay_both_its_tap_and_distinct_counter_resource(self):
        session = self.session(734030)
        source = self.add(session.engine, 'Bolrac-Clan Crusher')
        source.counters['+1/+1'] = 1
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session)
        self.checkpoint(session)
        action = self.selected_action(session, source)
        self.assertIn(source.ref, action['choice_schema']['cost_cards']['legal_refs'])
        self.pay_activation(session, source, source, ['D'])
        current = session.state.cards[source.object_id]
        self.assertTrue(current.tapped)
        self.assertEqual(0, current.counters.get('+1/+1', 0))
        self.assertEqual(40, session.state.players['D'].life)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([40, 40, 40, 38], [session.state.players[seat].life for seat in 'ABCD'])
        self.private_replay(session, private)

    def test_actual_ghave_uses_controlled_foreign_owned_payer_then_its_existing_token_sacrifice(self):
        session = self.session(734040)
        source = self.add(session.engine, 'Ghave, Guru of Spores', zone='hand')
        payer = self.add(session.engine, 'Generic Bound Body', ref='ghave-borrowed-payer')
        payer.owner = 'B'
        payer.counters['+1/+1'] = 1
        payer.tapped = True
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 4, 'W': 1, 'B': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.pay_activation(session, source, payer)
        self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertEqual(('battlefield', 'B', 'A', True),
            tuple(getattr(session.state.cards[payer.object_id], key) for key in ('zone', 'owner', 'controller', 'tapped')))
        self.assertIsNone(self.resolve(session))
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(token.object_id, field) for field in ('power', 'toughness')))
        self.assertIn('saproling', session.engine._type_parts(session.engine._effective_card_data(token)['type_line'])[1])
        action = next(row for row in self.actions(session) if row['id'].startswith('activate:' + session.state.cards[source.object_id].ref + ':')
            and any(choice.get('k') in {'sacrifice', 'sacrifice_one'} for choice in row['cost_summary'].get('choose_cost', ())))
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [token.ref],
            'targets': [session.state.cards[payer.object_id].ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertFalse(any(card.is_token and card.zone == 'battlefield' for card in session.state.cards.values()))
        self.assertEqual(5, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.private_replay(session, private)

    def test_actual_spike_rogue_removal_is_not_multiplied_but_its_result_uses_hardened_scales(self):
        session = self.session(734050)
        source = self.add(session.engine, 'Spike Rogue')
        source.counters['+1/+1'] = 2
        payer = self.add(session.engine, 'Generic Bound Body', ref='rogue-counter-payer')
        payer.counters['+1/+1'] = 1
        scales = self.add(session.engine, 'Hardened Scales')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(session, scales)['strict_capability_ready'])
        self.prepare(session, mana={'C': 2})
        self.checkpoint(session)
        self.pay_activation(session, source, payer)
        self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertEqual(2, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.private_replay(session, private)

    def test_actual_sunstar_and_korozda_keep_their_ordinary_targeted_results(self):
        for index, name in enumerate(('Sunstar Chaplain', 'Korozda Gorgon')):
            session = self.session(734060 + index)
            source = self.add(session.engine, name)
            payer = self.add(session.engine, 'Generic Bound Body', ref='ordinary-counter-payer')
            payer.counters['+1/+1'] = 1
            target = self.add(session.engine, 'Generic Bound Body', seat='D', ref='ordinary-counter-target')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.prepare(session, mana={'C': 2})
            self.checkpoint(session)
            self.pay_activation(session, source, payer, [target.ref])
            self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
            self.assertIsNone(self.resolve(session))
            if name == 'Sunstar Chaplain':
                self.assertTrue(session.state.cards[target.object_id].tapped)
            else:
                self.assertEqual((1, 5), tuple(session.engine._numeric_stat(target.object_id, field) for field in ('power', 'toughness')))
            self.private_replay(session, private)

    def test_actual_ray_mutagen_produces_the_counter_then_pays_it_for_a_draw(self):
        session = self.session(734070)
        source = self.add(session.engine, 'Ray Fillet, Man Ray', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 10, 'W': 2, 'U': 2, 'B': 2, 'R': 2, 'G': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        action = next(row for row in self.actions(session) if row['id'].startswith('activate:' + token.ref + ':'))
        result = session.act('pilot:A', {'action_id': action['id'],
            'targets': [session.state.cards[source.object_id].ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        hand = len(session.state.players['A'].zones['hand'])
        self.pay_activation(session, source, source)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_paid_payer_last_counter_death_makes_its_target_illegal_without_refund(self):
        session = self.session(734080)
        source = self.add(session.engine, 'Korozda Gorgon')
        payer = self.add(session.engine, 'Spike Rogue', ref='zero-payer-target')
        payer.counters['+1/+1'] = 1
        unselected = self.add(session.engine, 'Generic Bound Body', seat='D', ref='unchanged-counter-body')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 2})
        self.checkpoint(session)
        self.pay_activation(session, source, payer, [payer.ref])
        self.assertEqual('graveyard', session.state.cards[payer.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertTrue(session.state.stack)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[payer.object_id].zone)
        self.assertEqual((2, 6), tuple(session.engine._numeric_stat(unselected.object_id, field) for field in ('power', 'toughness')))
        self.private_replay(session, private)

    def test_runtime_cannot_omit_selected_payment_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(734090)
        source = self.add(session.engine, 'Shapers of Nature')
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if program.cost_schema and any(choice.get('k') == SELECTED_PLUS_COUNTER_COST_KIND
                for choice in program.cost_schema.get('choices', ())))
        deps = tuple(cap for cap in program.capability_dependencies if cap != SELECTED_COUNTER_COST_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + SELECTED_COUNTER_COST_CAPABILITY, bound['blockers'])

    def test_unrepresented_amounts_subjects_and_counter_kinds_remain_residual(self):
        card = self.db.lookup('Shapers of Nature')
        for text in (
            card.oracle_text.replace('Remove a +1/+1 counter', 'Remove two +1/+1 counters'),
            card.oracle_text.replace('from a creature you control', 'from a permanent you control'),
            card.oracle_text.replace('from a creature you control', 'from a creature an opponent controls'),
            card.oracle_text.replace('Remove a +1/+1 counter', 'Remove a charge counter'),
            card.oracle_text + '\nWhenever a player sneezes, draw a card.',
        ):
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=text),
                capability_registry=self.registry, capability_profile='commander_review').status, text)

    def test_fresh_original_admission_kills_omitted_selected_cost_compiler(self):
        method = 'test_seven_originals_have_whole_admission_and_authoritative_payment_forms'
        with patch('quorune.compiler.activated_counter_costs.fixed_selected_counter_activation', side_effect=lambda ability: ability):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

from inherited_composition_fixture import InheritedNativeFixture


class QuillspikeNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "quillspike.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "all-counter-self-cost-cards.json",
            fixtures / "all-kind-counter-cards.json",
            fixtures / "ability-source-counter-cards.json",
            fixtures / "controlled-counter-cost-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Quillspike witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def quillspike_offer(self, session, source):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        current = session.state.cards[source.object_id]
        return next((action for action in actions if action["id"].startswith(f"activate:{current.ref}:")), None)

    def payment(self, action, payer, color):
        option, = (option for option in action["cost_options"] if option["requirements"].get(color) == 1)
        return {"action_id": action["id"], "cost_cards": [payer.ref], "cost_option": option["id"],
                "pay": "manual", "payment": {color: 1}}

    def assert_stats(self, session, card, expected):
        self.assertEqual((expected, expected), tuple(
            session.engine._numeric_stat(card.object_id, field) for field in ("power", "toughness")
        ))

    def test_quillspike_price_compiler_and_runtime_reject_incomplete_shapes(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime

        card = self.db.lookup("Quillspike")
        for text in (
            card.oracle_text.replace("{B/G}", "{B/R}"),
            card.oracle_text.replace("{B/G}", "{1}{B/G}"),
            card.oracle_text.replace("Remove a -1/-1 counter", "Remove two -1/-1 counters"),
            card.oracle_text.replace("a creature you control", "another creature you control"),
        ):
            with self.subTest(text=text):
                changed = replace(card, oracle_text=text)
                ir = compile_oracle_card(changed, capability_registry=self.registry,
                                         capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)
        session = self.session(741198)
        source = self.add(session.engine, "Quillspike")
        program, = session.engine.semantics.programs_for_oracle(source.oracle_id)
        cost = dict(program.cost_schema)
        one_option = tuple(cost["mana_cost_options"][:1])
        invalid = replace(program, cost_schema={**cost, "mana_cost_options": one_option})
        result = bind_semantic_program_runtime(invalid, capability_registry=self.registry,
                                               profile="commander_review")
        self.assertFalse(result["strict"], result)

    def test_actual_quillspike_pays_both_hybrid_arms_and_adds_repeated_bonuses_until_cleanup(self):
        session = self.session(741200)
        source = self.add(session.engine, "Quillspike")
        self.add(session.engine, "Glorious Anthem")
        source.counters["-1/-1"] = 1
        payer = self.add(session.engine, "Generic Bound Body", ref="borrowed-counter-payer")
        payer.owner, payer.tapped = "B", True
        payer.counters["-1/-1"] = 1
        rival = self.add(session.engine, "Generic Bound Body", seat="B", ref="rival-counter-payer")
        rival.counters["-1/-1"] = 1
        wrong = self.add(session.engine, "Generic Bound Body", ref="wrong-counter-payer")
        wrong.counters["+1/+1"] = 1
        land = self.add(session.engine, "Generic Bound Plains", ref="noncreature-counter-payer")
        land.counters["-1/-1"] = 1
        self.assert_closed(session, source)
        self.prepare(session, mana={"B": 1, "G": 1})
        self.checkpoint(session)
        self.assert_stats(session, source, 1)
        action = self.quillspike_offer(session, source)
        self.assertCountEqual([source.ref, payer.ref], action["choice_schema"]["cost_cards"]["legal_refs"])
        self.assertFalse(action.get("target_schema"))
        command = self.payment(action, source, "B")
        before = authoritative_state_hash(session.state)
        for changes in (
            {"cost_cards": [rival.ref]}, {"cost_cards": [wrong.ref]}, {"cost_cards": [land.ref]},
            {"cost_cards": [source.ref, payer.ref]}, {"payment": {"W": 1}},
        ):
            rejected = session.act("pilot:A", {**command, **changes})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.stack[-1].targets)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get("-1/-1", 0))
        self.assert_stats(session, source, 2)
        self.assertEqual(0, session.state.players["A"].mana_pool["B"])
        self.assertEqual(1, session.state.players["A"].mana_pool["G"])

        action = self.quillspike_offer(session, source)
        self.assertEqual([payer.ref], action["choice_schema"]["cost_cards"]["legal_refs"])
        command = self.payment(action, payer, "G")
        before = authoritative_state_hash(session.state)
        stale = session.act("pilot:A", {**command, "cost_cards": [source.ref]})
        self.assertFalse(stale.ok, stale.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, len(session.state.stack))
        self.assertTrue(all(not item.targets for item in session.state.stack))
        donor = session.state.cards[payer.object_id]
        self.assertEqual(("B", "A", "battlefield", True, 0), (
            donor.owner, donor.controller, donor.zone, donor.tapped, donor.counters.get("-1/-1", 0),
        ))
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assert_stats(session, source, 5)
        self.resolve_top(session)
        self.assert_stats(session, source, 8)
        turn = session.state.turn_sequence
        self.until(session, lambda game: game.state.turn_sequence > turn)
        self.assert_stats(session, source, 2)
        self.replay(session, load=True)

    def test_actual_quillspike_preserves_the_selected_creature_from_mana_sacrifice(self):
        for pool in (False, True):
            with self.subTest(pool=pool):
                session = self.session(741205 + pool)
                source = self.add(session.engine, "Quillspike")
                self.add(session.engine, "Glorious Anthem")
                cantor = self.add(session.engine, "Wild Cantor")
                cantor.counters["-1/-1"] = 1
                other = self.add(session.engine, "Generic Bound Body", ref="other-minus-payer")
                other.counters["-1/-1"] = 1
                self.assert_closed(session, source)
                self.assert_closed(session, cantor)
                self.prepare(session, mana={"B": int(pool)})
                self.checkpoint(session)
                action = self.quillspike_offer(session, source)
                refs = action["choice_schema"]["cost_cards"]["legal_refs"]
                self.assertCountEqual([cantor.ref, other.ref] if pool else [other.ref], refs)
                command = self.payment(action, cantor, "G")
                before = authoritative_state_hash(session.state)
                invalid = dict(command, mana=[{"source": cantor.ref, "bundle": {"G": 1}}])
                result = session.act("pilot:A", invalid)
                self.assertFalse(result.ok, result.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                if not pool:
                    auto = {key: value for key, value in command.items() if key != "payment"}
                    result = session.act("pilot:A", {**auto, "pay": "auto"})
                    self.assertFalse(result.ok, result.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                command.pop("payment")
                command.update(pay="auto", cost_cards=[cantor.ref if pool else other.ref])
                if pool:
                    command["cost_option"] = self.payment(action, cantor, "B")["cost_option"]
                result = session.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("battlefield" if pool else "graveyard", session.state.cards[cantor.object_id].zone)
                self.assertEqual(0, session.state.cards[cantor.object_id if pool else other.object_id].counters.get("-1/-1", 0))
                self.assertIsNone(self.resolve(session))
                self.assert_stats(session, source, 5)
                self.replay(session, load=True)

    def test_actual_quillspike_paid_result_never_modifies_its_returned_incarnation(self):
        session = self.session(741210)
        source = self.add(session.engine, "Quillspike")
        self.add(session.engine, "Vedalken Orrery")
        donor = self.add(session.engine, "Generic Bound Body")
        donor.counters["-1/-1"] = 1
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        self.add(session.engine, "Island", seat="B")
        self.assert_closed(session, source)
        old = source.logical_object_id
        self.prepare(session, mana={"B": 1, "G": 1, "C": 2})
        self.checkpoint(session)
        action = self.quillspike_offer(session, source)
        command = self.payment(action, donor, "B")
        activated = session.act("pilot:A", command)
        self.assertTrue(activated.ok, activated.summary)
        self.cast(session, "B", bounce, [source.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(session.state.stack))
        self.cast(session, "A", source)
        self.resolve_top(session)
        returned = session.state.cards[source.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(old, returned.logical_object_id)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assert_stats(session, source, 1)
        self.assertEqual(0, session.state.cards[donor.object_id].counters.get("-1/-1", 0))
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.replay(session, load=True)


class DistributedCounterCostRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "distributed-counters.sqlite3"
        base = [ROOT / "tests/fixtures/bound-effect-program-cards.json",
                ROOT / "tests/fixtures/controlled-counter-cost-cards.json"]
        names = {card["name"] for fixture in base for card in json.loads(fixture.read_text(encoding="utf-8"))["cards"]}
        if "Sol Ring" not in names:
            other = json.loads((ROOT / "tests/fixtures/subtype-composite-grant-cards.json").read_text(encoding="utf-8"))
            ring, = (card for card in other["cards"] if card["name"] == "Sol Ring")
            supplement = path.with_name("distributed-counter-support.json")
            supplement.write_text(json.dumps({"schema_version": 1, "cards": [ring], "rulings": [
                row for row in other["rulings"] if row["oracle_id"] == ring["oracle_id"]
            ]}), encoding="utf-8")
            base.append(supplement)
        build_fixture_database(base, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Distributed counter cost", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _hopeful_actions(self, session, source):
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]

    def _rejected(self, session, actor, command):
        before = authoritative_state_hash(session.state)
        failed = session.act(f"pilot:{actor}", command)
        self.assertFalse(failed.ok, failed.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_hopeful_initiate_can_pay_two_from_one_or_one_each_and_replay(self):
        from quorune.choice_forms import build_action_form

        for split in (False, True):
            with self.subTest(split=split):
                session = self.session(734205 + int(split))
                engine = session.engine
                source = self.add(engine, "Hopeful Initiate", ref="HOPEFUL-SOURCE")
                source.counters["+1/+1"] = 1
                double = self.add(engine, "Generic Bound Body", ref="HOPEFUL-DOUBLE")
                double.counters["+1/+1"] = 2
                double.owner = "B"
                single = self.add(engine, "Generic Bound Body", ref="HOPEFUL-SINGLE")
                single.counters["+1/+1"] = 1
                opposing = self.add(engine, "Generic Bound Body", seat="B", ref="HOPEFUL-OPPONENT")
                opposing.counters["+1/+1"] = 2
                noncreature = self.add(engine, "Hardened Scales", ref="HOPEFUL-NONCREATURE")
                noncreature.counters["+1/+1"] = 2
                target = self.add(engine, "Sol Ring" if split else "Hardened Scales", seat="B", ref="HOPEFUL-TARGET")
                private = self.add(engine, "Generic Bound Growth", seat="C", zone="hand", ref="HOPEFUL-PRIVATE")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 2, "W": 1})
                self.checkpoint(session)

                action, = self._hopeful_actions(session, source)
                field = action["choice_schema"]["cost_cards"]
                self.assertEqual([1, 2], field["allowed_counts"])
                self.assertEqual([double.ref], field["single_creature_refs"])
                self.assertEqual({source.ref, double.ref, single.ref}, set(field["legal_refs"]))
                self.assertIn(target.ref, action["target_schema"]["legal_refs"])
                self.assertNotIn(opposing.ref, action["target_schema"]["legal_refs"])
                context = session.packet("pilot:A", full=True)["decision"]["ctx"]
                form = build_action_form(action, decision_kind="priority", context=context)
                self.assertTrue(any(field["name"] == "cost_cards" for field in form["fields"]))
                payment = [source.ref, single.ref] if split else [double.ref]
                command = {"action_id": action["id"], "targets": [target.ref], "pay": "auto"}
                for actor, refs in (
                    ("B", payment), ("A", [single.ref]), ("A", [opposing.ref]), ("A", [noncreature.ref]),
                    ("A", [double.ref, double.ref]), ("A", [source.ref, single.ref, double.ref]),
                ):
                    self._rejected(session, actor, {**command, "cost_cards": refs})
                self._rejected(session, "A", {**command, "cost_cards": payment, "targets": [opposing.ref]})
                self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
                accepted = session.act("pilot:A", {**command, "cost_cards": payment})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
                self.assertEqual((0, 2, 0) if split else (1, 0, 1),
                    tuple(session.state.cards[creature.object_id].counters.get("+1/+1", 0)
                          for creature in (source, double, single)))
                self.assertEqual(2, session.state.cards[opposing.object_id].counters["+1/+1"])
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
                self.replay(session, load=True)

    def test_hopeful_initiate_training_makes_split_payment_available(self):
        session = self.session(734207)
        engine = session.engine
        source = self.add(engine, "Hopeful Initiate", ref="HOPEFUL-TRAINING")
        larger = self.add(engine, "Generic Bound Body", ref="HOPEFUL-TRAINING-PARTNER")
        for attacker in (source, larger):
            attacker.acquired_control_turn_count = -1
        larger.counters["+1/+1"] = 1
        target = self.add(engine, "Sol Ring", seat="B", ref="HOPEFUL-TRAINING-TARGET")
        for index in range(3):
            self.add(engine, "Generic Bound Plains", ref=f"HOPEFUL-TRAINING-MANA-{index}")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.assertFalse(self._hopeful_actions(session, source))

        self.attack(session, [(source, "B"), (larger, "B")])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[source.object_id].counters.get("+1/+1"))
        self.until(session, lambda game: game.state.phase == "postcombat_main")
        action, = self._hopeful_actions(session, source)
        self.assertEqual([2], action["choice_schema"]["cost_cards"]["allowed_counts"])
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto",
                                          "targets": [target.ref], "cost_cards": [source.ref, larger.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.cards[source.object_id].counters)
        self.assertFalse(session.state.cards[larger.object_id].counters)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.replay(session, load=True)

    def test_hopeful_initiate_distributed_descriptor_and_authority_fail_closed(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.selected_counter_costs import (
            DISTRIBUTED_PLUS_COUNTER_COST_CAPABILITY, DISTRIBUTED_PLUS_COUNTER_COST_KIND,
            selected_counter_runtime_dependencies,
        )

        card = self.db.lookup("Hopeful Initiate")
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        result, = (node for node in ir.faces[0].nodes if node.event == "activate")
        self.assertIn(DISTRIBUTED_PLUS_COUNTER_COST_CAPABILITY, result.capability_dependencies)
        typed = CostChoice(DISTRIBUTED_PLUS_COUNTER_COST_KIND, card_type="creature")
        self.assertEqual(typed, CostChoice.from_dict(typed.to_dict()))
        for mutated in ({"count": 2}, {"card_type": "artifact"}, {"counter_amount": 3}):
            with self.subTest(mutated=mutated), self.assertRaises(ValueError):
                CostChoice.from_dict({**typed.to_dict(), **mutated})
        for text in (
            card.oracle_text.replace("Remove two", "Remove three"),
            card.oracle_text.replace("creatures you control", "permanents you control"),
            card.oracle_text.replace("from among creatures you control", "from among creatures an opponent controls"),
        ):
            with self.subTest(text=text):
                candidate = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                                capability_profile="commander_review")
                self.assertNotEqual("exact", candidate.status)

        session = self.session(734208)
        source = self.add(session.engine, "Hopeful Initiate")
        self.assert_closed(session, source)
        program, = (program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if program.event == "activate")
        self.assertIn(DISTRIBUTED_PLUS_COUNTER_COST_CAPABILITY, selected_counter_runtime_dependencies(program))
        broken = replace(program, cost_schema={**program.cost_schema, "choices": []})
        with self.assertRaises(ValueError):
            selected_counter_runtime_dependencies(broken)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for dependency in (DISTRIBUTED_PLUS_COUNTER_COST_CAPABILITY, SELECTED_COUNTER_COST_CAPABILITY):
            with self.subTest(dependency=dependency):
                registry = deepcopy(raw)
                owner = next(row for row in registry["capabilities"] if row["id"] == dependency)
                owner.update(status="blocked", blockers=["Selected counter removal capability withdrawn"])
                blocked = compile_oracle_card(card, capability_registry=CapabilityRegistry(registry),
                                              capability_profile="commander_review")
                self.assertNotEqual("exact", blocked.status)
