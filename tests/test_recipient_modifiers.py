from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.recipient_modifier_model import (RecipientQuantityKind as Kind,RecipientQuantitySpec,
    RECIPIENT_MODIFIER_CAPABILITY as CAP,RECIPIENT_MODIFIER_OPERATION as OP)
from quorune.continuous_effects import (CharacteristicState,ContinuousEffect,ContinuousOperation,Layer,evaluate_continuous_effects)
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compound_activation_restrictions as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Knight of New Alara','Light from Within','Clamavus','Diligent Zookeeper')


class RecipientModifierTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundActivationRestrictionTests.session
    start=controls.CompoundActivationRestrictionTests.start
    add=controls.CompoundActivationRestrictionTests.add
    ready=controls.CompoundActivationRestrictionTests.ready
    prepare=controls.CompoundActivationRestrictionTests.prepare
    checkpoint=controls.CompoundActivationRestrictionTests.checkpoint
    resolve=controls.CompoundActivationRestrictionTests.resolve
    replay=controls.CompoundActivationRestrictionTests.replay
    binding=controls.CompoundActivationRestrictionTests.binding
    priority=controls.CompoundActivationRestrictionTests.priority
    private_replay=controls.CompoundActivationRestrictionTests.private_replay
    private=controls.CompoundActivationRestrictionTests.private
    cast=controls.CompoundActivationRestrictionTests.cast
    reject=controls.CompoundActivationRestrictionTests.reject
    until=controls.CompoundActivationRestrictionTests.until
    actions=controls.CompoundActivationRestrictionTests.actions
    action=controls.CompoundActivationRestrictionTests.action
    activate=controls.CompoundActivationRestrictionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'recipient-modifiers.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/recipient-modifier-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Recipient-local scalars',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def stats(self,s,c):return tuple(s.engine._numeric_stat(c.object_id,k) for k in ('power','toughness'))

    def result(self,state,spec,*,counters=None,before=()):
        modifier={'schema_version':1,'quantity':spec.to_dict(),'power':1,'toughness':1}
        effect=ContinuousEffect(effect_id='recipient-test',source_id='source',layer=Layer.POWER_TOUGHNESS,sublayer='7c',timestamp=2,
            operations=(ContinuousOperation(OP,modifier),))
        context={'counters':{}} if counters is None else {'counters':counters}
        return evaluate_continuous_effects(deepcopy(state),(*before,effect),context=context).characteristics

    def test_closed_quantity_and_operation_codecs_reject_unknown_bool_and_wrong_fields(self):
        specs=[RecipientQuantitySpec(Kind.COLORS),RecipientQuantitySpec(Kind.COUNTERS,counter='+1/+1'),
            RecipientQuantitySpec(Kind.MANA_SYMBOLS,mana_color='W'),RecipientQuantitySpec(Kind.CREATURE_TYPES,maximum=10)]
        for spec in specs:
            self.assertEqual(spec,RecipientQuantitySpec.from_dict(spec.to_dict()))
            for update in ({'schema_version':True},{'kind':'power'},{'unknown':1},{'maximum':False},{'maximum':0}):
                with self.assertRaises(ValueError):RecipientQuantitySpec.from_dict({**spec.to_dict(),**update})
            value={'schema_version':1,'quantity':spec.to_dict(),'power':1,'toughness':1}
            operation=ContinuousOperation(OP,value);self.assertEqual(operation,ContinuousOperation.from_dict(operation.to_dict()))
            for update in ({'schema_version':True},{'power':True},{'power':0,'toughness':0},{'quantity':{}},{'unknown':0}):
                with self.assertRaises(ValueError):ContinuousOperation(OP,{**value,**update})
        for values in ({'kind':Kind.COLORS,'counter':'+1/+1'},{'kind':Kind.COUNTERS,'counter':' +1/+1'},
                {'kind':Kind.MANA_SYMBOLS,'mana_color':'WU'},{'kind':Kind.CREATURE_TYPES,'mana_color':'G'}):
            with self.assertRaises(ValueError):RecipientQuantitySpec(**values)

    def test_live_scalar_reads_each_recipients_own_public_values_and_the_explicit_cap(self):
        state=CharacteristicState('recipient',power=2,toughness=6,colors={'W','U','G'},mana_cost='{W/U}{W/P}{2/W}{C/W}{W/U/P}{B}{X}{S}',
            card_types={'Creature','Artifact'},subtypes={'Elf','Druid','Equipment'})
        for spec,amount in [(RecipientQuantitySpec(Kind.COLORS),3),
                (RecipientQuantitySpec(Kind.COUNTERS,counter='+1/+1'),2),
                (RecipientQuantitySpec(Kind.MANA_SYMBOLS,mana_color='W'),5),
                (RecipientQuantitySpec(Kind.CREATURE_TYPES),2)]:
            data=self.result(state,spec,counters={'+1/+1':2,'charge':8});self.assertEqual((2+amount,6+amount),(data['power'],data['toughness']))
        state.subtypes={'Elf','Druid','Wizard','Cleric','Goblin','Shaman','Knight','Rogue','Soldier','Warrior','Beast'}
        data=self.result(state,RecipientQuantitySpec(Kind.CREATURE_TYPES,maximum=10));self.assertEqual((12,16),(data['power'],data['toughness']))
        for bad in ({'+1/+1':True},{'+1/+1':-1},{'+1/+1':1,' +1/+1':2}):
            with self.assertRaises(ValueError):self.result(state,RecipientQuantitySpec(Kind.COUNTERS,counter='+1/+1'),counters=bad)
        for bad in ('{W}garbage','{W/W}','{T}'):
            with self.assertRaises(ValueError):self.result(replace(state,mana_cost=bad),RecipientQuantitySpec(Kind.MANA_SYMBOLS,mana_color='W'))
        from quorune.object_query import ObjectQueryResult
        from quorune.characteristic_fragments import CharacteristicQuantityReduction as Reduction
        from quorune.public_quantity_reductions import reduce_public_quantity
        row=ObjectQueryResult('hybrid','HY','Hybrid','A','A','battlefield',types=('creature',))
        rows=((row,{'mana_cost':'{C/W}{C/U}{C/B}{C/R}{C/G}'},'card'),)
        for reduction in (Reduction.DEVOTION,Reduction.COLORED_MANA_SYMBOLS):
            self.assertEqual(1,reduce_public_quantity(rows,reduction,mana_colors=('W',)))
            self.assertEqual(2,reduce_public_quantity(rows,reduction,mana_colors=('W','U')))

    def test_earlier_copy_color_type_and_base_layers_precede_recipient_quantities(self):
        state=CharacteristicState('recipient',power=2,toughness=6,colors={'G'},mana_cost='{G}',card_types={'Creature'},subtypes={'Human'})
        def effect(op,value,layer,sublayer,stamp):
            return ContinuousEffect(effect_id=op,source_id='other',layer=layer,sublayer=sublayer,timestamp=stamp,operations=(ContinuousOperation(op,value),))
        copy=effect('copy_values',{'mana_cost':'{W}{W}'},Layer.COPY,'1a',0)
        data=self.result(state,RecipientQuantitySpec(Kind.MANA_SYMBOLS,mana_color='W'),before=(copy,));self.assertEqual(4,data['power'])
        face_down=effect('face_down',{'power':2,'toughness':2},Layer.COPY,'1b',1)
        data=self.result(state,RecipientQuantitySpec(Kind.MANA_SYMBOLS,mana_color='W'),before=(copy,face_down))
        self.assertEqual((2,2,''),(data['power'],data['toughness'],data['mana_cost']))
        color=effect('set_colors',['W','U','B'],Layer.COLOR,'5',0)
        base=effect('set_power_toughness',[0,1],Layer.POWER_TOUGHNESS,'7b',99)
        data=self.result(state,RecipientQuantitySpec(Kind.COLORS),before=(base,color));self.assertEqual((3,4),(data['power'],data['toughness']))
        types=effect('add_types',['Elf','Druid'],Layer.TYPE,'4',0)
        types=replace(types,operations=(ContinuousOperation('add_types',['Elf','Druid'],field='subtypes'),))
        data=self.result(state,RecipientQuantitySpec(Kind.CREATURE_TYPES),before=(types,));self.assertEqual(5,data['power'])

    def test_four_originals_have_complete_normal_registration_and_independent_runtime_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.start(790000)
        for name in NAMES:
            source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,result['blockers'])

    def test_knight_actual_cast_colors_source_exclusion_and_cleanup_are_live(self):
        s=self.start(790010);source=self.add(s.engine,'Knight of New Alara',zone='hand')
        own=self.add(s.engine,'Generic Recipient Triad');white=self.add(s.engine,'Generic Recipient White')
        enemy=self.add(s.engine,'Generic Recipient Triad',seat='B',ref='recipient-knight-enemy')
        change=self.add(s.engine,'Gift of Tusks',zone='hand');private=self.private(s);self.ready(s)
        self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.assertEqual((2,2),self.stats(s,source));self.assertEqual((5,9),self.stats(s,own))
        self.assertEqual((2,6),self.stats(s,white));self.assertEqual((2,6),self.stats(s,enemy))
        self.cast(s,change,[own.ref]);self.assertIsNone(self.resolve(s));self.assertEqual((3,3),self.stats(s,own))
        self.until(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
        self.assertEqual((5,9),self.stats(s,own));self.private_replay(s,private)

    def test_light_uses_own_cost_not_current_colors_and_composes_with_base_setting_and_departure(self):
        s=self.start(790020);source=self.add(s.engine,'Light from Within',zone='hand')
        white=self.add(s.engine,'Generic Recipient White');tri=self.add(s.engine,'Generic Recipient Triad')
        blue=self.add(s.engine,'Generic Recipient Blue');enemy=self.add(s.engine,'Generic Recipient White',seat='B',ref='recipient-light-enemy')
        change=self.add(s.engine,'Gift of Tusks',zone='hand');humble=self.add(s.engine,'Humble',zone='hand')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        for c,stats in [(white,(4,8)),(tri,(3,7)),(blue,(2,6)),(enemy,(2,6))]:self.assertEqual(stats,self.stats(s,c))
        self.cast(s,change,[white.ref]);self.assertIsNone(self.resolve(s));self.assertEqual((5,5),self.stats(s,white))
        self.cast(s,humble,[white.ref]);self.assertIsNone(self.resolve(s));self.assertEqual((2,3),self.stats(s,white))
        self.cast(s,bounce,[source.ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual((0,1),self.stats(s,white))
        self.assertEqual((2,6),self.stats(s,tri));self.private_replay(s,private)

    def test_clamavus_adds_recipient_counters_and_ordinary_counter_bonus_without_sharing_source_counters(self):
        s=self.start(790030);source=self.add(s.engine,'Clamavus',zone='hand');own=self.add(s.engine,'Generic Recipient White')
        enemy=self.add(s.engine,'Generic Recipient White',seat='B',ref='recipient-clam-enemy');enemy.counters['+1/+1']=2
        first=self.add(s.engine,'Battlegrowth',zone='hand',ref='recipient-clam-first')
        second=self.add(s.engine,'Battlegrowth',zone='hand',ref='recipient-clam-second')
        humble=self.add(s.engine,'Humble',zone='hand');private=self.private(s);self.ready(s)
        self.cast(s,source);self.assertIsNone(self.resolve(s));self.assertEqual((2,6),self.stats(s,own))
        self.cast(s,first,[source.ref]);self.assertIsNone(self.resolve(s));self.assertEqual((5,5),self.stats(s,source));self.assertEqual((2,6),self.stats(s,own))
        self.cast(s,second,[own.ref]);self.assertIsNone(self.resolve(s));self.assertEqual((4,8),self.stats(s,own));self.assertEqual((4,8),self.stats(s,enemy))
        self.cast(s,humble,[source.ref]);self.assertIsNone(self.resolve(s));self.assertEqual((1,2),self.stats(s,source));self.assertEqual((3,7),self.stats(s,own))
        self.until(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
        self.assertEqual((4,8),self.stats(s,own));self.private_replay(s,private)

    def test_zookeeper_uses_distinct_creature_types_clamps_ten_and_excludes_humans_and_changelings(self):
        s=self.start(790040);source=self.add(s.engine,'Diligent Zookeeper',zone='hand')
        tri=self.add(s.engine,'Generic Recipient Triad');many=self.add(s.engine,'Generic Recipient Many')
        human=self.add(s.engine,'Generic Recipient Human');relic=self.add(s.engine,'Generic Recipient Relic')
        changeling=self.add(s.engine,'Universal Automaton');enemy=self.add(s.engine,'Generic Recipient Many',seat='B',ref='recipient-zoo-enemy')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        for c,stats in [(source,(4,4)),(tri,(4,8)),(many,(12,16)),(human,(2,6)),(relic,(3,7)),(changeling,(1,1)),(enemy,(2,6))]:
            self.assertEqual(stats,self.stats(s,c),c.printed_name)
        self.cast(s,bounce,[source.ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual((2,6),self.stats(s,many))
        self.private_replay(s,private)

    def test_native_control_changes_rebind_the_subjects_and_restore_without_a_new_incarnation(self):
        s=self.start(790050);source=self.add(s.engine,'Knight of New Alara',zone='hand')
        own=self.add(s.engine,'Generic Recipient Triad');enemy=self.add(s.engine,'Generic Recipient Triad',seat='B',ref='recipient-control-enemy')
        aura=self.add(s.engine,'Control Magic',seat='B',zone='hand');bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand')
        lands=[self.add(s.engine,name,seat='B',ref='recipient-control-mana-'+str(i)) for i,name in enumerate(('Island',)*4+('Plains',)*2)]
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s));logical=s.state.cards[source.object_id].logical_object_id
        self.until(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
        for land in (lands[0],lands[1],lands[4],lands[5]):
            ability=next(a for a in s.engine._activated_abilities(s.state.cards[land.object_id]) if a.mana_ability)
            self.activate(s,land,ability.ability_id,seat='B')
        self.cast(s,aura,[source.ref],seat='B',payment={'W':2,'U':2});self.assertIsNone(self.resolve(s))
        self.assertEqual((2,6),self.stats(s,own));self.assertEqual((5,9),self.stats(s,enemy))
        self.cast(s,bounce,[aura.ref],seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual(logical,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual((5,9),self.stats(s,own));self.assertEqual((2,6),self.stats(s,enemy));self.private_replay(s,private)

    def test_unsupported_quantity_feedback_unknown_labels_and_old_operation_wire_remain_bounded(self):
        from quorune.compiler.recipient_modifiers import recipient_modifier_handler
        for text in ['Creatures you control get +1/+1 for each of its colors and have flying.',
                'Creatures you control get +1/+1 for each point of its power.',
                'Creatures you control get +1/+1 for each card in its controller\'s hand.',
                'Creatures you control get +1/+1 for each of its colors, to a maximum of 0.',
                'Max speed — Creatures you control get +1/+1 for each of its colors.',
                'Level 2 — Creatures you control get +1/+1 for each of its colors.',
                'If you control an artifact — Creatures you control get +1/+1 for each of its colors.']:
            self.assertIsNone(recipient_modifier_handler(text),text)
        old={'op':'modify_power_toughness','value':[2,-1],'field':None}
        self.assertEqual(old,ContinuousOperation.from_dict(old).to_dict())
        for op,value in [('set_power',5),('set_toughness',7),('set_power_toughness',[4,8]),('switch_power_toughness',None)]:
            effect=ContinuousEffect(effect_id=op,source_id='s',layer=Layer.POWER_TOUGHNESS,sublayer='7b',timestamp=0,operations=(ContinuousOperation(op,value),))
            data=evaluate_continuous_effects(CharacteristicState('legacy',power=2,toughness=6),(effect,)).characteristics
            expected={'set_power':(5,6),'set_toughness':(2,7),'set_power_toughness':(4,8),'switch_power_toughness':(6,2)}[op]
            self.assertEqual(expected,(data['power'],data['toughness']))

    def test_verified_capability_withdrawal_refuses_the_normal_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn recipient authority'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Clamavus'),capability_registry=registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);blocks=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blocks),blocks);self.assertFalse(any('evidence_index:unverified' in b for b in blocks))

    def test_fresh_compiler_and_recipient_quantity_omissions_are_killed(self):
        method='test_four_originals_have_complete_normal_registration_and_independent_runtime_minima'
        with patch('quorune.compiler.recipient_modifiers.recipient_modifier_handler',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_knight_actual_cast_colors_source_exclusion_and_cleanup_are_live'
        with patch('quorune.continuous_stat_operations.recipient_quantity_amount',return_value=0):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_light_uses_own_cost_not_current_colors_and_composes_with_base_setting_and_departure'
        with patch('quorune.continuous_stat_operations.colored_mana_symbol_count',return_value=0):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
