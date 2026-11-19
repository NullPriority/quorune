"""CR115/120/608.2h: damage references are not targeting instructions."""
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
from quorune.attachment_references import AttachmentReferenceKind,AttachmentReferenceSpec,AttachmentObjectIdentity,SourceAttachmentSnapshot
from quorune.carddb import CardDatabase
from quorune.compiler.referenced_damage_templates import event_fixed_damage_template,attachment_fixed_damage_template,REFERENCED_DAMAGE_CAPABILITY
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.referenced_damage_shapes import referenced_damage_node_capabilities,referenced_damage_runtime_dependencies
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class ReferencedFixedDamageModelTests(unittest.TestCase):
    def test_event_recipient_is_sealed_and_requires_its_exact_proved_occurrence(self):
        for event,variant,body,expected in (
                ('creature.enter','creature:any:including_source:any_object','This enchantment deals 2 damage to it.','$event.entry_object'),
                ('creature.attacks','public_attack_a_creature','This artifact deals 1 damage to it.','$event.permanent_object')):
            binding=SimpleNamespace(event=SimpleNamespace(value=event),variant=variant,active_zone='battlefield')
            compiled=event_fixed_damage_template(binding,body,card_name='Generic Damage Permanent')
            self.assertIsNotNone(compiled)
            self.assertIsNone(compiled[2]);self.assertEqual('$source',compiled[1][0]['source'])
            self.assertEqual(expected,compiled[1][0]['target'])
            self.assertIn(REFERENCED_DAMAGE_CAPABILITY,referenced_damage_node_capabilities(
                effects=compiled[1],target_schema=None,mechanic_ids=compiled[3]))
            binding.variant='unrepresented-occurrence'
            self.assertIsNone(event_fixed_damage_template(binding,body,card_name='Generic Damage Permanent'))

    def test_attachment_object_and_controller_references_are_closed_and_non_targeted(self):
        for body in ('This Aura deals 2 damage to enchanted creature.',
                "This Aura deals 2 damage to enchanted artifact's controller."):
            compiled=attachment_fixed_damage_template(body,card_name='Generic Aura',source_is_permanent=True,
                relation=AttachmentReferenceKind.ENCHANTED)
            self.assertIsNotNone(compiled);self.assertIsNone(compiled[2])
            self.assertIn(REFERENCED_DAMAGE_CAPABILITY,referenced_damage_node_capabilities(
                effects=compiled[1],target_schema=None,mechanic_ids=compiled[3]))
            self.assertIsNone(attachment_fixed_damage_template(body,card_name='Generic Aura',
                source_is_permanent=False,relation=AttachmentReferenceKind.ENCHANTED))
        for body in ('This Aura deals X damage to enchanted creature.','This Aura deals 0 damage to enchanted creature.',
                'This Aura deals 2 damage to enchanted creature and draw a card.'):
            self.assertIsNone(attachment_fixed_damage_template(body,card_name='Generic Aura',
                source_is_permanent=True,relation=AttachmentReferenceKind.ENCHANTED))

    def test_damage_payload_requires_positive_amount_source_and_independent_recipient_authority(self):
        binding=SimpleNamespace(event=SimpleNamespace(value='creature.attacks'),variant='public_attack_a_creature',active_zone='battlefield')
        compiled=event_fixed_damage_template(binding,'This artifact deals 1 damage to it.',card_name='Generic')
        for change in ({'amount':True},{'amount':0},{'source':'$context.card'},{'extra':'unrepresented'}):
            effect={**compiled[1][0],**change}
            self.assertFalse(referenced_damage_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=('cr-120-damage',)))
        self.assertFalse(referenced_damage_node_capabilities(effects=compiled[1],target_schema={'count':1},mechanic_ids=('cr-120-damage',)))
        program=SimpleNamespace(effects=compiled[1],event='creature.attacks',active_zone='battlefield',coverage=())
        self.assertIn(REFERENCED_DAMAGE_CAPABILITY,referenced_damage_runtime_dependencies(program))
        program.event='creature.enter'
        with self.assertRaises(ValueError):referenced_damage_runtime_dependencies(program)

    def controller_fixture(self):
        from quorune.rules.attachment_controller_information import attachment_controller_reference,capture_attachment_controller_context
        snapshot=SourceAttachmentSnapshot(AttachmentReferenceKind.ENCHANTED,
            AttachmentObjectIdentity('aura-id','aura-old','aura-ref'),
            AttachmentObjectIdentity('artifact-id','artifact-old','artifact-ref'))
        target=SimpleNamespace(object_id='artifact-id',logical_object_id='artifact-old',ref='artifact-ref',
            controller='B',zone='battlefield',phased_out=False)
        ref=attachment_controller_reference(AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED,'artifact',schema_version=2))
        cards={target.object_id:target}
        context={'source_logical_object_id':'aura-old','source_attachment_snapshot':snapshot.to_dict(),
            'source_attachment_information_version':1,
            **capture_attachment_controller_context(cards,SimpleNamespace(effects=({'op':'damage','target':ref},)),snapshot)}
        host=SimpleNamespace(state=SimpleNamespace(cards=cards),_effective_card_data=lambda c:{'controller':c.controller})
        return ref,target,host,SimpleNamespace(source_object_id='aura-id',context=context)

    def test_attached_controller_reads_current_control_then_exact_departure_information(self):
        from quorune.rules.attachment_controller_information import resolve_attachment_controller,attachment_controller_departure_context
        ref,target,host,item=self.controller_fixture()
        self.assertEqual('B',resolve_attachment_controller(host,ref,item))
        target.controller='C';self.assertEqual('C',resolve_attachment_controller(host,ref,item))
        item.context=attachment_controller_departure_context(host.state.cards,item.context,{target.object_id:target})
        target.zone='graveyard';target.logical_object_id='artifact-new';target.controller='D'
        self.assertEqual('C',resolve_attachment_controller(host,ref,item))
        bad=deepcopy(item.context);bad['source_logical_object_id']='wrong-source'
        with self.assertRaises(ValueError):resolve_attachment_controller(host,ref,SimpleNamespace(source_object_id='aura-id',context=bad))

    def test_controller_reference_versions_and_departure_markers_fail_closed(self):
        from quorune.rules.attachment_controller_information import attachment_controller_spec,resolve_attachment_controller
        ref,target,host,item=self.controller_fixture()
        for change in ({'schema_version':True},{'schema_version':2},{'extra':'unrepresented'}):
            with self.assertRaises(ValueError):attachment_controller_spec({**ref,**change})
        item.context.pop('source_attachment_information_version')
        with self.assertRaises(ValueError):resolve_attachment_controller(host,ref,item)

    def test_missing_departure_refresh_mutant_cannot_use_an_old_controller(self):
        from quorune.rules import attachment_controller_information as owner
        def witness():
            ref,target,host,item=self.controller_fixture()
            target.controller='C'
            item.context=owner.attachment_controller_departure_context(host.state.cards,item.context,{target.object_id:target})
            target.zone='graveyard';target.logical_object_id='returned-object';target.controller='D'
            self.assertEqual('C',owner.resolve_attachment_controller(host,ref,item))
        witness()
        with patch.object(owner,'attachment_controller_departure_context',side_effect=lambda cards,context,departing:context),self.assertRaises(AssertionError):
            witness()


class ReferencedFixedDamageNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'referenced-fixed-damage.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/referenced-fixed-damage-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Referenced damage',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_referenced_damage_and_controller_payloads_require_their_runtime_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for index,(name,cap) in enumerate((('Caltrops',REFERENCED_DAMAGE_CAPABILITY),
                ('Gremlin Infestation','object.reference.source_attachment_controller'))):
            with self.subTest(name=name):
                s=self.session(1116000+index);source=self.add(s.engine,name)
                self.assert_closed(s,source)
                program,=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                    if any(e.get('op')=='damage' for e in p.effects)]
                deps=tuple(c for c in program.capability_dependencies if c!=cap)
                changed=replace(program,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_aether_flash_hits_a_shrouded_creature_without_targeting(self):
        s=self.session(1116010)
        source=self.add(s.engine,'Aether Flash',zone='hand')
        creature=self.add(s.engine,'Pincher Beetles',seat='B',zone='hand')
        self.add(s.engine,'Vedalken Orrery',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-aether-flash')
        self.assert_closed(s,source);self.assert_closed(s,creature)
        self.prepare(s,mana={'C':2,'R':2});s.state.players['B'].mana_pool.update({'C':2,'G':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.cast(s,'B',creature);self.resolve_top(s)
        trigger,=s.state.stack
        self.assertEqual(source.object_id,trigger.source_object_id);self.assertEqual([],trigger.targets)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertIn(creature.object_id,s.state.players['B'].zones['graveyard'])
        self.finish(s,private)

    def advance(self,s,predicate):
        for _ in range(100):
            s.engine.pump()
            if predicate(s):return
            decision=s.state.pending_decision;principal=s.pending_principals()[0]
            if decision.kind=='priority':command={'action_id':'pass'}
            elif decision.kind=='cleanup.discard':
                ctx=s.packet(principal,full=True)['decision']['ctx']
                command={'action_id':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
            elif decision.kind=='combat.attackers':command={'action_id':'attack','attackers':{}}
            elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
            else:self.fail('Unexpected reference-damage progression: '+decision.kind)
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail('Reference-damage progression did not reach its boundary')

    def test_actual_caltrops_keeps_old_attacker_identity_after_blink_and_kills_original_when_present(self):
        for index,blinked in enumerate((False,True)):
            with self.subTest(blinked=blinked):
                s=self.session(1116020+index)
                source=self.add(s.engine,'Caltrops',zone='hand')
                creature=self.add(s.engine,'Llanowar Elves',seat='B')
                blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand')
                self.add(s.engine,'Generic Bound Plains',seat='B')
                self.add(s.engine,'Fervor',seat='B');self.add(s.engine,'Vedalken Orrery')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-caltrops')
                self.assert_closed(s,source);self.prepare(s,active='B',mana={'C':3});self.checkpoint(s)
                self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
                self.advance(s,lambda current:current.state.pending_decision is not None
                    and current.state.pending_decision.kind=='combat.attackers')
                result=s.act('pilot:B',{'action_id':'attack','attackers':{creature.ref:'A'}});self.assertTrue(result.ok,result.summary)
                self.assertEqual(1,self.trigger_count(s,source))
                if blinked:
                    old=s.state.cards[creature.object_id].logical_object_id
                    self.cast(s,'B',blink,[creature.ref]);self.resolve_top(s)
                    self.assertNotEqual(old,s.state.cards[creature.object_id].logical_object_id)
                self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield' if blinked else 'graveyard',s.state.cards[creature.object_id].zone)
                if blinked:self.assertEqual(0,s.state.cards[creature.object_id].marked_damage)
                self.finish(s,private)

    def test_actual_tilonalli_crown_keeps_its_entry_damage_and_static_bonus(self):
        s=self.session(1116030)
        source=self.add(s.engine,"Tilonalli's Crown",zone='hand')
        creature=self.add(s.engine,'Grizzly Bears',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-crown-reference')
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        self.cast(s,'A',source,[creature.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(creature.object_id,s.state.cards[source.object_id].attached_to)
        self.assertEqual((5,2),tuple(s.engine._numeric_stat(creature.object_id,stat) for stat in ('power','toughness')))
        self.assertIn('trample',s.engine._combat_keywords(s.state.cards[creature.object_id]))
        self.assertEqual(1,s.state.cards[creature.object_id].marked_damage)
        self.finish(s,private)

    def test_actual_fatal_attraction_uses_both_amounts_and_attachment_information_after_source_leaves(self):
        from quorune.engine import TURN_STEPS
        s=self.session(1116040)
        source=self.add(s.engine,'Fatal Attraction',zone='hand')
        creature=self.add(s.engine,'Colossal Dreadmaw',seat='B')
        remove=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        self.add(s.engine,'Vedalken Orrery')
        for n in range(2):self.add(s.engine,'Generic Bound Plains',seat='B',ref='fatal-response-mana-'+str(n))
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-fatal-reference')
        self.assert_closed(s,source);self.prepare(s,active='D',mana={'C':2,'R':1})
        s.engine.permissions.invalidate_current();s.state.pending_decision=None;s.state.last_normal_turn_player='D'
        if s.state.current_turn is not None:s.state.current_turn=replace(s.state.current_turn,player='D')
        s.state.phase_index=len(TURN_STEPS)-2;s.state.phase,s.state.step=TURN_STEPS[s.state.phase_index]
        s.engine._grant_priority('A');s.engine.pump();self.checkpoint(s)
        self.cast(s,'A',source,[creature.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[creature.object_id].marked_damage)
        self.advance(s,lambda current:current.state.active_player=='A' and current.state.step=='upkeep'
            and bool(current.state.stack))
        self.assertEqual(0,s.state.cards[creature.object_id].marked_damage)
        self.cast(s,'B',remove,[source.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(4,s.state.cards[creature.object_id].marked_damage)
        self.finish(s,private)

    def gremlin_setup(self,seed,*,remove_source):
        s=self.session(seed)
        source=self.add(s.engine,'Gremlin Infestation',zone='hand')
        artifact=self.add(s.engine,'Sol Ring');artifact.owner='C'
        donate=self.add(s.engine,'Donate',zone='hand')
        first=self.add(s.engine,'Disenchant',zone='hand',ref='gremlin-first-disenchant')
        second=self.add(s.engine,'Disenchant',zone='hand',ref='gremlin-second-disenchant')
        self.add(s.engine,'Vedalken Orrery');self.add(s.engine,'Island')
        for n in range(4):self.add(s.engine,'Generic Bound Plains',ref='gremlin-response-mana-'+str(n))
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-gremlin-reference')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'R':1});self.checkpoint(s)
        self.cast(s,'A',source,[artifact.ref]);self.assertIsNone(self.resolve(s))
        self.advance(s,lambda current:current.state.phase=='ending' and current.state.step=='end_step'
            and bool(current.state.stack))
        life={seat:s.state.players[seat].life for seat in 'ABCD'}
        if remove_source:
            self.cast(s,'A',first,[source.ref]);self.resolve_top(s)
        self.cast(s,'A',donate,['B',artifact.ref]);self.resolve_top(s)
        self.assertEqual('B',s.state.cards[artifact.object_id].controller)
        if remove_source:
            self.cast(s,'A',second,[artifact.ref]);self.resolve_top(s)
            self.assertIn(artifact.object_id,s.state.players['C'].zones['graveyard'])
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(life['B']-2,s.state.players['B'].life)
        for seat in 'ACD':self.assertEqual(life[seat],s.state.players[seat].life)
        return s,source,artifact,first,private

    def test_actual_gremlin_current_controller_damage_and_owner_independent_token_sibling(self):
        s,source,artifact,remove,private=self.gremlin_setup(1116050,remove_source=False)
        self.cast(s,'A',remove,[artifact.ref]);self.assertIsNone(self.resolve(s))
        self.assertIn(artifact.object_id,s.state.players['C'].zones['graveyard'])
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
        token,=tokens;data=s.engine._effective_card_data(token)
        self.assertEqual('Gremlin Token',data['name']);self.assertEqual(['R'],list(data['colors']))
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
        self.finish(s,private)

    def test_actual_gremlin_retains_last_controller_when_source_and_artifact_leave(self):
        s,source,artifact,remove,private=self.gremlin_setup(1116060,remove_source=True)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertFalse(any(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        self.finish(s,private)

    def test_activated_attachment_controller_reference_is_captured_before_source_sacrifice_cost(self):
        s=self.session(1116070)
        source=self.add(s.engine,'Generic Controller Aura',zone='hand')
        artifact=self.add(s.engine,'Sol Ring',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='private-C-controller-activation')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s)
        self.cast(s,'A',source,[artifact.ref]);self.assertIsNone(self.resolve(s));self.priority(s,'A')
        ability,=s.engine._activated_abilities(s.state.cards[source.object_id])
        action='activate:'+source.ref+':'+ability.ability_id
        self.assertTrue(any(row['id']==action for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        self.reject(s,'D',{'action_id':action,'targets':[],'pay':'auto'})
        life=s.state.players['B'].life
        result=s.act('pilot:A',{'action_id':action,'targets':[],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(life-2,s.state.players['B'].life)
        self.finish(s,private)
