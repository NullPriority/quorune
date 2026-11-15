"""CR 602.2b/601.2h/608.2h: a paid sacrifice supplies its last battlefield stats."""
from dataclasses import replace
from types import SimpleNamespace
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as recipient_actions
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.activation_paid_characteristic_model import (
    ACTIVATION_PAID_STAT_CONTEXT, ACTIVATION_PAID_STAT_CAPABILITY,
    ActivationPaidCreatureFact, resolve_activation_paid_creature_stat,
)
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec, PaidCreatureStatSnapshot
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount


class ActivationPaidSacrificeStatModelTests(unittest.TestCase):
    def fact(self):
        return ActivationPaidCreatureFact('fixture:ability', 'source@1', 'paid-ref', 'paid@1',
            PaidCreatureStatSnapshot('paid@1', 3, 7))

    def item(self):
        fact = self.fact()
        return SimpleNamespace(kind='activated_ability', semantic_key=fact.semantic_key,
            controller='A', ref='paid-ability', label='Paid stat ability',
            context={'source_logical_object_id': fact.source_logical_object_id,
                'cost_objects': [fact.paid_ref], ACTIVATION_PAID_STAT_CONTEXT: fact.to_dict()})

    def test_activation_fact_is_immutable_closed_and_tied_to_actual_cost_and_source(self):
        fact = self.fact()
        self.assertEqual(fact, ActivationPaidCreatureFact.from_dict(fact.to_dict()))
        item = self.item()
        self.assertEqual(7, resolve_activation_paid_creature_stat(item, 'toughness'))
        for field, value in (('semantic_key', 'another:ability'), ('source_logical_object_id', 'source@2'),
                ('paid_logical_object_id', 'paid@2'), ('schema_version', True), ('extra', 1)):
            changed = {**fact.to_dict(), field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                item.context[ACTIVATION_PAID_STAT_CONTEXT] = changed
                resolve_activation_paid_creature_stat(item, 'toughness')
        item = self.item(); item.context['cost_objects'] = ['unrelated-cost']
        with self.assertRaises(ValueError):
            resolve_activation_paid_creature_stat(item, 'toughness')
        item = self.item(); item.kind = 'spell'
        with self.assertRaises(ValueError):
            resolve_activation_paid_creature_stat(item, 'toughness')

    def test_new_scalar_keeps_old_spell_paid_stat_scope_and_missing_history_closed(self):
        spec = ScalarEffectAmountSpec(ScalarAmountOrigin.PAID_ACTIVATION_CREATURE,
            characteristic='toughness', schema_version=13)
        self.assertEqual(spec, ScalarEffectAmountSpec.from_dict(spec.to_dict()))
        self.assertEqual(7, resolve_scalar_effect_amount(SimpleNamespace(), spec.to_dict(), self.item()))
        old = ScalarEffectAmountSpec(ScalarAmountOrigin.PAID_SACRIFICE_CREATURE,
            characteristic='toughness', schema_version=9)
        with self.assertRaises(ValueError):
            resolve_scalar_effect_amount(SimpleNamespace(), old.to_dict(), self.item())
        item = self.item(); del item.context[ACTIVATION_PAID_STAT_CONTEXT]
        with self.assertRaises(ValueError):
            resolve_scalar_effect_amount(SimpleNamespace(), spec.to_dict(), item)
        with self.assertRaises(ValueError):
            replace(spec, schema_version=9)

    def test_copied_fact_retains_paid_value_and_signed_snapshot_clamps_before_scaling(self):
        from quorune.selection.copy_targets import stack_copy_context
        original = self.item()
        item = deepcopy(original)
        item.context = stack_copy_context(original, {}, {}, permanent_spell=False, characteristics={})
        item.controller = 'B'
        item.ref = 'copied-paid-ability'
        self.assertEqual('paid-ability', item.context['copied_from_stack'])
        self.assertEqual(7, resolve_activation_paid_creature_stat(item, 'toughness'))
        fact = replace(self.fact(), stats=PaidCreatureStatSnapshot('paid@1', 3, -2))
        item.context[ACTIVATION_PAID_STAT_CONTEXT] = fact.to_dict()
        spec = ScalarEffectAmountSpec(ScalarAmountOrigin.PAID_ACTIVATION_CREATURE,
            characteristic='toughness', coefficient=-1, schema_version=13)
        self.assertEqual(0, resolve_scalar_effect_amount(SimpleNamespace(), spec.to_dict(), item))

    def test_paid_price_requires_one_actual_creature_and_valid_fixed_cost_fields(self):
        from quorune.rules.activation.paid_characteristic_price import activation_creature_sacrifice_choice
        from quorune.compiler.activated_costs import activated_ability_cost
        from quorune.compiler.activated_mana_nodes import _fixed_activated_costs
        from quorune.abilities import parse_activated_abilities
        ability = _fixed_activated_costs(parse_activated_abilities(card_name='Price witness', keywords=(),
            oracle_text="{1}, Sacrifice a creature: You gain life equal to the sacrificed creature's toughness.")[0])
        price = activated_ability_cost(ability)
        self.assertIsNotNone(activation_creature_sacrifice_choice(price))
        for mutate in (
            lambda p: p.update(tap_source=1), lambda p: p.update(mana={'GENERIC': True}),
            lambda p: p['choices'][0].update(n=2), lambda p: p['choices'][0].update(n=True),
            lambda p: p['choices'][0].pop('q'), lambda p: p['choices'][0].update(k='discard_one'),
            lambda p: p['choices'][0]['q'].update(types_any=['artifact']),
        ):
            bad = deepcopy(price); mutate(bad)
            with self.subTest(price=bad):
                self.assertIsNone(activation_creature_sacrifice_choice(bad))


class ActivationPaidSacrificeStatCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, text):
        return CardRecord(oracle_id='fixture:activation-paid-stat', name='Paid Stat Witness',
            mana_cost='{2}', mana_value=2, type_line='Artifact', oracle_text=text,
            power=None, toughness=None, loyalty=None, defense=None, colors=(),
            color_identity=(), keywords=(), produced_mana=(), layout='normal',
            released_at=None, legalities={'commander': 'legal'}, faces=(), raw={})

    def compile(self, text):
        return compile_oracle_card(self.record(text), capability_registry=self.registry,
            capability_profile='commander_review')

    def test_fixed_activation_sacrifice_life_result_requires_the_complete_price(self):
        for cost in ('{1}, Sacrifice a creature', '{T}, Sacrifice a creature',
                '{3}, {T}, Sacrifice a creature', '{1}{G}, Sacrifice another creature'):
            with self.subTest(cost=cost):
                text = cost + ": You gain life equal to the sacrificed creature's toughness."
                ir = self.compile(text)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = (n for n in ir.faces[0].nodes if n.event == 'activate')
                self.assertFalse(node.kind == 'mana_ability')
                self.assertIsNotNone(node.cost)
                life, = node.effects
                self.assertEqual(('life', '$controller'), (life['op'], life['player']))
                self.assertEqual('toughness', life['delta']['characteristic'])

    def test_unbound_aggregate_and_unknown_sacrifice_results_stay_material(self):
        for text in (
            "You gain life equal to the sacrificed creature's toughness.",
            "{1}, Sacrifice two creatures: You gain life equal to the sacrificed creature's toughness.",
            "{1}, Sacrifice an artifact: You gain life equal to the sacrificed creature's toughness.",
            "{1}, Sacrifice a creature: You gain life equal to the sacrificed creature's toughness. Invent an unknown result.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(text).status)

    def test_paid_activation_binding_keeps_the_actual_price_and_payload_minimum(self):
        class NoRulings:
            def rulings(self, record): return ()
        card = self.record("{1}, Sacrifice a creature: You gain life equal to the sacrificed creature's toughness.")
        ir = self.compile(card.oracle_text)
        program = _compile_best_available_card_program_from_ir(NoRulings(), card, ir,
            semantic_registry=SemanticRegistry(), capability_registry=self.registry,
            capability_profile='commander_review')
        ability = next(a for a in program.abilities if a.effects)
        def bind(value):
            return bind_semantic_program_runtime(value, capability_registry=self.registry, profile='commander_review')
        initial = bind(ability)
        self.assertTrue(initial['strict'], initial['blockers'])
        for field, value in (('cost_schema', None),
                ('coverage', [c for c in ability.coverage if c != 'paid-activation-creature-stat'])):
            with self.subTest(field=field):
                self.assertFalse(bind(replace(ability, **{field: value}))['strict'])
        with self.assertRaises(ValueError):
            replace(ability, event='resolve')
        deps = tuple(c for c in ability.capability_dependencies if c != ACTIVATION_PAID_STAT_CAPABILITY)
        self.assertFalse(bind(replace(ability, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict()))['strict'])
        drifted = deepcopy(ability.handlers)
        drifted[0]['ability']['mana']['GENERIC'] += 1
        self.assertFalse(bind(replace(ability, handlers=drifted))['strict'])

    def test_withdrawn_activation_stat_capability_keeps_the_result_unresolved(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == ACTIVATION_PAID_STAT_CAPABILITY).update(
            status='blocked', blockers=['Withheld activation paid-stat authority'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        ir = compile_oracle_card(self.record("{1}, Sacrifice a creature: You gain life equal to the sacrificed creature's toughness."),
            capability_registry=registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)


NAMES = ('Miren, the Moaning Well', 'Disciple of Griselbrand', 'Diamond Valley', 'Kheru Dreadmaw',
    "Arguel's Blood Fast // Temple of Aclazotz", 'Ayli, Eternal Pilgrim')


class ActivationPaidSacrificeStatNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipient_actions.RecipientControlNativeTests.reject
    finish = recipient_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'activation-stats.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/activation-paid-sacrifice-stat-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Paid activation originals',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def scene(self, name, *, mana=None):
        s = self.session(602207 + len(name))
        source = self.add(s.engine, name)
        if name.startswith("Arguel's"):
            source.active_face = 'Temple of Aclazotz'
        body = self.add(s.engine, 'Darksteel Myr', ref='paid-creature')
        body.counters['+1/+1'] = 4
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        foreign = self.add(s.engine, 'Grizzly Bears', seat='B')
        bounce = self.add(s.engine, 'Boomerang', seat='B', zone='hand')
        stifle = self.add(s.engine, 'Stifle', seat='B', zone='hand')
        copy = self.add(s.engine, 'Cackling Counterpart', zone='hand')
        self.assert_closed(s, source)
        s.state.config.semantic_policy = 'trusted_only'
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        s.state.active_player = 'A'; s.state.started = True
        s.state.phase = 'precombat_main'; s.state.step = 'main'; s.state.phase_index = 3
        s.state.players['A'].mana_pool.update(mana if mana is not None else {'C': 20, 'G': 5, 'U': 5, 'B': 5, 'W': 5})
        s.state.players['B'].mana_pool['U'] = 5
        for card in (source, body): card.acquired_control_turn_count = -1
        s.engine._grant_priority('A'); s.engine.pump(); self.checkpoint(s)
        return s, source, body, private, foreign, bounce, stifle, copy

    def activation_id(self, s, source):
        source = s.state.cards[source.object_id]
        ability = next(a for a in s.engine._activated_abilities(source)
            if a.effect_text == "You gain life equal to the sacrificed creature's toughness.")
        return 'activate:' + source.ref + ':' + ability.ability_id

    def activate(self, s, source, body):
        self.priority(s, 'A')
        action_id = self.activation_id(s, source)
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(r['id'] == action_id for r in actions))
        command = {'action_id': action_id, 'cost_objects': [body.ref], 'pay': 'auto'}
        self.reject(s, 'D', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)

    def paid_original(self, name):
        s, source, body, private, foreign, bounce, stifle, copy = self.scene(name)
        action_id = self.activation_id(s, source)
        self.reject(s, 'A', {'action_id': action_id, 'cost_objects': [foreign.ref], 'pay': 'auto'})
        if name in ('Kheru Dreadmaw', 'Ayli, Eternal Pilgrim'):
            self.reject(s, 'A', {'action_id': action_id, 'cost_objects': [source.ref], 'pay': 'auto'})
        self.activate(s, source, body)
        self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
        self.assertFalse(s.state.cards[body.object_id].counters)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual([45, 40, 40, 40], [s.state.players[p].life for p in 'ABCD'])
        self.finish(s, private)

    def test_actual_originals_pay_printed_cost_and_keep_last_battlefield_toughness(self):
        for name in NAMES:
            with self.subTest(name=name): self.paid_original(name)

    def test_pre_sacrifice_fact_omission_is_killed_by_real_diamond_valley_actions(self):
        with patch('quorune.rules.activation.paid_characteristic_results.capture_activation_paid_creature_fact', return_value=None):
            with self.assertRaises(AssertionError): self.paid_original('Diamond Valley')

    def test_actual_ability_survives_source_departure_and_countering_does_not_refund_cost(self):
        for counter in (False, True):
            with self.subTest(counter=counter):
                s, source, body, private, foreign, bounce, stifle, copy = self.scene('Diamond Valley')
                self.activate(s, source, body)
                target = s.state.stack[-1].ref if counter else source.ref
                self.cast(s, 'B', stifle if counter else bounce, [target])
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(40 if counter else 45, s.state.players['A'].life)
                self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
                self.assertEqual('battlefield' if counter else 'hand', s.state.cards[source.object_id].zone)
                self.finish(s, private)

    def test_actual_creature_token_can_pay_and_its_disappearance_keeps_paid_stat(self):
        s, source, body, private, foreign, bounce, stifle, copy = self.scene('Diamond Valley')
        self.cast(s, 'A', copy, [body.ref]); self.assertIsNone(self.resolve(s))
        token, = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.activate(s, source, token)
        self.assertEqual('outside', s.state.cards[token.object_id].zone)
        self.assertFalse(any(token.object_id in ids for p in s.state.players.values()
            for zone, ids in p.zones.items() if zone != 'outside'))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(41, s.state.players['A'].life)
        self.finish(s, private)

    def test_actual_source_can_pay_when_the_price_does_not_say_another(self):
        s, source, body, private, foreign, bounce, stifle, copy = self.scene('Disciple of Griselbrand')
        self.activate(s, source, source)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual([41, 40, 40, 40], [s.state.players[p].life for p in 'ABCD'])
        self.finish(s, private)

    def test_unpayable_complete_price_is_neither_offered_nor_accepted(self):
        s, source, body, private, foreign, bounce, stifle, copy = self.scene('Kheru Dreadmaw', mana={'C': 1})
        action_id = self.activation_id(s, source)
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r['id'] == action_id for r in actions))
        self.reject(s, 'A', {'action_id': action_id, 'cost_objects': [body.ref], 'pay': 'auto'})
        self.finish(s, private)


if __name__ == '__main__':
    unittest.main()
