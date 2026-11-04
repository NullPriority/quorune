from __future__ import annotations

"""CR 205.3: subtype quantities retain their correlated card-type domains."""

import unittest

from quorune.compiler.query_characteristic_templates import query_characteristic_quantity
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_declared_effect_amounts import generic_spell


class NoncreatureSubtypeQuantityCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def quantity(self, text):
        return query_characteristic_quantity(text, source_name='Generic Source', definition_extensions=True)

    def test_canonical_subtypes_keep_card_type_zone_plural_and_token_semantics(self):
        for text, kinds, subtype, token, zone in (
            ('Shrines you control', ('enchantment',), 'shrine', None, 'battlefield'),
            ('Bobbleheads you control', ('artifact',), 'bobblehead', None, 'battlefield'),
            ('Clue tokens you control', ('artifact',), 'clue', True, 'battlefield'),
            ('nontoken Foods you control', ('artifact',), 'food', False, 'battlefield'),
            ('Classes you control', ('enchantment',), 'class', None, 'battlefield'),
            ('Locus on the battlefield', ('land',), 'locus', None, 'battlefield'),
            ('Lesson cards in your graveyard', ('instant', 'sorcery'), 'lesson', False, 'graveyard'),
        ):
            with self.subTest(text=text):
                quantity = self.quantity(text)
                self.assertIsNotNone(quantity)
                query = quantity.query
                self.assertEqual((zone,), query.zones)
                self.assertEqual(kinds, query.types_all if len(kinds)==1 else query.types_any)
                self.assertEqual((subtype,), query.subtypes_all)
                self.assertIs(token, query.token)

    def test_complete_result_slots_and_static_definitions_use_the_same_query(self):
        for text in (
            'Draw X cards, where X is the number of Shrines you control.',
            'Each opponent loses X life and you gain X life, where X is the number of Shrines you control.',
            'Create a 1/1 green Saproling creature token for each Bobblehead you control.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(generic_spell(text, mana_cost='{2}{G}'), capability_registry=self.registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)

    def test_legacy_subtype_quantities_keep_their_predicate_encoding(self):
        for noun, owner, subtype in (('Auras', 'enchantment', 'aura'),
                ('Equipment', 'artifact', 'equipment'), ('Gates', 'land', 'gate'),
                ('Deserts', 'land', 'desert'), ('Islands', 'land', 'island')):
            quantity = self.quantity(noun+' you control')
            self.assertIsNotNone(quantity)
            self.assertEqual((owner,), quantity.query.types_all)
            self.assertEqual((subtype,), quantity.query.subtypes_all)
            self.assertEqual((), quantity.query.types_any)
            self.assertIsNone(quantity.query.token)

    def test_unknown_or_unconsumed_subtype_qualifiers_remain_residual(self):
        for text in ('Megashrines you control', 'Shrines chosen secretly',
                'Shrines and unseen objects you control', 'objects named Shrine you control'):
            with self.subTest(text=text):
                self.assertIsNone(self.quantity(text))

    def test_omitting_the_correlated_type_domain_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler.noncreature_quantity_subtypes import noncreature_quantity_subtype_fields
        def untyped(*args, **kwargs):
            fields = noncreature_quantity_subtype_fields(*args, **kwargs)
            return {k:v for k,v in fields.items() if k not in {'types_all','types_any'}} if fields else fields
        with patch('quorune.compiler.query_characteristic_templates.noncreature_quantity_subtype_fields', side_effect=untyped):
            result = unittest.TestResult()
            NoncreatureSubtypeQuantityCompilerTests(
                'test_canonical_subtypes_keep_card_type_zone_plural_and_token_semantics').run(result)
        self.assertFalse(result.errors, result.errors)
        self.assertTrue(result.failures)

    def test_verified_quantity_and_static_owner_withdrawals_keep_fixed_life_control(self):
        import json
        from dataclasses import replace
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        original = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        cases = (
            ('quantity_expression.public_query_effect_amount', generic_spell('Draw X cards, where X is the number of Shrines you control.', mana_cost='{2}{G}')),
            ('continuous.characteristics.query_count_modifier', replace(generic_spell('This creature gets +1/+1 for each Shrine you control.', mana_cost='{2}{G}'),type_line='Creature — Human',power='2',toughness='2')),
            ('continuous.characteristics.query_power_toughness_definition', replace(generic_spell("This creature's power and toughness are each equal to the number of Bobbleheads you control.", mana_cost='{2}{G}'),type_line='Creature — Human',power='*',toughness='*')),
        )
        for owner, card in cases:
            raw = json.loads(json.dumps(original))
            next(c for c in raw['capabilities'] if c['id']==owner).update(status='blocked', blockers=['Correlated quantity owner withdrawn'])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertEqual('exact', compile_oracle_card(card, capability_registry=self.registry).status)
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(generic_spell('You gain 2 life.'), capability_registry=registry).status)


import json
from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


class NoncreatureSubtypeQuantityNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'subtype-quantities.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/noncreature-subtype-quantity-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Subtype quantities', [DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)], ['Isamaru, Hound of Konda'])

    def reject(self, s, seat, command):
        before = authoritative_state_hash(s.state)
        result = s.act('pilot:'+seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state))

    def activate(self, s, seat, source):
        self.priority(s, seat)
        actions = s.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']
        offered = [row for row in actions if row['id'].startswith('activate:'+source.ref+':')
            and not row.get('mana_ability',False)]
        self.assertEqual(1, len(offered), offered)
        command = {'action_id': offered[0]['id'], 'pay': 'auto'}
        self.reject(s, 'D' if seat!='D' else 'C', command)
        result = s.act('pilot:'+seat, command)
        self.assertTrue(result.ok, result.summary)

    def clues(self, s, seat):
        return [c for c in s.state.cards.values() if c.zone=='battlefield' and c.controller==seat
            and 'clue' in s.engine._type_parts(s.engine._effective_card_data(c)['type_line'])[1]]

    def test_actual_copied_confront_investigates_before_counting_own_clues_and_locks_bonus(self):
        s = self.session(205608810)
        spell = self.add(s.engine, 'Confront the Unknown', zone='hand')
        copy = self.add(s.engine, 'Reverberate', seat='B', zone='hand')
        targets = {seat:self.add(s.engine,'Memnite',seat=seat,ref='clue-target-'+seat) for seat in 'AB'}
        s.state.players['B'].mana_pool.update({'R':2})
        self.assert_closed(s, spell)
        self.assert_closed(s, copy)
        self.prepare(s, mana={'G':1,'C':2})
        self.checkpoint(s)
        self.cast(s,'A',spell,[targets['A'].ref])
        self.cast(s,'B',copy,[s.state.stack[-1].ref])
        decision = self.resolve(s)
        self.assertEqual('semantic.choice', decision.kind)
        command = {'action_id':'choose','targets':[targets['B'].ref]}
        self.reject(s,'A',command)
        s = self.replay(s,load=True)
        self.assertTrue(s.act('pilot:B',command).ok)
        self.resolve_top(s)
        self.assertEqual((0,1),(len(self.clues(s,'A')),len(self.clues(s,'B'))))
        self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),(len(self.clues(s,'A')),len(self.clues(s,'B'))))
        for card in targets.values():
            self.assertEqual((2,2),tuple(s.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
        self.activate(s,'A',self.clues(s,'A')[0])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(0,len(self.clues(s,'A')))
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(targets['A'].object_id,k) for k in ('power','toughness')))
        self.replay(s,load=True)

    def test_actual_bobblehead_pays_five_and_tap_then_draws_only_own_public_subtypes(self):
        s = self.session(205608820)
        source = self.add(s.engine,'Intelligence Bobblehead',ref='active-bobblehead')
        self.add(s.engine,'Strength Bobblehead')
        self.add(s.engine,'Charisma Bobblehead',seat='B')
        self.add(s.engine,'Changeling Outcast')
        private = self.add(s.engine,'Intelligence Bobblehead',zone='hand',ref='private-bobblehead')
        self.assert_closed(s,source)
        self.prepare(s,mana={'C':5})
        self.checkpoint(s)
        hand = len(s.state.players['A'].zones['hand'])
        self.activate(s,'A',source)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+2,len(s.state.players['A'].zones['hand']))
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_ire_counts_instant_and_sorcery_arcane_cards_only_in_own_graveyard(self):
        s = self.session(205608830)
        spell = self.add(s.engine,'Ire of Kaminari',zone='hand')
        self.add(s.engine,'Peer Through Depths',zone='graveyard')
        self.add(s.engine,'Lava Spike',zone='graveyard',ref='own-arcane-sorcery')
        self.add(s.engine,'Lava Spike',seat='B',zone='graveyard',ref='other-arcane-sorcery')
        private = self.add(s.engine,'Peer Through Depths',zone='hand',ref='private-arcane-instant')
        self.assert_closed(s,spell)
        self.prepare(s,mana={'R':1,'C':3})
        self.checkpoint(s)
        self.cast(s,'A',spell,['B'])
        s = self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38,s.state.players['B'].life)
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_sanctum_reads_current_shrines_after_response_without_aggregate_gain(self):
        s = self.session(205608840)
        source = self.add(s.engine,'Sanctum of Stone Fangs')
        shrine = self.add(s.engine,'Go-Shintai of Hidden Cruelty')
        self.add(s.engine,'Honden of Cleansing Fire',seat='B')
        private = self.add(s.engine,'Honden of Infinite Rage',zone='hand')
        remove = self.add(s.engine,'Disenchant',seat='B',zone='hand')
        lands = [self.add(s.engine,'Generic Bound Plains',seat='B',ref='shrine-response-'+str(i)) for i in range(2)]
        self.assert_closed(s,source)
        self.prepare(s,mana={})
        e = s.engine
        e.permissions.invalidate_current()
        s.state.pending_decision = None
        s.state.priority_player = None
        s.state.priority_passes = []
        s.state.phase_index = TURN_STEPS.index(('beginning','upkeep'))
        e._enter_step()
        e.pump()
        self.checkpoint(s)
        self.until(s,lambda current:bool(current.state.stack) and current.state.phase=='precombat_main')
        self.priority(s,'B')
        for land in lands:
            actions = s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            offered = [row for row in actions if land.ref in row['id']]
            self.assertEqual(1,len(offered),offered)
            command = {'action_id':offered[0]['id'],'pay':'auto'}
            self.reject(s,'A',command)
            self.assertTrue(s.act('pilot:B',command).ok)
        self.cast(s,'B',remove,[shrine.ref])
        self.resolve_top(s)
        s = self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((41,39,39,39),tuple(s.state.players[p].life for p in 'ABCD'))
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)
