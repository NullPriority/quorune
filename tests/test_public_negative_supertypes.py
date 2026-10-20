"""Pinned CR 205.4/604.3/613: absent supertype differs from subtype or designation."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_scalar_mill_wording as controls


class PublicNegativeSupertypeCompilerTests(unittest.TestCase):
    def test_complete_public_phrases_reuse_versioned_supertype_exclusion_and_preserve_boundaries(self):
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
        from quorune.compiler.query_characteristic_templates import query_characteristic_quantity

        for text, kind, excluded in (
            ('Nonlegendary creatures you control','creature','legendary'),
            ('Nonbasic lands your opponents control','land','basic'),
        ):
            relation, query, other = fixed_characteristic_battlefield_query_subject(text)
            self.assertEqual('source_controller' if kind=='creature' else 'source_opponents',relation)
            self.assertFalse(other)
            self.assertEqual((kind,),query.types_all)
            self.assertEqual((excluded,),query.excluded_supertypes)
            self.assertIsNone(query.commander)
            self.assertIsNone(query.token)
            self.assertEqual(query,ObjectQuerySpec.from_dict(query.to_dict()))
        for text in ('Nonlegendary artifact creatures you control',
                'Nonbasic snow lands you control', 'Nonlegendary creatures of your choice'):
            self.assertIsNone(fixed_characteristic_battlefield_query_subject(text))
        relation, query, other = fixed_characteristic_battlefield_query_subject(
            'Other nonlegendary creatures you control that are modified')
        self.assertEqual('source_controller',relation)
        self.assertTrue(other)
        self.assertEqual(('legendary',),query.excluded_supertypes)
        self.assertTrue(query.state_predicate.modified)
        self.assertIsNone(query_characteristic_quantity('nonbasic lands in your hand',source_name='Generic Count'))
        self.assertIsNone(query_characteristic_quantity('nonbasic land cards in your graveyard',source_name='Generic Count'))


class PublicNegativeSupertypeRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ScalarMillWordingRuntimeTests.session
    add = controls.ScalarMillWordingRuntimeTests.add
    prepare = controls.ScalarMillWordingRuntimeTests.prepare
    cast = controls.ScalarMillWordingRuntimeTests.cast
    checkpoint = controls.ScalarMillWordingRuntimeTests.checkpoint
    resolve = controls.ScalarMillWordingRuntimeTests.resolve
    replay = controls.ScalarMillWordingRuntimeTests.replay
    until = controls.ScalarMillWordingRuntimeTests.until
    binding = controls.ScalarMillWordingRuntimeTests.binding
    resolve_spell = controls.ScalarMillWordingRuntimeTests.resolve_spell

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'negative-supertype.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-characteristic-enchant-cards.json',
            ROOT/'tests/fixtures/public-negative-supertype-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Negative supertype',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def private_and_replay(self,session,private):
        for viewer in 'ABD':
            self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        return self.replay(session,load=True)

    def test_actual_nonlegendary_anthem_keeps_legendary_branch_and_tracks_control_and_source_departure(self):
        session=self.session(71015001)
        source=self.add(session.engine,'Flowering of the White Tree',zone='hand')
        bear=self.add(session.engine,'Grizzly Bears',ref='negative-own-bear')
        legend=self.add(session.engine,'Isamaru, Hound of Konda')
        enemy=self.add(session.engine,'Grizzly Bears',seat='B',ref='negative-foreign-bear')
        control=self.add(session.engine,'Threads of Disloyalty',zone='hand')
        remove=self.add(session.engine,'Disenchant',zone='hand',ref='negative-remove-anthem')
        keyword=self.add(session.engine,'Generic Nonlegendary Vigilance',zone='hand')
        remove_keyword=self.add(session.engine,'Disenchant',zone='hand',ref='negative-remove-keyword')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':5,'U':5,'C':8})
        self.checkpoint(session)
        for card in (source,control,remove,keyword):
            self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':'cast:'+source.ref,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',source)
        session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((3,3),tuple(session.engine._numeric_stat(bear.object_id,x) for x in ('power','toughness')))
        self.assertEqual((4,3),tuple(session.engine._numeric_stat(legend.object_id,x) for x in ('power','toughness')))
        self.assertIn('ward {1}',session.engine._combat_keywords(session.state.cards[legend.object_id]))
        self.assertEqual((2,2),tuple(session.engine._numeric_stat(enemy.object_id,x) for x in ('power','toughness')))
        self.cast(session,'A',keyword)
        self.assertIsNone(self.resolve(session))
        self.assertIn('vigilance',session.engine._combat_keywords(session.state.cards[bear.object_id]))
        for card in (legend,enemy):
            self.assertNotIn('vigilance',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.cast(session,'A',control,[enemy.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('A',session.state.cards[enemy.object_id].controller)
        self.assertEqual((3,3),tuple(session.engine._numeric_stat(enemy.object_id,x) for x in ('power','toughness')))
        self.assertIn('vigilance',session.engine._combat_keywords(session.state.cards[enemy.object_id]))
        self.cast(session,'A',remove,[source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        for card in (bear,legend,enemy):
            self.assertEqual((2,2),tuple(session.engine._numeric_stat(card.object_id,x) for x in ('power','toughness')))
        self.assertNotIn('ward {1}',session.engine._combat_keywords(session.state.cards[legend.object_id]))
        self.cast(session,'A',remove_keyword,[keyword.ref])
        self.assertIsNone(self.resolve(session))
        for card in (bear,legend,enemy):
            self.assertNotIn('vigilance',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.private_and_replay(session,private)

    def test_actual_public_nonbasic_mill_reads_zero_after_real_sacrifice_and_excludes_basic_forest(self):
        session=self.session(71015003)
        first=self.add(session.engine,'Generic Public Nonbasic Mill',zone='hand',ref='negative-public-first')
        source=self.add(session.engine,'Generic Public Nonbasic Mill',zone='hand',ref='negative-public-responsive')
        taiga=self.add(session.engine,'Taiga')
        self.add(session.engine,'Forest')
        orb=self.add(session.engine,'Zuran Orb')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':3,'C':5})
        self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        libraries={seat:len(player.zones['library']) for seat,player in session.state.players.items()}
        self.cast(session,'A',first)
        self.assertIsNone(self.resolve(session))
        for seat,player in session.state.players.items():
            self.assertEqual(libraries[seat]-(seat=='A'),len(player.zones['library']),seat)
        libraries={seat:len(player.zones['library']) for seat,player in session.state.players.items()}
        self.cast(session,'A',source)
        self.resolve_spell(session,source)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+orb.ref+':'))
        command={'action_id':offered['id'],'cost_cards':[taiga.ref]}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',command)
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[taiga.object_id].zone)
        self.assertEqual(42,session.state.players['A'].life)
        for seat,player in session.state.players.items():
            self.assertEqual(libraries[seat],len(player.zones['library']),seat)
        self.private_and_replay(session,private)

    def test_actual_nonbasic_count_distinguishes_land_types_and_keeps_cda_in_hand_after_real_responses(self):
        session=self.session(71015002)
        source=self.add(session.engine,'Wilderness Elemental',zone='hand')
        taiga=self.add(session.engine,'Taiga',seat='B')
        second_land=self.add(session.engine,'Tree of Tales',seat='C')
        self.add(session.engine,'Seat of the Synod',seat='A')
        self.add(session.engine,'Snow-Covered Forest',seat='D')
        moon=self.add(session.engine,'Blood Moon',zone='hand')
        destroy=self.add(session.engine,'Stone Rain',zone='hand',ref='negative-first-rain')
        last_destroy=self.add(session.engine,'Stone Rain',zone='hand',ref='negative-second-rain')
        bounce=self.add(session.engine,'Unsummon',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':5,'G':4,'U':3,'C':12})
        self.checkpoint(session)
        for card in (source,moon,destroy,bounce,taiga):
            self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',moon)
        self.assertIsNone(self.resolve(session))
        changed_type=session.engine._effective_card_data(session.state.cards[taiga.object_id])['type_line'].casefold()
        self.assertIn('mountain',changed_type)
        self.assertNotIn('forest',changed_type)
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',destroy,[taiga.ref])
        session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[taiga.object_id].zone)
        self.assertEqual(1,session.engine._numeric_stat(source.object_id,'power'))
        identity=session.state.cards[source.object_id].logical_object_id
        self.cast(session,'A',bounce,[source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertNotEqual(identity,session.state.cards[source.object_id].logical_object_id)
        self.assertEqual(1,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',last_destroy,[second_land.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[second_land.object_id].zone)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertEqual(0,session.engine._numeric_stat(source.object_id,'power'))
        self.private_and_replay(session,private)

    def test_negative_supertype_producer_omission_is_killed_by_fresh_native_cases(self):
        with patch('quorune.compiler.public_state_queries._negative_supertype_fields',return_value=None):
            for method in (
                'test_actual_nonlegendary_anthem_keeps_legendary_branch_and_tracks_control_and_source_departure',
                'test_actual_nonbasic_count_distinguishes_land_types_and_keeps_cda_in_hand_after_real_responses',
                'test_actual_public_nonbasic_mill_reads_zero_after_real_sacrifice_and_excludes_basic_forest',
            ):
                with self.assertRaises(AssertionError):
                    getattr(type(self)(method),method)()
