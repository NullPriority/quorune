"""CR 120.2b/608.2b: optional recipients do not make their source optional."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_fixed_creature_power_damage as controls
import test_target_recipient_results as recipients
import optional_result_followup_fixture as copies
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from quorune.rules.prepared_creature_power_damage_shapes import prepared_creature_power_damage_capabilities
from quorune.creature_power_damage_model import CREATURE_POWER_DAMAGE_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database


class PowerDamageRecipientCompilerTests(unittest.TestCase):
    def compile(self,text,*,mana=None,registry=None):
        card=controls.card_record(text)
        if mana is not None:card=replace(card,mana_cost=mana)
        return compile_oracle_card(card,capability_registry=registry or load_default_capability_registry(),capability_profile='commander_review')

    def test_exact_recipient_domains_and_optional_other_preserve_separate_target_roles(self):
        cases=(('target player',['player'],None,False),
            ('another target creature, planeswalker, or battle',['battlefield'],['creature','planeswalker','battle'],False),
            ('up to one other target creature',['battlefield'],['creature'],True),
            ('up to one target creature an opponent controls',['battlefield'],['creature'],True))
        for recipient,zones,types,optional in cases:
            text='Target creature you control deals damage equal to its power to '+recipient+'.'
            ir=self.compile(text);self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes;first,second=node.target_schema['groups']
            self.assertEqual(('fighter','you',1),tuple(first[k] for k in ('id','controller_relation','count')))
            self.assertEqual(zones,list(second['zones']));self.assertEqual(types,list(second['types_any']) if 'types_any' in second else None)
            self.assertEqual(optional,'min' in second)
            if optional:self.assertEqual((0,1),(second['min'],second['max']))
            self.assertEqual('other' in recipient or 'another' in recipient,'different_from_groups' in second)
            self.assertEqual(('$target.0','$target.1'),tuple(node.effects[-1][k] for k in ('source','target')))
            self.assertIn(CAP,node.capability_dependencies)
        legacy=self.compile('Target creature you control deals damage equal to its power to target creature.')
        self.assertNotIn('different_from_groups',legacy.faces[0].nodes[0].target_schema['groups'][1])

    def test_preparation_and_announced_x_keep_the_same_source_and_optional_recipient(self):
        for prefix,mana in (('Put two +1/+1 counters on target creature you control.','{2}{G}{G}'),
                ('Target creature you control gets +1/+0 until end of turn.','{1}{G}'),
                ('Target creature you control gets +X/+0 until end of turn.','{X}{R}{R}')):
            ir=self.compile(prefix+' Then it deals damage equal to its power to up to one other target creature.',mana=mana)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes
            self.assertEqual('$target.0',node.effects[0]['card'])
            self.assertEqual(0,node.target_schema['groups'][1]['min'])
            self.assertIn('damage.creature_power.prepared_sequence',node.capability_dependencies)

    def test_closed_shapes_reject_unrepresented_domains_counts_and_source_rebinding(self):
        for text in ('Target creature you control deals damage equal to its power to target artifact.',
                'Target creature you control deals damage equal to its toughness to target player.',
                'Target creature you control deals damage equal to its power to up to two target creatures.',
                'Target creature you control deals damage equal to its power to each opponent.'):
            self.assertNotEqual('exact',self.compile(text).status,text)
        ir=self.compile('Target creature you control deals damage equal to its power to another target creature, planeswalker, or battle.')
        node,=ir.faces[0].nodes;effects=json.loads(json.dumps(node.effects));schema=json.loads(json.dumps(node.target_schema))
        for change in ('artifact','many','kind','source'):
            e,s=deepcopy(effects),deepcopy(schema)
            if change=='artifact':s['groups'][1]['types_any'].append('artifact')
            elif change=='many':s['groups'][1]['count']=2
            elif change=='kind':e[0]['kind']='fight'
            else:e[0]['source']='$target.1'
            self.assertEqual((),fixed_creature_power_damage_node_capabilities(effects=e,target_schema=s,mechanic_ids=node.mechanics),change)

    def test_verified_owner_withdrawal_blocks_new_domains_and_preserves_plain_preparation(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['Power damage withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for recipient in ('target player','another target creature, planeswalker, or battle','up to one other target creature'):
            self.assertNotEqual('exact',self.compile('Target creature you control deals damage equal to its power to '+recipient+'.',registry=registry).status)
        self.assertEqual('exact',self.compile('Target creature you control gets +1/+0 until end of turn.',registry=registry).status)


class PowerDamageRecipientNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    retarget_copy=copies.OptionalResultFollowupFixture.retarget_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(basic),encoding='utf-8')
        path=folder/'power-recipients.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/power-damage-recipient-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Power damage recipients',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,fighter='Grizzly Bears',target='Wall of Stone',before=None):
        s=self.session(2021014+len(name));spell=self.add(s.engine,name,zone='hand')
        first=self.add(s.engine,fighter,ref='power-recipient-source')
        second=self.add(s.engine,target,seat='B',ref='power-recipient-target')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='power-recipient-private-C')
        if before is not None:before(s,spell,first,second)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,spell);self.assert_closed(s,first)
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,spell,first,second,private

    def cast_checked(self,s,spell,first,targets,*,x=None,**fields):
        self.priority(s,'A');row=self.action(s,'A',spell)
        command={'action_id':row['id'],'targets':self.selections(first,targets),'pay':'auto',**fields}
        if x is not None:command['x']=x
        self.reject(s,'D',command)
        if x is not None:self.reject(s,'A',{**command,'x':True})
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        if x is not None:self.assertEqual(x,s.state.stack[-1].x_value)
        return s.state.stack[-1]

    def selections(self,first,recipients):
        return [{'group':'fighter','ref':first.ref},*[{'group':'recipient','ref':r} for r in recipients]]

    def test_actual_gravitic_punch_uses_current_creature_power_lifelink_and_jump_start(self):
        extra={}
        def before(s,spell,first,second):
            extra['growth']=self.add(s.engine,'Giant Growth',seat='B',zone='hand')
            extra['discard']=self.add(s.engine,'Forest',zone='hand',ref='punch-jump-discard')
            s.state.players['B'].mana_pool['G']=1
        s,spell,first,second,private=self.scene('Gravitic Punch',fighter='Vampire Nighthawk',before=before)
        row=self.action(s,'A',spell)
        self.reject(s,'A',{'action_id':row['id'],'targets':[first.ref,second.ref],'pay':'auto'})
        self.cast_checked(s,spell,first,['B'])
        self.cast(s,'B',extra['growth'],[first.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((45,35),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        spell=s.state.cards[spell.object_id];self.priority(s,'A')
        row=self.action(s,'A',spell)
        self.assertTrue(any(o['id']=='jump-start' for o in row['cost_options']))
        command={'action_id':row['id'],'targets':[s.state.cards[first.object_id].ref,'C'],'cost_option':'jump-start','discard_cards':[extra['discard'].ref],'pay':'auto'}
        self.reject(s,'D',command);self.reject(s,'A',{**command,'discard_cards':[]})
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[extra['discard'].object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((50,35),(s.state.players['A'].life,s.state.players['C'].life))
        self.assertEqual('exile',s.state.cards[spell.object_id].zone)
        self.assertEqual((24,8),tuple(s.state.players['A'].mana_pool[c] for c in ('C','R')))
        self.finish(s,private)

    def test_actual_bionic_and_assert_keep_preparation_with_zero_recipients(self):
        for name,x,chosen,boost in (('Bionic Blow',0,False,0),('Bionic Blow',2,False,2),
                ('Bionic Blow',2,True,2),('Assert Perfection',None,False,1)):
            with self.subTest(name=name,x=x,chosen=chosen):
                s,spell,first,second,private=self.scene(name)
                row=self.action(s,'A',spell)
                self.reject(s,'A',{'action_id':row['id'],'targets':self.selections(first,[first.ref]),'pay':'auto',**({'x':x} if x is not None else {})})
                self.cast_checked(s,spell,first,[second.ref] if chosen else [],x=x)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual((2+boost,2),tuple(s.engine._numeric_stat(first.object_id,k) for k in ('power','toughness')))
                self.assertEqual(2+boost if chosen else 0,s.state.cards[second.object_id].marked_damage)
                self.assertEqual(0,s.state.cards[first.object_id].marked_damage)
                if x is not None:self.assertEqual((30-x,8),tuple(s.state.players['A'].mana_pool[c] for c in ('C','R')))
                self.finish(s,private)

    def test_actual_venom_blast_keeps_replaced_counters_at_zero_and_one_recipient(self):
        for chosen in (False,True):
            with self.subTest(chosen=chosen):
                def before(s,spell,first,second):
                    scales=self.add(s.engine,'Hardened Scales');self.assert_closed(s,scales)
                s,spell,first,second,private=self.scene('Venom Blast',before=before)
                row=self.action(s,'A',spell)
                self.reject(s,'A',{'action_id':row['id'],'targets':self.selections(first,[first.ref]),'pay':'auto'})
                self.cast_checked(s,spell,first,[second.ref] if chosen else [])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(3,s.state.cards[first.object_id].counters.get('+1/+1'))
                self.assertEqual((5,5),tuple(s.engine._numeric_stat(first.object_id,k) for k in ('power','toughness')))
                self.assertEqual(5 if chosen else 0,s.state.cards[second.object_id].marked_damage)
                self.finish(s,private)

    def test_actual_assert_preserves_legal_preparation_but_never_uses_an_illegal_source(self):
        for invalid_source in (False,True):
            with self.subTest(invalid_source=invalid_source):
                extra={}
                def before(s,spell,first,second):
                    extra['bounce']=self.add(s.engine,'Unsummon',seat='B',zone='hand')
                    s.state.players['B'].mana_pool['U']=1
                s,spell,first,second,private=self.scene('Assert Perfection',before=before)
                self.cast_checked(s,spell,first,[second.ref])
                self.cast(s,'B',extra['bounce'],[first.ref if invalid_source else second.ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('hand' if invalid_source else 'battlefield',s.state.cards[first.object_id].zone)
                self.assertEqual(0,s.state.cards[second.object_id].marked_damage)
                if not invalid_source:self.assertEqual(3,s.engine._numeric_stat(first.object_id,'power'))
                self.finish(s,private)

    def test_actual_cosmic_hunger_distinguishes_creatures_walkers_battles_and_damage_source_protection(self):
        for target,expected,life in (('Wall of Stone','graveyard',42),('White Knight','battlefield',40),
                ('Jace Beleren','battlefield',42),('Invasion of Belenon // Belenon War Anthem','battlefield',42)):
            with self.subTest(target=target):
                def before(s,spell,first,second):
                    if target=='Jace Beleren':
                        second.counters['loyalty']=3;second.annotations['loyalty_initialized']=True
                    elif target.startswith('Invasion'):
                        second.counters['defense']=5;second.battle_protector='D'
                s,spell,first,second,private=self.scene('Cosmic Hunger',fighter='Vampire Nighthawk',target=target,before=before)
                row=self.action(s,'A',spell)
                for recipient in ('B',first.ref):self.reject(s,'A',{'action_id':row['id'],'targets':[first.ref,recipient],'pay':'auto'})
                self.cast_checked(s,spell,first,[second.ref])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(expected,s.state.cards[second.object_id].zone)
                self.assertEqual(life,s.state.players['A'].life)
                self.assertEqual(40,s.state.players['B'].life)
                if target=='Jace Beleren':self.assertEqual(1,s.state.cards[second.object_id].counters['loyalty'])
                if target.startswith('Invasion'):self.assertEqual(3,s.state.cards[second.object_id].counters['defense'])
                if target=='White Knight':self.assertEqual(0,s.state.cards[second.object_id].marked_damage)
                self.assertEqual(0,s.state.cards[first.object_id].marked_damage)
                self.finish(s,private)

    def test_actual_punch_revalidates_a_departed_target_player(self):
        s,spell,first,second,private=self.scene('Gravitic Punch',fighter='Vampire Nighthawk')
        self.cast_checked(s,spell,first,['B']);self.priority(s,'B')
        result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['A'].life)
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.finish(s,private)

    def test_actual_copied_bionic_blow_retains_x_and_the_optional_recipient_count(self):
        for chosen in (False,True):
            with self.subTest(chosen=chosen):
                extra={}
                def before(s,spell,first,second):
                    extra['copy']=self.add(s.engine,'Reverberate',zone='hand')
                    extra['other']=self.add(s.engine,'Wall of Stone',seat='D',ref='power-copy-other')
                s,spell,first,second,private=self.scene('Bionic Blow',before=before)
                self.cast_checked(s,spell,first,[second.ref] if chosen else [],x=2)
                self.cast(s,'A',extra['copy'],[s.state.stack[-1].ref])
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                changed_count=self.selections(first,[] if chosen else [second.ref])
                self.reject(s,'A',{'action_id':'choose','targets':changed_count})
                s=self.replay(s,load=True)
                self.retarget_copy(s,self.selections(first,[extra['other'].ref] if chosen else []))
                self.assertEqual(2,s.state.stack[-1].x_value)
                self.resolve_top(s)
                self.assertEqual(4 if chosen else 0,s.state.cards[extra['other'].object_id].marked_damage)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(6,s.engine._numeric_stat(first.object_id,'power'))
                self.assertEqual(6 if chosen else 0,s.state.cards[second.object_id].marked_damage)
                self.assertEqual((28,6),tuple(s.state.players['A'].mana_pool[c] for c in ('C','R')))
                self.finish(s,private)

    def test_actual_wrong_source_power_and_lost_other_exclusion_mutants_are_killed(self):
        with patch('quorune.creature_power_damage._creature_power',return_value=2):
            witness=type(self)('test_actual_gravitic_punch_uses_current_creature_power_lifelink_and_jump_start')
            with self.assertRaises(AssertionError):witness.test_actual_gravitic_punch_uses_current_creature_power_lifelink_and_jump_start()
        import quorune.compiler.creature_power_damage_templates as owner
        original=owner._recipient_group
        def include_source(*args,**kwargs):
            result=original(*args,**kwargs)
            if result is None:return None
            group,must=result;group.pop('different_from_groups',None)
            return group,must
        with patch.object(owner,'_recipient_group',include_source):
            witness=type(self)('test_actual_venom_blast_keeps_replaced_counters_at_zero_and_one_recipient')
            with self.assertRaises(AssertionError):witness.test_actual_venom_blast_keeps_replaced_counters_at_zero_and_one_recipient()
