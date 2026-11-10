from __future__ import annotations

"""Chosen-type occurrences use the existing entry choice and sealed events."""
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class ChosenTypeEventCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def test_cast_entry_death_and_entry_attack_keep_the_complete_chosen_type_subject(self):
        for body in (
            'Whenever you cast a creature spell of the chosen type, draw a card.',
            'Whenever you cast a spell of the chosen type, put a charge counter on this artifact.',
            'Whenever a creature of the chosen type dies, you may draw a card.',
            'Whenever another creature you control of the chosen type enters, draw a card.',
            'Whenever a creature you control of the chosen type enters or attacks, draw a card.',
        ):
            with self.subTest(body=body):
                text='As this artifact enters, choose a creature type.\n'+body
                ir=compile_oracle_card(record(text,'Artifact'),capability_registry=self.registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertIn('trigger.event.chosen_creature_type',ir.faces[0].nodes[-1].capability_dependencies)

    def test_ambiguous_aggregate_and_color_identity_subjects_remain_residual(self):
        for body in (
            'Whenever you cast a spell of the chosen color identity, draw a card.',
            'Whenever one or more creatures of the chosen type die, draw a card.',
            'Whenever a creature of the chosen type or the remembered type dies, draw a card.',
        ):
            text='As this artifact enters, choose a creature type.\n'+body
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Artifact'),capability_registry=self.registry).status)

    def test_verified_type_reader_and_entry_owner_withdrawals_keep_fixed_draw(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for missing in ('trigger.event.chosen_creature_type','zone.entry.public_designation'):
            changed=json.loads(json.dumps(raw))
            next(c for c in changed['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Chosen type authority withdrawn'])
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            text='As this artifact enters, choose a creature type.\nWhenever you cast a creature spell of the chosen type, draw a card.'
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Artifact'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(record('Draw a card.','Sorcery'),capability_registry=registry).status)


class ChosenTypeEventModelTests(unittest.TestCase):
    def matches(self,text,*,chosen='goblin',controller='B',types=('creature',),subtypes=('goblin',),event=None):
        from types import SimpleNamespace
        from quorune.compiler.chosen_type_event_bindings import chosen_type_event_binding_spec
        from quorune.trigger_discovery import semantic_event_condition_matches
        from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
        from quorune.qualified_attack_model import ATTACK_FACT_PREFIX
        spec=chosen_type_event_binding_spec(text)
        self.assertIsNotNone(spec)
        condition=spec.condition
        subscriptions=FixedEventSubscriptionSet.from_condition(condition)
        if subscriptions:condition=subscriptions.subscription_for(event).condition
        source=SimpleNamespace(owner='A',controller='B',ref='SOURCE',object_id='source',
            annotations={} if chosen is None else {'chosen_creature_type':chosen})
        context={'card':'OTHER','controller':controller,'types':list(types),'subtypes':list(subtypes),
            ATTACK_FACT_PREFIX+'version':1,ATTACK_FACT_PREFIX+'types':list(types),
            ATTACK_FACT_PREFIX+'subtypes':list(subtypes)}
        return semantic_event_condition_matches(SimpleNamespace(state=SimpleNamespace(active_player='D')),
            condition,source=source,context=context)

    def test_controller_type_domain_and_missing_choice_are_distinct(self):
        text='Whenever you cast a creature spell of the chosen type, draw a card.'
        self.assertTrue(self.matches(text))
        self.assertFalse(self.matches(text,controller='A'))
        self.assertFalse(self.matches(text,subtypes=('elf',)))
        self.assertFalse(self.matches(text,chosen=None))
        self.assertFalse(self.matches(text,types=('kindred','instant')))
        self.assertTrue(self.matches(text.replace('creature spell','spell'),types=('kindred','instant')))
        self.assertTrue(self.matches(text,subtypes=('goblin','elf','shapeshifter')))

    def test_entry_attack_and_global_death_conditions_use_their_sealed_facts(self):
        text='Whenever a creature you control of the chosen type enters or attacks, draw a card.'
        for event in ('permanent.enter','creature.attacks'):
            self.assertTrue(self.matches(text,event=event))
            self.assertFalse(self.matches(text,event=event,controller='A'))
            self.assertFalse(self.matches(text,event=event,subtypes=('elf',)))
        self.assertTrue(self.matches('Whenever a creature of the chosen type dies, you may draw a card.',controller='D'))

    def test_malformed_public_designations_and_omitted_membership_are_distinguished(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        text='Whenever you cast a creature spell of the chosen type, draw a card.'
        for chosen in ('Goblin','not-a-creature-type','',True,1):
            with self.subTest(chosen=chosen),self.assertRaises(GameRuleError):self.matches(text,chosen=chosen)
        import quorune.trigger_discovery as owner
        original=owner._semantic_condition_operator_matches
        with patch.object(owner,'_semantic_condition_operator_matches',
                side_effect=lambda op,actual,expected:True if op=='in' else original(op,actual,expected)):
            with self.assertRaises(AssertionError):self.assertFalse(self.matches(text,subtypes=('elf',)))

    def test_payload_dependency_is_present_without_compiler_annotations(self):
        from types import SimpleNamespace
        from quorune.rules.chosen_type_events import chosen_type_event_dependencies,CHOSEN_TYPE_EVENT_CAPABILITY
        from quorune.compiler.chosen_type_event_bindings import chosen_type_event_binding_spec
        spec=chosen_type_event_binding_spec('Whenever a creature you control of the chosen type enters or attacks, draw a card.')
        self.assertEqual((CHOSEN_TYPE_EVENT_CAPABILITY,),chosen_type_event_dependencies(
            SimpleNamespace(event=spec.event,event_condition=spec.condition,capability_dependencies=())))

    def test_attack_guards_apply_per_subscription_and_reject_facts_on_the_wrong_branch(self):
        from types import SimpleNamespace
        from quorune.rules.event_subscriptions import FixedEventSubscription,fixed_event_subscription_condition
        from quorune.rules.qualified_attacks import qualified_attack_runtime_dependencies
        from quorune.qualified_attack_model import ATTACK_FACT_PREFIX,QUALIFIED_ATTACK_CAPABILITY
        condition={'all':[{'field':ATTACK_FACT_PREFIX+'version','op':'eq','value':1},
            {'field':ATTACK_FACT_PREFIX+'types','op':'contains_any','value':['creature']}]}
        valid=fixed_event_subscription_condition((FixedEventSubscription('permanent.enter'),
            FixedEventSubscription('creature.attacks',condition)))
        self.assertEqual((QUALIFIED_ATTACK_CAPABILITY,),qualified_attack_runtime_dependencies(
            SimpleNamespace(event='permanent.enter',event_condition=valid)))
        invalid=fixed_event_subscription_condition((FixedEventSubscription('permanent.enter',condition),
            FixedEventSubscription('creature.attacks')))
        with self.assertRaises(ValueError):qualified_attack_runtime_dependencies(
            SimpleNamespace(event='permanent.enter',event_condition=invalid))


import json
from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class ChosenTypeEventNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'chosen-type-events.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/chosen-type-event-cards.json',
            ROOT/'tests/fixtures/changeling-characteristics-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Chosen type events',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def entry_type(self,s,seat,subtype):
        self.assertEqual('replacement.order',self.resolve(s).kind)
        ctx=s.packet('pilot:'+seat,full=True)['decision']['ctx']
        option=next(row['id'] for row in ctx['options'] if row['id'].endswith(':'+subtype))
        command={'action_id':'choose','replacement':option}
        self.reject(s,'D' if seat!='D' else 'C',command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def settle(self,s):
        for _ in range(12):
            decision=self.resolve(s)
            if decision is None or decision.kind!='trigger.order':return decision
            seat=decision.actors[0];ctx=s.packet('pilot:'+seat,full=True)['decision']['ctx']
            result=s.act('pilot:'+seat,{'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]})
            self.assertTrue(result.ok,result.summary)
        self.fail('Chosen-type trigger order did not settle')

    def test_actual_cast_observers_keep_controller_creature_gate_and_countered_kindred_cast(self):
        s=self.session(205603101)
        banner=self.add(s.engine,"Vanquisher's Banner",zone='hand')
        door=self.add(s.engine,'Chronicle of Victory',seat='B',zone='hand')
        control=self.add(s.engine,'Confiscate',seat='B',zone='hand')
        orrery=self.add(s.engine,'Vedalken Orrery',seat='B')
        own=self.add(s.engine,'Raging Goblin',zone='hand',ref='owner-goblin')
        foreign=self.add(s.engine,'Raging Goblin',seat='B',zone='hand',ref='controller-goblin')
        kindred=self.add(s.engine,'Tarfire',seat='B',zone='hand')
        counter=self.add(s.engine,'Counterspell',zone='hand')
        for card in (banner,door,control,orrery,own,foreign,kindred,counter):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'C':20,'U':10,'R':10})
        self.prepare(s,mana={'C':10,'U':5,'R':5});self.checkpoint(s)
        self.cast(s,'A',banner,[]);self.entry_type(s,'A','goblin');self.assertIsNone(self.settle(s))
        self.cast(s,'B',door,[]);self.entry_type(s,'B','goblin');self.assertIsNone(self.settle(s))
        self.cast(s,'B',control,[banner.ref]);self.assertIsNone(self.settle(s))
        self.assertEqual(('A','B'),(s.state.cards[banner.object_id].owner,s.state.cards[banner.object_id].controller))
        self.cast(s,'A',own,[]);self.assertIsNone(self.settle(s))
        hand=len(s.state.players['B'].zones['hand'])
        self.cast(s,'B',foreign,[])
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(hand+1,len(s.state.players['B'].zones['hand']))
        self.cast(s,'B',kindred,['D'])
        target=next(item.ref for item in s.state.stack if item.card_object_id==kindred.object_id)
        self.cast(s,'A',counter,[target]);self.assertIsNone(self.settle(s))
        self.assertEqual(40,s.state.players['D'].life)
        self.assertEqual(hand+1,len(s.state.players['B'].zones['hand']))
        self.replay(s,load=True)

    def test_actual_pretender_chosen_entry_counts_another_controlled_changeling_only(self):
        s=self.session(205603104)
        source=self.add(s.engine,'Bloodline Pretender',zone='hand',ref='pretender-source')
        own=self.add(s.engine,'Universal Automaton',zone='hand',ref='pretender-own-changeling')
        foreign=self.add(s.engine,'Universal Automaton',seat='B',zone='hand',ref='pretender-foreign-changeling')
        wrong=self.add(s.engine,'Elite Vanguard',zone='hand',ref='pretender-other-type')
        self.add(s.engine,'Vedalken Orrery',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='pretender-private')
        for card in (source,own,foreign,wrong):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'C':1})
        self.prepare(s,mana={'C':5,'W':1});self.checkpoint(s)
        self.cast(s,'A',source,[])
        self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual('stack',s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True)
        self.entry_type(s,'A','elf');self.assertIsNone(self.settle(s))
        self.assertEqual('elf',s.state.cards[source.object_id].annotations['chosen_creature_type'])
        self.assertEqual({},s.state.cards[source.object_id].counters)
        self.cast(s,'A',own,[]);self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0))
        self.cast(s,'A',wrong,[]);self.assertIsNone(self.settle(s))
        self.cast(s,'B',foreign,[]);self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_specialist_simultaneous_death_uses_old_choice_after_reentry(self):
        s=self.session(205603102)
        source=self.add(s.engine,'Species Specialist',zone='hand')
        self.add(s.engine,'Elite Vanguard',ref='own-human')
        self.add(s.engine,'Elite Vanguard',seat='B',ref='other-human')
        self.add(s.engine,'Vedalken Orrery')
        wipe=self.add(s.engine,'Wrath of God',zone='hand')
        restore=self.add(s.engine,'Zombify',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        for card in (source,wipe,restore):self.assert_closed(s,card)
        self.prepare(s,mana={'C':20,'B':10,'W':10});self.checkpoint(s)
        self.cast(s,'A',source,[]);self.entry_type(s,'A','human');self.assertIsNone(self.settle(s))
        old=s.state.cards[source.object_id].logical_object_id
        self.cast(s,'A',wipe,[]);decision=self.resolve(s)
        self.assertEqual('trigger.order',decision.kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(3,len(ctx['triggers']))
        s=self.replay(s,load=True)
        self.assertTrue(s.act('pilot:A',{'action_id':'order','triggers':[r['id'] for r in ctx['triggers']]}).ok)
        self.cast(s,'A',restore,[source.ref]);self.entry_type(s,'A','elf')
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual('elf',s.state.cards[source.object_id].annotations['chosen_creature_type'])
        hand=len(s.state.players['A'].zones['hand'])
        for _ in range(3):
            decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
            self.reject(s,'B',{'action_id':'choose','choice':'apply'})
            result=s.act('pilot:A',{'action_id':'choose','choice':'apply'})
            self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(hand+3,len(s.state.players['A'].zones['hand']))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_discovery_observes_entry_and_attack_but_not_countered_cast(self):
        s=self.session(205603103)
        source=self.add(s.engine,'Kindred Discovery',zone='hand')
        first=self.add(s.engine,'Raging Goblin',zone='hand',ref='countered-goblin')
        second=self.add(s.engine,'Raging Goblin',zone='hand',ref='entering-goblin')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        for card in (source,first,second,counter):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'U':2})
        self.prepare(s,mana={'U':5,'C':10,'R':5});self.checkpoint(s)
        self.cast(s,'A',source,[]);self.entry_type(s,'A','goblin');self.assertIsNone(self.settle(s))
        hand=len(s.state.players['A'].zones['hand'])
        self.cast(s,'A',first,[]);self.assertEqual(hand-1,len(s.state.players['A'].zones['hand']))
        self.cast(s,'B',counter,[s.state.stack[-1].ref]);self.assertIsNone(self.settle(s))
        self.assertEqual(hand-1,len(s.state.players['A'].zones['hand']))
        self.cast(s,'A',second,[]);self.assertIsNone(self.settle(s))
        self.assertEqual(hand-1,len(s.state.players['A'].zones['hand']))
        before=len(s.state.players['A'].zones['hand']);self.attack(s,[(second,'B')])
        self.assertIsNone(self.settle(s));self.assertEqual(before+1,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)


    def test_actual_banner_reads_effective_cast_types_with_record_provenance(self):
        from unittest.mock import patch
        from quorune.cast_event_type_model import CAST_EVENT_TYPE_SNAPSHOT_VERSION_FIELD
        from quorune.record_state_provenance import format_state_versions, validate_state_versions

        cases = (
            ("Turtleshell Changeling", "goblin", 1, False, 1),
            ("Turtleshell Changeling", "goblin", None, False, 0),
            ("Aerie Bowmasters", "dog", 1, True, 0),
        )
        for index, (name, subtype, version, face_down, expected) in enumerate(cases):
            with self.subTest(name=name, version=version, face_down=face_down):
                session = self.session(205_603_157 + index)
                engine = session.engine
                session.state.cast_event_type_snapshot_version = version
                format_value = format_state_versions(session.state)
                field = CAST_EVENT_TYPE_SNAPSHOT_VERSION_FIELD
                self.assertEqual(version, format_value.get(field))
                self.assertEqual(version is not None, field in format_value)
                with self.assertRaises(ValueError):
                    validate_state_versions({"format": {**format_value, field: True}}, session.state)
                source = self.add(engine, "Vanquisher's Banner", zone="hand")
                spell = self.add(engine, name, zone="hand")
                for card in (source, spell):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"C": 8, **({} if face_down else {"U": 1})})
                self.checkpoint(session)
                self.cast(session, "A", source)
                self.entry_type(session, "A", subtype)
                self.assertIsNone(self.settle(session))
                dispatch = engine._dispatch_semantic_event
                cast_facts = []

                def observe(event, context, *args, **kwargs):
                    if event == "spell.cast":
                        cast_facts.append((context["types"], context["subtypes"]))
                    return dispatch(event, context, *args, **kwargs)

                before = len(session.state.players["A"].zones["hand"])
                with patch.object(engine, "_dispatch_semantic_event", side_effect=observe):
                    if face_down:
                        action_id = f"cast-megamorph:{spell.ref}"
                        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                        self.assertIn(action_id, {row["id"] for row in actions})
                        accepted = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
                        self.assertTrue(accepted.ok, accepted.summary)
                    else:
                        self.cast(session, "A", spell)
                (types, subtypes), = cast_facts
                self.assertIn("creature", types)
                self.assertEqual(bool(expected), subtype in subtypes)
                if face_down:
                    self.assertEqual((), tuple(subtypes))
                self.assertEqual(expected, self.trigger_count(session, source))
                self.assertIsNone(self.settle(session))
                self.assertEqual(before - 1 + expected, len(session.state.players["A"].zones["hand"]))
                self.replay(session, load=True)
