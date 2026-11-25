"""Pinned CR 615/701.27: live negative subtypes and resolution-time transformation."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json, tempfile, unittest
from unittest.mock import patch
from common import ROOT
from quorune.compiler.subtype_transform_templates import fixed_subtype_transform_effect_template
from quorune.compiler.prevention_templates import fixed_prevention_effect_template
from quorune.compiler.fixed_all_damage_prevention import fixed_all_damage_prevention_specs
from quorune.rules.subtype_transform import (
    SubtypeTransformSpec, SUBTYPE_TRANSFORM_VERSION as VERSION,
    SUBTYPE_TRANSFORM_CAPABILITY as CAPABILITY, SUBTYPE_TRANSFORM_MECHANIC as MECHANIC,
    subtype_transform_node_capabilities, subtype_transform_runtime_dependencies,
)
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.binding import bind_semantic_program_runtime
import test_qualified_zone_event_queries as synthetic
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls
import test_combat_prevention_source_scopes as combat
import test_activated_self_transform as transform_controls

FIXTURE=ROOT/'tests/fixtures/subtype-combat-fog-cards.json'
CANDIDATES=('Moonmist','Galadhrim Ambush')

class SubtypeCombatFogModelTests(unittest.TestCase):
    def test_negative_subtype_grammar_exempts_either_member_and_preserves_old_payload(self):
        for phrase,expected in (('non-Elf creatures',('elf',)),('non-Spider creatures',('spider',)),
                ('creatures other than Werewolves and Wolves',('werewolf','wolf'))):
            row=fixed_all_damage_prevention_specs('Prevent all combat damage that would be dealt this turn by '+phrase+'.',card_name='Anonymous')
            self.assertEqual(expected,row[0].source.characteristics_none)
            self.assertEqual(('creature',),row[0].source.characteristics_all)
            self.assertEqual('combat',row[0].damage_kind)
        for bad in ('non-invented creatures','creatures other than target creature','creatures other than Wolves and invented',
                'creatures other than Wolves and Wolves','creatures other than Wolves and Humans and Elves'):
            self.assertIsNone(fixed_prevention_effect_template('Prevent all combat damage that would be dealt this turn by '+bad+'.'))
        old=fixed_prevention_effect_template('Prevent all combat damage non-Soldier creatures would deal this turn.')
        generic=fixed_prevention_effect_template('Prevent all combat damage non-soldier creatures would deal this turn.')
        self.assertEqual(old,generic)
    def test_subtype_transform_codec_is_closed_and_does_not_accept_an_arbitrary_query(self):
        spec=SubtypeTransformSpec('human');effect=spec.to_effect()
        self.assertEqual(spec,SubtypeTransformSpec.from_effect(json.loads(json.dumps(effect))))
        self.assertEqual(('human',),spec.query().subtypes_all)
        self.assertEqual((CAPABILITY,'permanent.transform.face_change'),subtype_transform_node_capabilities(
            effects=(effect,),target_schema=None,mechanic_ids=(MECHANIC,)))
        for change in ({VERSION:True},{VERSION:2},{'subtype':'Human'},{'subtype':None},{'subtype':False},{'subtype':'invented'},{'predicate':{}},
                {'card':'$target.0'},{'expected_transform_count':0}):
            with self.assertRaises((TypeError,ValueError)):SubtypeTransformSpec.from_effect({**effect,**change})
            self.assertFalse(subtype_transform_node_capabilities(effects=({**effect,**change},),target_schema=None,mechanic_ids=(MECHANIC,)))
    def test_transform_production_keeps_front_face_and_target_boundaries(self):
        for phrase,subtype in (('Transform all Humans.','human'),('Transform all Elves.','elf'),('Transform all Werewolves.','werewolf')):
            row=fixed_subtype_transform_effect_template(phrase);self.assertEqual(subtype,row[1][0]['subtype'])
        for bad in ('Transform all invented.','Transform all Humans you control.','Transform target Human.',
                'Transform all Humans on their front face.','Convert all Humans.','Transform all Humans and Elves.'):
            self.assertIsNone(fixed_subtype_transform_effect_template(bad))
    def test_runtime_dependency_is_derived_even_without_coverage_and_validates_nested_shape(self):
        effect=SubtypeTransformSpec('human').to_effect()
        program=SimpleNamespace(effects=(effect,),coverage=())
        self.assertEqual({CAPABILITY,'permanent.transform.face_change'},set(subtype_transform_runtime_dependencies(program)))
        with self.assertRaises(ValueError):subtype_transform_runtime_dependencies(SimpleNamespace(effects=({'op':'offer_optional_effect','effects':[effect]},),coverage=()))
        with self.assertRaises(ValueError):subtype_transform_runtime_dependencies(SimpleNamespace(effects=({**effect,VERSION:True},)))

    def test_timestamp_resolution_v2_retains_historical_v1_encoding_and_closed_parent_frame(self):
        from quorune.transform_order_model import TransformTimestampContinuation,TransformTimestampModelError
        old=transform_controls.TransformTimestampVersionTests.ordering_frame(self)
        encoded=old.to_dict();self.assertEqual(1,encoded['schema_version']);self.assertNotIn('resolution',encoded)
        self.assertEqual(encoded,TransformTimestampContinuation.from_dict(encoded).to_dict())
        retained={'stack_ref':'SPELL','effect':SubtypeTransformSpec('human').to_effect(),
            'remaining':[],'destination':'graveyard','note':'model',
            'semantic_frame':{'schema_version':1,'semantic_program_id':'program','semantic_program_version':1,
                'stack_object':'SPELL','instruction_pointer':0,'locals':{},'controller':'A','pending_choice_id':None}}
        request=replace(old.request,day_night_instruction=False,
            expected_counts={v['object_id']:v.get('transform_count',0) for v in old.request.subjects})
        frame=replace(old,request=request,resume_kind='resolution',resolution=retained)
        new=frame.to_dict();self.assertEqual(2,new['schema_version'])
        self.assertEqual(new,TransformTimestampContinuation.from_dict(new).to_dict())
        for change in ({'schema_version':True},{'schema_version':1},{'resolution':None},{'resume_kind':'stabilize'},
                {'resolution':{**retained,'extra':1}},
                {'resolution':{**retained,'semantic_frame':{**retained['semantic_frame'],'instruction_pointer':True}}}):
            with self.assertRaises(TransformTimestampModelError):TransformTimestampContinuation.from_dict({**new,**change})
        with self.assertRaises(TransformTimestampModelError):TransformTimestampContinuation.from_dict({**encoded,'resolution':None})

class SubtypeCombatFogRuntimeTests(unittest.TestCase):
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
    activate=combat.CombatPreventionSourceRuntimeTests.activate
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'subtype-fogs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Subtype combat fogs',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def test_originals_are_normally_complete_and_no_unrelated_transform_union_is_promoted(self):
        s=self.start(789000)
        for name in CANDIDATES:
            card=self.add(s.engine,name,zone='hand');binding=self.binding(s,card)
            self.assertTrue(binding['strict_capability_ready'],(name,binding['blockers']))
            self.assertEqual('capability_closed',binding['trust_basis'])
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
        promoted=self.add(s.engine,'Arachnogenesis',zone='hand')
        self.assertTrue(self.binding(s,promoted)['strict_capability_ready'])
        self.assertEqual('capability_closed',self.binding(s,promoted)['trust_basis'])
        neighbor=self.add(s.engine,'Terrifying Presence',zone='hand')
        self.assertTrue(self.binding(s,neighbor)['strict_capability_ready'])
        record=self.db.lookup('Terrifying Presence')
        widened=replace(record,oracle_text=record.oracle_text.replace('target creature','up to two target creatures'))
        self.assertNotEqual('exact',compile_oracle_card(widened,capability_registry=self.registry,
            capability_profile='commander_review').status)
        control=self.add(s.engine,'Frontline Strategist',zone='hand')
        self.assertTrue(self.binding(s,control)['strict_capability_ready'])
    def test_actual_zero_attacker_ambush_cast_creates_no_tokens_but_keeps_live_shield(self):
        s=self.start(789010);spell=self.add(s.engine,'Galadhrim Ambush',zone='hand');private=self.private(s)
        self.ready(s);before=set(s.state.cards);self.cast(s,spell);self.assertIsNone(self.resolve(s))
        self.assertEqual(before,set(s.state.cards));self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.assertEqual(1,len(s.state.damage_prevention_shields));self.private_replay(s,private)

    def settle_transforms(self,s,*,expect_order=False):
        ordered=False
        for _ in range(12):
            pending=self.resolve(s)
            if pending is None:
                if expect_order:self.assertTrue(ordered)
                return s
            self.assertEqual('permanent.timestamp_order',pending.kind)
            actor=pending.actors[0];ctx=s.packet('pilot:'+actor,full=True)['decision']['ctx']
            refs=[c['id'] for c in ctx['cards']]
            command={'action_id':'order','cards':list(reversed(refs))}
            self.reject(s,command,'D' if actor!='D' else 'C')
            s=self.replay(s,load=True)
            result=s.act('pilot:'+actor,command);self.assertTrue(result.ok,result.summary);ordered=True
        self.fail('Transform ordering did not finish')
    def add_face(self,s,name,*,seat='A',ref=None,phased=False):
        card=self.add(s.engine,name,seat=seat,**({'ref':ref} if ref else {}))
        record=self.db.by_oracle_id(card.oracle_id)
        self.assertTrue(self.binding(s,card)['strict_capability_ready'],name)
        if record.faces:card.active_face=record.faces[0]['name']
        card.phased_out=phased
        return card
    def test_actual_moonmist_transforms_current_humans_simultaneously_across_controllers(self):
        s=self.start(789020);spell=self.add(s.engine,'Moonmist',zone='hand')
        a=self.add_face(s,'Thraben Sentry // Thraben Militia',ref='human-a')
        a2=self.add_face(s,'Thraben Sentry // Thraben Militia',ref='human-a2')
        b=self.add_face(s,'Lambholt Elder // Silverpelt Werewolf',seat='B')
        daybound=self.add_face(s,'Tavern Ruffian // Tavern Smasher',seat='C')
        phased=self.add_face(s,'Thraben Sentry // Thraben Militia',seat='D',phased=True)
        human=self.add(s.engine,'Prodigal Pyromancer',seat='B');private=self.private(s)
        identities={c.object_id:c.logical_object_id for c in (a,a2,b,daybound,phased,human)}
        self.ready(s);self.cast(s,spell);s=self.settle_transforms(s,expect_order=True)
        for card,face,count in ((a,'Thraben Militia',1),(a2,'Thraben Militia',1),
                (b,'Silverpelt Werewolf',1),(daybound,'Tavern Ruffian',0),(phased,'Thraben Sentry',0)):
            current=s.state.cards[card.object_id];self.assertEqual(face,current.active_face)
            self.assertEqual(count,current.transform_count);self.assertEqual(identities[card.object_id],current.logical_object_id)
            self.assertEqual('battlefield',current.zone)
        self.assertEqual(0,s.state.cards[human.object_id].transform_count)
        self.assertEqual(1,len(s.state.damage_prevention_shields));self.private_replay(s,private)
    def ambush(self,seed,*,change_type=False):
        s=self.start(seed);spell=self.add(s.engine,'Galadhrim Ambush',zone='hand')
        types=self.add(s.engine,'Standardize',zone='hand') if change_type else None
        first=self.add(s.engine,'Grizzly Bears',seat='B',ref='ambush-bear-a')
        second=self.add(s.engine,'Grizzly Bears',seat='B',ref='ambush-bear-c')
        elf=self.add(s.engine,'Llanowar Elves',seat='B');pinger=self.add(s.engine,'Prodigal Pyromancer',seat='B')
        self.add(s.engine,'Fervor',seat='B')
        self.add(s.engine,'Forest',ref='ambush-green')
        for n in range(5 if change_type else 3):self.add(s.engine,'Island',ref='ambush-blue-'+str(n))
        private=self.private(s);self.ready(s)
        self.attack(s,'B',[(first,'A'),(second,'C'),(elf,'D')]);self.priority(s,'A')
        self.cast(s,spell);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.object_kind=='token' and c.controller=='A']
        self.assertEqual(3,len(tokens))
        for token in tokens:
            self.assertIn('elf',s.engine._type_parts(str(s.engine._effective_card_data(token)['type_line']))[1])
        if types:
            self.assertTrue(self.binding(s,types)['strict_capability_ready']);self.cast(s,types)
            self.assertEqual('semantic.choice',self.resolve(s).kind)
            command={'action_id':'choose','creature_type':'elf'};self.reject(s,command,'D')
            s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.priority(s,'B');actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        row=next(a for a in actions if a['id'].startswith('activate:'+pinger.ref+':'))
        command={'action_id':row['id'],'targets':['A'],'pay':'auto'};self.reject(s,command,'D')
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(39,s.state.players['A'].life)
        self.block(s,'A',[(tokens[0],first)]);s=self.replay(s,load=True)
        self.advance(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual(38 if change_type else 40,s.state.players['C'].life)
        self.assertEqual(39,s.state.players['D'].life)
        self.assertEqual('outside' if change_type else 'battlefield',s.state.cards[tokens[0].object_id].zone)
        self.assertEqual(1,s.state.cards[first.object_id].marked_damage)
        self.private_replay(s,private)
    def test_actual_ambush_counts_attackers_of_every_player_and_new_elf_tokens_block(self):self.ambush(789030)
    def test_actual_subtype_gained_after_ambush_changes_damage_time_exemption(self):self.ambush(789031,change_type=True)

    def test_runtime_withdrawal_cannot_hide_marker_by_removing_coverage_or_dependencies(self):
        s=self.start(789040);source=self.add(s.engine,'Moonmist',zone='hand')
        programs=s.engine.semantics.programs_for_oracle(source.oracle_id)
        program=next(p for p in programs if any(e.get('op')=='transform' for e in p.effects))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Fixed subtype transform withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        for candidate in (program,replace(program,coverage=(),capability_dependencies=(),capability_closure=None)):
            binding=bind_semantic_program_runtime(candidate,capability_registry=registry,profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertIn(CAPABILITY,binding['required_registered_capabilities'])
        malformed=replace(program,effects=({**program.effects[0],VERSION:True},*program.effects[1:]),coverage=(),capability_dependencies=(),capability_closure=None)
        self.assertFalse(bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')['strict'])
        nested=replace(program,effects=({'op':'offer_optional_effect','player':'$controller','effects':list(program.effects)},),coverage=(),capability_dependencies=(),capability_closure=None)
        binding=bind_semantic_program_runtime(nested,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict']);self.assertIn('direct resolution instruction',str(binding['blockers']))
    def test_actual_transform_batch_fault_rolls_back_faces_and_following_shield_before_retry(self):
        from quorune import permanent_transform
        from quorune.errors import GameRuleError
        s=self.start(789050);spell=self.add(s.engine,'Moonmist',zone='hand')
        a=self.add_face(s,'Thraben Sentry // Thraben Militia',ref='rollback-human-a')
        b=self.add_face(s,'Thraben Sentry // Thraben Militia',ref='rollback-human-b');private=self.private(s)
        self.ready(s);self.cast(s,spell);pending=self.resolve(s);self.assertEqual('permanent.timestamp_order',pending.kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];command={'action_id':'order','cards':[r['id'] for r in ctx['cards']]}
        before=authoritative_state_hash(s.state);original=permanent_transform._commit_transform_faces;partial=[]
        def fault(host,card,faces):
            result=original(host,card,faces);partial.append(card.transform_count)
            if len(partial)==2:raise GameRuleError('Injected subtype transform batch failure')
            return result
        with patch.object(permanent_transform,'_commit_transform_faces',side_effect=fault):result=s.act('pilot:A',command)
        self.assertFalse(result.ok);self.assertIn('Injected subtype transform batch failure',result.summary)
        self.assertEqual([1,1],partial);self.assertEqual(before,authoritative_state_hash(s.state))
        for card in (a,b):self.assertEqual(0,s.state.cards[card.object_id].transform_count)
        self.assertFalse(s.state.damage_prevention_shields)
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        s=self.settle_transforms(s)
        for card in (a,b):self.assertEqual(1,s.state.cards[card.object_id].transform_count)
        self.assertEqual(1,len(s.state.damage_prevention_shields));self.private_replay(s,private)
    def test_fresh_transform_selection_omission_mutation_is_killed(self):
        from quorune.rules import subtype_transform
        with patch.object(subtype_transform,'apply_subtype_transform',return_value=()):
            with self.assertRaises(AssertionError):self.test_actual_moonmist_transforms_current_humans_simultaneously_across_controllers()


    def test_two_actual_stacked_moonmists_transform_each_current_human_twice(self):
        s=self.start(789060);first=self.add(s.engine,'Moonmist',zone='hand',ref='moonmist-lower')
        second=self.add(s.engine,'Moonmist',zone='hand',ref='moonmist-upper')
        source=self.add_face(s,'Thraben Sentry // Thraben Militia');private=self.private(s)
        self.ready(s);self.cast(s,first);self.cast(s,second)
        self.assertEqual(2,len(s.state.stack));s=self.settle_transforms(s)
        current=s.state.cards[source.object_id];self.assertEqual(2,current.transform_count)
        self.assertEqual('Thraben Sentry',current.active_face);self.assertEqual(source.logical_object_id,current.logical_object_id)
        self.assertEqual(2,len(s.state.damage_prevention_shields));self.private_replay(s,private)
    def moonmist_combat(self,seed,*,lose_wolf=False):
        s=self.start(seed);spell=self.add(s.engine,'Moonmist',zone='hand')
        werewolf=self.add_face(s,'Reckless Waif // Merciless Predator')
        wolf=self.add(s.engine,'Watchwolf');bear=self.add(s.engine,'Grizzly Bears')
        late=self.add(s.engine,'Changeling Outcast',zone='hand');crafter=self.add(s.engine,'Imagecrafter')
        self.add(s.engine,'Fervor');private=self.private(s);self.ready(s)
        self.cast(s,spell);s=self.settle_transforms(s)
        self.assertEqual('Merciless Predator',s.state.cards[werewolf.object_id].active_face)
        self.cast(s,late);self.assertIsNone(self.resolve(s))
        self.attack(s,'A',[(werewolf,'B'),(wolf,'B'),(bear,'B'),(late,'B')])
        if lose_wolf:
            self.activate(s,crafter,[wolf.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
            command={'action_id':'choose','creature_type':'human'};self.reject(s,command,'D')
            s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        s=self.replay(s,load=True);self.advance(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual(36 if lose_wolf else 33,s.state.players['B'].life)
        self.assertEqual(1,len(s.state.damage_prevention_shields));self.private_replay(s,private)
    def test_actual_moonmist_exempts_wolf_or_werewolf_and_later_changeling_but_prevents_bear(self):self.moonmist_combat(789070)
    def test_actual_losing_wolf_subtype_after_moonmist_enables_combat_prevention(self):self.moonmist_combat(789071,lose_wolf=True)

class SubtypeCombatFogCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()
    def record(self,text):return replace(synthetic.query_record(text),type_line='Instant')
    def test_ordered_transform_and_prevention_lower_as_independently_owned_complete_clauses(self):
        text='Transform all Humans. Prevent all combat damage that would be dealt this turn by creatures other than Werewolves and Wolves.'
        ir=compile_oracle_card(self.record(text),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertEqual(['transform','create_damage_prevention_shield'],[e['op'] for e in node.effects])
        self.assertIn(CAPABILITY,node.capability_dependencies)
        self.assertEqual(['werewolf','wolf'],node.effects[1]['scope']['source_characteristics_none'])
        for bad in (text.replace('all Humans','all artifacts and Phyrexian creatures on their front face'),
                text.replace('other than Werewolves and Wolves','other than target creature')):
            self.assertNotEqual('exact',compile_oracle_card(self.record(bad),capability_registry=self.registry).status)
    def test_transform_dependency_withdrawal_keeps_plain_subtype_prevention_supported(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Subtype transform withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        for text,exact in (('Transform all Humans.',False),('Prevent all combat damage that would be dealt this turn by non-Elf creatures.',True)):
            self.assertEqual(exact,compile_oracle_card(self.record(text),capability_registry=registry).status=='exact')
