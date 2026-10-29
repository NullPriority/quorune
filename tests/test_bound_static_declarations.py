"""CR611.1/611.3: rules supplied by a giver are not granted receiver abilities."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_enchanted_player_statics as native
from quorune.ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
from quorune.bound_declaration_model import BoundStaticDeclarationRulesSpec,BOUND_DECLARATION_CAPABILITY as CAPABILITY
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.bound_static_declarations import current_bound_declaration_rules
from scripts.build_test_database import build_fixture_database

HERALD="Creatures you control with +1/+1 counters on them can't be blocked."
MIXED='All creatures have double strike and attack each combat if able.'
CONDITIONAL="As long as you have the city's blessing, Detectives you control can't be blocked."


class BoundStaticDeclarationCompilerTests(unittest.TestCase):
    def test_direct_mixed_conditional_and_attached_rules_have_giver_owned_fragments(self):
        from quorune.bound_declaration_model import BOUND_DECLARATION_HANDLER
        registry=load_default_capability_registry()
        for text in (HERALD,MIXED,CONDITIONAL,'Enchanted creature attacks each combat if able.',
                "As long as enchanted creature is blue, it gets +1/+1 and can't be blocked."):
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(text,ir.material_residuals));node,=ir.faces[0].nodes
            self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end));self.assertIn(CAPABILITY,node.capability_dependencies)
            owned=[h for h in node.handlers if h['handler_id']==BOUND_DECLARATION_HANDLER];self.assertEqual(1,len(owned))
            self.assertIsInstance(ability_fragment_from_dict(owned[0]['fragment']),BoundStaticDeclarationRulesSpec)
            self.assertFalse(any(v.get('kind') in {'declaration_requirement','declaration_restriction'}
                for h in node.handlers for v in h.get('modifier',{}).get('add_ability_fragments',())))
        for text in (HERALD+' Draw a card.',HERALD.replace('+1/+1 counters','chosen counters'),
                MIXED.replace('each combat if able','during the chosen phase')):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)

    def test_closed_codec_reserves_authority_and_relative_receiver_rule_scopes(self):
        ir=compile_oracle_card(query_record(HERALD),capability_registry=load_default_capability_registry())
        value=ir.faces[0].nodes[0].handlers[0]['fragment'];spec=ability_fragment_from_dict(value)
        self.assertEqual(spec,ability_fragment_from_dict(ability_fragment_to_dict(spec)))
        for mutate in (lambda v:v.update(schema_version=True),lambda v:v.update(giver='A'),
                lambda v:v.update(controller='B'),lambda v:v.update(rules=[]),
                lambda v:v['query'].update(controller='B'),lambda v:v['query'].update(known_to_actor=True),
                lambda v:v['rules'][0]['value'].update(scope='global')):
            raw=deepcopy(value['value']);mutate(raw)
            with self.assertRaises(ValueError):BoundStaticDeclarationRulesSpec.from_dict(raw)

    def test_verified_rule_query_and_condition_owners_withdraw_without_hiding_unrelated_support(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner,text in ((CAPABILITY,HERALD),('combat.declaration.typed_components',HERALD),
                ('state_query.permanent.public_state_predicate',HERALD),
                ('continuous.characteristics.fixed_public_state',CONDITIONAL),('player.designation.citys_blessing',CONDITIONAL),
                ('variant.monarch.designate',HERALD)):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['Bound rule owner withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            result=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(owner=='variant.monarch.designate',result.status=='exact',owner)


class BoundStaticDeclarationNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.EnchantedPlayerStaticNativeTests.session
    add=native.EnchantedPlayerStaticNativeTests.add
    prepare=native.EnchantedPlayerStaticNativeTests.prepare
    cast=native.EnchantedPlayerStaticNativeTests.cast
    cast_for=native.EnchantedPlayerStaticNativeTests.cast_for
    action=native.EnchantedPlayerStaticNativeTests.action
    priority=native.EnchantedPlayerStaticNativeTests.priority
    checkpoint=native.EnchantedPlayerStaticNativeTests.checkpoint
    resolve=native.EnchantedPlayerStaticNativeTests.resolve
    replay=native.EnchantedPlayerStaticNativeTests.replay
    assert_closed=native.EnchantedPlayerStaticNativeTests.assert_closed
    until=native.EnchantedPlayerStaticNativeTests.until
    stats=native.EnchantedPlayerStaticNativeTests.stats

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'bound-rules.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/bound-static-declaration-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bound rules',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def scene(self,seed,*,counter=True):
        session=self.session(seed);giver=self.add(session.engine,'Herald of Secret Streams',seat='B');giver.acquired_control_turn_count=-1
        body=self.add(session.engine,'Grizzly Bears',seat='B');body.acquired_control_turn_count=-1
        if counter:body.counters['+1/+1']=1
        blocker=self.add(session.engine,'Wind Drake',seat='C');blocker.acquired_control_turn_count=-1
        self.add(session.engine,'Levitation',seat='B');self.add(session.engine,'Vedalken Orrery')
        aura=self.add(session.engine,"Kasmina's Transmutation",zone='hand');private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,giver);self.assert_closed(session,aura)
        return session,giver,body,blocker,aura,private

    def attack(self,session,attacks):
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:B',{'action_id':'attack','attackers':attacks});self.assertTrue(result.ok,result.summary)

    def blockers(self,session):
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')

    def test_actual_herald_rejects_block_after_receiver_blanking_while_true_flying_grant_is_removed(self):
        session,giver,body,blocker,aura,private=self.scene(611624010)
        removal=self.add(session.engine,'Unsummon',zone='hand')
        self.add(session.engine,'Island',ref='later-removal-island')
        self.assertIn('flying',session.engine._combat_keywords(body))
        self.prepare(session,active='B',mana={'C':1,'U':2});self.checkpoint(session)
        self.cast_for(session,'A',aura,[body.ref],reserve={'U':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((2,2),self.stats(session,body));self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.attack(session,{body.ref:'C',giver.ref:'C'});self.blockers(session)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:body.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{'action_id':'block','blocks':{blocker.ref:giver.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:giver.ref}});self.assertTrue(result.ok,result.summary)
        self.cast(session,'A',removal,[giver.ref]);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.engine._can_block(session.state.cards[body.object_id],session.state.cards[blocker.object_id])[0])
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_counter_membership_changes_and_later_unblockability_does_not_erase_declared_blockers(self):
        session,giver,body,blocker,aura,_private=self.scene(611624020)
        hexmage=self.add(session.engine,'Vampire Hexmage',seat='B');growth=self.add(session.engine,'Battlegrowth',seat='B',zone='hand')
        self.add(session.engine,'Forest',seat='B',ref='later-growth-forest')
        self.prepare(session,active='B',mana={'C':1,'U':1});session.state.players['B'].mana_pool['G']=1;self.checkpoint(session)
        self.cast(session,'A',aura,[body.ref]);self.assertIsNone(self.resolve(session));self.attack(session,{body.ref:'C',giver.ref:'C'})
        self.priority(session,'B');offered=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+hexmage.ref+':'))
        result=session.act('pilot:B',{'action_id':offered['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[body.object_id].counters);self.blockers(session)
        result=session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:body.ref}});self.assertTrue(result.ok,result.summary)
        declared=deepcopy(session.state.combat.blockers);self.assertEqual([blocker.object_id],declared[body.object_id])
        self.cast(session,'B',growth,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[body.object_id].counters['+1/+1']);self.assertEqual(declared,session.state.combat.blockers)
        self.replay(session,load=True)

    def test_actual_steel_retains_conditional_rule_and_stat_bonuses_but_receiver_loses_lifelink(self):
        session=self.session(611624030);body=self.add(session.engine,'Deputy of Acquittals',seat='B');body.acquired_control_turn_count=-1
        giver=self.add(session.engine,'Steel of the Godhead',seat='B');giver.attached_to=body.object_id;body.attachments.append(giver.object_id)
        blocker=self.add(session.engine,'Grizzly Bears',seat='C');other=self.add(session.engine,'Grizzly Bears',seat='B',ref='other-attacker');other.acquired_control_turn_count=-1
        self.add(session.engine,'Vedalken Orrery');aura=self.add(session.engine,"Kasmina's Transmutation",zone='hand')
        self.assert_closed(session,giver);self.assertIn('lifelink',session.engine._combat_keywords(body))
        self.prepare(session,active='B',mana={'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',aura,[body.ref]);self.assertIsNone(self.resolve(session));self.assertEqual((3,3),self.stats(session,body))
        self.assertNotIn('lifelink',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.attack(session,{body.ref:'C',other.ref:'C'});self.blockers(session);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:body.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:other.ref}});self.assertTrue(result.ok,result.summary);self.replay(session,load=True)

    def test_actual_avatar_keeps_attack_requirement_after_receiver_loses_granted_double_strike(self):
        session=self.session(611624040);giver=self.add(session.engine,'Avatar of Slaughter',seat='B');giver.acquired_control_turn_count=-1
        body=self.add(session.engine,'Grizzly Bears',seat='B');body.acquired_control_turn_count=-1
        self.add(session.engine,'Vedalken Orrery');aura=self.add(session.engine,"Kasmina's Transmutation",zone='hand')
        self.assert_closed(session,giver);self.prepare(session,active='B',mana={'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',aura,[body.ref]);self.assertIsNone(self.resolve(session));self.assertNotIn('double strike',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'attack','attackers':{giver.ref:'C'}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'attack','attackers':{giver.ref:'C',body.ref:'D'}});self.assertTrue(result.ok,result.summary);self.replay(session,load=True)

    def test_actual_grand_melee_requires_able_attack_and_block_after_blocker_ability_loss(self):
        session=self.session(611624045);giver=self.add(session.engine,'Grand Melee')
        body=self.add(session.engine,'Grizzly Bears',seat='B',ref='melee-attacker');body.acquired_control_turn_count=-1
        blocker=self.add(session.engine,'Grizzly Bears',seat='C',ref='melee-blocker');blocker.acquired_control_turn_count=-1
        self.add(session.engine,'Vedalken Orrery');aura=self.add(session.engine,"Kasmina's Transmutation",zone='hand')
        self.assert_closed(session,giver);self.prepare(session,active='B',mana={'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',aura,[blocker.ref]);self.assertIsNone(self.resolve(session))
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'attack','attackers':{}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'attack','attackers':{body.ref:'C'}});self.assertTrue(result.ok,result.summary);self.blockers(session)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'block','blocks':{}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:body.ref}});self.assertTrue(result.ok,result.summary);self.replay(session,load=True)

    def test_actual_detective_uses_current_giver_city_blessing_after_ascend_and_receiver_blanking(self):
        from quorune.city_blessing_model import player_has_citys_blessing
        session=self.session(611624050);giver=self.add(session.engine,'Detective of the Month',seat='B')
        body=self.add(session.engine,'Novice Inspector',seat='B');body.acquired_control_turn_count=-1
        other=self.add(session.engine,'Grizzly Bears',seat='B',ref='detective-other-attacker');other.acquired_control_turn_count=-1
        blocker=self.add(session.engine,'Grizzly Bears',seat='C',ref='detective-blocker')
        for index in range(6):self.add(session.engine,'Plains',seat='B',ref='ascend-land-'+str(index))
        land=self.add(session.engine,'Plains',seat='B',zone='hand',ref='tenth-ascend-permanent')
        self.add(session.engine,'Vedalken Orrery');aura=self.add(session.engine,"Kasmina's Transmutation",zone='hand')
        self.assert_closed(session,giver);self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'C':1,'U':1})
        self.assertFalse(player_has_citys_blessing(session.state.players['B']));self.checkpoint(session)
        result=session.act('pilot:B',{'action_id':'play-land:'+land.ref});self.assertTrue(result.ok,result.summary)
        self.assertTrue(player_has_citys_blessing(session.state.players['B']))
        self.cast(session,'A',aura,[body.ref]);self.assertIsNone(self.resolve(session))
        self.attack(session,{body.ref:'C',other.ref:'C'});self.blockers(session);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:body.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:C',{'action_id':'block','blocks':{blocker.ref:other.ref}});self.assertTrue(result.ok,result.summary);self.replay(session,load=True)

    def test_runtime_bound_rule_and_dynamic_query_owners_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(611624060);source=self.add(session.engine,'Herald of Secret Streams');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        self.assertGreater(checked,0)

    def test_current_binding_reacts_to_control_phasing_giver_presence_and_fails_omission_mutant(self):
        from quorune.declaration_fragments import DeclarationRequirementTemplate,DeclarationRestrictionTemplate
        session,giver,body,_blocker,_aura,_private=self.scene(611624070)
        self.assertTrue(any(isinstance(f,BoundStaticDeclarationRulesSpec) for f in session.engine._effective_ability_fragments(giver)))
        self.assertFalse(any(isinstance(f,(BoundStaticDeclarationRulesSpec,DeclarationRequirementTemplate,DeclarationRestrictionTemplate))
            for f in session.engine._effective_ability_fragments(body)))
        def witness():self.assertTrue(current_bound_declaration_rules(session.engine,body,error_type=ValueError))
        witness()
        reader=session.engine._effective_ability_fragments
        with patch.object(session.engine,'_effective_ability_fragments',side_effect=lambda card,**kwargs:() if card.object_id==giver.object_id else reader(card,**kwargs)),self.assertRaises(AssertionError):witness()
        session.engine.change_control(body.object_id,'C',reason='Bound rule current controller fixture');self.assertFalse(current_bound_declaration_rules(session.engine,body,error_type=ValueError))
        session.engine.change_control(giver.object_id,'C',reason='Bound rule giver controller fixture');witness()
        giver=session.state.cards[giver.object_id];giver.phased_out=True;self.assertFalse(current_bound_declaration_rules(session.engine,body,error_type=ValueError));giver.phased_out=False;witness()
