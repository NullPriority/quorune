"""CR 111.3, 201.5a, 301.5 and 702.6a: intrinsic Equipment token programs."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_fixed_token_creation_effects import token_record
import test_target_recipient_results as recipients


MABEL = 'When this creature enters, create Cragflame, a legendary colorless Equipment artifact token with "Equipped creature gets +1/+1 and has vigilance, trample, and haste" and equip {2}.'
ICING = 'When this creature dies, create Icingdeath, Frost Tongue, a legendary white Equipment artifact token with "Equipped creature gets +2/+0," "Whenever equipped creature attacks, tap target creature defending player controls," and equip {2}.'
ROCK = '''Whenever a land you control enters, create a colorless Equipment artifact token named Rock with "Equipped creature has '{1}, {T}, Sacrifice Rock: This creature deals 2 damage to any target'" and equip {1}.'''


class NamedEquipmentCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text):
        return compile_oracle_card(token_record('Generic Equipment Maker', text, 111301,
            type_line='Creature — Mouse Soldier'), capability_registry=self.registry,
            capability_profile='commander_review')

    def test_complete_static_triggered_and_nested_granted_definitions_preserve_equip(self):
        for text, name, count, color, legendary in ((MABEL,'Cragflame',2,[],True),
                (ICING,'Icingdeath, Frost Tongue',4,['W'],True),(ROCK,'Rock',2,[],False)):
            with self.subTest(name=name):
                ir=self.compile(text)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                nodes=ir.faces[0].nodes
                creation=next(e for n in nodes for e in n.effects if e.get('op')=='create_token')
                self.assertEqual(name,creation['name'])
                self.assertEqual(color,creation['characteristics']['colors'])
                self.assertEqual(legendary,'Legendary' in creation['characteristics']['type_line'])
                self.assertNotIn('power',creation['characteristics'])
                self.assertNotIn('toughness',creation['characteristics'])
                self.assertEqual(count,len(creation['characteristics']['ability_fragments']))
                equip=next(n for n in nodes if n.kind=='granted_activated_ability' and n.effects[0]['op']=='attach')
                self.assertEqual('you',equip.target_schema['controller'])
                self.assertTrue(equip.cost)

    def test_variable_missing_or_unrepresented_abilities_remain_residual(self):
        for text in (MABEL.replace('equip {2}','equip {X}'),MABEL.replace(' and equip {2}',''),
                MABEL.replace('haste"','haste. Invent a rule"'),
                MABEL.replace('Equipment artifact token','Equipment artifact creature token'),
                ROCK.replace('Sacrifice Rock','Sacrifice another artifact')):
            with self.subTest(text=text):
                self.assertNotEqual('exact',self.compile(text).status)

    def test_blueprint_shape_rejects_hidden_fields_stats_and_unbound_components(self):
        from copy import deepcopy
        from quorune.compiler.named_equipment_token_templates import fixed_named_equipment_token_template
        from quorune.rules.equipment_token_shapes import fixed_equipment_token_closure
        value=fixed_named_equipment_token_template('Create a colorless Equipment artifact token named Rock.').effect
        self.assertIsNotNone(fixed_equipment_token_closure(value))
        for field, item in (('power','2'),('oracle_text','Equip {1}'),('literal_token_name_version',True),
                ('ability_fragments',[{'kind':'static_component','value':{'schema_version':1,'semantic_key':'unbound'}}])):
            changed=deepcopy(value);changed['characteristics'][field]=item
            with self.subTest(field=field):self.assertIsNone(fixed_equipment_token_closure(changed))

    def test_omitted_token_program_mutant_cannot_pass_the_positive_contract(self):
        self.assertEqual('exact',self.compile(MABEL).status)
        with patch('quorune.compiler.named_equipment_token_templates.equipment_token_children',return_value=None):
            self.assertNotEqual('exact',self.compile(MABEL).status)

    def test_equipped_attack_keeps_its_required_current_component_and_kills_omission(self):
        from quorune.compiler import named_equipment_token_templates as owner
        original=owner.equipment_token_children
        def witness():
            ir=self.compile(ICING)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            creation=next(e for n in ir.faces[0].nodes for e in n.effects if e.get('op')=='create_token')
            fragments=creation['characteristics']['ability_fragments']
            trigger=next(f for f in fragments if f['kind']=='granted_triggered')
            self.assertIn({'kind':'static_component','value':{'schema_version':1,
                'semantic_key':trigger['value']['semantic_key']}},fragments)
        witness()
        def omit(**kwargs):
            value=original(**kwargs)
            if value is None:return None
            fragments,children,mechanics=value
            return [f for f in fragments if not (f['kind']=='static_component' and ':trigger:' in f['value']['semantic_key'])],children,mechanics
        with patch.object(owner,'equipment_token_children',side_effect=omit),self.assertRaises(AssertionError):
            witness()

    def test_equipment_runtime_payload_infers_owners_and_rejects_unrepresented_fields(self):
        from copy import deepcopy
        from quorune.rules.token_creation_capability_shapes import token_definition_payload_dependencies
        ir=self.compile(MABEL)
        creation=next(e for n in ir.faces[0].nodes for e in n.effects if e.get('op')=='create_token')
        self.assertTrue({'token.creation.fixed_definition','token.creation.typed_ability_definition',
            'attachment.equip.fixed_mana'} <= set(token_definition_payload_dependencies(creation)))
        bad=deepcopy(creation);bad['characteristics']['power']='3'
        with self.assertRaises(ValueError):token_definition_payload_dependencies(bad)

    def test_equipment_and_nested_payment_authority_withdrawal_preserves_unrelated_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap,text in (('attachment.equip.fixed_mana',MABEL),('activation.granting_attachment.fixed_payment',ROCK)):
            changed=deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['token owner withdrawn'])
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            record=token_record('Generic Equipment Maker',text,111301,type_line='Creature — Mouse Soldier')
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry,
                capability_profile='commander_review').status)
            draw=token_record('Independent Draw','Draw a card.',111302,type_line='Sorcery')
            self.assertEqual('exact',compile_oracle_card(draw,capability_registry=registry,
                capability_profile='commander_review').status)


class NamedEquipmentNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    tokens=recipients.TargetRecipientNativeTests.tokens

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'named-equipment.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/named-equipment-token-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Named equipment tokens',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def equip(self,s,token,creature):
        ability,=s.engine._activated_abilities(token)
        self.assertTrue(ability.sorcery_speed)
        self.priority(s,token.controller)
        command={'action_id':'activate:'+token.ref+':'+ability.ability_id,'targets':[creature.ref],'pay':'auto'}
        offered=next(row for row in s.packet('pilot:'+token.controller,full=True)['decision']['ctx']['legal']['actions']
            if row['id']==command['action_id'])
        self.assertIn(creature.ref,offered['target_schema']['legal_refs'])
        self.reject(s,'D',command)
        before=sum(s.state.players[token.controller].mana_pool.values())
        accepted=s.act('pilot:'+token.controller,command)
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(ability.mana['GENERIC'],before-sum(s.state.players[token.controller].mana_pool.values()))
        self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(creature.object_id,s.state.cards[token.object_id].attached_to)

    def test_actual_mabel_creates_unattached_complete_token_then_pays_equip_and_moves_effect(self):
        s=self.session(1113010)
        source=self.add(s.engine,'Mabel, Heir to Cragflame',zone='hand')
        first=self.add(s.engine,'Grizzly Bears',ref='cragflame-first')
        second=self.add(s.engine,'Grizzly Bears',ref='cragflame-second')
        opponent=self.add(s.engine,'Grizzly Bears',seat='B',ref='cragflame-opponent')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='equipment-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':7,'R':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s,'A');data=s.engine._effective_card_data(token)
        self.assertEqual('Cragflame',data['name']);self.assertEqual([],list(data['colors']))
        self.assertIn('Legendary',data['type_line']);self.assertIn('Equipment',data['type_line'])
        self.assertIsNone(token.attached_to)
        self.assertEqual(2,s.engine._numeric_stat(first.object_id,'power'))
        ability,=s.engine._activated_abilities(token)
        self.priority(s,'A')
        self.reject(s,'A',{'action_id':'activate:'+token.ref+':'+ability.ability_id,'targets':[opponent.ref],'pay':'auto'})
        self.equip(s,token,first)
        self.assertEqual(3,s.engine._numeric_stat(first.object_id,'power'))
        self.assertTrue({'vigilance','trample','haste'}<=s.engine._combat_keywords(s.state.cards[first.object_id]))
        self.equip(s,s.state.cards[token.object_id],second)
        self.assertEqual(2,s.engine._numeric_stat(first.object_id,'power'))
        self.assertEqual(3,s.engine._numeric_stat(second.object_id,'power'))
        self.attack(s,[(s.state.cards[second.object_id],'B')])
        self.assertFalse(s.state.cards[second.object_id].tapped)
        self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+token.ref+':') for row in actions))
        self.reject(s,'A',{'action_id':'activate:'+token.ref+':'+ability.ability_id,'targets':[first.ref],'pay':'auto'})
        self.finish(s,private)

    def test_actual_icingdeath_dies_then_equipped_attack_taps_only_defending_seat_creature(self):
        s=self.session(1113020)
        source=self.add(s.engine,'Icingdeath, Frost Tyrant',zone='hand')
        destroy=self.add(s.engine,'Murder',zone='hand')
        body=self.add(s.engine,'Grizzly Bears',ref='frost-tongue-wearer')
        target=self.add(s.engine,'Grizzly Bears',seat='C',ref='frost-tongue-defender')
        foreign=self.add(s.engine,'Grizzly Bears',seat='D',ref='frost-tongue-foreign')
        other_defender=self.add(s.engine,'Grizzly Bears',seat='C',ref='frost-tongue-other-defender')
        self.add(s.engine,'Fervor')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='frost-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':9,'W':2,'B':2});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',destroy,[source.ref]);self.assertIsNone(self.resolve(s))
        token,=self.tokens(s,'A')
        self.assertEqual('Icingdeath, Frost Tongue',s.engine._effective_card_data(token)['name'])
        self.assertEqual(['W'],list(s.engine._effective_card_data(token)['colors']))
        self.equip(s,token,body)
        self.assertEqual(4,s.engine._numeric_stat(body.object_id,'power'))
        self.attack(s,[(body,'C')])
        self.assertEqual(1,self.trigger_count(s,token),[item.to_dict() for item in s.state.stack])
        decision=s.state.pending_decision
        self.assertIn(decision.kind,('semantic.target','trigger.target'),[item.to_dict() for item in s.state.stack])
        self.reject(s,'A',{'action_id':'choose','targets':[foreign.ref]})
        self.reject(s,'D',{'action_id':'choose','targets':[target.ref]})
        result=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertTrue(s.state.cards[target.object_id].tapped)
        self.assertFalse(s.state.cards[foreign.object_id].tapped)
        self.assertFalse(s.state.cards[other_defender.object_id].tapped)
        self.finish(s,private)

    def test_actual_toggo_landfall_then_creature_pays_and_deals_damage_using_its_rock(self):
        s=self.session(1113030)
        source=self.add(s.engine,'Toggo, Goblin Weaponsmith',zone='hand')
        body=self.add(s.engine,'Grizzly Bears',ref='rock-wearer')
        self.add(s.engine,'Fervor')
        land=self.add(s.engine,'Forest',zone='hand',ref='rock-landfall')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='rock-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':7,'R':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.priority(s,'A')
        land_action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('action')=='play_land' and row.get('card')==land.ref)
        result=s.act('pilot:A',{'action_id':land_action['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));token,=self.tokens(s,'A')
        self.assertEqual('Rock',s.engine._effective_card_data(token)['name'])
        self.equip(s,token,body);self.priority(s,'A')
        offers=[row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+body.ref+':') and 'granting_attachment' in row.get('cost_summary',{})]
        offered,=offers
        self.assertEqual(token.ref,offered['cost_summary']['granting_attachment']['card'])
        life=s.state.players['B'].life
        command={'action_id':offered['id'],'targets':['B'],'pay':'auto'}
        self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(s.state.cards[body.object_id].tapped)
        self.assertNotEqual('battlefield',s.state.cards[token.object_id].zone)
        self.assertEqual(body.object_id,s.state.stack[-1].source_object_id)
        self.assertEqual(life,s.state.players['B'].life)
        self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(life-2,s.state.players['B'].life)
        self.finish(s,private)

    def test_actual_donated_rock_keeps_creature_grant_but_cannot_pay_foreign_sacrifice(self):
        s=self.session(1113040)
        source=self.add(s.engine,'Toggo, Goblin Weaponsmith',zone='hand')
        body=self.add(s.engine,'Grizzly Bears',ref='foreign-rock-wearer')
        self.add(s.engine,'Fervor')
        land=self.add(s.engine,'Forest',zone='hand',ref='foreign-rock-landfall')
        donate=self.add(s.engine,'Donate',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='foreign-rock-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':9,'R':1,'U':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.priority(s,'A')
        land_action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('action')=='play_land' and row.get('card')==land.ref)
        result=s.act('pilot:A',{'action_id':land_action['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));token,=self.tokens(s,'A')
        self.equip(s,token,body);self.priority(s,'A')
        offered,=[row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+body.ref+':') and 'granting_attachment' in row.get('cost_summary',{})]
        self.cast(s,'A',donate,['B',token.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('A',s.state.cards[body.object_id].controller)
        self.assertEqual('B',s.state.cards[token.object_id].controller)
        self.assertEqual('A',s.state.cards[token.object_id].owner)
        self.assertEqual(body.object_id,s.state.cards[token.object_id].attached_to)
        self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+body.ref+':') and 'granting_attachment' in row.get('cost_summary',{}) for row in actions))
        self.reject(s,'A',{'action_id':offered['id'],'targets':['B'],'pay':'auto'})
        self.finish(s,private)
