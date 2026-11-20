"""Pinned CR609.4/509: a source-owned assumption changes only block legality."""
from dataclasses import FrozenInstanceError,asdict,replace
import json,unittest
from quorune.block_as_though_model import (
    BlockAsThoughPermission as Permission,BlockAsThoughAssumptions as Assumptions,
    BLOCK_AS_THOUGH_HANDLER as HANDLER,BLOCK_AS_THOUGH_CAPABILITY as CAP,
)
from quorune.combat_evasion import CombatantEvasionCharacteristics as Facts,combat_evasion_verdict
from quorune.compiler.block_as_though_templates import static_block_as_though_handler
from quorune.semantic_runtime.block_as_though import BlockAsThoughFragmentHandler
from quorune.semantic_runtime.context import SemanticNodeError


def combatant(*keywords):return Facts(frozenset(keywords),frozenset(),frozenset({'creature'}),2)
def verdict(assumptions,attacker=frozenset(),blocker=frozenset()):
    a,b=assumptions.keywords(attacker,blocker)
    return combat_evasion_verdict(combatant(*a),combatant(*b),frozenset())


class BlockAsThoughModelTests(unittest.TestCase):
    def test_five_closed_source_scope_assumptions_are_immutable_and_round_trip(self):
        for scope,mode in (('self','attacker_without_shadow'),('self','blocker_with_shadow'),
                           ('self','reach_against_dragons'),('attached_blocker','attacker_without_shadow'),
                           ('controlled_tapped_blocker','untapped')):
            spec=Permission(scope,mode);self.assertEqual(spec,Permission.from_dict(json.loads(json.dumps(spec.to_dict()))))
            with self.assertRaises(FrozenInstanceError):spec.scope='global'
    def test_open_versions_unknown_assumptions_and_cross_scope_widening_fail_closed(self):
        raw=Permission('self','blocker_with_shadow').to_dict()
        for changed in ({'schema_version':True},{'schema_version':2},{'scope':'global'},
                        {'scope':'attached_blocker'},{'assumption':'flying'},{'extra':True}):
            with self.assertRaises((TypeError,ValueError)):Permission.from_dict({**raw,**changed})
        with self.assertRaises(ValueError):Assumptions(untapped=1)
    def test_distinct_shadow_wordings_preserve_the_pinned_real_shadow_blocker_ruling(self):
        ignore=Assumptions(attacker_without_shadow=True);pretend=Assumptions(blocker_with_shadow=True)
        for attacker in (frozenset(),frozenset({'shadow'})):
            self.assertTrue(verdict(ignore,attacker).allowed)
            self.assertTrue(verdict(pretend,attacker).allowed)
            self.assertFalse(verdict(ignore,attacker,frozenset({'shadow'})).allowed)
        self.assertFalse(verdict(pretend,frozenset(),frozenset({'shadow'})).allowed)
        self.assertTrue(verdict(pretend,frozenset({'shadow'}),frozenset({'shadow'})).allowed)
    def test_intersecting_as_though_conditions_use_the_stated_evaluation_and_keep_flying(self):
        both=Assumptions(attacker_without_shadow=True,blocker_with_shadow=True)
        self.assertTrue(verdict(both,frozenset({'shadow'})).allowed)
        self.assertFalse(verdict(both,frozenset({'shadow'}),frozenset({'shadow'})).allowed)
        self.assertEqual('attacker_has_flying',verdict(both,frozenset({'shadow','flying'})).reason)
        self.assertTrue(verdict(Assumptions(attacker_without_shadow=True,reach_against_dragons=True),frozenset({'shadow','flying'})).allowed)
    def test_assumptions_do_not_change_actual_characteristics_or_old_evasion_field_shapes(self):
        actual=combatant('shadow');before=asdict(actual)
        Assumptions(attacker_without_shadow=True).keywords(actual.keywords,frozenset())
        self.assertEqual(before,asdict(actual));self.assertEqual({'keywords','colors','card_types','power'},set(before))
        for bad in ({'shadow'},frozenset({'Shadow'}),frozenset({''})):
            with self.assertRaises(ValueError):Assumptions().keywords(bad,frozenset())

    def test_new_fragment_round_trip_preserves_existing_source_owned_landwalk_wire_shape(self):
        from quorune.ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
        from quorune.landwalk_permission_model import LandwalkBlockPermission
        old={'kind':'landwalk_block_permission','value':{'schema_version':1,'scope':'attached_blocker','keyword':None}}
        self.assertEqual(old,ability_fragment_to_dict(ability_fragment_from_dict(old)))
        self.assertIsInstance(ability_fragment_from_dict(old),LandwalkBlockPermission)
        new=Permission('self','blocker_with_shadow');self.assertEqual(new,ability_fragment_from_dict(ability_fragment_to_dict(new)))


class BlockAsThoughRuleTests(unittest.TestCase):
    def test_current_source_control_ability_and_phasing_gate_only_tapped_eligibility(self):
        from types import SimpleNamespace as Row
        from quorune.rules.block_as_though import blocker_tap_allows_block
        source=Row(object_id='giver',logical_object_id='giver@1',ref='giver',controller='A',zone='battlefield',phased_out=False,
                   fragments=(Permission('controlled_tapped_blocker','untapped'),))
        blocker=Row(object_id='body',logical_object_id='body@1',ref='body',controller='A',zone='battlefield',phased_out=False,tapped=True,fragments=())
        host=Row(state=Row(cards={'giver':source,'body':blocker}),_effective_ability_fragments=lambda card:card.fragments)
        self.assertTrue(blocker_tap_allows_block(host,blocker));self.assertTrue(blocker.tapped)
        source.phased_out=True;self.assertFalse(blocker_tap_allows_block(host,blocker));source.phased_out=False
        blocker.controller='B';self.assertFalse(blocker_tap_allows_block(host,blocker));source.controller='B'
        self.assertTrue(blocker_tap_allows_block(host,blocker));source.fragments=()
        self.assertFalse(blocker_tap_allows_block(host,blocker));blocker.tapped=False;self.assertTrue(blocker_tap_allows_block(host,blocker))

    def test_aura_rule_uses_reciprocal_current_attachment_and_not_recipient_abilities(self):
        from types import SimpleNamespace as Row
        from quorune.rules.block_as_though import current_block_as_though_assumptions
        source=Row(object_id='aura',logical_object_id='aura@1',ref='aura',controller='C',zone='battlefield',phased_out=False,
                   attached_to='body',fragments=(Permission('attached_blocker','attacker_without_shadow'),))
        blocker=Row(object_id='body',logical_object_id='body@1',ref='body',controller='A',zone='battlefield',phased_out=False,tapped=False,attachments=['aura'],fragments=())
        host=Row(state=Row(cards={'aura':source,'body':blocker}),_effective_ability_fragments=lambda card:card.fragments)
        self.assertTrue(current_block_as_though_assumptions(host,blocker).attacker_without_shadow)
        blocker.attachments=[];self.assertFalse(current_block_as_though_assumptions(host,blocker).attacker_without_shadow)
        blocker.attachments=['aura'];blocker.phased_out=True;self.assertFalse(current_block_as_though_assumptions(host,blocker).attacker_without_shadow)


class BlockAsThoughCompilerTests(unittest.TestCase):
    def test_exact_five_clauses_keep_aether_web_stats_reach_and_one_source_owned_rule(self):
        texts=(
         "This creature can block creatures with shadow as though they didn't have shadow.",
         'This creature can block creatures with shadow as though it had shadow.',
         'This creature can block Dragons as though it had reach.',
         'Tapped creatures you control can block as though they were untapped.',
         "Enchanted creature gets +1/+1, has reach, and can block creatures with shadow as though they didn't have shadow.")
        for i,text in enumerate(texts):
            compiled=static_block_as_though_handler(text,source_name='Generic Rule Source');self.assertIsNotNone(compiled)
            self.assertIn(CAP,compiled[2]);self.assertEqual(2 if i==4 else 1,len(compiled[1]))
            handler=BlockAsThoughFragmentHandler();self.assertIsInstance(handler.validate(compiled[1][0]),Permission)
            if i==4:
                modifier=compiled[1][1]['modifier'];self.assertEqual((1,1),(modifier['power'],modifier['toughness']))
                self.assertIn('Reach',modifier['add_abilities'])
    def test_unrepresented_attacker_types_recipients_temporary_forms_and_riders_stay_residual(self):
        for text in (
         'This creature can block Demons as though it had reach.',
         'Creatures you control can block creatures with shadow as though they had shadow.',
         'Tapped creatures can block as though they were untapped.',
         'Tapped creatures you control can attack as though they were untapped.',
         'This creature can block creatures with shadow as though it had shadow until end of turn.',
         'This creature can block Dragons as though it had reach. Draw a card.'):
            self.assertIsNone(static_block_as_though_handler(text,source_name='Generic Rule Source'))


class BlockAsThoughHandlerTests(unittest.TestCase):
    def test_explicit_component_discriminator_version_scope_and_fine_authority_are_closed(self):
        compiled=static_block_as_though_handler('This creature can block Dragons as though it had reach.',source_name='Generic Rule Source')
        descriptor=compiled[1][0];handler=BlockAsThoughFragmentHandler()
        self.assertEqual((CAP,),handler.capability_dependencies);self.assertEqual((handler.validate(descriptor),),handler.lower(descriptor,object()))
        for change in ({'schema_version':True},{'schema_version':2},{'event':'combat.attack'}, {'handler_id':'old-handler'}, {'unknown':1}):
            with self.assertRaises(SemanticNodeError):handler.validate({**descriptor,**change})
        with self.assertRaises(SemanticNodeError):handler.validate({**descriptor,'fragment':{**descriptor['fragment'],'kind':'landwalk_block_permission'}})


from pathlib import Path
import tempfile
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from quorune.record import authoritative_state_hash

FIXTURE=ROOT/'tests/fixtures/bounded-block-as-though-cards.json'
CANDIDATES=('Aether Web','Aetherflame Wall','Dragon Hunter','Heartwood Dryad','Masako the Humorless','Wall of Diffusion')


class BlockAsThoughRegistrationTests(unittest.TestCase):
    def test_six_complete_originals_and_existing_landwalk_control_bind_normally(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'block-originals.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,'Street Savvy'):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertTrue(bound['strict_capability_ready'],bound['blockers']);self.assertEqual('capability_closed',bound['trust_basis'])


class BlockAsThoughGameplayTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'block-as-though.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bounded block assumptions',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])
    def reject(self,s,command,seat='A'):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))
    def cast_closed(self,s,seat,card,targets=()):
        self.assert_closed(s,card);self.priority(s,seat)
        command={'action_id':'cast:'+s.state.cards[card.object_id].ref,'targets':list(targets),'pay':'auto'}
        self.reject(s,command,'D' if seat!='D' else 'C');self.cast(s,seat,card,targets);self.assertIsNone(self.resolve(s))
    def attack_for(self,s,seat,assignments):
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.attackers')
        self.assertEqual([seat],s.state.pending_decision.actors)
        command={'action_id':'attack','attackers':{card.ref:target for card,target in assignments}}
        self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
    def blockers(self,s,seat='A'):
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        self.assertEqual([seat],s.state.pending_decision.actors)
        return s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal_blocks']
    def block(self,s,blocker,attacker,seat='A'):
        command={'action_id':'block','blocks':{blocker.ref:attacker.ref}}
        self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.assertIn(blocker.object_id,s.state.combat.blockers.get(attacker.object_id,()))
    def lands(self,s,seat,name,count):
        return [self.add(s.engine,name,seat=seat,ref='block-real-mana-'+seat+'-'+name+'-'+str(i)) for i in range(count)]
    def can(self,s,attacker,blocker):
        return s.engine._can_block(s.state.cards[attacker.object_id],s.state.cards[blocker.object_id])[0]

    def test_actual_three_shadow_blockers_cast_preserve_siblings_and_offer_both_attacker_kinds(self):
        for i,name in enumerate(('Aetherflame Wall','Heartwood Dryad','Wall of Diffusion')):
            with self.subTest(name=name):
                s=self.session(933010+i);source=self.add(s.engine,name,zone='hand')
                plain=self.add(s.engine,'Grizzly Bears',seat='B');shadow=self.add(s.engine,'Soltari Foot Soldier',seat='B')
                flyer=self.add(s.engine,'Wind Drake',seat='B')
                self.prepare(s,mana={k:20 for k in 'CWUBRG'});self.checkpoint(s);self.cast_closed(s,'A',source)
                if name!='Heartwood Dryad':self.assertIn('defender',s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.assertNotIn('shadow',s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.assertTrue(self.can(s,plain,source));self.assertTrue(self.can(s,shadow,source));self.assertFalse(self.can(s,flyer,source))
                if name=='Aetherflame Wall':
                    action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
                    result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                    self.assertEqual(1,s.engine._numeric_stat(source.object_id,'power'))
                self.until(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
                self.attack_for(s,'B',[(plain,'A'),(shadow,'A'),(flyer,'A')]);legal=self.blockers(s)
                self.assertEqual({plain.ref,shadow.ref},set(legal[source.ref]));self.reject(s,{'action_id':'block','blocks':{source.ref:flyer.ref}})
                s=self.replay(s,load=True);self.block(s,s.state.cards[source.object_id],s.state.cards[shadow.object_id]);self.replay(s,load=True)

    def test_actual_aetherflame_gains_real_shadow_and_cannot_block_either_kind(self):
        for i,name in enumerate(('Aetherflame Wall','Aether Web')):
            with self.subTest(name=name):
                s=self.session(933020+i);source=self.add(s.engine,name,zone='hand');rift=self.add(s.engine,'Shadow Rift',zone='hand')
                body=self.add(s.engine,'Grizzly Bears',ref='web-real-shadow-blocker') if name=='Aether Web' else source
                shadow=self.add(s.engine,'Soltari Foot Soldier',seat='B');plain=self.add(s.engine,'Grizzly Bears',seat='B',ref='real-shadow-plain-attacker')
                self.prepare(s,mana={k:20 for k in 'CWUBRG'});self.checkpoint(s)
                self.cast_closed(s,'A',source,[body.ref] if name=='Aether Web' else ());self.cast_closed(s,'A',rift,[body.ref])
                self.assertIn('shadow',s.engine._combat_keywords(s.state.cards[body.object_id]))
                self.assertFalse(self.can(s,shadow,body));self.assertFalse(self.can(s,plain,body));self.replay(s,load=True)

    def test_actual_dragon_hunter_has_only_dragon_reach_and_retains_protection_during_real_block(self):
        s=self.session(933030);source=self.add(s.engine,'Dragon Hunter',zone='hand')
        dragon=self.add(s.engine,'Shivan Dragon',seat='B');bird=self.add(s.engine,'Wind Drake',seat='B')
        self.prepare(s,mana={'W':1});self.checkpoint(s);self.cast_closed(s,'A',source)
        self.assertNotIn('reach',s.engine._combat_keywords(s.state.cards[source.object_id]));self.assertTrue(self.can(s,dragon,source));self.assertFalse(self.can(s,bird,source))
        self.until(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.attack_for(s,'B',[(dragon,'A'),(bird,'A')]);self.assertEqual([dragon.ref],self.blockers(s)[source.ref])
        self.reject(s,{'action_id':'block','blocks':{source.ref:bird.ref}})
        s=self.replay(s,load=True);self.block(s,s.state.cards[source.object_id],s.state.cards[dragon.object_id])
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual(0,s.state.cards[source.object_id].marked_damage)
        self.assertEqual(38,s.state.players['A'].life);self.replay(s,load=True)

    def test_actual_aether_web_flashes_into_combat_and_preserves_mixed_shadow_rule_and_stats(self):
        s=self.session(933040);aura=self.add(s.engine,'Aether Web',zone='hand');body=self.add(s.engine,'Heartwood Dryad')
        shadow=self.add(s.engine,'Soltari Foot Soldier',seat='B');flyer=self.add(s.engine,'Wind Drake',seat='B')
        for card in (shadow,flyer):card.acquired_control_turn_count=-1
        self.lands(s,'A','Forest',2);self.prepare(s,actor='B',active='B');self.checkpoint(s)
        self.attack_for(s,'B',[(shadow,'A'),(flyer,'A')]);self.cast_closed(s,'A',aura,[body.ref])
        self.assertEqual((3,2),tuple(s.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
        self.assertIn('reach',s.engine._combat_keywords(s.state.cards[body.object_id]));self.assertNotIn('shadow',s.engine._combat_keywords(s.state.cards[body.object_id]))
        self.assertEqual({shadow.ref,flyer.ref},set(self.blockers(s)[body.ref]))
        s=self.replay(s,load=True);self.block(s,s.state.cards[body.object_id],s.state.cards[shadow.object_id]);self.replay(s,load=True)

    def test_actual_web_rule_survives_recipient_ability_loss_and_ends_with_aura(self):
        s=self.session(933050);aura=self.add(s.engine,'Aether Web',zone='hand');body=self.add(s.engine,'Grizzly Bears',seat='B')
        shadow=self.add(s.engine,'Soltari Foot Soldier',seat='C');flyer=self.add(s.engine,'Wind Drake',seat='C')
        humble=self.add(s.engine,'Humble',zone='hand');remove=self.add(s.engine,'Disenchant',zone='hand')
        self.prepare(s,mana={k:20 for k in 'CWUBRG'});self.checkpoint(s);self.cast_closed(s,'A',aura,[body.ref])
        self.assertTrue(self.can(s,shadow,body));self.assertTrue(self.can(s,flyer,body))
        self.cast_closed(s,'A',humble,[body.ref]);self.assertTrue(self.can(s,shadow,body));self.assertFalse(self.can(s,flyer,body))
        self.cast_closed(s,'A',remove,[aura.ref]);self.assertFalse(self.can(s,shadow,body));self.replay(s,load=True)

    def masako_combat(self,seed,*,lifetime=False):
        s=self.session(seed);source=self.add(s.engine,'Masako the Humorless',zone='hand')
        own=self.add(s.engine,'Grizzly Bears',ref='masako-tapped-own-bear');own.tapped=True
        cannot=self.add(s.engine,'Goblin Raider');cannot.tapped=True
        other=self.add(s.engine,'Grizzly Bears',seat='C',ref='masako-tapped-other-bear');other.tapped=True
        attacker=self.add(s.engine,'Grizzly Bears',seat='B',ref='masako-attacking-bear');flyer=self.add(s.engine,'Wind Drake',seat='B')
        for card in (attacker,flyer):card.acquired_control_turn_count=-1
        self.add(s.engine,'Grizzly Bears',ref='masako-untapped-control')
        secret=self.add(s.engine,'Shock',seat='C',zone='hand',ref='masako-private-C')
        cards={name:self.add(s.engine,name,zone='hand') for name in ('Humble','Cloudshift','Unsummon')} if lifetime else {}
        self.lands(s,'A','Plains',6 if lifetime else 3)
        # Earlier spells can spend blue for three total generic mana. Leave
        # one actual blue source for the final bounce regardless of that plan.
        if lifetime:self.lands(s,'A','Island',4)
        self.prepare(s,actor='B',active='B');self.checkpoint(s)
        self.attack_for(s,'B',[(attacker,'A'),(flyer,'A')]);self.cast_closed(s,'A',source)
        return s,source,own,cannot,other,attacker,flyer,secret,cards

    def test_actual_masako_flash_enables_only_own_otherwise_legal_tapped_blocker_and_never_untaps_it(self):
        s,source,own,cannot,other,attacker,flyer,secret,_=self.masako_combat(933060)
        legal=self.blockers(s);self.assertEqual([attacker.ref],legal[own.ref]);self.assertNotIn(cannot.ref,legal);self.assertNotIn(other.ref,legal)
        for principal in ('pilot:A','pilot:B','pilot:D','spectator'):
            packet=str(s.packet(principal,full=True));self.assertNotIn(secret.ref,packet);self.assertNotIn(secret.object_id,packet)
        self.reject(s,{'action_id':'block','blocks':{own.ref:flyer.ref}});self.reject(s,{'action_id':'block','blocks':{cannot.ref:attacker.ref}})
        self.reject(s,{'action_id':'block','blocks':{own.ref:attacker.ref,cannot.ref:attacker.ref}})
        s=self.replay(s,load=True);self.block(s,s.state.cards[own.object_id],s.state.cards[attacker.object_id])
        self.assertTrue(s.state.cards[own.object_id].tapped);self.replay(s,load=True)

    def test_actual_masako_source_ability_loss_blink_and_departure_recompute_the_tapped_block_offer(self):
        from quorune.rules.block_as_though import blocker_tap_allows_block
        s,source,own,_,_,attacker,_,_,cards=self.masako_combat(933080,lifetime=True)
        self.assertTrue(blocker_tap_allows_block(s.engine,s.state.cards[own.object_id]))
        self.cast_closed(s,'A',cards['Humble'],[source.ref]);self.assertFalse(blocker_tap_allows_block(s.engine,s.state.cards[own.object_id]))
        old=s.state.cards[source.object_id].logical_object_id
        self.cast_closed(s,'A',cards['Cloudshift'],[source.ref]);self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertTrue(blocker_tap_allows_block(s.engine,s.state.cards[own.object_id]))
        self.cast_closed(s,'A',cards['Unsummon'],[s.state.cards[source.object_id].ref])
        self.assertFalse(blocker_tap_allows_block(s.engine,s.state.cards[own.object_id]));self.assertNotIn(own.ref,self.blockers(s))
        self.reject(s,{'action_id':'block','blocks':{own.ref:attacker.ref}});self.replay(s,load=True)

    def test_actual_masako_post_relationship_fault_restores_tapped_state_decision_and_retries(self):
        from quorune import block_transition_engine_adapter as owned
        from quorune.errors import GameRuleError
        s,_,own,_,_,attacker,_,_,_=self.masako_combat(933090);self.blockers(s)
        s=self.replay(s,load=True);before=authoritative_state_hash(s.state);original=owned.commit_block_declaration;observed=[]
        def fault(combat,cards,**kwargs):
            result=original(combat,cards,**kwargs);observed.append(cards[own.object_id].blocking)
            raise GameRuleError('Injected as-though blocker relationship failure')
        with patch.object(owned,'commit_block_declaration',fault):
            result=s.act('pilot:A',{'action_id':'block','blocks':{own.ref:attacker.ref}})
        self.assertFalse(result.ok);self.assertEqual([attacker.object_id],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.block(s,s.state.cards[own.object_id],s.state.cards[attacker.object_id])
        self.assertTrue(s.state.cards[own.object_id].tapped);self.replay(s,load=True)

    def test_actual_component_requires_fine_authority_even_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(933070);source=self.add(s.engine,'Heartwood Dryad');self.assert_closed(s,source)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        deps=tuple(d for d in program.capability_dependencies if d!=CAP)
        missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_fresh_omitted_live_assumption_mutant_is_killed_by_actual_web(self):
        from quorune import combat_evasion_engine_adapter as owned
        with patch.object(owned,'current_block_as_though_assumptions',return_value=Assumptions()):
            with self.assertRaises(AssertionError):self.test_actual_aether_web_flashes_into_combat_and_preserves_mixed_shadow_rule_and_stats()
