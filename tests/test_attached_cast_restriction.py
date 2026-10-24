from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_myrel_composition as controls
import test_ability_label_composition as journeys
from quorune.rules.evidence_storage import read_evidence_document

CAP='casting.prohibition.source_attachment'
BASE='actions.restriction.static_player'
NAMES=("Conqueror's Flail",)


class AttachedCastRestrictionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.MyrelCompositionTests.session
    add=controls.MyrelCompositionTests.add
    prepare=controls.MyrelCompositionTests.prepare
    checkpoint=controls.MyrelCompositionTests.checkpoint
    binding=controls.MyrelCompositionTests.binding
    resolve=controls.MyrelCompositionTests.resolve
    replay=controls.MyrelCompositionTests.replay
    private_replay=controls.MyrelCompositionTests.private_replay
    priority=controls.MyrelCompositionTests.priority
    actions=controls.MyrelCompositionTests.actions
    cast=controls.MyrelCompositionTests.cast
    activate=controls.MyrelCompositionTests.activate
    reject=controls.MyrelCompositionTests.reject
    advance=journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'attached-cast.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-cast-restriction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attached casting restriction',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def activation(self,s,source,*,targets=(),payment=None,needle=None):
        self.priority(s,'A');rows=[r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':')]
        row=next(r for r in rows if needle is None or needle in json.dumps(r).casefold())
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto' if payment is None else 'manual'}
        if payment is not None:command['payment']=payment
        self.reject(s,command,seat='D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);return row['id']

    def blocked(self,s,card,*,seat='B'):
        self.priority(s,seat);self.assertNotIn('cast:'+card.ref,{r['id'] for r in self.actions(s,seat)})
        self.reject(s,{'action_id':'cast:'+card.ref,'targets':['D'],'pay':'auto'},seat=seat)

    def test_schema2_is_opt_in_and_legacy_actor_scopes_stay_unchanged(self):
        from quorune.static_player_restriction_model import StaticPlayerRestrictionSpec as Spec,StaticPlayerRestrictionKind as Kind
        from quorune.compiler.static_player_restrictions import static_player_restriction_template
        from quorune.semantic_runtime.activation_restrictions import FixedPublicActivationProhibitionHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        old=Spec(Kind.SOURCE_TURN_OPPONENTS);self.assertEqual({'schema_version':1,'kind':'source_turn_opponents'},old.to_dict())
        new=Spec(Kind.SOURCE_TURN_OPPONENTS,2,True);self.assertEqual(new,Spec.from_dict(json.loads(json.dumps(new.to_dict()))))
        for actor in 'BCD':
            self.assertTrue(old.applies(actor=actor,active_player='A',source_controller='A'))
            self.assertFalse(new.applies(actor=actor,active_player='A',source_controller='A'))
            self.assertTrue(new.applies(actor=actor,active_player='A',source_controller='A',source_attached_creature=True))
        self.assertFalse(new.applies(actor='A',active_player='A',source_controller='A',source_attached_creature=True))
        self.assertFalse(new.applies(actor='B',active_player='B',source_controller='A',source_attached_creature=True))
        for value in ({'schema_version':1,'kind':'source_turn_opponents','requires_source_attached_creature':True},{'schema_version':2,'kind':'source_turn_opponents','requires_source_attached_creature':False},{'schema_version':2,'kind':'actor_other_turn','requires_source_attached_creature':True}):
            with self.assertRaises(ValueError):Spec.from_dict(value)
        legacy=static_player_restriction_template("During your turn, your opponents can't cast spells or activate abilities of artifacts, creatures, or enchantments.")[1][1]
        with self.assertRaises(SemanticNodeError):FixedPublicActivationProhibitionHandler().validate({**legacy,'player_restriction':new.to_dict()})

    def test_current_attachment_query_rejects_one_sided_departed_and_noncreature(self):
        from quorune.semantic_runtime.static_cast_rules import source_is_attached_to_creature
        source=SimpleNamespace(object_id='equipment',zone='battlefield',phased_out=False,attached_to='body')
        target=SimpleNamespace(object_id='body',logical_object_id='body-lid',zone='battlefield',phased_out=False,attachments=[])
        from quorune.continuous_effect_model import Layer
        types={'body':'Creature — Human'};layers=[]
        def effective(c,**kwargs):
            layers.append(kwargs.get('maximum_layer'));return {'type_line':types[c.object_id]}
        host=SimpleNamespace(state=SimpleNamespace(cards={'equipment':source,'body':target}),_effective_card_data=effective)
        self.assertFalse(source_is_attached_to_creature(host,source));target.attachments.append('equipment');self.assertTrue(source_is_attached_to_creature(host,source))
        target.zone='graveyard';self.assertFalse(source_is_attached_to_creature(host,source));target.zone='battlefield'
        target.phased_out=True;self.assertFalse(source_is_attached_to_creature(host,source));target.phased_out=False
        types['body']='Artifact';self.assertFalse(source_is_attached_to_creature(host,source))
        source.phased_out=True;self.assertFalse(source_is_attached_to_creature(host,source))
        self.assertTrue(layers);self.assertTrue(all(layer is Layer.TYPE for layer in layers))

    def test_original_normal_admission_keeps_all_components_and_requires_own_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(819000);source=self.add(s.engine,NAMES[0],zone='hand');b=self.binding(s,source);self.assertTrue(b['strict_capability_ready'],b['blockers'])
        ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        self.assertIn(BASE,program.capability_dependencies);deps=tuple(c for c in program.capability_dependencies if c!=CAP)
        changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_real_cast_equip_response_move_fizzle_detach_and_reentry(self):
        s=self.session(819010);source=self.add(s.engine,NAMES[0],zone='hand');wolf=self.add(s.engine,'Watchwolf');wizard=self.add(s.engine,'Fugitive Wizard')
        bounce1=self.add(s.engine,'Unsummon',zone='hand',ref='bounce_wizard');bounce2=self.add(s.engine,'Unsummon',zone='hand',ref='bounce_wolf')
        self.add(s.engine,'Mountain');ring=self.add(s.engine,'Sol Ring',seat='B');response_land=self.add(s.engine,'Mountain',seat='B',ref='response_mountain')
        response_land2=self.add(s.engine,'Mountain',seat='B',ref='second_response_mountain')
        shocks=[self.add(s.engine,'Shock',seat='B',zone='hand',ref='opponent_shock'+str(i)) for i in range(4)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');s.state.players['B'].mana_pool['R']=4
        self.prepare(s,mana={'C':8,'U':3});self.checkpoint(s)
        self.cast(s,source,payment={'C':2});self.assertIsNone(self.resolve(s));self.cast(s,shocks[0],['D'],seat='B',payment={'R':1});self.assertIsNone(self.resolve(s))
        self.activation(s,source,targets=[wolf.ref],payment={'C':2},needle='equip');s=self.replay(s,load=True)
        self.cast(s,shocks[1],['D'],seat='B',payment={'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(wolf.object_id,s.state.cards[source.object_id].attached_to);self.assertEqual(6,s.engine._numeric_stat(wolf.object_id,'power'))
        self.blocked(s,shocks[2]);self.activate(s,response_land,seat='B');self.assertEqual(3,s.state.players['B'].mana_pool['R'])
        self.activation(s,source,targets=[wizard.ref],payment={'C':2},needle='equip');s=self.replay(s,load=True);self.blocked(s,shocks[2])
        # An actual B action starts a fresh priority round, so A can respond
        # before everyone passes and the pending equip ability resolves.
        self.activate(s,ring,seat='B');self.assertEqual(2,s.state.players['B'].mana_pool['C'])
        self.priority(s,'A');self.assertTrue(s.state.stack);self.assertEqual(wolf.object_id,s.state.cards[source.object_id].attached_to)
        old=s.state.cards[wizard.object_id].logical_object_id;self.cast(s,bounce1,[wizard.ref],payment={'U':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(wolf.object_id,s.state.cards[source.object_id].attached_to);self.assertEqual(5,s.engine._numeric_stat(wolf.object_id,'power'));self.blocked(s,shocks[2]);self.activate(s,response_land2,seat='B')
        self.cast(s,bounce2,[wolf.ref],payment={'U':1});self.assertIsNone(self.resolve(s));self.assertIsNone(s.state.cards[source.object_id].attached_to)
        self.cast(s,shocks[2],['D'],seat='B',payment={'R':1});self.assertIsNone(self.resolve(s));self.assertEqual(34,s.state.players['D'].life)
        self.cast(s,wizard,payment={'U':1});self.assertIsNone(self.resolve(s));self.assertNotEqual(old,s.state.cards[wizard.object_id].logical_object_id)
        self.activation(s,source,targets=[wizard.ref],payment={'C':2},needle='equip');self.assertIsNone(self.resolve(s));self.assertEqual(2,s.engine._numeric_stat(wizard.object_id,'power'));self.blocked(s,shocks[3]);self.private_replay(s,private)

    def test_real_equipment_controller_defines_colors_turn_and_opponents(self):
        from quorune.attachments import attach_objects
        s=self.session(819020);source=self.add(s.engine,NAMES[0]);wolf=self.add(s.engine,'Watchwolf');self.add(s.engine,'Fugitive Wizard')
        own=[self.add(s.engine,'Shock',zone='hand',ref='controller_own_shock'+str(i)) for i in range(2)]
        magic=self.add(s.engine,'Control Magic',seat='B',zone='hand');steal=self.add(s.engine,'Steal Artifact',seat='B',zone='hand')
        enemy=self.add(s.engine,'Shock',seat='B',zone='hand');seal=self.add(s.engine,'Seal of Cleansing')
        for i in range(2):self.add(s.engine,'Mountain',ref='controller_a_mountain'+str(i))
        self.add(s.engine,'Mountain',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        # Cast/equip is exercised above. This checkpoint starts before the real
        # control changes, with its initial relation committed by the owner.
        attach_objects(s.state.cards,source,wolf,source_timestamp=s.engine._next_zone_timestamp(),players=s.state.players)
        self.prepare(s,actor='B',active='B',mana={'U':8});self.checkpoint(s)
        self.cast(s,magic,[wolf.ref],seat='B');self.assertEqual(4,s.state.players['B'].mana_pool['U']);self.assertIsNone(self.resolve(s));self.assertEqual('B',s.state.cards[wolf.object_id].controller);self.assertEqual(5,s.engine._numeric_stat(wolf.object_id,'power'))
        self.cast(s,own[0],['D']);self.assertIsNone(self.resolve(s));self.cast(s,steal,[source.ref],seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual(0,s.state.players['B'].mana_pool['U'])
        self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller));self.assertEqual(6,s.engine._numeric_stat(wolf.object_id,'power'))
        self.blocked(s,own[1],seat='A');self.cast(s,enemy,['D'],seat='B');self.assertIsNone(self.resolve(s))
        self.activation(s,seal,targets=[source.ref]);self.assertEqual('graveyard',s.state.cards[seal.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(3,s.engine._numeric_stat(wolf.object_id,'power'))
        self.cast(s,own[1],['D']);self.assertIsNone(self.resolve(s));self.assertEqual(34,s.state.players['D'].life);self.private_replay(s,private)

    def test_verified_own_base_withdrawal_and_fresh_attachment_omissions_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap in (CAP,BASE):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn attached casting authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        method='test_real_cast_equip_response_move_fizzle_detach_and_reentry'
        for result in (False,True):
            with patch('quorune.semantic_runtime.static_cast_rules.source_is_attached_to_creature',return_value=result):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
