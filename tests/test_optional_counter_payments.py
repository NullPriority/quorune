"""CR 118.12 distinguishes an optional resolution cost from an optional result."""

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_fixed_optional_mana_payment_triggers as payment_native


class OptionalCounterPaymentCompilerTests(unittest.TestCase):
    def test_cost_clause_stays_whole_and_ordinary_optional_counter_keeps_legacy_shape(self):
        registry = load_default_capability_registry()
        for prefix, operation in (
            ('you may sacrifice another creature. If you do, ', 'offer_optional_mana_payment'),
            ('you may discard a card. If you do, ', 'offer_optional_mana_payment'),
            ('you may pay {1}. If you do, ', 'offer_optional_mana_payment'),
            ('you may ', 'offer_optional_counter_placement'),
        ):
            with self.subTest(prefix=prefix):
                ir = compile_oracle_card(query_record('Whenever this creature attacks, ' + prefix +
                    'put a +1/+1 counter on this creature.'), capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                effect = ir.faces[0].nodes[0].effects[0]
                self.assertEqual(operation, effect['op'])
                counter = effect.get('effects', [effect.get('effect')])[0]
                self.assertEqual('place_counters', counter['op'])
                self.assertEqual('$source.zone_object', counter['card'])
                if prefix.startswith('you may sacrifice'):
                    self.assertEqual(6, effect['payment']['schema_version'])
                    self.assertEqual('$source.zone_object', effect['payment']['predicate']['exclude_ref'])
                if prefix.startswith('you may pay'):
                    self.assertNotIn('schema_version', effect)

    def test_unknown_costs_results_and_siblings_remain_residual(self):
        registry = load_default_capability_registry()
        for body in ('You may rewrite history. If you do, put a +1/+1 counter on this creature.',
                     'You may sacrifice another creature with a mystery quality. If you do, put a +1/+1 counter on this creature.',
                     'You may sacrifice another creature. If you do, put a remembered counter on the prophecy.',
                     'You may sacrifice another creature. If you do, put a +1/+1 counter on this creature. Rewrite history.'):
            ir = compile_oracle_card(query_record('Whenever this creature attacks, ' + body), capability_registry=registry)
            self.assertNotEqual('exact', ir.status)
            self.assertTrue(ir.material_residuals)

    def test_restoring_optional_result_misclassification_is_killed(self):
        record = query_record('Whenever this creature attacks, you may sacrifice another creature. '
            'If you do, put a +1/+1 counter on this creature.')
        registry = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(record, capability_registry=registry).status)
        with patch('quorune.compiler.fixed_effect_payment_templates.is_optional_resolution_payment', return_value=False):
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=registry).status)

    def test_verified_cost_and_counter_withdrawal_preserve_unrelated_results(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declaration = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        for omitted in ('cost.sacrifice.another_fixed_resolution', 'counter.placement.quantity_replacement',
                        'variant.monarch.designate'):
            value = deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==omitted).update(
                status='blocked', blockers=['tested result/payment owner withdrawal'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declaration, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            text = 'Whenever this creature attacks, you may sacrifice another creature. If you do, put a +1/+1 counter on this creature.'
            ir = compile_oracle_card(query_record(text), capability_registry=registry)
            self.assertEqual(omitted=='variant.monarch.designate', ir.status=='exact')
            control = compile_oracle_card(query_record('Whenever this creature attacks, draw a card.'), capability_registry=registry)
            self.assertEqual('exact', control.status)


class OptionalCounterPaymentRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    begin_attack = payment_native.BoundEventPaymentRuntimeTests.begin_attack
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name'] in {'Generic Bound Commander','Generic Bound Plains'}]
        base['rulings'] = []
        base_path = Path(cls.temporary.name)/'synthetic-deck.json'
        base_path.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8', newline='\n')
        path = Path(cls.temporary.name)/'optional-counter-costs.sqlite3'
        build_fixture_database([base_path, ROOT/'tests/fixtures/optional-counter-payment-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Optional counter costs', [
            DeckEntry('Sliver Hivelord',1,'commander'), DeckEntry('Generic Bound Plains',30)],
            ['Sliver Hivelord'])

    def pay(self, session, refs, *, rejected=()):
        self.assertEqual('semantic.choice', session.state.pending_decision.kind)
        self.assertEqual(['A'], session.state.pending_decision.actors)
        schema = session.packet('pilot:A', full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        for ref in refs:self.assertIn(ref, schema['legal_refs'])
        before = authoritative_state_hash(session.state)
        for actor, cards in (('B',list(refs)), *(('A',[ref]) for ref in rejected)):
            result = session.act('pilot:'+actor, {'action_id':'choose','cards':cards})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act('pilot:A', {'action_id':'choose','cards':list(refs)})
        self.assertTrue(result.ok, result.summary)
        return session

    def private_replay(self, session, private):
        for seat in 'ABD':self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        return self.replay(session, load=True)

    def test_actual_hunter_sacrifice_counter_and_decline_preserve_order_and_replay(self):
        for offset, pay in enumerate((True,False)):
            with self.subTest(pay=pay):
                s = self.session(984_100+offset)
                source = self.add(s.engine, 'High-Society Hunter', ref='original-hunter')
                borrowed = self.add(s.engine, 'Grizzly Bears', ref='borrowed-cost');borrowed.owner = 'B'
                foreign = self.add(s.engine, 'Grizzly Bears', seat='B', ref='foreign-cost');foreign.owner = 'A'
                private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='counter-cost-private')
                self.assert_closed(s, source)
                source.acquired_control_turn_count = -1
                self.prepare(s);self.checkpoint(s)
                self.attack(s, [(source,'D')]);self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.assertEqual('battlefield', s.state.cards[borrowed.object_id].zone)
                self.assertFalse(s.state.cards[source.object_id].counters)
                before_hand = len(s.state.players['A'].zones['hand'])
                s = self.pay(s, [borrowed.ref] if pay else [], rejected=(source.ref,foreign.ref))
                self.resolve(s)
                self.assertEqual(int(pay), s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual('graveyard' if pay else 'battlefield', s.state.cards[borrowed.object_id].zone)
                self.assertEqual(before_hand+int(pay), len(s.state.players['A'].zones['hand']))
                if pay:self.assertIn(borrowed.object_id, s.state.players['B'].zones['graveyard'])
                self.private_replay(s, private)

    def test_actual_merchants_draw_survives_blink_and_another_uses_source_incarnation(self):
        s = self.session(984_200)
        source = self.add(s.engine, 'Beetle-Headed Merchants', ref='original-merchants')
        payment = self.add(s.engine, 'Ornithopter', ref='artifact-cost')
        blink = self.add(s.engine, 'Cloudshift', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='blink-cost-private')
        self.assert_closed(s, source);self.assert_closed(s, blink)
        original_identity = source.logical_object_id
        source.acquired_control_turn_count = -1
        self.begin_attack(s, source, {'W':1})
        self.cast(s, 'A', blink, targets=[source.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        returned = s.state.cards[source.object_id]
        self.assertNotEqual(original_identity, returned.logical_object_id)
        schema = s.packet('pilot:A', full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        self.assertIn(returned.ref, schema['legal_refs'])
        before_hand = len(s.state.players['A'].zones['hand'])
        s = self.pay(s, [payment.ref])
        self.resolve(s)
        self.assertEqual('graveyard', s.state.cards[payment.object_id].zone)
        self.assertEqual(before_hand+1, len(s.state.players['A'].zones['hand']))
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.private_replay(s, private)
