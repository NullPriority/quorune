from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.compiler.direct_target import DirectPermanentTargetSpec,direct_permanent_target_spec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_characteristics import TargetCharacteristicSnapshot
from quorune.target_forms import TargetCharacteristicForm
from quorune.targets import TargetGroup
from test_fixed_direct_target_predicates import record
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from scripts.build_test_database import build_fixture_database
import test_life_gain_prohibitions as native


class MixedPermanentTargetModelTests(unittest.TestCase):
    def test_mixed_domains_keep_card_types_and_subtypes_as_alternatives(self):
        cases=(
            ('target creature or Vehicle',('Creature — Human','Artifact — Vehicle','Artifact Creature — Vehicle'),
                ('Artifact','Enchantment — Aura','Land')),
            ('target Spirit or enchantment',('Creature — Spirit','Enchantment — Aura','Enchantment — Spirit'),
                ('Creature — Human','Artifact','Land')),
            ('target artifact, creature, or Vehicle',('Artifact','Creature — Human','Artifact — Vehicle'),
                ('Enchantment','Land')),
        )
        for subject,allowed,rejected in cases:
            spec=direct_permanent_target_spec(subject);self.assertIsNotNone(spec,subject)
            schema=spec.to_target_schema();group=TargetGroup.from_mapping(schema)
            self.assertEqual(1,schema['count'])
            self.assertEqual(spec,DirectPermanentTargetSpec.from_target_schema(schema))
            self.assertTrue(spec.uses_compound_characteristics)
            for type_line in (*allowed,*rejected):
                with self.subTest(subject=subject,type_line=type_line):
                    facts=TargetCharacteristicSnapshot.from_effective_data({'type_line':type_line})
                    self.assertEqual(type_line in allowed,facts.matches(group))

    def test_shared_scope_and_canonical_alternatives_do_not_change_branch_meaning(self):
        spec=direct_permanent_target_spec('another target creature or Vehicle you control')
        self.assertIsNotNone(spec)
        self.assertEqual('you',spec.controller_relation);self.assertTrue(spec.source_exclusion)
        reversed_spec=direct_permanent_target_spec('another target Vehicle or creature you control')
        self.assertEqual(spec,reversed_spec)
        group=TargetGroup.from_mapping(spec.to_target_schema())
        for type_line,expected in (('Artifact — Vehicle',True),('Artifact Creature — Vehicle',True),
                ('Creature',True),('Land — Forest',False)):
            self.assertEqual(expected,TargetCharacteristicSnapshot.from_effective_data({'type_line':type_line}).matches(group))

    def test_mixed_target_grammar_retains_unowned_and_malformed_boundaries(self):
        for subject in ('target creature or Spaceship','target creature or unknown-subtype',
                'target creature or Vehicle,','target creature or Vehicle or','target creature or creature or Vehicle',
                'target creature you control or Vehicle','target creature or tapped Vehicle',
                'target legendary creature or Vehicle','target creature or Vehicle with flying',
                'target creature or Vehicle with mana value 2','target creature or Vehicle card',
                'target creature or Aura spell','target creature or Vehicle in your graveyard'):
            with self.subTest(subject=subject):self.assertIsNone(direct_permanent_target_spec(subject))
        spec=direct_permanent_target_spec('target creature or Vehicle');self.assertIsNotNone(spec)
        for forms in ((),(TargetCharacteristicForm(types_all=('creature',)),),
                (TargetCharacteristicForm(types_all=('creature',)),)*2,
                (TargetCharacteristicForm(types_all=('creature',)),TargetCharacteristicForm(types_all=('artifact',))),
                (TargetCharacteristicForm(types_all=('creature',)),TargetCharacteristicForm(subtypes_any=('unknown-subtype',))),
                (TargetCharacteristicForm(types_all=('creature',)),TargetCharacteristicForm(types_all=('artifact',),subtypes_any=('vehicle',)))):
            if not forms:continue
            with self.subTest(forms=forms),self.assertRaises(ValueError):replace(spec,characteristic_forms_any=forms)
        with self.assertRaises(ValueError):replace(spec,types_any=('creature',))
        with self.assertRaises(ValueError):replace(spec,characteristic_forms_any=True)
        schema=spec.to_target_schema();schema['characteristic_forms_any'][0]['unknown']=True
        with self.assertRaises(ValueError):DirectPermanentTargetSpec.from_target_schema(schema)

    def test_shared_direct_effect_lowering_keeps_source_spans_and_target_authority(self):
        caps=load_default_capability_registry()
        texts=('Destroy target creature or Vehicle.','Exile target Spirit or enchantment.',
            'Put a +1/+1 counter on target creature or Spacecraft you control.',
            'Target creature or Vehicle gets +2/+0 until end of turn.')
        for i,text in enumerate(texts):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,suffix=815000+i),capability_registry=caps,capability_profile='commander_review')
                self.assertFalse(ir.material_residuals)
                nodes=[n for face in ir.faces for n in face.nodes if n.target_schema]
                self.assertEqual(1,len(nodes))
                self.assertTrue(nodes[0].exact)
                self.assertEqual(text,nodes[0].text)
                self.assertIn('target.permanent.characteristic_predicate',nodes[0].capability_dependencies)
                self.assertTrue(nodes[0].target_schema.get('characteristic_forms_any'))

    def test_accepting_every_characteristic_alternative_is_detected(self):
        method='test_mixed_domains_keep_card_types_and_subtypes_as_alternatives'
        with patch.object(TargetCharacteristicForm,'matches',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_flat_alternative_limits_and_common_scope_roundtrip(self):
        subject='another target artifact, creature, Vehicle, or Spacecraft an opponent controls'
        spec=direct_permanent_target_spec(subject);self.assertIsNotNone(spec)
        self.assertEqual(4,len(spec.characteristic_forms_any))
        self.assertEqual('opponent',spec.controller_relation);self.assertTrue(spec.source_exclusion)
        self.assertEqual(spec,DirectPermanentTargetSpec.from_target_schema(spec.to_target_schema()))
        self.assertIsNone(direct_permanent_target_spec('target artifact, creature, Vehicle, Spacecraft, or Spirit'))
        for fields in ({'count':True},{'zones':['hand']},{'categories':['spell']},{'unknown':True}):
            with self.assertRaises(ValueError):DirectPermanentTargetSpec.from_target_schema({**spec.to_target_schema(),**fields})


class MixedPermanentTargetRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.LifeGainProhibitionRuntimeTests.session
    add=native.LifeGainProhibitionRuntimeTests.add
    prepare=native.LifeGainProhibitionRuntimeTests.prepare
    checkpoint=native.LifeGainProhibitionRuntimeTests.checkpoint
    resolve=native.LifeGainProhibitionRuntimeTests.resolve
    replay=native.LifeGainProhibitionRuntimeTests.replay
    private_replay=native.LifeGainProhibitionRuntimeTests.private_replay
    priority=native.LifeGainProhibitionRuntimeTests.priority
    actions=native.LifeGainProhibitionRuntimeTests.actions
    reject=native.LifeGainProhibitionRuntimeTests.reject
    binding=native.LifeGainProhibitionRuntimeTests.binding
    resume=native.LifeGainProhibitionRuntimeTests.resume
    settle=native.LifeGainProhibitionRuntimeTests.settle
    advance=native.LifeGainProhibitionRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'mixed-permanent-targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/mixed-permanent-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mixed permanent target alternatives',[
            DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fixture(self,seed,name):
        s=self.session(seed);source=self.add(s.engine,name,zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        return s,source,private

    def pay_cast(self,s,source,targets=(),*,payment,seat='A',**fields):
        self.priority(s,seat)
        row=next((r for r in self.actions(s,seat) if r['id']=='cast:'+source.ref),None)
        self.assertIsNotNone(row,source.printed_name)
        self.assertTrue(self.binding(s,source)['strict_capability_ready'])
        command=dict(action_id=row['id'],targets=list(targets),pay='manual',payment=payment,**fields)
        self.reject(s,command,seat='D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def private_draws(self,s,before,*,seat='A'):
        drawn=set(s.state.players[seat].zones['hand'])-before
        for oid in drawn:
            for other in 'ABCD':
                if other!=seat:self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+other,full=True)))
        return drawn

    def test_paid_destruction_and_exile_originals_keep_each_branch_and_reject_plain_artifacts(self):
        cases=(('Daring Demolition','Sky Skiff',{'C':2,'B':2},'graveyard'),
            ('Spin Out','Geist of the Moors',{'C':1,'B':2},'graveyard'),
            ('Gravkill','Generic Mixed Target Spacecraft',{'C':3,'B':1},'exile'),
            ('Urgent Exorcism','Geist of the Moors',{'C':1,'W':1},'graveyard'),
            ('Urgent Exorcism','Pacifism',{'C':1,'W':1},'graveyard'))
        for i,(name,target_name,payment,destination) in enumerate(cases):
            with self.subTest(name=name,target=target_name):
                s,source,private=self.fixture(595100+i,name)
                target=self.add(s.engine,target_name,seat='B');wrong=self.add(s.engine,'Sol Ring',seat='B')
                if target_name=='Pacifism':
                    creature=self.add(s.engine,'Geist of the Moors',seat='B')
                    target.attached_to=creature.object_id;creature.attachments.append(target.object_id)
                self.assertTrue(self.binding(s,target)['strict_capability_ready'])
                self.prepare(s,mana=payment);self.checkpoint(s)
                self.reject(s,dict(action_id='cast:'+source.ref,targets=[wrong.ref],pay='manual',payment=payment))
                self.pay_cast(s,source,[target.ref],payment=payment)
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual(destination,s.state.cards[target.object_id].zone)
                self.assertEqual('battlefield',s.state.cards[wrong.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.private_replay(s,private)

    def test_embrace_oblivion_pays_actual_sacrifice_only_for_a_legal_union_target(self):
        s,source,private=self.fixture(595110,'Embrace Oblivion')
        cost=self.add(s.engine,'Sol Ring');target=self.add(s.engine,'Generic Mixed Target Spacecraft',seat='B')
        self.prepare(s,mana={'B':1});self.checkpoint(s)
        self.reject(s,dict(action_id='cast:'+source.ref,targets=[cost.ref],pay='manual',payment={'B':1},sacrifice_cards=[cost.ref]))
        self.pay_cast(s,source,[target.ref],payment={'B':1},sacrifice_cards=[cost.ref])
        self.assertEqual('graveyard',s.state.cards[cost.object_id].zone)
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.private_replay(s,private)

    def test_roadside_uses_actual_target_discount_and_keeps_private_draw(self):
        for i,cheap in enumerate((False,True)):
            with self.subTest(cheap=cheap):
                s,source,private=self.fixture(595120+i,'Roadside Blowout')
                target=self.add(s.engine,'Llanowar Elves' if cheap else 'Sky Skiff',seat='B')
                wrong=self.add(s.engine,'Sol Ring',seat='B')
                payment={'U':1} if cheap else {'C':2,'U':1}
                self.prepare(s,mana=payment);self.checkpoint(s)
                self.reject(s,dict(action_id='cast:'+source.ref,targets=[wrong.ref],pay='manual',payment=payment))
                if not cheap:self.reject(s,dict(action_id='cast:'+source.ref,targets=[target.ref],pay='manual',payment={'U':1}))
                self.pay_cast(s,source,[target.ref],payment=payment)
                before=set(s.state.players['A'].zones['hand'])
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual('hand',s.state.cards[target.object_id].zone)
                self.assertEqual(1,len(self.private_draws(s,before)))
                self.assertEqual(0,s.state.players['A'].mana_pool['U'])
                self.private_replay(s,private)

    def test_actual_type_change_invalidates_both_branches_and_skips_roadside_draw(self):
        s,source,private=self.fixture(595130,'Roadside Blowout')
        target=self.add(s.engine,'Sky Skiff',seat='B')
        self.add(s.engine,'Vedalken Orrery',seat='B');change=self.add(s.engine,'Song of the Dryads',seat='B',zone='hand')
        s.state.players['B'].mana_pool.update({'C':2,'G':1})
        self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s)
        self.pay_cast(s,source,[target.ref],payment={'C':2,'U':1})
        before=set(s.state.players['A'].zones['hand'])
        self.pay_cast(s,change,[target.ref],payment={'C':2,'G':1},seat='B')
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual('battlefield',s.state.cards[target.object_id].zone)
        self.assertEqual(before,set(s.state.players['A'].zones['hand']))
        self.assertEqual({'land'},s.engine._type_parts(s.engine._effective_card_data(s.state.cards[target.object_id])['type_line'])[0])
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.private_replay(s,private)

    def test_suit_up_animates_the_noncreature_vehicle_and_draws_privately(self):
        s,source,private=self.fixture(595140,'Suit Up')
        target=self.add(s.engine,'Sky Skiff')
        self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s)
        self.pay_cast(s,source,[target.ref],payment={'C':2,'U':1})
        before=set(s.state.players['A'].zones['hand'])
        s=self.resume(s);self.assertIsNone(self.settle(s))
        card=s.state.cards[target.object_id]
        types,subtypes,_=s.engine._type_parts(s.engine._effective_card_data(card)['type_line'])
        self.assertTrue({'artifact','creature'}<=types);self.assertIn('vehicle',subtypes)
        self.assertEqual((4,5),tuple(s.engine._numeric_stat(card.object_id,key) for key in ('power','toughness')))
        self.assertEqual(1,len(self.private_draws(s,before)))
        self.private_replay(s,private)

    def test_light_the_way_keeps_counter_untap_and_return_modes(self):
        for i,mode in enumerate(('mode_1','mode_2')):
            with self.subTest(mode=mode):
                s,source,private=self.fixture(595150+i,'Light the Way')
                target=self.add(s.engine,'Sky Skiff');target.tapped=True
                self.prepare(s,mana={'W':1});self.checkpoint(s)
                self.pay_cast(s,source,[target.ref],payment={'W':1},modes=[mode])
                s=self.resume(s);self.assertIsNone(self.settle(s))
                card=s.state.cards[target.object_id]
                if i:self.assertEqual('hand',card.zone)
                else:
                    self.assertEqual('battlefield',card.zone);self.assertFalse(card.tapped)
                    self.assertEqual(1,card.counters.get('+1/+1',0))
                self.private_replay(s,private)

    def test_actual_entry_grants_accept_noncreature_vehicles_and_keep_shared_scope(self):
        cases=(('Lotusguard Disciple',{'C':2,'W':1}),
            ("Captain America's Motorcycle",{'C':1,'R':1}),('Fang Guardian',{'C':3,'G':1}))
        for i,(name,payment) in enumerate(cases):
            with self.subTest(name=name):
                s,source,private=self.fixture(595160+i,name)
                target=self.add(s.engine,'Sky Skiff');wrong=self.add(s.engine,'Sol Ring')
                self.prepare(s,mana=payment);self.checkpoint(s)
                self.pay_cast(s,source,payment=payment)
                decision=self.resolve(s);self.assertEqual('semantic.target',decision.kind)
                self.reject(s,{'action_id':'choose','targets':[wrong.ref]})
                if name=='Fang Guardian':self.reject(s,{'action_id':'choose','targets':[source.ref]})
                s=self.resume(s);self.assertIsNone(self.settle(s,target=target.ref))
                card=s.state.cards[target.object_id]
                if name=='Lotusguard Disciple':
                    self.assertTrue({'lifelink','indestructible'}<=s.engine._combat_keywords(card))
                else:
                    expected=(4,3) if i==1 else (4,5)
                    self.assertEqual(expected,tuple(s.engine._numeric_stat(card.object_id,key) for key in ('power','toughness')))
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                self.private_replay(s,private)

    def test_seven_tail_entry_and_actual_death_each_counter_the_same_vehicle_once(self):
        s,source,private=self.fixture(595170,'Seven-Tail Mentor')
        target=self.add(s.engine,'Sky Skiff');removal=self.add(s.engine,'Murder',seat='B',zone='hand')
        s.state.players['B'].mana_pool.update({'C':1,'B':2})
        self.prepare(s,mana={'C':3,'W':1});self.checkpoint(s)
        self.pay_cast(s,source,payment={'C':3,'W':1})
        self.assertIsNone(self.settle(s,target=target.ref))
        self.assertEqual(1,s.state.cards[target.object_id].counters.get('+1/+1',0))
        self.pay_cast(s,removal,[source.ref],payment={'C':1,'B':2},seat='B')
        s=self.resume(s);self.assertIsNone(self.settle(s,target=target.ref))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(2,s.state.cards[target.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)

    def test_grafted_growth_keeps_aura_land_and_vehicle_trigger_references_distinct(self):
        s,source,private=self.fixture(595180,'Grafted Growth')
        land=self.add(s.engine,'Forest');target=self.add(s.engine,'Sky Skiff')
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
        self.pay_cast(s,source,[land.ref],payment={'C':2,'G':1})
        s=self.resume(s);self.assertIsNone(self.settle(s,target=target.ref))
        self.assertEqual(land.object_id,s.state.cards[source.object_id].attached_to)
        self.assertEqual(1,s.state.cards[target.object_id].counters.get('+1/+1',0))
        self.assertEqual(0,s.state.cards[land.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)

    def test_scrap_compactor_keeps_its_two_target_domains_and_sacrificed_ability(self):
        s,source,private=self.fixture(595190,'Scrap Compactor')
        target=self.add(s.engine,'Sky Skiff',seat='B');wrong=self.add(s.engine,'Sol Ring',seat='B')
        self.add(s.engine,'Geist of the Moors',seat='B')
        self.prepare(s,mana={'C':7});self.checkpoint(s)
        self.pay_cast(s,source,payment={'C':1});self.assertIsNone(self.settle(s))
        self.priority(s,'A')
        rows=[r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':')]
        damage=next(r for r in rows if 'damage' in json.dumps(r).casefold())
        destroy=next(r for r in rows if 'destroy' in json.dumps(r).casefold())
        self.reject(s,dict(action_id=damage['id'],targets=[target.ref],pay='manual',payment={'C':3}))
        self.reject(s,dict(action_id=destroy['id'],targets=[wrong.ref],pay='manual',payment={'C':6}))
        command=dict(action_id=destroy['id'],targets=[target.ref],pay='manual',payment={'C':6})
        self.reject(s,command,seat='D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[wrong.object_id].zone)
        self.assertEqual(0,s.state.players['A'].mana_pool['C'])
        self.private_replay(s,private)

    def test_actual_cycling_trigger_targets_opposing_vehicle_before_private_draw(self):
        s,source,private=self.fixture(595200,"Howler's Heavy")
        target=self.add(s.engine,'Sky Skiff',seat='B')
        self.prepare(s,mana={'C':1,'U':1});self.checkpoint(s)
        row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':') and 'draw' in json.dumps(r).casefold())
        command=dict(action_id=row['id'],pay='manual',payment={'C':1,'U':1})
        self.reject(s,command,seat='D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        before=set(s.state.players['A'].zones['hand'])
        s=self.resume(s);self.assertIsNone(self.settle(s,target=target.ref))
        card=s.state.cards[target.object_id]
        self.assertEqual((-1,3),tuple(s.engine._numeric_stat(card.object_id,key) for key in ('power','toughness')))
        self.assertEqual(1,len(self.private_draws(s,before)))
        self.assertEqual('battlefield',card.zone)
        self.private_replay(s,private)

    def test_pride_of_the_road_requires_actual_max_speed_and_expires_its_grant(self):
        for i,increase in enumerate((False,True)):
            with self.subTest(increase=increase):
                s,source,private=self.fixture(595210+i,'Pride of the Road')
                target=self.add(s.engine,'Sky Skiff');wrong=self.add(s.engine,'Sol Ring')
                damage=self.add(s.engine,'Shock',zone='hand');s.state.players['A'].stats['speed']=3
                self.prepare(s,mana={'C':3,'W':1,'R':1});self.checkpoint(s)
                self.pay_cast(s,source,payment={'C':3,'W':1});self.assertIsNone(self.settle(s))
                self.assertEqual(3,s.state.players['A'].stats['speed'])
                if increase:
                    self.pay_cast(s,damage,['B'],payment={'R':1});self.assertIsNone(self.settle(s))
                    self.assertEqual(4,s.state.players['A'].stats['speed'])
                self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) in {'semantic.target','combat.attackers'})
                if increase:
                    self.assertEqual('semantic.target',s.state.pending_decision.kind)
                    self.reject(s,{'action_id':'choose','targets':[wrong.ref]})
                    s=self.resume(s);self.assertIsNone(self.settle(s,target=target.ref))
                else:self.assertEqual('combat.attackers',s.state.pending_decision.kind)
                keywords=s.engine._combat_keywords(s.state.cards[target.object_id])
                self.assertEqual(increase,any(k.replace('_',' ')=='double strike' for k in keywords))
                self.advance(s,lambda x:x.state.active_player=='B')
                self.assertFalse(any(k.replace('_',' ')=='double strike' for k in s.engine._combat_keywords(s.state.cards[target.object_id])))
                self.private_replay(s,private)

    def test_fire_nation_engineer_uses_actual_attack_history_and_excludes_itself(self):
        for i,attacked in enumerate((False,True)):
            with self.subTest(attacked=attacked):
                s,source,private=self.fixture(595220+i,'Fire Nation Engineer')
                target=self.add(s.engine,'Sky Skiff');attacker=self.add(s.engine,'Memnite')
                attacker.acquired_control_turn_count=-1
                self.prepare(s,mana={'C':2,'B':1});self.checkpoint(s)
                self.pay_cast(s,source,payment={'C':2,'B':1});self.assertIsNone(self.settle(s))
                self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.attackers')
                command={'action_id':'attack','atk':{attacker.ref:'B'} if attacked else {}}
                self.reject(s,command,seat='D')
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='semantic.target' or x.state.active_player=='B')
                if attacked:
                    self.assertEqual('semantic.target',s.state.pending_decision.kind)
                    self.reject(s,{'action_id':'choose','targets':[source.ref]})
                    s=self.resume(s);self.assertIsNone(self.settle(s,target=target.ref))
                    self.assertEqual(39,s.state.players['B'].life)
                else:self.assertEqual('B',s.state.active_player)
                self.assertEqual(1 if attacked else 0,s.state.cards[target.object_id].counters.get('+1/+1',0))
                self.assertEqual(0,s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.private_replay(s,private)

    def test_perilous_snare_keeps_entry_exile_and_max_speed_vehicle_activation(self):
        s,source,private=self.fixture(595230,'Perilous Snare')
        target=self.add(s.engine,'Sky Skiff');opposing=self.add(s.engine,'Sky Skiff',seat='B',ref='opposing-skiff')
        victim=self.add(s.engine,'Sol Ring',seat='B');damage=self.add(s.engine,'Shock',zone='hand')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
        s.state.players['A'].stats['speed']=3
        self.prepare(s,mana={'C':2,'W':1,'R':1});self.checkpoint(s)
        self.pay_cast(s,source,payment={'C':2,'W':1});self.assertIsNone(self.settle(s,target=victim.ref))
        self.assertEqual('exile',s.state.cards[victim.object_id].zone)
        self.priority(s,'A')
        self.assertFalse(any(r['id'].startswith('activate:'+source.ref+':') for r in self.actions(s)))
        self.pay_cast(s,damage,['B'],payment={'R':1});self.assertIsNone(self.settle(s))
        self.assertEqual(4,s.state.players['A'].stats['speed'])
        self.priority(s,'A');row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':'))
        self.reject(s,dict(action_id=row['id'],targets=[opposing.ref],pay='auto'))
        command=dict(action_id=row['id'],targets=[target.ref],pay='auto')
        self.reject(s,command,seat='D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.pay_cast(s,bounce,[source.ref],payment={'U':2},seat='B')
        s=self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.cards[target.object_id].counters.get('+1/+1',0))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[victim.object_id].zone)
        self.assertEqual('B',s.state.cards[victim.object_id].controller)
        self.assertEqual(4,s.state.players['A'].stats['speed'])
        self.private_replay(s,private)

    def test_mixed_target_authority_withdrawals_are_specific_canonical_blocks(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        tests=set(discover_tests(ROOT).values())
        names=('Roadside Blowout','Embrace Oblivion','Lotusguard Disciple','Gravkill',"Captain America's Motorcycle",
            'Fang Guardian','Suit Up','Pride of the Road','Urgent Exorcism','Seven-Tail Mentor','Scrap Compactor',
            'Light the Way',"Howler's Heavy",'Daring Demolition','Fire Nation Engineer','Spin Out','Grafted Growth','Perilous Snare')
        for cap in ('target.permanent.characteristic_predicate','target.revalidate_resolution'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',
                blockers=['withdrawn mixed permanent target authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in names:
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                blockers=[b for residual in ir.material_residuals for b in residual.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers),(name,blockers))


if __name__=='__main__':unittest.main()
