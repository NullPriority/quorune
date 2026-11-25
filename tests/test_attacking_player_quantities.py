"""CR508.1b: count direct player attacks at resolution, independently of controller."""
from dataclasses import replace,asdict
from types import SimpleNamespace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls
import test_combat_prevention_source_scopes as combat
import test_public_query_effect_amounts as compiler_controls
from quorune.live_battlefield_quantity_model import (
    LiveBattlefieldQuantitySpec,LIVE_ATTACKING_PLAYER_COUNT_CAPABILITY as CAP,
)
from quorune.compiler.live_battlefield_quantities import resolution_quantity
from quorune.live_battlefield_quantities import current_live_battlefield_count
from quorune.object_predicate import ObjectQuerySpec,PermanentStatePredicateSpec
from quorune.characteristic_fragments import characteristic_quantity_capabilities
from quorune.model import CardInstance

FIXTURE=ROOT/'tests/fixtures/attacking-player-quantity-cards.json'

def attack_spec():return resolution_quantity('creatures attacking you',source_name='Anonymous')

class AttackingPlayerQuantityModelTests(unittest.TestCase):
    def test_v3_is_explicit_closed_and_v1_v2_keep_wire_and_dataclass_fields(self):
        spec=attack_spec();self.assertEqual(3,spec.schema_version)
        wire=spec.to_dict();self.assertEqual('controller_player',wire['attack_destination'])
        self.assertEqual(wire,LiveBattlefieldQuantitySpec.from_dict(json.loads(json.dumps(wire))).to_dict())
        old=LiveBattlefieldQuantitySpec('any',spec.query)
        qualified=LiveBattlefieldQuantitySpec('source_controller',ObjectQuerySpec(zones=('battlefield',),
            types_all=('creature',),state_predicate=PermanentStatePredicateSpec(modified=True)),schema_version=2)
        for original in (old,qualified):
            raw=original.to_dict();self.assertNotIn('attack_destination',raw)
            self.assertEqual(raw,LiveBattlefieldQuantitySpec.from_dict(raw).to_dict())
            self.assertEqual({'controller_relation','query','exclude_source','schema_version'},set(asdict(original)))
        for change in ({'schema_version':True},{'schema_version':1},{'attack_destination':'controller_permanents'},
                {'attack_destination':'B'},{'controller_relation':'source_controller'},{'exclude_source':True},{'extra':1}):
            with self.assertRaises(ValueError):LiveBattlefieldQuantitySpec.from_dict({**wire,**change})
        for query in (replace(spec.query,colors_any=('G',)),replace(spec.query,zones=('graveyard',)),
                replace(spec.query,include_phased_out=True),replace(spec.query,state_predicate=None)):
            with self.assertRaises(ValueError):replace(spec,query=query)
        with self.assertRaises(ValueError):LiveBattlefieldQuantitySpec.from_dict({k:v for k,v in wire.items() if k!='attack_destination'})
    def test_compiler_keeps_direct_player_grammar_separate_from_all_attackers_and_permanent_unions(self):
        for text,complete in (('creatures attacking you',False),('creature attacking you',False),('the number of creatures attacking you',True)):
            self.assertEqual(3,resolution_quantity(text,source_name='Source',complete_value=complete).schema_version)
        old=resolution_quantity('attacking creatures',source_name='Source');self.assertEqual(1,old.schema_version)
        for text in ('creatures attacking target player','creatures attacking the defending player',
                'creatures attacking you or planeswalkers you control','creatures attacking a battle you protect'):
            self.assertIsNone(resolution_quantity(text,source_name='Source'))
    def frame(self):
        cards=[]
        for index,controller,defender,zone,phased,types in (
                (1,'B','A','battlefield',False,'Creature'),(2,'C','A','battlefield',False,'Creature'),
                (3,'B','C','battlefield',False,'Creature'),(4,'B','PW-A','battlefield',False,'Creature'),
                (5,'B','BATTLE-A','battlefield',False,'Creature'),(6,'B','A','graveyard',False,'Creature'),
                (7,'B','A','battlefield',True,'Creature'),(8,'B',None,'battlefield',False,'Creature'),
                (9,'B','A','battlefield',False,'Artifact')):
            card=CardInstance(object_id=str(index),ref='attacker-'+str(index),oracle_id='fixture:attack-count',
                printed_name='Public attacker',owner=controller,controller=controller,zone=zone)
            card.attacking=defender;card.phased_out=phased;cards.append((card,types))
        data={c.object_id:{'type_line':kind,'power':'2','toughness':'2','keywords':[]} for c,kind in cards}
        host=SimpleNamespace(state=SimpleNamespace(players={p:object() for p in 'ABCD'},cards={c.object_id:c for c,_ in cards}),
            _effective_card_data=lambda c:data[c.object_id],_type_parts=lambda t:({t.casefold()},set(),set()))
        return host,data
    def test_current_attack_destination_excludes_other_players_planeswalkers_battles_and_unavailable_members(self):
        host,data=self.frame();self.assertEqual(2,current_live_battlefield_count(host,'A',attack_spec()))
        self.assertEqual(1,current_live_battlefield_count(host,'C',attack_spec()))
        host.state.cards['1'].zone='hand';self.assertEqual(1,current_live_battlefield_count(host,'A',attack_spec()))
        host.state.cards['3'].attacking='A';self.assertEqual(2,current_live_battlefield_count(host,'A',attack_spec()))
        host.state.cards['2'].attacking=None;self.assertEqual(1,current_live_battlefield_count(host,'A',attack_spec()))
        data['3']['type_line']='Artifact';self.assertEqual(0,current_live_battlefield_count(host,'A',attack_spec()))
    def test_payload_alone_requires_new_producer_and_old_counts_do_not_inherit_it(self):
        wire=attack_spec().to_dict();self.assertIn(CAP,characteristic_quantity_capabilities(wire))
        old=LiveBattlefieldQuantitySpec('any',attack_spec().query).to_dict()
        self.assertNotIn(CAP,characteristic_quantity_capabilities(old))
        with self.assertRaises(ValueError):characteristic_quantity_capabilities({**wire,'attack_destination':'any_player'})

class AttackingPlayerQuantityCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()
    def test_complete_token_and_prevention_body_retains_reach_and_both_producer_dependencies(self):
        text='Create X 1/2 green Spider creature tokens with reach, where X is the number of creatures attacking you. Prevent all combat damage that would be dealt this turn by non-Spider creatures.'
        record=compiler_controls.query_amount_record(text,suffix=365201101)
        ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0]
        self.assertEqual(['create_token','create_damage_prevention_shield'],[e['op'] for e in node.effects])
        self.assertEqual(['Reach'],node.effects[0]['characteristics']['keywords'])
        self.assertIn(CAP,node.capability_dependencies);self.assertIn('damage.prevention.persistent_amount',node.capability_dependencies)
        self.assertEqual(['spider'],node.effects[1]['scope']['source_characteristics_none'])
        for bad in (text.replace('creatures attacking you','creatures attacking you or planeswalkers you control'),text+' Draw a card for each chosen creature.'):
            self.assertNotEqual('exact',compile_oracle_card(compiler_controls.query_amount_record(bad,suffix=365201102),capability_registry=self.registry).status)
    def test_new_producer_withdrawal_preserves_the_existing_all_attacker_count(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['Direct-player count withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        for body,exact in (('Draw a card for each creature attacking you.',False),('Draw a card for each attacking creature.',True)):
            self.assertEqual(exact,compile_oracle_card(compiler_controls.query_amount_record(body,suffix=365201103),capability_registry=registry).status=='exact')

class AttackingPlayerQuantityRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    ready=controls.PublicConditionSelfReplacementTests.ready
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    private_replay=controls.PublicConditionSelfReplacementTests.private_replay
    private=controls.PublicConditionSelfReplacementTests.private
    cast=controls.PublicConditionSelfReplacementTests.cast
    reject=controls.PublicConditionSelfReplacementTests.reject
    advance=controls.PublicConditionSelfReplacementTests.advance
    reach=combat.CombatPreventionSourceRuntimeTests.reach
    attack=combat.CombatPreventionSourceRuntimeTests.attack
    block=combat.CombatPreventionSourceRuntimeTests.block
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'attacking-player.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Direct player attack counts',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def test_full_original_and_old_all_attackers_control_bind_normally_while_selected_exception_stays_unready(self):
        s=self.start(791000)
        for name in ('Arachnogenesis','Blessed Reversal','Galadhrim Ambush','Jace Beleren','Giant Spider'):
            card=self.add(s.engine,name,zone='hand');binding=self.binding(s,card)
            self.assertTrue(binding['strict_capability_ready'],(name,binding['blockers']));self.assertEqual('capability_closed',binding['trust_basis'])
        exception=self.add(s.engine,'Terrifying Presence',zone='hand')
        binding=self.binding(s,exception)
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])
    def setup_combat(self,seed,*,response=False,reversal=False,double=False):
        s=self.start(seed);source=self.add(s.engine,'Blessed Reversal' if reversal else 'Arachnogenesis',zone='hand')
        spider=self.add(s.engine,'Giant Spider',seat='B')
        direct=self.add(s.engine,'Grizzly Bears',seat='B',ref='direct-player-bear')
        other=self.add(s.engine,'Grizzly Bears',seat='B',ref='other-player-bear')
        to_walker=self.add(s.engine,'Wind Drake',seat='B',ref='planeswalker-flyer')
        walker=self.add(s.engine,'Jace Beleren');walker.counters['loyalty']=3
        pinger=self.add(s.engine,'Prodigal Pyromancer',seat='B');self.add(s.engine,'Fervor',seat='B')
        for index in range(3):self.add(s.engine,'Plains' if reversal else 'Forest',ref='attack-count-land-'+str(index))
        if double:
            reflection=self.add(s.engine,'Boon Reflection');self.assertTrue(self.binding(s,reflection)['strict_capability_ready'])
        if response:
            bounce=self.add(s.engine,'Unsummon',zone='hand')
            for index in range(3):self.add(s.engine,'Island',ref='arachnogenesis-island-'+str(index))
        else:bounce=None
        private=self.private(s);self.ready(s)
        self.attack(s,'B',[(spider,'A'),(direct,'A'),(other,'C'),(to_walker,walker.ref)])
        self.priority(s,'A');return s,source,spider,direct,walker,pinger,bounce,private
    def tokens(self,s):return [c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A']
    def scenario(self,seed,*,response=False):
        s,source,spider,direct,walker,pinger,bounce,private=self.setup_combat(seed,response=response)
        self.cast(s,source)
        if response:
            self.cast(s,bounce,[direct.ref]);self.assertIsNone(self.resolve_top(s))
            self.assertEqual('hand',s.state.cards[direct.object_id].zone)
        self.assertIsNone(self.resolve(s));tokens=self.tokens(s);self.assertEqual(1 if response else 2,len(tokens))
        for token in tokens:
            self.assertIn('spider',s.engine._type_parts(s.engine._effective_card_data(token)['type_line'])[1])
            self.assertIn('reach',s.engine._combat_keywords(token))
            self.assertEqual(1,s.engine._numeric_stat(token.object_id,'power'));self.assertEqual(2,s.engine._numeric_stat(token.object_id,'toughness'))
        self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in rows if r['id'].startswith('activate:'+pinger.ref+':'))
        command={'action_id':row['id'],'targets':['A'],'pay':'auto'};self.reject(s,command,'D')
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(39,s.state.players['A'].life)
        flyer=next(c for c in s.state.cards.values() if c.ref=='planeswalker-flyer')
        pairs=[(tokens[0],spider)]+([] if response else [(tokens[1],flyer)])
        self.block(s,'A',pairs);s=self.replay(s,load=True);self.advance(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual('outside',s.state.cards[tokens[0].object_id].zone)
        if not response:
            self.assertEqual('battlefield',s.state.cards[tokens[1].object_id].zone)
            self.assertEqual(1,s.state.cards[flyer.object_id].marked_damage)
        self.assertEqual(1,s.state.cards[spider.object_id].marked_damage)
        self.assertEqual(40,s.state.players['C'].life);self.assertEqual(3,s.state.cards[walker.object_id].counters['loyalty'])
        self.assertEqual(39,s.state.players['A'].life);self.private_replay(s,private)
    def resolve_top(self,s):
        depth=len(s.state.stack)
        for _ in range(20):
            if len(s.state.stack)<depth:return None
            self.assertEqual('priority',s.state.pending_decision.kind)
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Response did not resolve')
    def test_actual_original_counts_direct_player_attacks_and_reach_tokens_block_spider_damage(self):self.scenario(791010)
    def test_actual_response_departure_changes_resolution_time_count_without_losing_prevention(self):self.scenario(791011,response=True)
    def test_actual_zero_attacker_cast_creates_no_tokens_but_preserves_combat_prevention(self):
        s=self.start(791020);source=self.add(s.engine,'Arachnogenesis',zone='hand');private=self.private(s)
        self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.assertFalse(self.tokens(s));self.assertEqual(1,len(s.state.damage_prevention_shields));self.private_replay(s,private)
    def test_marker_requires_new_runtime_authority_even_without_coverage_or_declared_dependencies(self):
        s=self.start(791030);source=self.add(s.engine,'Arachnogenesis',zone='hand')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['Direct-player count withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        for candidate in (program,replace(program,coverage=(),capability_dependencies=(),capability_closure=None)):
            bound=bind_semantic_program_runtime(candidate,capability_registry=registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn(CAP,bound['required_registered_capabilities'])
    def test_fault_after_tokens_and_shield_rolls_back_the_whole_resolution_before_retry(self):
        from quorune.effect_runtime import damage_modifiers
        from quorune.errors import GameRuleError
        s,source,spider,direct,walker,pinger,bounce,private=self.setup_combat(791040)
        self.cast(s,source);original=damage_modifiers.commit_prevention_shield_creation;partial=[];failed=None
        def fault(host,plan):
            result=original(host,plan);partial.append((len(self.tokens(s)),len(s.state.damage_prevention_shields)))
            raise GameRuleError('Injected attacking-player result failure')
        with patch.object(damage_modifiers,'commit_prevention_shield_creation',side_effect=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected attacking-player result failure',result.summary);self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertIsNotNone(failed);self.assertEqual([(2,1)],partial)
        self.assertFalse(self.tokens(s));self.assertFalse(s.state.damage_prevention_shields)
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(self.tokens(s)));self.assertEqual(1,len(s.state.damage_prevention_shields));self.private_replay(s,private)
    def test_fresh_all_attackers_substitution_mutation_is_killed_by_actual_player_and_planeswalker_combat(self):
        import quorune.semantic_runtime.query_effect_amounts as owner
        original=owner.current_live_battlefield_count
        def mutant(host,controller,spec,**kwargs):
            return original(host,controller,replace(spec,schema_version=1) if spec.schema_version==3 else spec,**kwargs)
        with patch.object(owner,'current_live_battlefield_count',side_effect=mutant):
            with self.assertRaises(AssertionError):self.test_actual_original_counts_direct_player_attacks_and_reach_tokens_block_spider_damage()

    def test_actual_blessed_reversal_uses_live_direct_attacks_response_removal_and_life_replacement(self):
        for response,double in ((False,False),(True,False),(False,True)):
            with self.subTest(response=response,double=double):
                s,source,spider,direct,walker,pinger,bounce,private=self.setup_combat(791050+response+2*double,response=response,reversal=True,double=double)
                self.cast(s,source)
                if response:
                    self.cast(s,bounce,[direct.ref]);self.resolve_top(s)
                    self.assertEqual('hand',s.state.cards[direct.object_id].zone)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(40+(3 if response else 6)*(2 if double else 1),s.state.players['A'].life)
                self.assertEqual(40,s.state.players['C'].life);self.assertFalse(self.tokens(s));self.assertFalse(s.state.damage_prevention_shields)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)
