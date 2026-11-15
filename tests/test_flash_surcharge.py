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
from quorune.card_programs.binding import bind_semantic_program_runtime
from dataclasses import replace
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class FlashSurchargeCompilerTests(unittest.TestCase):
    text='You may cast this spell as though it had flash if you pay {2} more to cast it.'

    def test_complete_price_clause_has_one_face_bound_permission_and_preserves_the_source_span(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.cast_timing import SURCHARGE_FLASH_CAPABILITY
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for generic in (2,4):
            text=self.text.replace('{2}','{'+str(generic)+'}')
            ir=compile_oracle_card(record(text,'Creature'),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes;descriptor,=node.handlers
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))
            self.assertEqual(text,node.text);self.assertEqual('playable',node.active_zone)
            self.assertIn(SURCHARGE_FLASH_CAPABILITY,node.capability_dependencies)
            self.assertEqual({'schema_version':1,'generic':generic},descriptor['surcharge'])
            self.assertEqual(SURCHARGE_FLASH_HANDLER,descriptor['handler_id'])

    def test_unknown_prices_siblings_and_missing_producer_or_dependency_remain_incomplete(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.cast_timing import SURCHARGE_FLASH_CAPABILITY
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for text in (self.text.replace('{2}','{U}'),self.text.replace('{2}','{X}'),
                     self.text.replace('{2}','{0}'),self.text+' Rewrite history.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),capability_registry=registry).status)
        with patch('quorune.compiler.surcharge_flash_nodes.surcharge_flash_node',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(self.text,'Creature'),capability_registry=registry).status)
        for missing in (SURCHARGE_FLASH_CAPABILITY,'timing.cast.printed_flash'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(row for row in raw['capabilities'] if row['id']==missing).update(status='blocked',blockers=['Surcharge dependency withdrawn'])
            self.assertNotEqual('exact',compile_oracle_card(record(self.text,'Creature'),capability_registry=CapabilityRegistry(raw)).status)


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

    def scene(self,forests,*,independent=False,night=False):
        s=self.session(1003100+forests+10*independent)
        source=self.add(s.engine,'Oakshade Stalker // Moonlit Ambusher',zone='hand')
        for index in range(forests):self.add(s.engine,'Forest',ref='surcharge-forest-'+str(index))
        if independent:self.add(s.engine,'Vedalken Orrery')
        if night:
            from quorune.day_night import set_day_night_designation
            set_day_night_designation(s.engine,'night',reason='surcharge entry-face fixture')
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

    def test_runtime_permission_minimum_and_missing_price_contribution_cannot_admit_free_timing(self):
        from quorune.cast_timing import SURCHARGE_FLASH_CAPABILITY
        from quorune.rules.casting.proposal import build_cast_offer
        s,source,private=self.scene(3)
        program=next(program for program in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(row.get('handler_id')==SURCHARGE_FLASH_HANDLER for row in program.handlers))
        deps=tuple(value for value in program.capability_dependencies if value!=SURCHARGE_FLASH_CAPABILITY)
        for coverage in (program.coverage,{}):
            changed=replace(program,coverage=coverage,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+SURCHARGE_FLASH_CAPABILITY,bound['blockers'])
        self.opposing_turn(s)
        def witness():self.assertIsNone(build_cast_offer(s.engine,'A',s.state.cards[source.object_id]).offer)
        witness()
        def omit(host,seat,card,options,**kwargs):return options
        with patch('quorune.rules.casting.surcharge_flash_costs.mana_surcharge_flash_variants',side_effect=omit):
            with self.assertRaises(AssertionError):witness()
        self.finish(s,private)

    def test_actual_surcharge_can_cast_the_front_at_night_and_existing_owner_enters_the_back(self):
        s,source,private=self.scene(4,night=True);self.opposing_turn(s)
        offered=next(row for row in self.offers(s) if row['id']=='cast:'+source.ref)
        result=s.act('pilot:A',{'action_id':offered['id'],'cost_option':offered['cost_options'][0]['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        current=s.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone);self.assertEqual('night',s.state.day_night)
        self.assertIn('Moonlit Ambusher',s.engine._effective_card_data(current)['name'])
        self.assertEqual(6,s.engine._numeric_stat(source.object_id,'power'));self.finish(s,private)

    def test_actual_announced_x_adds_surcharge_before_reduction_without_flash_keyword_or_uncounterability(self):
        for counter in (False,True):
            with self.subTest(counter=counter):
                s=self.session(1003140+counter);source=self.add(s.engine,'Ghitu Fire',zone='hand')
                self.add(s.engine,'Goblin Electromancer');self.add(s.engine,'Cunning Nightbonder')
                for index in range(4):self.add(s.engine,'Mountain',ref='surcharge-mountain-'+str(index))
                answer=self.add(s.engine,'Counterspell',seat='B',zone='hand',ref='surcharge-counter-answer')
                for index in range(2):self.add(s.engine,'Island',seat='B',ref='surcharge-B-island-'+str(index))
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='surcharge-x-private-card')
                s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
                self.prepare(s);self.checkpoint(s);self.opposing_turn(s)
                offered=next(row for row in self.offers(s) if row['id']=='cast:'+source.ref)
                paid=next(row for row in offered['cost_options'] if 'instant-surcharge' in row['id'])
                captured=[];dispatch=s.engine._dispatch_semantic_event
                def observe(event,context=None,**values):
                    if event=='spell.cast' and context and context.get('card')==source.ref:captured.append(dict(context))
                    return dispatch(event,context,**values)
                with patch.object(s.engine,'_dispatch_semantic_event',side_effect=observe):
                    result=s.act('pilot:A',{'action_id':offered['id'],'cost_option':paid['id'],'x':3,'targets':['B'],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);card=s.state.cards[source.object_id]
                data=s.engine._effective_card_data(card)
                self.assertEqual(1,len(captured));self.assertEqual(4,captured[0]['mana_value'])
                self.assertNotIn('flash',{str(value).casefold() for value in data.get('keywords',())})
                spell_item=next(item for item in s.state.stack if item.card_object_id==source.object_id)
                self.assertEqual(5,spell_item.context['mana_spent_total'])
                self.assertEqual(5,sum(s.state.cards[oid].tapped for oid in s.state.players['A'].zones['battlefield']
                    if 'land' in str(s.engine._effective_card_data(s.state.cards[oid])['type_line']).casefold()))
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                if counter:self.cast(s,'B',answer,[spell_item.ref])
                self.assertIsNone(self.resolve(s));self.assertEqual(40 if counter else 37,s.state.players['B'].life)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_opposing_turn_surcharge_preserves_creature_count_tokens_and_mass_destruction(self):
        for name in ('Saproling Symbiosis','Rout'):
            with self.subTest(name=name):
                s=self.session(1003160+(name=='Rout'));source=self.add(s.engine,name,zone='hand')
                own=self.add(s.engine,'Grizzly Bears',ref='surcharge-own-body')
                borrowed=self.add(s.engine,'Grizzly Bears',ref='surcharge-borrowed-body');borrowed.owner='D'
                opposing=self.add(s.engine,'Grizzly Bears',seat='B',ref='surcharge-opposing-body')
                for index in range(6 if name=='Rout' else 5):
                    self.add(s.engine,'Generic Bound Plains' if name=='Rout' else 'Forest',ref='surcharge-body-land-'+str(index))
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='surcharge-body-private')
                s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
                self.prepare(s);self.checkpoint(s);self.opposing_turn(s)
                offered=next(row for row in self.offers(s) if row['id']=='cast:'+source.ref)
                paid=next(row for row in offered['cost_options'] if 'instant-surcharge' in row['id'])
                result=s.act('pilot:A',{'action_id':offered['id'],'cost_option':paid['id'],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                if name=='Rout':
                    self.assertIn(own.object_id,s.state.players['A'].zones['graveyard'])
                    self.assertIn(borrowed.object_id,s.state.players['D'].zones['graveyard'])
                    self.assertIn(opposing.object_id,s.state.players['B'].zones['graveyard'])
                else:
                    tokens=[card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield']
                    self.assertEqual(2,len(tokens))
                    for token in tokens:
                        self.assertEqual('A',token.controller);self.assertEqual('A',token.owner)
                        self.assertIn('Saproling',s.engine._effective_card_data(token)['type_line'])
                        self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_paid_flash_entry_keeps_target_controller_owner_and_linked_return_roles(self):
        for name in ('Harbinger of the Tides','Mystical Tether'):
            with self.subTest(name=name):
                s=self.session(1003180+(name=='Mystical Tether'));source=self.add(s.engine,name,zone='hand')
                target=self.add(s.engine,'Grizzly Bears',seat='C');target.owner='D';target.tapped=True
                for index in range(4 if name=='Mystical Tether' else 3):
                    self.add(s.engine,'Generic Bound Plains' if name=='Mystical Tether' else 'Island',ref='surcharge-target-land-'+str(index))
                answer=self.add(s.engine,'Disenchant',seat='B',zone='hand')
                for index in range(2):self.add(s.engine,'Generic Bound Plains',seat='B',ref='surcharge-B-answer-land-'+str(index))
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='surcharge-target-private')
                s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
                self.prepare(s);self.checkpoint(s);self.opposing_turn(s)
                offered=next(row for row in self.offers(s) if row['id']=='cast:'+source.ref)
                paid=next(row for row in offered['cost_options'] if 'instant-surcharge' in row['id'])
                result=s.act('pilot:A',{'action_id':offered['id'],'cost_option':paid['id'],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);decision=self.resolve(s)
                if decision is not None and decision.kind=='semantic.target':
                    command={'action_id':'choose','targets':[target.ref]};self.reject(s,'D',command)
                    result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);decision=self.resolve(s)
                if name=='Harbinger of the Tides' and decision is not None:
                    self.assertEqual('semantic.choice',decision.kind)
                    result=s.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                if name=='Harbinger of the Tides':self.assertIn(target.object_id,s.state.players['D'].zones['hand'])
                else:
                    self.assertIn(target.object_id,s.state.players['D'].zones['exile'])
                    self.cast(s,'B',answer,[s.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(s))
                    self.assertIn(target.object_id,s.state.players['D'].zones['battlefield'])
                    self.assertEqual('D',s.state.cards[target.object_id].controller)
                self.finish(s,private)

    def test_actual_cascade_resolution_uses_its_server_timing_authority_without_the_surcharge(self):
        s=self.session(1003190);source=self.add(s.engine,'Violent Outburst',zone='hand')
        hit=self.add(s.engine,'Harbinger of the Tides',zone='library',ref='surcharge-cascade-hit')
        library=s.state.players['A'].zones['library'];library[:]=[oid for oid in library if oid!=hit.object_id]+[hit.object_id]
        self.add(s.engine,'Forest');self.add(s.engine,'Mountain')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='surcharge-cascade-private')
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source);self.assert_closed(s,hit)
        self.prepare(s);self.checkpoint(s);self.opposing_turn(s);self.cast(s,'A',source)
        decision=self.resolve(s);self.assertEqual('selection.exile_cast',decision.kind)
        self.assertTrue(all(s.state.cards[oid].tapped for oid in s.state.players['A'].zones['battlefield']))
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.reject(s,'D',{'action_id':'cast'});result=s.act('pilot:A',{'action_id':'cast'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[hit.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)
