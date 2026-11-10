from __future__ import annotations

"""CR 603.6/603.10/700.9: sealed public permanent state qualifications."""
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


class StateQualifiedZoneCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def test_equipped_modified_enchanted_and_combat_subjects_keep_state_at_occurrence(self):
        for text in (
            'Whenever an equipped creature you control dies, draw a card.',
            'Whenever a modified creature you control dies, manifest the top card of your library.',
            'Whenever another modified creature you control dies, put two +1/+1 counters on this creature.',
            'Whenever an attacking creature you control dies, this creature deals 2 damage to each opponent.',
            'Whenever a blocking creature an opponent controls dies, draw a card.',
            'Whenever an enchanted creature dies, draw a card.',
        ):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=self.registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertIn('trigger.event.state_qualified_zone_change',ir.faces[0].nodes[0].capability_dependencies)

    def test_aggregate_and_unknown_state_qualifiers_stay_residual(self):
        for text in ('Whenever one or more modified creatures you control die, draw a card.',
                'Whenever a secretly modified creature dies, draw a card.',
                'Whenever an equipped creature with the remembered quality dies, draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=self.registry).status)

    def test_triggered_source_exile_requires_matching_zone_and_untargeted_death(self):
        from types import SimpleNamespace
        from quorune.rules.public_zone_move_capability_shapes import triggered_source_exile_runtime_dependencies

        effect = {"op": "exile_public_graveyard_card", "card": "$source.graveyard_object"}
        bound = {"coverage": (), "active_zone": "graveyard", "event": "creature.dies",
                 "target_schema": None, "effects": (effect,)}
        self.assertEqual(("card.exile.public_graveyard",),
                         triggered_source_exile_runtime_dependencies(SimpleNamespace(**bound)))
        for changed in (
            {"active_zone": "battlefield"},
            {"event": "permanent.enter"},
            {"target_schema": {"zones": ["graveyard"], "count": 1}},
            {"effects": ({**effect, "card": "$source.zone_object"},)},
            {"effects": ({**effect, "card": "$target.0"},), "coverage": ("triggered-source-exile",)},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                triggered_source_exile_runtime_dependencies(SimpleNamespace(**{**bound, **changed}))

    def test_verified_state_and_zone_owner_withdrawals_keep_plain_entry_draw(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for missing in ('trigger.event.state_qualified_zone_change','trigger.event.qualified_zone_change'):
            changed=json.loads(json.dumps(raw))
            next(c for c in changed['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Sealed zone state owner withdrawn'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record('Whenever an equipped creature you control dies, draw a card.'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(query_record('When this creature enters, draw a card.'),capability_registry=registry).status)


class StateQualifiedDamageCompilerTests(unittest.TestCase):
    def test_damage_subjects_require_versioned_source_state_and_the_actual_damage_domain(self):
        registry=load_default_capability_registry()
        for text in ('Whenever a modified creature you control deals combat damage to a player, draw a card.',
                'Whenever an enchanted creature you control deals combat damage to a player, draw a card.',
                'Whenever an equipped creature you control deals damage to an opponent, you gain 1 life.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertIn('trigger.event.state_qualified_damage',ir.faces[0].nodes[0].capability_dependencies)

    def test_aggregate_unknown_and_unrepresented_damage_subjects_stay_residual(self):
        registry=load_default_capability_registry()
        for text in ('Whenever one or more modified creatures you control deal combat damage to a player, draw a card.',
                'Whenever a secretly modified creature deals damage to a player, draw a card.',
                'Whenever a modified creature with the remembered quality deals damage to a player, draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_verified_damage_state_and_damage_owner_withdrawals_keep_plain_damage(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for missing in ('trigger.event.state_qualified_damage','trigger.event.qualified_damage_singular'):
            changed=json.loads(json.dumps(raw))
            next(c for c in changed['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Sealed damage state owner withdrawn'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record('Whenever a modified creature you control deals combat damage to a player, draw a card.'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(query_record('Whenever this creature deals combat damage to a player, draw a card.'),capability_registry=registry).status)


class PermanentDamageStateModelTests(unittest.TestCase):
    def test_omitting_final_damage_state_projection_is_killed(self):
        from unittest.mock import patch
        method='test_final_damage_keeps_sealed_facts_and_rejects_partial_or_coerced_payload'
        baseline=unittest.TestResult();PermanentDamageStateModelTests(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(),baseline.errors+baseline.failures)
        with patch('quorune.damage.damage_permanent_state_context',return_value={}):
            result=unittest.TestResult();PermanentDamageStateModelTests(method).run(result)
        self.assertFalse(result.wasSuccessful())
        self.assertFalse(result.errors,result.errors)
        self.assertEqual(1,len(result.failures))

    def test_final_damage_keeps_sealed_facts_and_rejects_partial_or_coerced_payload(self):
        from dataclasses import replace
        from quorune.damage import _final_event
        from quorune.damage_values import DamageProposal,DamageRecipientSnapshot
        from quorune.damage_source import DamageSourceSnapshot,DamageError
        from quorune.permanent_event_state_model import PermanentEventState,damage_permanent_state_context
        source=DamageSourceSnapshot('C1','object1','object1@0','A','A')
        facts=PermanentEventState(True,True,False,True,False,False,False)
        proposal=DamageProposal('damage1',replace(source,permanent_state=facts),
            DamageRecipientSnapshot('B','player','B'),2,True,'combat')
        event=proposal.event()
        final=_final_event(event,proposed=2,dealt=1,prevented=1)
        self.assertEqual(facts,final.source_permanent_state)
        for key,value in damage_permanent_state_context(facts).items():
            self.assertEqual(value,final.semantic_context().get(key))
        old=replace(proposal,source=source).event()
        self.assertFalse(any(key.startswith('source_permanent_state_') for key in
            _final_event(old,proposed=2,dealt=2,prevented=0).semantic_context()))
        for change in ('missing','coerced','unknown'):
            payload=dict(event.payload)
            if change=='missing':payload.pop('source_permanent_state_version')
            elif change=='coerced':payload['source_permanent_state_modified']=0
            else:payload['source_permanent_state_secret']=True
            with self.assertRaises(DamageError):_final_event(replace(event,payload=payload),proposed=2,dealt=2,prevented=0)

    def test_damage_source_codec_preserves_absence_and_rejects_malformed_facts(self):
        from quorune.damage_source import DamageSourceSnapshot,DamageError
        from quorune.permanent_event_state_model import PermanentEventState
        from dataclasses import replace
        source=DamageSourceSnapshot('C1','object1','object1@0','A','A')
        old=source.to_dict();self.assertNotIn('permanent_state',old)
        self.assertEqual(old,DamageSourceSnapshot.from_dict(old).to_dict())
        facts=PermanentEventState(False,True,False,True,False,False,False)
        current=replace(source,permanent_state=facts)
        self.assertEqual(current,DamageSourceSnapshot.from_dict(current.to_dict()))
        with self.assertRaises(DamageError):DamageSourceSnapshot.from_dict({**old,'permanent_state':{**facts.to_dict(),'modified':1}})

    def test_damage_minimum_requires_its_own_version_and_event(self):
        from types import SimpleNamespace
        from quorune.permanent_event_state_model import state_qualified_damage_runtime_dependencies,STATE_QUALIFIED_DAMAGE_CAPABILITY
        condition={'all':[{'field':'source_permanent_state_version','op':'eq','value':1},
            {'field':'source_permanent_state_modified','op':'eq','value':True}]}
        self.assertEqual((STATE_QUALIFIED_DAMAGE_CAPABILITY,),state_qualified_damage_runtime_dependencies(
            SimpleNamespace(event='damage.dealt',event_condition=condition)))
        for event,c in (('creature.dies',condition),('damage.dealt',condition['all'][1])):
            with self.assertRaises(ValueError):state_qualified_damage_runtime_dependencies(SimpleNamespace(event=event,event_condition=c))


class PermanentEventStateModelTests(unittest.TestCase):
    def test_sealed_facts_round_trip_and_unknown_is_distinct_from_false(self):
        from quorune.permanent_event_state_model import PermanentEventState,sealed_permanent_state_context,PERMANENT_EVENT_STATE_KEY
        from quorune.replacement.immutable import FrozenMap
        facts=PermanentEventState(False,True,False,True,False,False,False)
        self.assertEqual(facts,PermanentEventState.from_dict(facts.to_dict()))
        self.assertEqual({},sealed_permanent_state_context({}))
        context=sealed_permanent_state_context({PERMANENT_EVENT_STATE_KEY:FrozenMap(facts.to_dict())})
        self.assertEqual(1,context['permanent_state_version'])
        self.assertTrue(context['permanent_state_enchanted'])
        self.assertFalse(context['permanent_state_modified'])
        for changes in ({'schema_version':True},{'schema_version':2},{'modified':1},{'extra':False}):
            with self.assertRaises(ValueError):PermanentEventState.from_dict({**facts.to_dict(),**changes})

    def test_capture_uses_public_query_and_historical_mode_preserves_characteristics(self):
        from types import SimpleNamespace
        from quorune.rules.permanent_event_state import permanent_event_characteristics
        from quorune.permanent_event_state_model import PERMANENT_EVENT_STATE_KEY
        row=SimpleNamespace(tapped=True,attacking=False,blocking=True,enchanted=True,equipped=False,
            modified=False,monstrous_value=None)
        host=SimpleNamespace(state=SimpleNamespace(permanent_event_state_version=1),_public_object_query_result=lambda card:row)
        card=SimpleNamespace(zone='battlefield')
        data={'type_line':'Creature — Human'}
        captured=permanent_event_characteristics(host,card,data)
        row.enchanted=False
        self.assertTrue(captured[PERMANENT_EVENT_STATE_KEY]['enchanted'])
        self.assertFalse(captured[PERMANENT_EVENT_STATE_KEY]['modified'])
        host.state.permanent_event_state_version=None
        self.assertIs(data,permanent_event_characteristics(host,card,data))

    def test_runtime_minimum_requires_version_and_correct_event_without_annotations(self):
        from types import SimpleNamespace
        from quorune.permanent_event_state_model import state_qualified_zone_runtime_dependencies,STATE_QUALIFIED_ZONE_CAPABILITY
        condition={'all':[{'field':'permanent_state_version','op':'eq','value':1},
            {'field':'permanent_state_modified','op':'eq','value':True}]}
        self.assertEqual((STATE_QUALIFIED_ZONE_CAPABILITY,),state_qualified_zone_runtime_dependencies(
            SimpleNamespace(event='creature.dies',event_condition=condition)))
        for event,value in (('spell.cast',condition),('creature.dies',condition['all'][1]),
                ('creature.dies',{'all':[condition['all'][0],{'field':'permanent_state_modified','op':'eq','value':1}]})):
            with self.assertRaises(ValueError):state_qualified_zone_runtime_dependencies(SimpleNamespace(event=event,event_condition=value))

    def test_execution_marker_absence_and_malformed_values_follow_the_state_codec(self):
        from quorune.model import GameState,GameConfig
        from quorune.permanent_event_state_model import permanent_event_state_version
        state=GameState(game_id='state-facts-codec',config=GameConfig(),players={},cards={},deck_names={},
            commander_oracle_ids={},turn_order=[],current_turn=0,last_normal_turn_player=None)
        self.assertEqual(2,state.to_dict()['permanent_event_state_version'])
        legacy=state.to_dict();legacy.pop('permanent_event_state_version')
        restored=GameState.from_dict(legacy)
        self.assertIsNone(restored.permanent_event_state_version)
        self.assertNotIn('permanent_event_state_version',restored.to_dict())
        version_one=GameState.from_dict({**legacy,'permanent_event_state_version':1})
        self.assertEqual(1,version_one.to_dict()['permanent_event_state_version'])
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        for checkpoint in (state,version_one,restored):
            validate_state_versions({'format':format_state_versions(checkpoint)},checkpoint)
        for value in (True,0,1,3,'2'):
            with self.assertRaises(ValueError):validate_state_versions(
                {'format':{**format_state_versions(state),'permanent_event_state_version':value}},state)
        for value in (True,0,3,'2'):
            with self.assertRaises(ValueError):permanent_event_state_version(value)

    def test_normalized_departure_keeps_old_state_and_entry_uses_new_state(self):
        from test_zone_trigger_events import occurrence
        from quorune.zone_trigger_events import normalized_zone_trigger_events
        from quorune.permanent_event_state_model import PermanentEventState,PERMANENT_EVENT_STATE_KEY
        old=PermanentEventState(True,True,False,True,True,True,False)
        new=PermanentEventState(False,False,False,False,False,False,False)
        base={'type_line':'Creature — Human','mana_value':2}
        dead=occurrence(origin='battlefield',destination='graveyard',
            previous_characteristics={**base,PERMANENT_EVENT_STATE_KEY:old.to_dict()},current_characteristics=base)
        death=next(e for e in normalized_zone_trigger_events(dead) if e.kind=='creature.dies')
        self.assertEqual(True,death.context.get('permanent_state_equipped'))
        self.assertTrue(death.context['permanent_state_attacking'])
        entered=occurrence(previous_characteristics={**base,PERMANENT_EVENT_STATE_KEY:old.to_dict()},
            current_characteristics={**base,PERMANENT_EVENT_STATE_KEY:new.to_dict()})
        event=next(e for e in normalized_zone_trigger_events(entered) if e.kind=='permanent.enter')
        self.assertFalse(event.context['permanent_state_equipped'])
        exiled=occurrence(origin='battlefield',destination='exile',previous_characteristics={**base,PERMANENT_EVENT_STATE_KEY:old.to_dict()})
        self.assertFalse(any(e.kind=='creature.dies' for e in normalized_zone_trigger_events(exiled)))

    def test_omitting_the_equipped_fact_is_killed(self):
        from unittest.mock import patch
        from quorune.permanent_event_state_model import PermanentEventState
        original=PermanentEventState.context
        def omitted(facts):
            value=original(facts);value.pop('permanent_state_equipped');return value
        with patch.object(PermanentEventState,'context',omitted):
            result=unittest.TestResult()
            PermanentEventStateModelTests('test_normalized_departure_keeps_old_state_and_entry_uses_new_state').run(result)
        self.assertFalse(result.errors,result.errors)
        self.assertTrue(result.failures)

    def test_new_object_context_does_not_inherit_old_permanent_state_when_current_is_unknown(self):
        from test_zone_trigger_events import occurrence
        from quorune.zone_trigger_events import normalized_zone_trigger_events
        from quorune.permanent_event_state_model import PermanentEventState,PERMANENT_EVENT_STATE_KEY
        base={'type_line':'Creature — Human','mana_value':2}
        facts=PermanentEventState(True,True,False,True,True,True,False)
        for origin,destination,kind in (('hand','battlefield','permanent.enter'),
                ('battlefield','graveyard','card.enter_graveyard')):
            event=occurrence(origin=origin,destination=destination,
                previous_characteristics={**base,PERMANENT_EVENT_STATE_KEY:facts.to_dict()},current_characteristics=base)
            current=next(row for row in normalized_zone_trigger_events(event) if row.kind==kind)
            self.assertFalse(any(key.startswith('permanent_state_') for key in current.context))


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.attachments import attach_objects
from scripts.build_test_database import build_fixture_database


class StateQualifiedZoneNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'state-qualified-zone.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/state-qualified-zone-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Sealed state',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);r=s.act('pilot:'+seat,command)
        self.assertFalse(r.ok,r.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def attach(self,s,source,target):
        attach_objects(s.state.cards,source,target,source_timestamp=s.state.turn_sequence+1,players=s.state.players)

    def test_death_stat_threshold_and_amount_preserve_version_one_replay(self):
        for version in (1, 2):
            with self.subTest(version=version):
                session = self.session(848601)
                session.state.permanent_event_state_version = version
                engine = session.engine
                burial = self.add(engine, "Proper Burial")
                flayer = self.add(engine, "Sultai Flayer")
                bear = self.add(engine, "Grizzly Bears")
                kill = self.add(engine, "Murder", zone="hand")
                bear.counters["+1/+1"] = 2
                for card in (burial, flayer, kill):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"B": 2, "C": 1})
                self.checkpoint(session)
                self.assertEqual(4, engine._numeric_stat(bear.object_id, "toughness"))

                self.cast(session, "A", kill, [bear.ref])
                self.resolve_top(session)
                self.assertEqual("graveyard", bear.zone)
                if version == 2:
                    self.assertEqual("trigger.order", session.state.pending_decision.kind)
                    offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
                    self.assertEqual(2, len(offered))
                    command = {"action_id": "order", "triggers": [item["id"] for item in offered]}
                    self.reject(session, "B", command)
                    ordered = session.act("pilot:A", command)
                    self.assertTrue(ordered.ok, ordered.summary)
                expected_sources = {burial.object_id, flayer.object_id} if version == 2 else {burial.object_id}
                self.assertEqual(expected_sources, {item.source_object_id for item in session.state.stack})
                self.assertEqual({4 if version == 2 else 2}, {item.context["toughness"] for item in session.state.stack})
                self.assertIsNone(self.resolve(session))
                self.assertEqual(48 if version == 2 else 42, session.state.players["A"].life)
                self.replay(session, load=True)

    def test_entry_stat_threshold_reads_completed_entering_creature(self):
        session = self.session(848602)
        engine = session.engine
        uprising = self.add(engine, "Garruk's Uprising")
        ballista = self.add(engine, "Walking Ballista", zone="hand")
        for card in (uprising, ballista):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 8})
        self.checkpoint(session)
        self.priority(session, "A")
        action = self.action(session, "A", ballista)
        result = session.act("pilot:A", {"action_id": action["id"], "x": 4, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        hand_after_cast = len(session.state.players["A"].zones["hand"])

        self.resolve_top(session)
        self.assertEqual(("battlefield", 4), (ballista.zone, engine._numeric_stat(ballista.object_id, "power")))
        trigger, = session.state.stack
        self.assertEqual((uprising.object_id, 4), (trigger.source_object_id, trigger.context["power"]))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand_after_cast + 1, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)

    def test_actual_combat_damage_distinguishes_enchanted_modified_and_opposing_equipment(self):
        s=self.session(603108120)
        modified=self.add(s.engine,'SP//dr, Piloted by Peni')
        enchanted=self.add(s.engine,'Ellivere of the Wild Court')
        attackers=[self.add(s.engine,'Raging Goblin',ref='state-damage-'+str(i)) for i in range(3)]
        aura=self.add(s.engine,'Oakenform',seat='B');self.attach(s,aura,attackers[0])
        for i,seat in enumerate(('A','B'),start=1):
            equipment=self.add(s.engine,'Lightning Greaves',seat=seat,ref='state-greaves-'+str(i))
            self.attach(s,equipment,attackers[i])
        attackers[1].counters['charge']=1
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.assert_closed(s,modified);self.assert_closed(s,enchanted)
        self.prepare(s,mana={});self.checkpoint(s)
        hand=len(s.state.players['A'].zones['hand'])
        self.attack(s,list(zip(attackers,'BCD')))
        self.until(s,lambda current:current.state.pending_decision is not None
            and current.state.pending_decision.kind=='trigger.order')
        self.assertEqual([36,39,39],[s.state.players[seat].life for seat in 'BCD'])
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual(3,len(ctx['triggers']))
        command={'action_id':'order','triggers':[r['id'] for r in ctx['triggers']]}
        self.reject(s,'B',command)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        s=self.replay(s,load=True);self.assertTrue(s.act('pilot:A',command).ok)
        self.assertIsNone(self.resolve(s));self.assertEqual(hand+3,len(s.state.players['A'].zones['hand']))
        self.replay(s,load=True)

    def test_actual_outfitter_simultaneous_death_keeps_equipment_and_old_controller(self):
        s=self.session(603108101)
        source=self.add(s.engine,'Stone Haven Outfitter')
        own=self.add(s.engine,'Memnite',ref='own-equipped')
        other=self.add(s.engine,'Memnite',seat='B',ref='opponent-equipped')
        self.add(s.engine,'Phyrexian Walker')
        for i,card in enumerate((source,own,other)):
            equipment=self.add(s.engine,'Bonesplitter',seat=card.controller,ref='death-equipment-'+str(i))
            self.attach(s,equipment,card)
        wipe=self.add(s.engine,'Wrath of God',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,wipe)
        self.prepare(s,mana={'W':2,'C':2});self.checkpoint(s)
        hand=len(s.state.players['A'].zones['hand']);self.cast(s,'A',wipe,[])
        self.assertEqual('trigger.order',self.resolve(s).kind)
        self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in (source,own,other)))
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual(2,len(ctx['triggers']))
        command={'action_id':'order','triggers':[r['id'] for r in ctx['triggers']]}
        self.reject(s,'B',command);s=self.replay(s,load=True)
        self.assertTrue(s.act('pilot:A',command).ok);self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_creature_death_destination_uses_owner_and_last_known_toughness(self):
        session = self.session(851001)
        engine = session.engine
        skirge = self.add(engine, "Lurking Skirge")
        feast = self.add(engine, "Grim Feast")
        magus = self.add(engine, "Magus of the Bridge")
        own = self.add(engine, "Grizzly Bears", ref="death-own")
        stolen = self.add(engine, "Grizzly Bears", seat="B", ref="death-stolen")
        kills = [self.add(engine, "Murder", zone="hand", ref=f"death-kill-{index}") for index in range(2)]
        engine.change_control(stolen.object_id, "A", reason="set up shared previous controller")
        stolen.counters["+1/+1"] = 1
        self.assertEqual(3, engine._numeric_stat(stolen.object_id, "toughness"))
        for card in (skirge, feast, magus, *kills):
            self.assert_closed(session, card)
        self.prepare(session, mana={"B": 4, "C": 2})
        self.checkpoint(session)

        self.cast(session, "A", kills[0], [own.ref])
        self.assertIsNone(self.resolve(session))
        self.assertIn(own.object_id, session.state.players["A"].zones["graveyard"])
        self.assertEqual(40, session.state.players["A"].life)
        self.assertEqual({"enchantment"}, engine._effective_type_parts(skirge)[0])
        self.assertEqual("battlefield", magus.zone)
        zombie, = (card for card in session.state.cards.values() if card.is_token)
        self.assertEqual(("A", "battlefield", "Zombie Token"), (zombie.controller, zombie.zone, zombie.printed_name))

        self.cast(session, "A", kills[1], [stolen.ref])
        self.assertEqual("trigger.order", self.resolve(session).kind)
        batch, = session.state.pending_trigger_batches
        self.assertEqual({card.object_id for card in (skirge, feast, magus)},
                         {item.source_object_id for item in batch.groups[0].items})
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        command = {"action_id": "order", "triggers": [row["id"] for row in offered]}
        self.reject(session, "B", command)
        ordered = session.act("pilot:A", command)
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertIsNone(self.resolve(session))
        self.assertIn(stolen.object_id, session.state.players["B"].zones["graveyard"])
        self.assertEqual(2, engine._numeric_stat(stolen.object_id, "toughness"))
        self.assertEqual(43, session.state.players["A"].life)
        self.assertEqual(({"creature"}, {"phyrexian", "imp"}), engine._effective_type_parts(skirge)[:2])
        self.assertEqual((3, 2), tuple(engine._numeric_stat(skirge.object_id, name) for name in ("power", "toughness")))
        self.assertIn("flying", engine._combat_keywords(skirge))
        self.assertEqual("exile", magus.zone)
        self.assertEqual([zombie.object_id], [card.object_id for card in session.state.cards.values() if card.is_token])
        self.replay(session, load=True)

    def test_creature_death_destination_ignores_graveyard_move_replaced_by_exile(self):
        session = self.session(851004)
        engine = session.engine
        feast = self.add(engine, "Grim Feast")
        skirge = self.add(engine, "Lurking Skirge")
        magus = self.add(engine, "Magus of the Bridge")
        bridge = self.add(engine, "Bridge from Below", zone="graveyard")
        self.add(engine, "Dauthi Voidwalker")
        opponent = self.add(engine, "Grizzly Bears", seat="B")
        kill = self.add(engine, "Murder", zone="hand")
        for card in (feast, skirge, magus, bridge, kill):
            self.assert_closed(session, card)
        self.prepare(session, mana={"B": 2, "C": 1})
        self.checkpoint(session)

        self.cast(session, "A", kill, [opponent.ref])
        self.resolve_top(session)
        self.assertEqual("exile", opponent.zone)
        self.assertEqual(1, opponent.counters.get("void"))
        self.assertFalse(session.state.stack)
        self.assertFalse(session.state.pending_trigger_batches)
        self.assertEqual(40, session.state.players["A"].life)
        self.assertEqual({"enchantment"}, engine._effective_type_parts(skirge)[0])
        self.assertEqual(("battlefield", "graveyard"), (magus.zone, bridge.zone))
        self.replay(session, load=True)

    def test_bridge_from_below_orders_both_results_and_rechecks_original_graveyard_source(self):
        for mode in ("token-first", "exile-first", "reentered", "absent"):
            with self.subTest(mode=mode):
                session = self.session(851002)
                engine = session.engine
                bridge = self.add(engine, "Bridge from Below", zone="hand" if mode == "absent" else "graveyard")
                own = self.add(engine, "Grizzly Bears", ref="bridge-own")
                opponent = self.add(engine, "Grizzly Bears", seat="B", ref="bridge-opponent")
                wipe = self.add(engine, "Wrath of God", zone="hand")
                if mode == "reentered":
                    reclaim = self.add(engine, "Reclaim", zone="hand")
                    mill = self.add(engine, "Thought Scour", zone="hand")
                self.assert_closed(session, bridge)
                self.assert_closed(session, wipe)
                self.prepare(session, mana={"W": 2, "C": 2, "G": 1, "U": 1})
                self.checkpoint(session)

                self.cast_for(session, "A", wipe, reserve={"G": 1, "U": 1})
                self.resolve_top(session)
                self.assertEqual(["graveyard", "graveyard"], [own.zone, opponent.zone])
                if mode == "absent":
                    self.assertFalse(session.state.stack)
                    self.assertFalse(session.state.pending_trigger_batches)
                    self.replay(session, load=True)
                    continue

                self.assertEqual("trigger.order", session.state.pending_decision.kind)
                batch, = session.state.pending_trigger_batches
                items = batch.groups[0].items
                self.assertEqual([bridge.object_id, bridge.object_id], [item.source_object_id for item in items])
                refs = {engine.semantics.get(item.source_ability_id).effects[0]["op"]: item.ref for item in items}
                token, exile = refs["create_token"], refs["exile_public_graveyard_card"]
                order = [exile, token] if mode == "token-first" else [token, exile]
                self.assertEqual(set(order), {row["id"] for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]})
                command = {"action_id": "order", "triggers": order}
                self.reject(session, "B", command)
                ordered = session.act("pilot:A", command)
                self.assertTrue(ordered.ok, ordered.summary)

                if mode == "reentered":
                    original = bridge.logical_object_id
                    self.cast(session, "A", reclaim, [bridge.ref])
                    self.resolve_top(session)
                    self.assertEqual("library", bridge.zone)
                    self.cast(session, "A", mill, ["A"])
                    self.resolve_top(session)
                    self.assertEqual("graveyard", bridge.zone)
                    self.assertNotEqual(original, bridge.logical_object_id)

                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                expected_zone = "graveyard" if mode == "reentered" else "exile"
                self.assertEqual(expected_zone, session.state.cards[bridge.object_id].zone)
                self.assertEqual(int(mode == "token-first"), sum(card.is_token for card in session.state.cards.values()))
                self.replay(session, load=True)

    def test_magus_exiles_original_token_copy_and_spares_returned_permanent(self):
        session = self.session(851003)
        engine = session.engine
        magus = self.add(engine, "Magus of the Bridge")
        opponent = self.add(engine, "Grizzly Bears", seat="B")
        copy = self.add(engine, "Cackling Counterpart", zone="hand")
        kill = self.add(engine, "Murder", zone="hand")
        blink = self.add(engine, "Cloudshift", zone="hand")
        for card in (magus, copy, kill, blink):
            self.assert_closed(session, card)
        self.prepare(session, mana={"U": 2, "B": 2, "W": 1, "C": 2})
        self.checkpoint(session)
        self.cast_for(session, "A", copy, [magus.ref], reserve={"W": 1, "B": 2})
        self.assertIsNone(self.resolve(session))
        token, = (card for card in session.state.cards.values() if card.is_token)

        self.cast_for(session, "A", kill, [opponent.ref], reserve={"W": 1})
        self.assertEqual("trigger.order", self.resolve(session).kind)
        batch, = session.state.pending_trigger_batches
        self.assertEqual({magus.object_id, token.object_id}, {item.source_object_id for item in batch.groups[0].items})
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        ordered = session.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in offered]})
        self.assertTrue(ordered.ok, ordered.summary)
        original = magus.logical_object_id
        self.cast(session, "A", blink, [magus.ref])
        self.resolve_top(session)
        self.assertEqual("battlefield", magus.zone)
        self.assertNotEqual(original, magus.logical_object_id)

        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", magus.zone)
        self.assertEqual("outside", session.state.cards[token.object_id].zone)
        exile, = (event for event in session.state.events if event.code == "permanent.exile"
                  and event.details.get("object") == token.ref)
        self.assertEqual("exile", exile.details["destination"])
        self.replay(session, load=True)

    def test_actual_guardian_distinguishes_opponent_aura_current_controller_and_any_counter(self):
        s=self.session(603108102)
        observer=self.add(s.engine,'Guardian of the Forgotten')
        creatures=[self.add(s.engine,'Grizzly Bears',ref='modified-bear-'+str(i)) for i in range(3)]
        auras=[self.add(s.engine,'Pacifism',seat='B',ref='foreign-aura-'+str(i)) for i in range(2)]
        for aura,creature in zip(auras,creatures):self.attach(s,aura,creature)
        creatures[2].counters['charge']=1
        control=self.add(s.engine,'Confiscate',zone='hand')
        kills=[self.add(s.engine,'Murder',zone='hand',ref='state-kill-'+str(i)) for i in range(3)]
        self.assert_closed(s,observer)
        for card in (control,*kills):self.assert_closed(s,card)
        self.prepare(s,mana={'C':20,'B':10,'U':10});self.checkpoint(s)
        facedown=lambda current:sum(c.zone=='battlefield' and c.controller=='A' and c.face_down for c in current.state.cards.values())
        self.cast(s,'A',kills[0],[creatures[0].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(0,facedown(s))
        self.cast(s,'A',control,[auras[1].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(('B','A'),(s.state.cards[auras[1].object_id].owner,s.state.cards[auras[1].object_id].controller))
        self.cast(s,'A',kills[1],[creatures[1].ref]);self.resolve_top(s)
        self.assertTrue(s.state.stack);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,facedown(s))
        self.cast(s,'A',kills[2],[creatures[2].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,facedown(s));self.replay(s,load=True)

    def test_generated_attacking_death_uses_current_combat_state_and_not_the_returned_object(self):
        for blinked in (False,True):
            with self.subTest(blinked=blinked):
                s=self.session(603108110+int(blinked))
                observer=self.add(s.engine,'Generic Attacking Death Observer')
                creature=self.add(s.engine,'Raging Goblin')
                kill=self.add(s.engine,'Shock',seat='B',zone='hand')
                blink=self.add(s.engine,'Cloudshift',zone='hand') if blinked else None
                white=self.add(s.engine,'Plains',ref='combat-white')
                red=self.add(s.engine,'Mountain',seat='B',ref='combat-red')
                self.assert_closed(s,observer);self.assert_closed(s,kill)
                self.prepare(s,mana={});self.checkpoint(s)
                initial=creature.logical_object_id
                self.attack(s,[(creature,'B')])
                def mana(seat,land):
                    self.priority(s,seat)
                    rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
                    offered=[r for r in rows if land.ref in r['id']]
                    self.assertEqual(1,len(offered),offered)
                    self.assertTrue(s.act('pilot:'+seat,{'action_id':offered[0]['id'],'pay':'auto'}).ok)
                if blinked:
                    mana('A',white);self.cast(s,'A',blink,[creature.ref]);self.resolve_top(s)
                    self.assertNotEqual(initial,s.state.cards[creature.object_id].logical_object_id)
                hand=len(s.state.players['A'].zones['hand'])
                mana('B',red);self.cast(s,'B',kill,[creature.ref])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(hand+int(not blinked),len(s.state.players['A'].zones['hand']))
                self.replay(s,load=True)
