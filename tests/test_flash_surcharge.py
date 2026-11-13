"""A timing permission never substitutes for its additional mana payment."""
from types import SimpleNamespace
from pathlib import Path
import json,tempfile
import unittest
from unittest.mock import patch
from quorune.cast_timing import (
    ManaSurchargeFlashSpec,CastTimingPermissionError,SURCHARGE_FLASH_HANDLER,
    SURCHARGE_FLASH_OPTION_FIELD,
)
from quorune.semantic_runtime.surcharge_flash import ManaSurchargeFlashPermissionHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.rules.casting.surcharge_flash_costs import mana_surcharge_flash_variants
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class FlashSurchargeModelTests(unittest.TestCase):
    def test_cost_spec_and_permission_component_are_closed_and_do_not_grant_a_keyword(self):
        spec=ManaSurchargeFlashSpec(2);self.assertEqual(spec,ManaSurchargeFlashSpec.from_dict(spec.to_dict()))
        for value in (0,-1,True,'2',None):
            with self.subTest(value=value),self.assertRaises(CastTimingPermissionError):ManaSurchargeFlashSpec(value)
        for changed in ({'schema_version':True,'generic':2},{'schema_version':1,'generic':2,'extra':0}):
            with self.assertRaises(CastTimingPermissionError):ManaSurchargeFlashSpec.from_dict(changed)
        handler=ManaSurchargeFlashPermissionHandler()
        descriptor={'handler_id':SURCHARGE_FLASH_HANDLER,'schema_version':1,'event':'cast.permission','surcharge':spec.to_dict()}
        self.assertEqual(spec,handler.validate(descriptor));permission,=handler.lower(descriptor,None)
        self.assertEqual({'schema_version':1,'timing':'instant','scope':'this_face'},permission.to_dict())
        for changed in ({**descriptor,'schema_version':True},{**descriptor,'event':'characteristics.evaluate'},
                        {**descriptor,'permission':permission.to_dict()}):
            with self.assertRaises(SemanticNodeError):handler.validate(changed)

    def test_only_the_paid_branch_survives_without_independent_timing_and_server_override_is_explicit(self):
        class Host:
            state=SimpleNamespace(active_player='B',phase='precombat_main',stack=[],config=SimpleNamespace(strict_timing=True))
            def card_record(self,card):return SimpleNamespace(type_line='Creature')
            def _select_cast_face(self,record,face):return None
            def _mana_vector(self,value):return {'GENERIC':0,**dict(value)}
        host=Host();card=SimpleNamespace(active_face='front');base=[{'id':'printed','kind':'normal','requirements':{'GENERIC':3,'G':1}}]
        with patch('quorune.rules.casting.surcharge_flash_costs.compiled_mana_surcharge_flash_spec',return_value=ManaSurchargeFlashSpec(2)),patch(
                'quorune.rules.casting.proposal._combined_cast_timing_permissions',return_value=()):
            paid=mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=False)
            self.assertEqual(1,len(paid));self.assertEqual(5,paid[0]['requirements']['GENERIC'])
            self.assertEqual({'schema_version':1,'generic':2},paid[0][SURCHARGE_FLASH_OPTION_FIELD])
            self.assertEqual(3,base[0]['requirements']['GENERIC'])
            override=mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=False,ignore_timing=True)
            self.assertEqual(2,len(override));self.assertEqual('printed',override[0]['id'])
            host.state.active_player='A'
            ordinary=mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=False)
            self.assertEqual(2,len(ordinary));self.assertNotIn(SURCHARGE_FLASH_OPTION_FIELD,ordinary[0])
        self.assertIs(base,mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=True))


class FlashSurchargeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'flash-surcharge.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/flash-surcharge-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Surcharge timing',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def offers(self,s):
        return s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']

    def scene(self,forests,*,independent=False):
        s=self.session(1003100+forests+10*independent)
        source=self.add(s.engine,'Oakshade Stalker // Moonlit Ambusher',zone='hand')
        for index in range(forests):self.add(s.engine,'Forest',ref='surcharge-forest-'+str(index))
        if independent:self.add(s.engine,'Vedalken Orrery')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='surcharge-private-card')
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s);self.checkpoint(s)
        return s,source,private

    def opposing_turn(self,s):
        row=next(row for row in self.offers(s) if row.get('action')=='play_land')
        result=s.act('pilot:A',{'action_id':row['id']});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda current:current.state.active_player=='B' and current.state.phase=='precombat_main'
                   and current.state.pending_decision is not None and current.state.pending_decision.kind=='priority')
        self.priority(s,'A')

    def test_actual_ordinary_price_is_legal_but_opposing_turn_requires_the_additional_mana(self):
        for opposing in (False,True):
            with self.subTest(opposing=opposing):
                s,source,private=self.scene(4 if opposing else 3)
                original=next(row for row in self.offers(s) if row['id']=='cast:'+source.ref)
                base_id=original['cost_options'][0]['id']
                if opposing:self.opposing_turn(s)
                offered=next(row for row in self.offers(s) if row['id']=='cast:'+source.ref)
                options=offered['cost_options'];self.assertTrue(options)
                chosen=options[0]['id']
                if opposing:
                    self.assertNotEqual(base_id,chosen)
                    self.reject(s,'A',{'action_id':offered['id'],'cost_option':base_id,'pay':'auto','ignore_timing':True})
                command={'action_id':offered['id'],'cost_option':chosen,'pay':'auto'}
                self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual(3,s.engine._effective_card_data(s.state.cards[source.object_id])['mana_value'])
                self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'));self.finish(s,private)

    def test_actual_underpayment_is_unavailable_and_independent_timing_keeps_the_ordinary_branch(self):
        for independent in (False,True):
            with self.subTest(independent=independent):
                s,source,private=self.scene(3,independent=independent);self.opposing_turn(s)
                offered=next((row for row in self.offers(s) if row['id']=='cast:'+source.ref),None)
                if not independent:
                    self.assertIsNone(offered)
                    self.reject(s,'A',{'action_id':'cast:'+source.ref,'pay':'auto','ignore_timing':True})
                else:
                    self.assertIsNotNone(offered)
                    option=next(row for row in offered['cost_options'] if 'instant-surcharge' not in row['id'])
                    result=s.act('pilot:A',{'action_id':offered['id'],'cost_option':option['id'],'pay':'auto'})
                    self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.finish(s,private)
