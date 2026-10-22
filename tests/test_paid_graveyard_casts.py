from __future__ import annotations

from dataclasses import replace
import json
import unittest

from quorune.additional_cost_vocabulary import DISCARD_ONE_COST, SACRIFICE_ONE_COST, EXILE_ONE_FROM_GRAVEYARD_COST
from quorune.object_predicate import ObjectQuerySpec
from quorune.replacement.immutable import FrozenMap
from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from quorune.rules.casting_additional_cost_groups import FixedLifePaymentAdditionalCost
from quorune.rules.fixed_payment_groups import fixed_life_zone_costs, fixed_life_zone_dependencies
from quorune.rules.graveyard_cast_costs import PaidGraveyardCastSpec, PAID_GRAVEYARD_CONTEXT
from quorune.rules.self_graveyard_discard import SelfGraveyardDiscardSpec
from common import ROOT
from pathlib import Path
import tempfile
from unittest.mock import patch
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_self_zone_discard_casts as controls
from quorune.rules.evidence_storage import read_evidence_document


class PaidGraveyardModelTests(unittest.TestCase):
    def prices(self):
        discard = FixedZoneChangeAdditionalCost(DISCARD_ONE_COST, 'discard_cards',
            ObjectQuerySpec(zones=('hand',), owner='$actor', known_to_actor=True))
        sacrifice = FixedZoneChangeAdditionalCost(SACRIFICE_ONE_COST, 'sacrifice_cards',
            ObjectQuerySpec(zones=('battlefield',), controller='$actor', known_to_actor=True, types_all=('creature',)), count=2)
        union = FixedZoneChangeAdditionalCost(SACRIFICE_ONE_COST, 'sacrifice_cards',
            ObjectQuerySpec(zones=('battlefield',), controller='$actor', known_to_actor=True, types_any=('artifact', 'creature')))
        exile = FixedZoneChangeAdditionalCost(EXILE_ONE_FROM_GRAVEYARD_COST, 'exile_cards',
            ObjectQuerySpec(zones=('graveyard',), owner='$actor', known_to_actor=True, types_all=('creature',)))
        return (PaidGraveyardCastSpec('{2}{W}'),
            PaidGraveyardCastSpec('{B}{B}', (FrozenMap(sacrifice.to_descriptor()),)),
            PaidGraveyardCastSpec(additional_costs=(FrozenMap(FixedLifePaymentAdditionalCost(3).to_descriptor()), FrozenMap(discard.to_descriptor()))),
            PaidGraveyardCastSpec(additional_costs=(FrozenMap(FixedLifePaymentAdditionalCost(2).to_descriptor()), FrozenMap(union.to_descriptor()))),
            PaidGraveyardCastSpec(additional_costs=(FrozenMap(exile.to_descriptor()),)))

    def test_closed_prices_round_trip_without_changing_legacy_discard_wire(self):
        self.assertEqual({'schema_version': 1, 'discard_count': 2}, SelfGraveyardDiscardSpec(2).to_dict())
        printed = {'id': 'normal', 'requirements': {'GENERIC': 5, 'B': 2}, 'kind': 'normal'}
        for spec in self.prices():
            self.assertEqual(spec, PaidGraveyardCastSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            option = spec.cost_option(printed)
            self.assertEqual(spec.to_dict(), option[PAID_GRAVEYARD_CONTEXT])
            self.assertEqual(list(spec.to_dict()['additional_costs']), option['_additional_option_costs'])
            self.assertEqual({'GENERIC': 5, 'B': 2}, printed['requirements'])
            self.assertEqual('graveyard', option['source_zone'])
        self.assertEqual('alternate', self.prices()[0].cost_option(printed)['kind'])
        self.assertEqual('zone_cast', self.prices()[2].cost_option(printed)['kind'])
        self.assertEqual(2, self.prices()[0].cost_option(printed)['requirements']['GENERIC'])
        self.assertEqual(0, self.prices()[1].cost_option(printed)['requirements']['GENERIC'])

    def test_life_zone_composition_keeps_only_its_actual_resource_owner(self):
        for spec in self.prices()[2:4]:
            values = spec.to_dict()['additional_costs']
            self.assertTrue(fixed_life_zone_costs(values))
            self.assertIn('casting.additional_cost.fixed_life_zone', fixed_life_zone_dependencies(values))
            self.assertFalse(fixed_life_zone_costs(list(reversed(values))))
            self.assertFalse(fixed_life_zone_costs([values[0], values[0]]))
        self.assertNotIn('casting.additional_cost.zone_change.fixed_discard', self.prices()[3].capabilities())
        self.assertNotIn('casting.additional_cost.fixed_life_payment', self.prices()[4].capabilities())

    def test_unknown_variable_and_mutable_price_fields_are_rejected(self):
        for spec in self.prices():
            for patch in ({'schema_version': True}, {'schema_version': 2}, {'future': 1},
                    {'kind': 'future_payment'}, {'mana_cost': '{X}'}, {'mana_cost': '{B/P}'},
                    {'additional_costs': [dict(kind='unknown')]}):
                with self.assertRaises(ValueError):
                    PaidGraveyardCastSpec.from_dict({**spec.to_dict(), **patch})
        with self.assertRaises(ValueError):
            PaidGraveyardCastSpec()
        with self.assertRaises(ValueError):
            replace(self.prices()[2], additional_costs=tuple(self.prices()[2].to_dict()['additional_costs']))
        invalid = self.prices()[2].to_dict()
        invalid['additional_costs'][1]['predicate']['types_all'] = ['creature']
        with self.assertRaises(ValueError):
            PaidGraveyardCastSpec.from_dict(invalid)


class PaidGraveyardCompilerTests(unittest.TestCase):
    def test_source_scope_rejects_other_cost_methods_variable_mana_and_printed_additional_costs(self):
        from quorune.oracle_ir import compile_oracle_card
        # Build through the fixture database so canonical record decoding remains authoritative.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'paid-scope.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/paid-graveyard-cast-cards.json'], path)
            with CardDatabase(path) as db:
                card = db.lookup("Raffine's Guidance")
                registry = load_default_capability_registry()
                for changed in (
                    replace(card, mana_cost='{X}{W}'),
                    replace(card, keywords=(*card.keywords, 'Kicker'),
                        oracle_text=card.oracle_text+'\nKicker {1}'),
                    replace(card, oracle_text=card.oracle_text+'\nAs an additional cost to cast this spell, discard a card.'),
                ):
                    ir = compile_oracle_card(changed, capability_registry=registry, capability_profile='commander_review')
                    self.assertNotEqual('exact', ir.status)
                    self.assertTrue(ir.material_residuals)

    def test_original_fixed_payment_clauses_keep_alternative_and_additional_prices_distinct(self):
        from quorune.compiler.paid_graveyard_templates import paid_graveyard_spec
        data = json.loads((ROOT/'tests/fixtures/paid-graveyard-cast-cards.json').read_text(encoding='utf-8'))
        by_name = {r['name']: r for r in data['cards']}
        expected = {"Raffine's Guidance": ('{2}{W}', 0), 'Scourge of Nel Toth': ('{B}{B}', 1),
            'Demonic Embrace': (None, 2), 'Wickerfolk Indomitable': (None, 2), 'Helbrute': (None, 1)}
        for name, (mana, count) in expected.items():
            card = by_name[name]
            text = next(line for line in card['oracle_text'].splitlines() if 'from your graveyard by' in line)
            spec = paid_graveyard_spec(text)
            self.assertIsNotNone(spec, name)
            self.assertEqual((mana, count), (spec.mana_cost, len(spec.additional_costs)))

    def test_conditions_variable_prices_and_unrepresented_companion_results_remain_unparsed(self):
        from quorune.compiler.paid_graveyard_templates import paid_graveyard_spec
        for text in (
            'You may cast this card from your graveyard by paying {X}{B} rather than paying its mana cost.',
            'You may cast this card from your graveyard by paying {B/P} rather than paying its mana cost.',
            'You may cast this card from your graveyard by paying {B} in addition to paying its other costs.',
            'You may cast this card from your graveyard by sacrificing 100 creatures rather than paying its mana cost.',
            'You may cast this card from your graveyard by paying 2 life and sacrificing a tapped creature in addition to paying its other costs.',
            'You may cast this card from your graveyard by exiling a creature card from an opponent\'s graveyard in addition to paying its other costs.',
            'You may cast this card from your graveyard by paying {W} rather than paying its mana cost. If you do, it enters with two counters.',
            'You may cast this card from your graveyard if you control a creature.',
        ):
            self.assertIsNone(paid_graveyard_spec(text), text)
        decorated = 'If You Do — You may cast this card from your graveyard by discarding a card in addition to paying its other costs.'
        self.assertIsNone(paid_graveyard_spec(decorated))
        self.assertIsNone(paid_graveyard_spec('Escape — {2}{B}, Exile three other cards from your graveyard.'))


class PaidGraveyardRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.SelfZoneDiscardCastTests.session
    add = controls.SelfZoneDiscardCastTests.add
    prepare = controls.SelfZoneDiscardCastTests.prepare
    checkpoint = controls.SelfZoneDiscardCastTests.checkpoint
    resolve = controls.SelfZoneDiscardCastTests.resolve
    replay = controls.SelfZoneDiscardCastTests.replay
    binding = controls.SelfZoneDiscardCastTests.binding
    actions = controls.SelfZoneDiscardCastTests.actions
    priority = controls.SelfZoneDiscardCastTests.priority
    private_replay = controls.SelfZoneDiscardCastTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); path = Path(cls.temp.name)/'paid-graveyard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/paid-graveyard-cast-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Paid graveyard methods', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def build(self, name, seed, *, mana=None, life=40, birds=False, fist=False, origin='graveyard'):
        s = self.session(seed); source = self.add(s.engine, name, zone=origin)
        target = self.add(s.engine, 'Generic Bound Body') if name in ("Raffine's Guidance", 'Demonic Embrace') else None
        hand = self.add(s.engine, 'Generic Bound Growth', zone='hand', ref='paid-own-hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='paid-private-c')
        payments = []
        if name == 'Scourge of Nel Toth':
            for i in range(2):
                c = self.add(s.engine, 'Birds of Paradise' if birds else 'Generic Bound Body', ref='paid-sac-'+str(i))
                c.acquired_control_turn_count = -1; payments.append(c)
        elif name == 'Wickerfolk Indomitable':
            payments = [self.add(s.engine, 'Memnite', ref='paid-artifact-creature')]
        elif name == 'Demonic Embrace':
            payments = [hand]
        elif name == 'Helbrute':
            payments = [self.add(s.engine, 'Generic Bound Body', zone='graveyard', ref='paid-other-grave-creature')]
        if fist:
            self.add(s.engine, 'Fist of Suns')
        s.state.players['A'].life = life
        normal = {"Raffine's Guidance": {'C': 2, 'W': 1}, 'Scourge of Nel Toth': {'B': 2},
            'Demonic Embrace': {'C': 1, 'B': 2}, 'Wickerfolk Indomitable': {'C': 3, 'B': 1},
            'Helbrute': {'C': 3, 'B': 1, 'R': 1}}[name]
        self.prepare(s, mana=normal if mana is None else mana); self.checkpoint(s)
        return s, source, target, payments, private

    def action(self, s, source):
        return next((a for a in self.actions(s) if a['id'] == 'cast:'+source.ref), None)

    def command(self, s, source, target, payments, *, global_price=False):
        action = self.action(s, source); self.assertIsNotNone(action, 'Supported paid graveyard cast must be offered')
        option = next((o for o in action['cost_options'] if ('battlefield-alternative:' in o['id']) == global_price
            and 'graveyard-fixed-payment:' in o['id']), None)
        self.assertIsNotNone(option, 'The exact authorized graveyard price must be available')
        fields = {'Demonic Embrace': 'discard_cards', 'Scourge of Nel Toth': 'sacrifice_cards',
            'Wickerfolk Indomitable': 'sacrifice_cards', 'Helbrute': 'exile_cards'}
        command = {'action_id': action['id'], 'cost_option': option['id'],
            'targets': [target.ref] if target else [], 'pay': 'auto'}
        if source.printed_name in fields:
            command[fields[source.printed_name]] = [c.ref for c in payments]
        return command

    def reject(self, s, command, principal='pilot:A'):
        before = authoritative_state_hash(s.state); result = s.act(principal, command)
        self.assertFalse(result.ok, result.summary); self.assertEqual(before, authoritative_state_hash(s.state))

    def test_pinned_originals_have_complete_payment_programs_and_independent_minima(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.graveyard_cast_costs import PAID_GRAVEYARD_CAPABILITY
        for name in ("Raffine's Guidance", 'Scourge of Nel Toth', 'Demonic Embrace', 'Wickerfolk Indomitable', 'Helbrute'):
            card = self.db.lookup(name); ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            p = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
                capability_registry=self.registry, capability_profile='commander_review')
            binding = bind_card_program_runtime(p, capability_registry=self.registry, profile='commander_review')
            self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
            programs = [a for a in p.abilities if PAID_GRAVEYARD_CAPABILITY in a.capability_dependencies]
            self.assertEqual(1, len(programs))
            ability = programs[0]; self.assertTrue(ability.handlers)
            for cap in ability.capability_dependencies:
                deps = tuple(c for c in ability.capability_dependencies if c != cap)
                missing = replace(ability, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+cap, bound['blockers'])

    def test_all_five_originals_pay_and_resolve_with_pending_load_private_projection_and_replay(self):
        for i, name in enumerate(("Raffine's Guidance", 'Scourge of Nel Toth', 'Demonic Embrace', 'Wickerfolk Indomitable', 'Helbrute')):
            s, source, target, payments, private = self.build(name, 784000+i)
            self.assertTrue(self.binding(s, source)['strict_capability_ready'])
            command = self.command(s, source, target, payments)
            self.reject(s, command, 'pilot:D')
            result = s.act('pilot:A', command); self.assertTrue(result.ok, name+': '+result.summary)
            self.assertEqual('stack', s.state.cards[source.object_id].zone)
            self.assertEqual(37 if name == 'Demonic Embrace' else 38 if name == 'Wickerfolk Indomitable' else 40,
                s.state.players['A'].life)
            for payment in payments:
                self.assertEqual('exile' if name == 'Helbrute' else 'graveyard', s.state.cards[payment.object_id].zone)
            self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
            s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
            if target:
                self.assertEqual(target.object_id, s.state.cards[source.object_id].attached_to)
                self.assertEqual((3, 7) if name == "Raffine's Guidance" else (5, 7),
                    tuple(s.engine._numeric_stat(target.object_id, k) for k in ('power', 'toughness')))
                if name == 'Demonic Embrace':
                    self.assertIn('flying', s.engine._combat_keywords(s.state.cards[target.object_id]))
                    subtypes = s.engine._type_parts(s.engine._effective_card_data(s.state.cards[target.object_id])['type_line'])[1]
                    self.assertIn('demon', subtypes)
                    self.assertLessEqual(s.engine._type_parts(self.db.lookup('Generic Bound Body').type_line)[1], subtypes)
            else:
                card = self.db.lookup(name)
                self.assertEqual((int(card.power), int(card.toughness)),
                    tuple(s.engine._numeric_stat(source.object_id, k) for k in ('power', 'toughness')))
                for keyword in ('flying', 'haste'):
                    if keyword in {k.casefold() for k in card.keywords}:
                        self.assertIn(keyword, s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.private_replay(s, private)

    def test_missing_duplicate_foreign_resource_and_principal_payments_reject_without_mutation(self):
        for i, name in enumerate(('Scourge of Nel Toth', 'Demonic Embrace', 'Wickerfolk Indomitable', 'Helbrute')):
            s, source, target, payments, private = self.build(name, 784020+i)
            command = self.command(s, source, target, payments)
            field = next(k for k in ('discard_cards', 'sacrifice_cards', 'exile_cards') if k in command)
            self.reject(s, {**command, field: []})
            self.reject(s, {**command, field: [private.ref]*len(payments)})
            if len(payments) == 2:
                self.reject(s, {**command, field: [payments[0].ref]*2})
            if name == 'Helbrute':
                self.reject(s, {**command, field: [source.ref]})
            self.reject(s, command, 'pilot:C'); self.replay(s)
        for i, (name, life) in enumerate((('Demonic Embrace', 2), ('Wickerfolk Indomitable', 1))):
            s, source, _, _, _ = self.build(name, 784030+i, life=life)
            self.assertIsNone(self.action(s, source)); self.replay(s)

    def test_additional_graveyard_prices_keep_resources_with_a_global_alternative_price(self):
        s, source, target, payments, private = self.build('Demonic Embrace', 784040,
            mana={c: 1 for c in 'WUBRG'}, fist=True)
        command = self.command(s, source, target, payments, global_price=True)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual(37, s.state.players['A'].life)
        self.assertEqual('graveyard', s.state.cards[payments[0].object_id].zone)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s)); self.private_replay(s, private)

    def test_fixed_graveyard_alternatives_cannot_combine_or_use_an_unpaid_normal_route(self):
        for i, name in enumerate(("Raffine's Guidance", 'Scourge of Nel Toth')):
            s, source, target, payments, _ = self.build(name, 784042+i, fist=True)
            command = self.command(s, source, target, payments)
            self.assertFalse(any('battlefield-alternative:' in o['id'] for o in self.action(s, source)['cost_options']))
            self.reject(s, {**command, 'cost_option': 'normal'}); self.replay(s)

    def test_constructed_life_and_graveyard_exile_uses_the_same_atomic_payment_owner(self):
        s = self.session(784048)
        source = self.add(s.engine, 'Generic Paid Graveyard Life Exile', zone='graveyard')
        payment = self.add(s.engine, 'Generic Bound Body', zone='graveyard')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(s, mana={'B': 1}); self.checkpoint(s)
        command = self.command(s, source, None, [payment]); command['exile_cards'] = [payment.ref]
        self.reject(s, {**command, 'exile_cards': [source.ref]})
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual(38, s.state.players['A'].life)
        self.assertEqual('exile', s.state.cards[payment.object_id].zone)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone); self.private_replay(s, private)

    def test_mana_creatures_can_tap_before_being_sacrificed_for_the_same_cast(self):
        s, source, target, payments, private = self.build('Scourge of Nel Toth', 784050, mana={}, birds=True)
        command = self.command(s, source, target, payments)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for c in payments))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s)); self.private_replay(s, private)

    def test_unrepresented_entry_surcharge_and_foreign_graveyard_sources_stay_closed(self):
        s = self.session(784060)
        cards = [self.add(s.engine, name, zone='graveyard') for name in ('Worldheart Phoenix', 'Risen Executioner')]
        foreign = self.add(s.engine, 'Demonic Embrace', seat='C', zone='graveyard')
        self.prepare(s, mana={c: 8 for c in 'WUBRGC'}); self.checkpoint(s)
        for card in cards:
            self.assertFalse(self.binding(s, card)['strict_capability_ready'])
        self.assertFalse(any(a['id'] == 'cast:'+foreign.ref for a in self.actions(s)))
        self.reject(s, {'action_id': 'cast:'+foreign.ref, 'pay': 'auto'}); self.replay(s)

    def test_all_five_normal_hand_casts_keep_printed_mana_and_no_graveyard_resource_price(self):
        prices = {"Raffine's Guidance": {'W': 1}, 'Scourge of Nel Toth': {'C': 5, 'B': 2},
            'Demonic Embrace': {'C': 1, 'B': 2}, 'Wickerfolk Indomitable': {'C': 3, 'B': 1},
            'Helbrute': {'C': 3, 'B': 1, 'R': 1}}
        for i, (name, mana) in enumerate(prices.items()):
            s, source, target, payments, private = self.build(name, 784070+i, mana=mana, origin='hand')
            before = {c.object_id: c.zone for c in payments}
            action = self.action(s, source); self.assertIsNotNone(action)
            self.assertFalse(any('graveyard-fixed-payment:' in o['id'] for o in action['cost_options']))
            result = s.act('pilot:A', {'action_id': action['id'], 'cost_option': 'normal',
                'targets': [target.ref] if target else [], 'pay': 'auto'})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(40, s.state.players['A'].life)
            self.assertEqual(before, {key: s.state.cards[key].zone for key in before})
            self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
            s = self.replay(s, load=True); self.assertIsNone(self.resolve(s)); self.private_replay(s, private)

    def test_countered_paid_spell_keeps_payment_and_recast_uses_fresh_resources_and_incarnation(self):
        s = self.session(784080)
        source = self.add(s.engine, 'Scourge of Nel Toth', zone='graveyard')
        first = [self.add(s.engine, 'Generic Bound Body', ref='counter-first-'+str(i)) for i in range(2)]
        second = [self.add(s.engine, 'Memnite', zone='hand', ref='counter-next-'+str(i)) for i in range(2)]
        lands = [self.add(s.engine, 'Swamp', ref='counter-swamp-'+str(i)) for i in range(2)]
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        s.state.players['B'].mana_pool.update({'U': 2})
        self.prepare(s, mana={'B': 2}); self.checkpoint(s)
        result = s.act('pilot:A', self.command(s, source, None, first)); self.assertTrue(result.ok, result.summary)
        first_identity = s.state.cards[source.object_id].logical_object_id
        stack_ref = s.state.stack[-1].ref; self.priority(s, 'B')
        rows = s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        action = next((r for r in rows if r['id'] == 'cast:'+counter.ref), None); self.assertIsNotNone(action)
        result = s.act('pilot:B', {'action_id': action['id'], 'targets': [stack_ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for c in first))
        self.assertNotEqual(first_identity, s.state.cards[source.object_id].logical_object_id)
        for card in second:
            self.priority(s, 'A'); action = self.action(s, s.state.cards[card.object_id]); self.assertIsNotNone(action)
            result = s.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}); self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(s))
        self.priority(s, 'A')
        result = s.act('pilot:A', self.command(s, s.state.cards[source.object_id], None,
            [s.state.cards[c.object_id] for c in second])); self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for c in second))
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in lands))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s)); self.private_replay(s, private)

    def test_fresh_originals_kill_permission_offer_and_runtime_minimum_omissions(self):
        admission = 'test_pinned_originals_have_complete_payment_programs_and_independent_minima'
        getattr(type(self)(admission), admission)()
        for owner, method in (
            ('quorune.compiler.paid_graveyard_nodes.paid_graveyard_template', admission),
            ('quorune.card_programs.runtime_requirements.paid_graveyard_runtime_dependencies', admission),
            ('quorune.rules.casting.base_cost_options.paid_graveyard_base_options',
                'test_all_five_originals_pay_and_resolve_with_pending_load_private_projection_and_replay')):
            with patch(owner, return_value=None if 'template' in owner else () if 'dependencies' in owner else []):
                with self.assertRaises(AssertionError):
                    getattr(type(self)(method), method)()

    def test_verified_owner_withdrawal_blocks_only_the_affected_payment_branches(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        all_names = {"Raffine's Guidance", 'Scourge of Nel Toth', 'Demonic Embrace', 'Wickerfolk Indomitable', 'Helbrute'}
        cases = {
            'casting.zone.self_graveyard.fixed_payment': all_names,
            'casting.zone.self_public_repeatable': all_names,
            'casting.additional_cost.fixed_life_zone': {'Demonic Embrace', 'Wickerfolk Indomitable'},
            'casting.additional_cost.fixed_life_payment': {'Demonic Embrace', 'Wickerfolk Indomitable'},
            'casting.additional_cost.fixed_sacrifice': {'Scourge of Nel Toth', 'Wickerfolk Indomitable'},
            'casting.additional_cost.zone_change.fixed_discard': {'Demonic Embrace'},
            'casting.additional_cost.zone_change.fixed_exile': {'Helbrute'},
        }
        for cap, affected in cases.items():
            value = deepcopy(raw)
            row = next(c for c in value['capabilities'] if c['id'] == cap)
            row.update(status='blocked', blockers=['withdrawn fixed payment owner'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            for name in sorted(all_names):
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
                if name not in affected:
                    self.assertEqual('exact', ir.status, (cap, name, ir.material_residuals)); continue
                self.assertNotEqual('exact', ir.status, (cap, name))
                blockers = [b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers), blockers)
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_permission_handler_and_selected_price_seal_reject_drift_before_mutation(self):
        from copy import deepcopy
        from quorune.rules.graveyard_cast_costs import (
            PAID_GRAVEYARD_CONTEXT, PAID_GRAVEYARD_FINGERPRINT, compiled_paid_graveyard_specs,
            validate_paid_graveyard_option,
        )
        from quorune.semantic_runtime.paid_graveyard_casts import PaidGraveyardCastPermissionHandler
        from quorune.compiler.paid_graveyard_templates import paid_graveyard_template
        from quorune.card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
        s, source, _, _, _ = self.build('Demonic Embrace', 784090)
        spec, = compiled_paid_graveyard_specs(s.engine, 'A', source)
        option = spec.cost_option({'id': 'normal', 'requirements': {'GENERIC': 1, 'B': 2}})
        option['additional_costs'] = option.pop('_additional_option_costs')
        validate_paid_graveyard_option(s.engine, 'A', source, option, origin='graveyard')
        variants = ({**option, PAID_GRAVEYARD_FINGERPRINT: 'stale'}, {**option, 'id': 'normal'},
            {**option, 'source_zone': 'hand'}, {**option, 'additional_costs': option['additional_costs'][:-1]},
            {**option, 'additional_costs': option['additional_costs']*2})
        before = authoritative_state_hash(s.state)
        for changed in (*variants, {k: v for k, v in option.items() if k != PAID_GRAVEYARD_CONTEXT}):
            with self.assertRaises(ValueError):
                validate_paid_graveyard_option(s.engine, 'A', source, changed, origin='graveyard')
            self.assertEqual(before, authoritative_state_hash(s.state))
        descriptor = paid_graveyard_template(self.db.lookup('Demonic Embrace').oracle_text.splitlines()[-1])[1]
        for changed in ({**descriptor, 'source_zone': 'hand'}, {**descriptor, 'schema_version': True},
                {**descriptor, REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: False}, {**descriptor, 'future': 1}):
            with self.assertRaises(ValueError):
                PaidGraveyardCastPermissionHandler().validate(changed)
        malformed = deepcopy(descriptor); malformed['payment']['additional_costs'][0]['amount'] = True
        with self.assertRaises(ValueError):
            PaidGraveyardCastPermissionHandler().validate(malformed)
