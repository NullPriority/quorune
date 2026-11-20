"""CR 201.5/400.7/701.21: exclude an original source, not its returned object."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.affected_player_sacrifice_templates import (
    fixed_affected_player_sacrifice_effect_template as template,
    SOURCE_EXCLUDED_SACRIFICE_CAPABILITY as CAP,
)
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.affected_player_sacrifice_capability_shapes import (
    fixed_affected_player_sacrifice_node_capabilities as node_capabilities,
    source_excluded_sacrifice_runtime_dependencies as runtime_dependencies,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class SourceExcludedSacrificeModelTests(unittest.TestCase):
    def test_another_and_explicit_source_exclusion_keep_closed_predicates_and_typed_reference(self):
        for text,all_types,any_types in (
                ('Sacrifice a creature other than this creature.',('creature',),()),
                ('Sacrifice another permanent.',(),()),
                ('Sacrifice another creature or land.',(),('creature','land'))):
            with self.subTest(text=text):
                value=template(text);self.assertIsNotNone(value)
                self.assertTrue(value.exclude_source)
                self.assertEqual(all_types,value.predicate.types_all)
                self.assertEqual(any_types,value.predicate.types_any)
                self.assertEqual('$source.zone_object',value.effects[0]['exclude_ref'])
                self.assertIn(CAP,node_capabilities(effects=value.effects,target_schema=None,mechanic_ids=value.mechanics))
        ordinary=template('Sacrifice a creature.')
        self.assertFalse(ordinary.exclude_source);self.assertNotIn('exclude_ref',ordinary.effects[0])
        self.assertTrue(ordinary.template_id.endswith('-v1'))

    def test_source_exclusion_payload_and_tag_tampering_fail_closed(self):
        value=template('Sacrifice another creature or land.')
        for mutation in ('missing','card_ref','arbitrary','predicate','tag'):
            effects=deepcopy(value.effects);mechanics=value.mechanics
            if mutation=='missing':effects[0].pop('exclude_ref')
            elif mutation=='card_ref':effects[0]['exclude_ref']='$source.ref'
            elif mutation=='arbitrary':effects[0]['exclude_ref']='some-other-permanent'
            elif mutation=='predicate':effects[0]['predicate']['controller']='B'
            else:mechanics=tuple(m for m in mechanics if m!='source-excluded-affected-player-sacrifice')
            with self.subTest(mutation=mutation):
                self.assertFalse(node_capabilities(effects=effects,target_schema=None,mechanic_ids=mechanics))
        self.assertEqual((CAP,),runtime_dependencies(SimpleNamespace(effects=value.effects,active_zone='battlefield',coverage=())))
        with self.assertRaises(ValueError):runtime_dependencies(SimpleNamespace(effects=value.effects,active_zone='graveyard'))

    def test_source_reference_retains_old_incarnation_and_rejects_missing_identity(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.semantic_runtime.context import SemanticNodeError
        card=SimpleNamespace(object_id='physical-source',logical_object_id='old-incarnation',ref='source-ref',
            zone='battlefield',phased_out=False)
        host=SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card}))
        item=SimpleNamespace(source_object_id=card.object_id,context={'source_logical_object_id':'old-incarnation'},controller='A')
        self.assertEqual('source-ref',resolve_semantic_value(host,'$source.zone_object',item))
        card.logical_object_id='returned-incarnation'
        self.assertIsNone(resolve_semantic_value(host,'$source.zone_object',item))
        effect=template('Sacrifice another creature.').effects[0]
        resolved=resolve_semantic_value(host,effect,item)
        self.assertNotIn('exclude_ref',resolved)
        item.context={}
        with self.assertRaises(SemanticNodeError):resolve_semantic_value(host,effect,item)

    def test_unsupported_other_objects_counts_and_qualifiers_remain_unrepresented(self):
        for text in ('Sacrifice another permanent other than this creature.',
                'Sacrifice another creature with an unrepresented property.',
                'Sacrifice X other creatures.','Sacrifice another creature or artifact with flying.',
                'Sacrifice a creature other than that creature.'):
            with self.subTest(text=text):self.assertIsNone(template(text))

    def test_current_card_rebinding_mutant_cannot_replace_the_original_source_identity(self):
        from quorune.semantic_runtime import values as owner
        card=SimpleNamespace(object_id='source-id',logical_object_id='returned-object',ref='source-ref',
            zone='battlefield',phased_out=False)
        host=SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card}))
        item=SimpleNamespace(source_object_id=card.object_id,context={'source_logical_object_id':'old-object'},controller='A')
        original=owner.resolve_semantic_value
        def witness():self.assertIsNone(owner.resolve_semantic_value(host,'$source.zone_object',item))
        witness()
        def wrong(current,value,stack_item):
            return current.state.cards[stack_item.source_object_id].ref if value=='$source.zone_object' else original(current,value,stack_item)
        with patch.object(owner,'resolve_semantic_value',side_effect=wrong),self.assertRaises(AssertionError):witness()


class SourceExcludedSacrificeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-excluded-sacrifices.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/source-excluded-sacrifice-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source-excluded sacrifices',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def choose_sacrifice(self,s,card):
        self.assertEqual('choice.apnap',s.state.pending_decision.kind)
        command={'action_id':'choose','cards':[card.ref]}
        self.reject(s,'D',command)
        self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))

    def test_source_exclusion_payload_requires_its_runtime_authority_even_without_the_tag(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(1115000);source=self.add(s.engine,'Demonic Taskmaster')
        self.assert_closed(s,source)
        program,=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(e.get('exclude_ref')=='$source.zone_object' for e in p.effects)]
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict'])
        dependencies=tuple(c for c in program.capability_dependencies if c!=CAP)
        changed=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict(),
            coverage=tuple(m for m in program.coverage if m!='source-excluded-affected-player-sacrifice'))
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_actual_korvold_sacrifices_borrowed_creature_then_land_and_keeps_counter_draw_siblings(self):
        s=self.session(1115010)
        source=self.add(s.engine,'Korvold, Fae-Cursed King',zone='hand')
        borrowed=self.add(s.engine,'Grizzly Bears',seat='B',ref='korvold-borrowed-B')
        control=self.add(s.engine,'Act of Treason',zone='hand')
        land=self.add(s.engine,'Forest',ref='korvold-later-land')
        self.add(s.engine,'Fervor')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-korvold')
        self.assert_closed(s,source);self.prepare(s,mana={'C':8,'R':2,'B':1,'G':1});self.checkpoint(s)
        self.cast(s,'A',control,[borrowed.ref]);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',source);self.assertEqual('choice.apnap',self.resolve(s).kind)
        options=set(s.packet('pilot:A',full=True)['decision']['ctx']['options'])
        self.assertNotIn(source.ref,options);self.assertIn(borrowed.ref,options);self.assertIn(land.ref,options)
        self.reject(s,'A',{'action_id':'choose','cards':[source.ref]})
        hand=len(s.state.players['A'].zones['hand'])
        self.choose_sacrifice(s,borrowed)
        self.assertIn(borrowed.object_id,s.state.players['B'].zones['graveyard'])
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual(5,s.engine._numeric_stat(source.object_id,'power'))
        self.attack(s,[(s.state.cards[source.object_id],'B')])
        self.assertEqual('choice.apnap',self.resolve(s).kind)
        self.choose_sacrifice(s,land)
        self.assertEqual(6,s.engine._numeric_stat(source.object_id,'power'))
        self.assertIn(land.object_id,s.state.players['A'].zones['graveyard'])
        self.finish(s,private)

    def test_actual_upkeep_excludes_original_taskmaster_but_allows_its_blinked_incarnation(self):
        from quorune.engine import TURN_STEPS
        for index,mode in enumerate(('ordinary','blink','empty')):
            with self.subTest(mode=mode):
                s=self.session(1115020+index)
                source=self.add(s.engine,'Demonic Taskmaster')
                other=self.add(s.engine,'Grizzly Bears') if mode!='empty' else None
                artifact=self.add(s.engine,'Sol Ring')
                blink=self.add(s.engine,'Cloudshift',zone='hand')
                self.add(s.engine,'Generic Bound Plains',ref='taskmaster-blink-mana')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-taskmaster')
                self.assert_closed(s,source);self.prepare(s,actor='D',active='D')
                s.engine.permissions.invalidate_current();s.state.pending_decision=None
                s.state.last_normal_turn_player='D'
                if s.state.current_turn is not None:s.state.current_turn=replace(s.state.current_turn,player='D')
                s.state.phase_index=len(TURN_STEPS)-2
                s.state.phase,s.state.step=TURN_STEPS[s.state.phase_index]
                s.engine._grant_priority('D');s.engine.pump();self.checkpoint(s)
                for _ in range(100):
                    s.engine.pump();decision=s.state.pending_decision
                    if (s.state.active_player=='A' and s.state.step=='upkeep' and s.state.stack
                            and decision is not None and decision.kind=='priority'):break
                    principal=s.pending_principals()[0]
                    if decision.kind=='priority':command={'action_id':'pass'}
                    elif decision.kind=='cleanup.discard':
                        ctx=s.packet(principal,full=True)['decision']['ctx']
                        command={'action_id':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
                    else:self.fail('Unexpected upkeep transition decision: '+decision.kind)
                    result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
                else:self.fail('The actual next upkeep was not reached')
                old=s.state.cards[source.object_id].logical_object_id
                if mode=='blink':
                    self.cast(s,'A',blink,[source.ref]);self.resolve_top(s)
                    self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
                decision=self.resolve(s)
                if mode=='empty':
                    self.assertIsNone(decision);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                else:
                    self.assertEqual('choice.apnap',decision.kind)
                    options=set(s.packet('pilot:A',full=True)['decision']['ctx']['options'])
                    self.assertNotIn(artifact.ref,options);self.assertIn(other.ref,options)
                    self.assertEqual(mode=='blink',source.ref in options)
                    if mode=='ordinary':self.reject(s,'A',{'action_id':'choose','cards':[source.ref]})
                    chosen=source if mode=='blink' else other
                    self.choose_sacrifice(s,chosen)
                    self.assertEqual('graveyard',s.state.cards[chosen.object_id].zone)
                self.finish(s,private)

    def test_actual_morkrut_attack_chooses_one_other_creature_or_artifact_land(self):
        s=self.session(1115030)
        source=self.add(s.engine,'Morkrut Necropod',zone='hand')
        body=self.add(s.engine,'Grizzly Bears')
        land=self.add(s.engine,'Darksteel Citadel')
        artifact=self.add(s.engine,'Sol Ring')
        self.add(s.engine,'Fervor')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-necropod-attack')
        self.assert_closed(s,source);self.prepare(s,mana={'C':6,'B':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertIn('menace',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.attack(s,[(s.state.cards[source.object_id],'B'),(body,'B')])
        self.assertEqual('choice.apnap',self.resolve(s).kind)
        options=list(s.packet('pilot:A',full=True)['decision']['ctx']['options'])
        self.assertEqual({body.ref,land.ref},set(options));self.assertEqual(2,len(options))
        self.reject(s,'A',{'action_id':'choose','cards':[source.ref]})
        self.reject(s,'A',{'action_id':'choose','cards':[artifact.ref]})
        self.reject(s,'A',{'action_id':'choose','cards':[]})
        self.choose_sacrifice(s,body)
        self.assertEqual('graveyard',s.state.cards[body.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[land.object_id].zone)
        self.finish(s,private)

    def test_actual_morkrut_block_sacrifices_other_blocker_without_unblocking_its_attacker(self):
        s=self.session(1115040)
        source=self.add(s.engine,'Morkrut Necropod')
        body=self.add(s.engine,'Grizzly Bears',ref='necropod-other-blocker')
        first=self.add(s.engine,'Grizzly Bears',seat='B',ref='necropod-first-attacker')
        second=self.add(s.engine,'Grizzly Bears',seat='B',ref='necropod-second-attacker')
        self.add(s.engine,'Fervor',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-necropod-block')
        self.assert_closed(s,source);self.prepare(s,actor='B',active='B');self.checkpoint(s)
        self.until(s,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='combat.attackers')
        result=s.act('pilot:B',{'action_id':'attack','attackers':{first.ref:'A',second.ref:'A'}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{source.ref:[first.ref],body.ref:[second.ref]}}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('choice.apnap',self.resolve(s).kind)
        self.choose_sacrifice(s,body)
        life=s.state.players['A'].life
        self.until(s,lambda current:current.state.phase=='postcombat_main')
        self.assertEqual(life,s.state.players['A'].life)
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[second.object_id].zone)
        self.finish(s,private)
