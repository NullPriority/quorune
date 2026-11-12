"""CR 611.2c and 702.96: fixed singular grants lock one resolved set."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_public_characteristic_sets import fixed_public_characteristic_set_effect_template
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_fixed_overload as overload
import test_singular_public_permanent_sets as public_sets


class SingularCharacteristicCompilerTests(unittest.TestCase):
    record=overload.FixedOverloadModelCompilerTests.record

    @classmethod
    def setUpClass(cls): cls.registry=load_default_capability_registry()

    def test_singular_coordinated_grants_preserve_the_existing_plural_effect_and_query(self):
        for source,plural in (
            ('Each creature you control gets +0/+1 and gains hexproof until end of turn.',
                'Creatures you control get +0/+1 and gain hexproof until end of turn.'),
            ('Each creature you control gains first strike and gets +1/+0 until end of turn.',
                'Creatures you control get +1/+0 and gain first strike until end of turn.'),
            ("Each creature you don't control gets -4/-0 until end of turn.",
                'Creatures your opponents control get -4/-0 until end of turn.'),
            ("Each attacking creature you don't control gets -2/-1 until end of turn.",
                'Attacking creatures your opponents control get -2/-1 until end of turn.'),
        ):
            with self.subTest(source=source):
                expected=fixed_public_characteristic_set_effect_template(plural)
                actual=fixed_public_characteristic_set_effect_template(source)
                self.assertIsNotNone(expected); self.assertIsNotNone(actual)
                self.assertEqual(expected[1:3],actual[1:3])
                self.assertEqual(set(expected[3]),set(actual[3]))

    def test_full_overload_bodies_close_without_widening_protection_or_unknown_qualifiers(self):
        for body in ('Target creature you control gets +0/+1 and gains hexproof until end of turn.',
                'Target creature you control gets +1/+0 and gains first strike until end of turn.',
                "Target creature you don't control gets -4/-0 until end of turn."):
            ir=compile_oracle_card(self.record(body=body),capability_registry=self.registry,
                capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            normal,paid=ir.faces[0].nodes
            self.assertIsNotNone(normal.target_schema); self.assertIsNone(paid.target_schema)
            self.assertEqual(normal.span,paid.span)
        for text in ('Each chosen creature gets +1/+1 until end of turn.',
                'Each creature you control gains protection from each color until end of turn.',
                'Each creature you control gets +1/+1 and gains an unrepresented ability until end of turn.',
                "Each creature you don't control gets -4/-0 until your next turn."):
            self.assertIsNone(fixed_public_characteristic_set_effect_template(text))

    def test_missing_public_query_owner_cannot_satisfy_the_whole_pair_contract(self):
        record=self.record(body="Target creature you don't control gets -4/-0 until end of turn.")
        with patch('quorune.compiler.fixed_public_characteristic_sets._public_query',return_value=None):
            ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
            with self.assertRaises(AssertionError): self.assertEqual('exact',ir.status)


class SingularCharacteristicNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=public_sets.SingularPublicSetNativeTests.reject
    cast_variant=public_sets.SingularPublicSetNativeTests.cast_variant
    finish=public_sets.SingularPublicSetNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(); path=Path(cls.temporary.name)/'singular-characteristics.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains']; base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json'; basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/singular-characteristic-set-cards.json'],path)
        cls.db=CardDatabase(path); cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Singular characteristic sets',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def cleanup(self,session):
        turn=session.state.turn_sequence
        self.until(session,lambda s:s.state.turn_sequence>turn)

    def test_actual_mizzium_variants_protect_only_the_resolved_controlled_set_until_cleanup(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                session=self.session(998001+paid); source=self.add(session.engine,'Mizzium Skin',zone='hand')
                first=self.add(session.engine,'Grizzly Bears'); second=self.add(session.engine,'Runeclaw Bear')
                enemy=self.add(session.engine,'Grizzly Bears',seat='B',ref='singular-mizzium-opponent')
                bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand'); self.add(session.engine,'Island',seat='B')
                later=self.add(session.engine,'Ashcoat Bear',zone='hand')
                self.add(session.engine,'Forest'); self.add(session.engine,'Wastes')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-mizzium-private-card')
                self.assert_closed(session,source); self.prepare(session,mana={'C':1,'U':1} if paid else {'U':1}); self.checkpoint(session)
                self.cast(session,'B',bounce,targets=[first.ref]); self.priority(session,'A')
                self.reject(session,'A',{'action_id':'cast:'+source.ref,'cost_option':'normal','targets':[enemy.ref],'pay':'auto'})
                self.reject(session,'A',{'action_id':'cast:'+source.ref,'cost_option':'overload','targets':[first.ref],'pay':'auto'})
                self.cast_variant(session,source,'overload' if paid else 'normal',[] if paid else [first.ref])
                session=self.replay(session,load=True); self.resolve_top(session)
                for card,affected in ((first,True),(second,paid),(enemy,False)):
                    self.assertEqual(3 if affected else 2,session.engine._numeric_stat(card.object_id,'toughness'))
                    self.assertEqual(affected,'hexproof' in session.engine._combat_keywords(session.state.cards[card.object_id]))
                self.assertIsNone(self.resolve(session)); self.assertEqual('battlefield',session.state.cards[first.object_id].zone)
                self.assertEqual('graveyard',session.state.cards[bounce.object_id].zone)
                self.cast(session,'A',later); self.assertIsNone(self.resolve(session))
                self.assertEqual(2,session.engine._numeric_stat(later.object_id,'toughness'))
                self.assertNotIn('hexproof',session.engine._combat_keywords(session.state.cards[later.object_id]))
                self.cleanup(session)
                for card in (first,second):
                    self.assertEqual(2,session.engine._numeric_stat(card.object_id,'toughness'))
                    self.assertNotIn('hexproof',session.engine._combat_keywords(session.state.cards[card.object_id]))
                self.finish(session,private)

    def test_actual_weapon_surge_variants_lock_stats_and_keyword_together_then_expire(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                session=self.session(998010+paid); source=self.add(session.engine,'Weapon Surge',zone='hand')
                first=self.add(session.engine,'Grizzly Bears'); second=self.add(session.engine,'Runeclaw Bear')
                enemy=self.add(session.engine,'Grizzly Bears',seat='B',ref='singular-surge-opponent')
                later=self.add(session.engine,'Ashcoat Bear',zone='hand')
                self.add(session.engine,'Forest'); self.add(session.engine,'Wastes')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-surge-private-card')
                self.assert_closed(session,source); self.prepare(session,mana={'C':1,'R':1} if paid else {'R':1}); self.checkpoint(session)
                self.reject(session,'A',{'action_id':'cast:'+source.ref,'cost_option':'normal','targets':[enemy.ref],'pay':'auto'})
                self.cast_variant(session,source,'overload' if paid else 'normal',[] if paid else [first.ref])
                session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
                for card,affected in ((first,True),(second,paid),(enemy,False)):
                    self.assertEqual(3 if affected else 2,session.engine._numeric_stat(card.object_id,'power'))
                    self.assertEqual(affected,'first strike' in session.engine._combat_keywords(session.state.cards[card.object_id]))
                self.cast(session,'A',later); self.assertIsNone(self.resolve(session))
                self.assertEqual(2,session.engine._numeric_stat(later.object_id,'power'))
                self.assertNotIn('first strike',session.engine._combat_keywords(session.state.cards[later.object_id]))
                self.cleanup(session)
                for card in (first,second):
                    self.assertEqual(2,session.engine._numeric_stat(card.object_id,'power'))
                    self.assertNotIn('first strike',session.engine._combat_keywords(session.state.cards[card.object_id]))
                self.finish(session,private)

    def test_actual_downsize_variants_select_other_controllers_and_include_later_entry(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                session=self.session(998020+paid); source=self.add(session.engine,'Downsize',zone='hand')
                own=self.add(session.engine,'Grizzly Bears',ref='singular-downsize-own')
                first=self.add(session.engine,'Grizzly Bears',seat='B',ref='singular-downsize-second-seat')
                second=self.add(session.engine,'Runeclaw Bear',seat='C')
                protected=self.add(session.engine,'Gladecover Scout',seat='D')
                later=self.add(session.engine,'Ashcoat Bear',seat='D',zone='hand')
                self.add(session.engine,'Forest',seat='D'); self.add(session.engine,'Wastes',seat='D')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-downsize-private-card')
                self.assert_closed(session,source); self.prepare(session,mana={'C':2,'U':1} if paid else {'U':1}); self.checkpoint(session)
                for invalid in (own,protected):
                    self.reject(session,'A',{'action_id':'cast:'+source.ref,'cost_option':'normal','targets':[invalid.ref],'pay':'auto'})
                self.cast_variant(session,source,'overload' if paid else 'normal',[] if paid else [first.ref])
                self.cast(session,'D',later); self.resolve_top(session)
                session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
                self.assertEqual(2,session.engine._numeric_stat(own.object_id,'power'))
                for card,base,affected in ((first,2,True),(second,2,paid),(protected,1,paid),(later,2,paid)):
                    self.assertEqual(base-4 if affected else base,session.engine._numeric_stat(card.object_id,'power'))
                self.cleanup(session)
                for card,base in ((first,2),(second,2),(protected,1),(later,2)):
                    self.assertEqual(base,session.engine._numeric_stat(card.object_id,'power'))
                self.finish(session,private)

    def test_actual_kodama_cast_trigger_excludes_source_and_spell_then_locks_each_grant(self):
        session=self.session(998030); source=self.add(session.engine,'Kodama of the South Tree')
        own=self.add(session.engine,'Grizzly Bears')
        opposing=self.add(session.engine,'Runeclaw Bear',seat='B')
        ordinary=self.add(session.engine,'Ornithopter',zone='hand')
        spirit=self.add(session.engine,'Lantern Kami',zone='hand')
        arcane=self.add(session.engine,'Reach Through Mists',zone='hand',ref='singular-kodama-own-arcane')
        foreign=self.add(session.engine,'Reach Through Mists',seat='B',zone='hand',ref='singular-kodama-foreign-arcane')
        self.add(session.engine,'Island',seat='B')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-kodama-private-card')
        self.assert_closed(session,source); self.prepare(session,mana={'W':1,'U':1}); self.checkpoint(session)
        self.cast(session,'A',ordinary); self.assertEqual(0,self.trigger_count(session,source)); self.assertIsNone(self.resolve(session))
        self.cast(session,'B',foreign); self.assertEqual(0,self.trigger_count(session,source)); self.assertIsNone(self.resolve(session))
        self.cast(session,'A',spirit); self.assertEqual(1,self.trigger_count(session,source))
        session=self.replay(session,load=True); self.resolve_top(session)
        self.assertEqual('stack',session.state.cards[spirit.object_id].zone)
        self.assertEqual(3,session.engine._numeric_stat(own.object_id,'power'))
        self.assertEqual(1,session.engine._numeric_stat(ordinary.object_id,'power'))
        self.assertIsNone(self.resolve(session)); self.assertEqual(1,session.engine._numeric_stat(spirit.object_id,'power'))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[spirit.object_id]))
        self.cast(session,'A',arcane); self.assertEqual(1,self.trigger_count(session,source)); self.resolve_top(session)
        for card,power in ((own,4),(ordinary,2),(spirit,2)):
            self.assertEqual(power,session.engine._numeric_stat(card.object_id,'power'))
            self.assertIn('trample',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertEqual(2,session.engine._numeric_stat(opposing.object_id,'power'))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[opposing.object_id]))
        self.assertTrue(any(item.card_object_id==arcane.object_id for item in session.state.stack))
        self.assertIsNone(self.resolve(session)); self.finish(session,private)
