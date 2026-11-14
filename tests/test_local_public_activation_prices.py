"""CR 118.7/601.2f/602.2b: public hand and draw facts price one activation."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
import test_recipient_control as recipients
import test_fixed_creature_power_damage as records
from inherited_composition_fixture import InheritedNativeFixture
from quorune.abilities import ActivatedAbility
from quorune.activated_ability_descriptor import ABILITY_LOCAL_MANA_REDUCTION_FIELD
from quorune.activation_cost_modifiers import (
    AbilityLocalPublicManaAdjustment, ConditionalActivationReduction,
    FixedActivationCostModifierSpec, ability_local_mana_reduction_from_dict,
)
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.compiler.activated_costs import parse_compiled_activation_line
from quorune.continuous_conditions import FixedPublicStateConditionKind as Kind, FixedPublicStateConditionSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.activation.pricing import _ordinary_prices
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import compact_ci_fixture_paths, build_fixture_database

NAMES = ("Loreseeker's Stone", 'Arm-Mounted Anchor', 'Kami of Jealous Thirst')
CAPABILITY = 'activation.mana_cost.ability_local_public_value'


class LocalPublicActivationCompilerTests(unittest.TestCase):
    def test_typed_adjustments_roundtrip_and_reject_hidden_counts_or_unknown_conditions(self):
        condition = FixedPublicStateConditionSpec(Kind.CONTROLLER_DRAW_COUNT_AT_LEAST, amount=3)
        spec = AbilityLocalPublicManaAdjustment(FrozenMap({'GENERIC':4,'B':1}), 'reduce', condition=condition)
        self.assertEqual(spec, ability_local_mana_reduction_from_dict(spec.to_dict()))
        hand = CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE, query=ObjectQuerySpec(zones=('hand',)))
        tax = AbilityLocalPublicManaAdjustment(FrozenMap({'GENERIC':1}), 'increase', quantity=hand)
        self.assertEqual(tax, ability_local_mana_reduction_from_dict(tax.to_dict()))
        for changes in ({'schema_version':True}, {'extra':1}, {'mana':{'GENERIC':True}},
                {'mana':{'E':1}}, {'mana':{}}, {'quantity':hand.to_dict()},
                {'condition':FixedPublicStateConditionSpec(Kind.CONTROLLER_LIFE_AT_LEAST, amount=20).to_dict()}):
            with self.subTest(changes=changes), self.assertRaises((ValueError,TypeError)):
                ability_local_mana_reduction_from_dict({**spec.to_dict(), **changes})
        filtered = {**hand.to_dict(), 'query':ObjectQuerySpec(zones=('hand',), types_all=('instant',)).to_dict()}
        for changes in ({'mana':{'U':1}}, {'quantity':filtered}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ability_local_mana_reduction_from_dict({**tax.to_dict(), **changes})
        old = ConditionalActivationReduction(2, FixedPublicStateConditionSpec(Kind.CONTROLLER_TURN))
        self.assertEqual({'schema_version':1,'generic':2,'condition':old.condition.to_dict()}, old.to_dict())

    def test_increases_precede_colored_reductions_and_both_minimum_orders_remain_legal(self):
        condition = FixedPublicStateConditionSpec(Kind.CONTROLLER_DRAW_COUNT_AT_LEAST, amount=3)
        adjustment = AbilityLocalPublicManaAdjustment(FrozenMap({'GENERIC':4,'B':1}), 'reduce', condition=condition)
        ability = SimpleNamespace(mana={'GENERIC':4,'B':1}, announced_x_symbols=None,
            generic_reduction_per_legendary_creature=0, ability_local_mana_reduction=adjustment)
        modifiers = (FixedActivationCostModifierSpec(2, subject_relation='any'),
            FixedActivationCostModifierSpec(-3, subject_relation='any', minimum_mana=1))
        with patch('quorune.rules.activation.public_state_conditions.current_activation_public_state_matches', return_value=True):
            prices, _, _ = _ordinary_prices(None, 'A', SimpleNamespace(controller='A'), ability, modifiers)
            self.assertEqual({0,1}, {p.requirements['GENERIC'] for p in prices})
            self.assertEqual({0}, {p.requirements['B'] for p in prices})
            ability.mana = {'GENERIC':5,'U':2}
            prices, _, _ = _ordinary_prices(None, 'A', SimpleNamespace(controller='A'), ability, ())
            self.assertEqual((0,2,0), tuple(prices[0].requirements[k] for k in ('GENERIC','U','B')))

    def test_compiler_preserves_complete_cost_clauses_and_nearest_exclusions(self):
        registry = load_default_capability_registry()
        for text in ('{3}, {T}: Draw three cards. This ability costs {1} more to activate for each card in your hand.',
                "{4}{B}: You gain 2 life. This ability costs {4}{B} less to activate if you've drawn three or more cards this turn."):
            ability, = parse_compiled_activation_line(text, card_name='Observer', keywords=())[0]
            self.assertIsInstance(ability.ability_local_mana_reduction, AbilityLocalPublicManaAdjustment)
            self.assertEqual(ability, ActivatedAbility.from_dict(ability.to_dict()))
            self.assertNotIn('This ability costs', ability.effect_text)
        for clause in ('{1} more to activate for each instant card in your hand.',
                '{B} more to activate for each card in your hand.',
                '{1} less to activate if an opponent has three cards in hand.',
                '{1} less to activate if it targets a creature with power 3 or less.',
                '{X} less to activate if you have one card in hand.'):
            text = '{3}, {T}: Draw a card. This ability costs ' + clause
            self.assertNotEqual('exact', compile_oracle_card(records.card_record(text,
                type_line='Artifact'), capability_registry=registry).status, clause)


class LocalPublicActivationNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish
    finish_turn = recipients.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        names = {*NAMES, 'Sliver Hivelord', 'Generic Bound Plains', 'Grizzly Bears', 'Counterspell',
            'Lightning Bolt', 'Opt', 'Divination', 'Defiant Strike', 'Opportunity', 'Island', 'Swamp', 'Fervor',
            'Chromatic Sphere', 'Sol Ring'}
        paths = (*compact_ci_fixture_paths(root=ROOT), ROOT/'tests/fixtures/bound-effect-program-cards.json')
        cards = {r['name']:r for path in paths for r in json.loads(path.read_text(encoding='utf-8'))['cards'] if r['name'] in names}
        assert set(cards) == names, names-set(cards)
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        document = json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'))
        document.update(cards=list(cards.values()), rulings=[])
        fixture = folder/'prices.json'; fixture.write_text(json.dumps(document), encoding='utf-8')
        path = folder/'prices.sqlite3'; build_fixture_database([fixture], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public activation prices', [DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Sliver Hivelord'])

    def scene(self, name, *, mana, hand=(), before=None):
        s = self.session(2021033 + len(name))
        for oid in tuple(s.state.players['A'].zones['hand']):s.engine.move_card(oid, 'library', log=False)
        source = self.add(s.engine, name)
        body = self.add(s.engine, 'Grizzly Bears')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        cards = {n:self.add(s.engine, n, zone='hand') for n in hand}
        if before:before(s, source, body, cards)
        s.state.config.semantic_policy = 'trusted_only'; self.assert_closed(s, source)
        self.prepare(s, mana=mana); self.checkpoint(s)
        return s, source, body, private, cards

    def activation(self, s, source):
        self.priority(s, 'A')
        return next((r for r in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if r['id'].startswith('activate:' + source.ref + ':') and not r.get('mana_ability')), None)

    def activate(self, s, source, targets=(), *, payment=None):
        row = self.activation(s, source); self.assertIsNotNone(row)
        command = {'action_id':row['id'], 'targets':list(targets), 'pay':'auto'}
        if payment is not None:command.update(pay='manual', payment=payment)
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        return command

    def cast_manual(self, s, source, payment, targets=()):
        self.priority(s, 'A'); row = self.action(s, 'A', source)
        command = {'action_id':row['id'], 'targets':list(targets), 'pay':'manual', 'payment':payment}
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)

    def test_originals_have_complete_normal_binding_and_independent_runtime_minima(self):
        for name in NAMES:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                semantic = SemanticRegistry()
                program = _compile_best_available_card_program_from_ir(self.db, card, ir, semantic_registry=semantic,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                self.assertEqual('capability_closed', binding['trust_basis'])
                s = self.session(202103301); source = self.add(s.engine, name, zone='hand')
                programs = [p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                    if p.event == 'activate' and (p.cost_schema or {}).get(ABILITY_LOCAL_MANA_REDUCTION_FIELD) is not None]
                self.assertEqual(1, len(programs))
                p, = programs; unlabelled = replace(p, coverage=[])
                self.assertTrue(bind_semantic_program_runtime(unlabelled, capability_registry=self.registry,
                    profile='commander_review')['strict'])
                deps = tuple(c for c in p.capability_dependencies if c != CAPABILITY)
                changed = replace(unlabelled, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY, bound['blockers'])

    def test_actual_stone_prices_current_own_hand_then_pays_taps_and_draws_three(self):
        s, source, body, private, cards = self.scene("Loreseeker's Stone", mana={'C':5,'R':1}, hand=('Lightning Bolt','Opt'))
        row = self.activation(s, source)
        self.assertEqual({'GENERIC':5}, row['cost_summary']['m'])
        self.reject(s, 'A', {'action_id':row['id'], 'pay':'manual', 'payment':{'C':4}})
        self.cast(s, 'A', cards['Lightning Bolt'], ['B'])
        self.assertIsNone(self.resolve(s))
        row = self.activation(s, source); self.assertEqual({'GENERIC':4}, row['cost_summary']['m'])
        hand = len(s.state.players['A'].zones['hand'])
        self.activate(s, source)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(1, s.state.players['A'].mana_pool['C'])
        self.assertEqual(hand, len(s.state.players['A'].zones['hand']))
        self.assertIsNone(self.activation(s, source))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+3, len(s.state.players['A'].zones['hand']))
        self.assertEqual(37, s.state.players['B'].life)
        self.finish(s, private)

    def test_actual_anchor_locks_paid_cost_then_offers_free_equip_and_keeps_combat_loot(self):
        extra = {}
        def before(s, source, body, cards):
            extra['second'] = self.add(s.engine, 'Grizzly Bears', ref='anchor-second-body')
            self.add(s.engine, 'Fervor')
        s, source, body, private, cards = self.scene('Arm-Mounted Anchor', mana={'C':2,'R':1},
            hand=('Lightning Bolt','Opt'), before=before)
        row = self.activation(s, source); self.assertEqual({'GENERIC':2}, row['cost_summary']['m'])
        self.reject(s, 'A', {'action_id':row['id'], 'targets':[body.ref], 'pay':'manual', 'payment':{}})
        self.activate(s, source, [body.ref], payment={'C':2}); self.assertEqual(0, s.state.players['A'].mana_pool['C'])
        self.cast(s, 'A', cards['Lightning Bolt'], ['B'])
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(0, s.state.players['A'].mana_pool['C'])
        self.assertEqual(body.object_id, s.state.cards[source.object_id].attached_to)
        row = self.activation(s, source); self.assertIsNotNone(row)
        self.assertEqual({}, row['cost_summary']['m'])
        self.activate(s, source, [extra['second'].ref]); self.assertIsNone(self.resolve(s))
        body = s.state.cards[extra['second'].object_id]
        self.assertEqual(4, s.engine._numeric_stat(body.object_id, 'power'))
        self.assertIn('menace', s.engine._combat_keywords(body))
        hand = set(s.state.players['A'].zones['hand'])
        self.attack(s, [(body,'B')])
        self.until(s, lambda g:g.state.pending_decision is not None and g.state.pending_decision.kind == 'choice.apnap')
        drawn = set(s.state.players['A'].zones['hand']) - hand; self.assertEqual(2, len(drawn))
        command = {'action_id':'choose', 'cards':[s.state.cards[oid].ref for oid in sorted(drawn)], 'discard_mode':'normal'}
        self.reject(s, 'B', command)
        self.reject(s, 'A', {'action_id':'choose','cards':[cards['Opt'].ref],'discard_mode':'matching'})
        s = self.replay(s, load=True)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[oid].zone == 'graveyard' for oid in drawn))
        self.assertEqual(33, s.state.players['B'].life)
        self.finish(s, private)

    def test_actual_kami_uses_third_controller_draw_removes_black_and_resets_next_turn(self):
        extra = {}
        def before(s, source, body, cards):
            extra['opportunity'] = self.add(s.engine, 'Opportunity', seat='B', zone='hand')
            for i in range(6):self.add(s.engine, 'Island', seat='B', ref='kami-opponent-island-'+str(i))
            extra['payers'] = [self.add(s.engine, 'Generic Bound Plains', ref='kami-next-turn-mana-'+str(i)) for i in range(4)]
            extra['payers'].append(self.add(s.engine, 'Swamp', ref='kami-next-turn-black'))
        s, source, body, private, cards = self.scene('Kami of Jealous Thirst', mana={'C':6,'U':1,'W':1,'B':1},
            hand=('Divination','Defiant Strike'), before=before)
        self.assertEqual({'GENERIC':4,'B':1}, self.activation(s, source)['cost_summary']['m'])
        self.cast_manual(s, cards['Divination'], {'C':2,'U':1})
        self.cast(s, 'B', extra['opportunity'], ['B']); self.assertIsNone(self.resolve(s))
        self.assertEqual({'GENERIC':4,'B':1}, self.activation(s, source)['cost_summary']['m'])
        self.cast_manual(s, cards['Defiant Strike'], {'W':1}, [body.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual({}, self.activation(s, source)['cost_summary']['m'])
        paid_before = dict(s.state.players['A'].mana_pool)
        command = self.activate(s, source)
        self.assertEqual(paid_before, dict(s.state.players['A'].mana_pool))
        self.assertIsNone(self.activation(s, source)); self.reject(s, 'A', command)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual((42,38,38,38), tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertIn('deathtouch', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.finish_turn(s)
        self.assertEqual({'GENERIC':4,'B':1}, self.activation(s, source)['cost_summary']['m'])
        self.activate(s, source)
        self.assertEqual(0, s.state.players['A'].mana_pool['C'])
        self.assertEqual(0, s.state.players['A'].mana_pool['B'])
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in extra['payers']))
        self.assertIsNone(self.resolve(s))
        self.assertEqual((44,36,36,36), tuple(s.state.players[p].life for p in 'ABCD'))
        self.finish(s, private)
        self.kami_third_draw_during_mana_payment()

    def kami_third_draw_during_mana_payment(self):
        extra = {}
        def before(s, source, body, cards):
            extra['sphere'] = self.add(s.engine, 'Chromatic Sphere')
            extra['ring'] = self.add(s.engine, 'Sol Ring')
        s, source, body, private, cards = self.scene('Kami of Jealous Thirst', mana={'C':6,'U':1},
            hand=('Divination',), before=before)
        for card in extra.values():self.assert_closed(s, card)
        self.cast_manual(s, cards['Divination'], {'C':2,'U':1}); self.assertIsNone(self.resolve(s))
        self.assertEqual({'GENERIC':4,'B':1}, self.activation(s, source)['cost_summary']['m'])
        self.assertEqual((4,0), tuple(s.state.players['A'].mana_pool[key] for key in ('C','B')))
        hand = len(s.state.players['A'].zones['hand'])
        self.activate(s, source)
        self.assertEqual('graveyard', s.state.cards[extra['sphere'].object_id].zone)
        self.assertTrue(s.state.cards[extra['ring'].object_id].tapped)
        self.assertEqual(hand+1, len(s.state.players['A'].zones['hand']))
        self.assertEqual((1,0), tuple(s.state.players['A'].mana_pool[key] for key in ('C','B')))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual((42,38,38,38), tuple(s.state.players[p].life for p in 'ABCD'))
        self.finish(s, private)

    def test_fresh_hand_tax_and_conditional_reduction_omissions_are_killed(self):
        for owner, method in (
            ('quorune.rules.activation.cost_modifiers._ability_local_mana_modifiers',
             'test_actual_stone_prices_current_own_hand_then_pays_taps_and_draws_three'),
            ('quorune.rules.activation.pricing.ability_local_mana_reductions',
             'test_actual_anchor_locks_paid_cost_then_offers_free_equip_and_keeps_combat_loot')):
            with patch(owner, return_value=[]), self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()


if __name__ == '__main__':unittest.main()
