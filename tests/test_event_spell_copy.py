"""A cast trigger copies the referenced spell through current/LKI information."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_activation_public_state as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import CardInstance,StackItem
from quorune.event_spell_copy_model import EventSpellCopySource,EVENT_SPELL_COPY_CONTEXT as KEY,EVENT_SPELL_COPY_CAPABILITY as CAPABILITY,event_spell_copy_effect
from quorune.rules.event_spell_copy import capture_event_spell_copy_source,resolve_event_spell_copy_source,pin_event_spell_copy_departure,event_spell_copy_trigger_context
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


def copy_host():
    card=CardInstance(object_id='original',ref='original',oracle_id='original-oracle',printed_name='Blaze',owner='A',controller='A',zone='stack')
    original=StackItem('id:S1','S1','spell','A','Blaze',card_object_id=card.object_id,semantic_key='blaze',targets=['B'],x_value=3,context={'target_groups':{'target_0':['B']},'semantic_frame':{'instruction_pointer':99}})
    class Host:
        def __init__(self):
            self.state=SimpleNamespace(cards={card.object_id:card},stack=[original]);self.semantics=SimpleNamespace(get=lambda key:SimpleNamespace())
            self.characteristics={'name':'Blaze','type_line':'Sorcery','mana_cost':'{X}{R}','colors':['R']}
        def semantic_program_is_current_trusted(self,program):return True
        def _copyable_characteristics(self,card):return deepcopy(self.characteristics)
        def _stack_target_schema(self,item,program):return {'zones':['player'],'categories':['player'],'count':1}
    return Host(),card,original


class EventSpellCopyCompilerTests(unittest.TestCase):
    def test_event_permanent_spell_copy_requires_verified_physical_face_dependency(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        record=query_record('Whenever you cast a creature spell, copy it.')
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']=='token.creation.defined_transforming').update(
            status='blocked',blockers=['Physical spell-copy faces withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
        control=replace(query_record('Draw a card.'),type_line='Instant')
        self.assertEqual('exact',compile_oracle_card(control,capability_registry=registry).status)

    def test_spell_source_codec_rejects_malformed_physical_faces_and_orphan_orientation(self):
        from quorune.rules.event_spell_copy import decode_event_spell_copy_source
        from test_incubate_rules import DefinedTokenFaceTests
        from quorune.defined_token_faces import DefinedTransformingToken,DEFINED_TOKEN_FACES_FIELD,DEFINED_TOKEN_ACTIVE_FACE_FIELD
        host,card,item=copy_host()
        fixture=DefinedTokenFaceTests()
        faces=DefinedTransformingToken(fixture.copied_face(),fixture.copied_face('physical-back'),schema_version=2).to_dict()
        source=capture_event_spell_copy_source(host,item)
        values={**dict(source.characteristics),'type_line':'Creature',DEFINED_TOKEN_FACES_FIELD:faces,
            DEFINED_TOKEN_ACTIVE_FACE_FIELD:'physical-front'}
        valid=replace(source,characteristics=values)
        self.assertEqual(valid,decode_event_spell_copy_source(valid.to_dict()))
        for changed in ({**values,DEFINED_TOKEN_FACES_FIELD:{**faces,'schema_version':True}},
                {**values,DEFINED_TOKEN_ACTIVE_FACE_FIELD:'unknown-face'},
                {key:value for key,value in values.items() if key!=DEFINED_TOKEN_FACES_FIELD}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                decode_event_spell_copy_source(replace(source,characteristics=changed).to_dict())
    def test_closed_source_codec_preserves_copiable_choices_without_execution_progress(self):
        host,card,original=copy_host();source=capture_event_spell_copy_source(host,original)
        self.assertEqual(source,EventSpellCopySource.from_dict(source.to_dict()));self.assertEqual(3,source.item().x_value)
        self.assertNotIn('semantic_frame',source.item().context)
        for change in ({'schema_version':True},{'schema_version':2},{'logical_object_id':''},{'departed':1},{'extra':1}):
            with self.assertRaises((TypeError,ValueError)):EventSpellCopySource.from_dict({**source.to_dict(),**change})

    def test_live_then_departure_information_tracks_retargeting_without_current_card_substitution(self):
        host,card,original=copy_host();initial=capture_event_spell_copy_source(host,original)
        pending=StackItem('id:S2','S2','triggered_ability','A','Copy',context={KEY:initial.to_dict()});host.state.stack.append(pending)
        original.targets=['C'];original.context['target_groups']={'target_0':['C']}
        effect={'op':'copy_stack_item','stack':'S1','copy_source':initial.to_dict()}
        self.assertEqual(['C'],resolve_event_spell_copy_source(host,effect).item().targets)
        pin_event_spell_copy_departure(host,original);host.state.stack.remove(original);card.zone='graveyard';card.zone_change_counter+=1
        host.characteristics={'name':'Other','type_line':'Creature','mana_cost':'{1}','colors':['G']}
        saved=resolve_event_spell_copy_source(host,effect)
        self.assertEqual(['C'],saved.item().targets);self.assertEqual(3,saved.item().x_value);self.assertEqual('Sorcery',saved.characteristics['type_line']);self.assertTrue(saved.departed)

    def test_absent_unpinned_or_replaced_sources_fail_closed_and_legacy_effect_is_unchanged(self):
        host,card,original=copy_host();source=capture_event_spell_copy_source(host,original);effect={'stack':'S1','copy_source':source.to_dict()}
        host.state.stack.remove(original)
        with self.assertRaises(ValueError):resolve_event_spell_copy_source(host,effect)
        with self.assertRaises(ValueError):resolve_event_spell_copy_source(host,{'stack':'S1','copy_source':None})
        self.assertIsNone(resolve_event_spell_copy_source(host,{'op':'copy_stack_item','stack':'S1'}))
        host.state.stack.append(original);card.zone_change_counter+=1
        with self.assertRaises(ValueError):resolve_event_spell_copy_source(host,effect)

    def test_reference_capture_is_inferred_from_the_instruction_without_a_coverage_marker(self):
        from quorune.semantics import SemanticProgram
        host,card,original=copy_host()
        program=SemanticProgram(key='fixture:event-spell-copy',label='Fixture Event Spell Copy',
            effects=[event_spell_copy_effect()],event='spell.cast')
        context=event_spell_copy_trigger_context(host,program,'spell.cast',{'stack':original.ref,'logical_object_id':card.logical_object_id})
        self.assertEqual(original.ref,EventSpellCopySource.from_dict(context[KEY]).item().ref)
        with self.assertRaises(ValueError):event_spell_copy_trigger_context(host,program,'spell.cast',{'stack':original.ref,'logical_object_id':'different-incarnation'})

    def test_omitting_departure_pin_mutant_is_killed(self):
        import quorune.rules.event_spell_copy as owner
        def witness():
            host,card,original=copy_host();source=capture_event_spell_copy_source(host,original)
            host.state.stack.append(StackItem('id:S2','S2','triggered_ability','A','Copy',context={KEY:source.to_dict()}))
            owner.pin_event_spell_copy_departure(host,original);host.state.stack.remove(original)
            self.assertEqual(3,resolve_event_spell_copy_source(host,{'stack':'S1','copy_source':source.to_dict()}).item().x_value)
        witness()
        with patch.object(owner,'pin_event_spell_copy_departure',return_value=None),self.assertRaises(ValueError):witness()

    def test_departure_pin_preserves_immutable_waiting_occurrence_and_unrelated_batch_payload(self):
        from quorune.trigger_batches import PendingTriggerItem,create_pending_trigger_batch
        host,card,original=copy_host();source=capture_event_spell_copy_source(host,original)
        waiting=StackItem('id:S2','S2','triggered_ability','B','Copy',context={KEY:source.to_dict()})
        unrelated=StackItem('id:S3','S3','triggered_ability','B','Other',context={'retained':'unchanged'})
        batch=create_pending_trigger_batch(batch_id='batch:1',ref='TB1',items=[PendingTriggerItem.from_dict(i.to_dict()) for i in (waiting,unrelated)],apnap_order=['A','B','C','D'],turn_sequence=1,priority_epoch=3)
        host.state.pending_trigger_batches=[batch];original.targets=['D'];original.context['target_groups']={'target_0':['D']}
        pin_event_spell_copy_departure(host,original);host.state.stack.remove(original)
        changed=host.state.pending_trigger_batches[0];self.assertNotEqual(batch,changed);self.assertEqual(batch.batch_id,changed.batch_id)
        self.assertEqual(batch.groups[0].items[1],changed.groups[0].items[1]);self.assertFalse(EventSpellCopySource.from_dict(batch.groups[0].items[0].payload['context'][KEY]).departed)
        placed=StackItem.from_dict(changed.groups[0].items[0].to_dict());host.state.stack.append(placed)
        result=resolve_event_spell_copy_source(host,{'stack':'S1','copy_source':placed.context[KEY]});self.assertTrue(result.departed);self.assertEqual(['D'],result.item().targets)

    def test_qualified_cast_copy_forms_and_verified_withdrawal_preserve_targeted_copy(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        registry=load_default_capability_registry()
        text='Whenever you cast an instant or sorcery spell, copy that spell. You may choose new targets for the copy.'
        self.assertEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
        permanent='Whenever you cast a creature spell, copy it.'
        self.assertEqual('exact',compile_oracle_card(query_record(permanent),capability_registry=registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['event spell reference withdrawn'])
        changed=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        changed.mark_evidence_verified(validate_capability_evidence_index(index,registry=changed))
        self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=changed).status)
        control=replace(query_record('Copy target instant or sorcery spell. You may choose new targets for the copy.'),type_line='Instant')
        self.assertEqual('exact',compile_oracle_card(control,capability_registry=changed).status)

    def test_subtype_creature_cast_predicate_requires_both_type_and_one_closed_subtype(self):
        from quorune.compiler.spell_cast_predicates import fixed_spell_cast_binding_spec
        text='Whenever you cast a Pegasus, Unicorn, or Horse creature spell, copy it.'
        binding=fixed_spell_cast_binding_spec(text)
        self.assertIsNotNone(binding)
        self.assertEqual({'all':[{'field':'controller','op':'eq','value':'$source.controller'},
            {'field':'types','op':'contains_any','value':['creature']},
            {'any':[{'field':'subtypes','op':'contains_any','value':[s]} for s in ('horse','pegasus','unicorn')]}]},binding.subject.event_condition)
        self.assertEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)
        for descriptor in ('Pegasus, Unicorn, Horse','Pegasus and Unicorn','Pegasus or Pegasus','Pegasus, Unicorn, Horse, or Bird','Unrepresented','legendary'):
            self.assertIsNone(fixed_spell_cast_binding_spec('Whenever you cast a '+descriptor+' creature spell, copy it.'))


    def test_cast_type_disjunction_keeps_relations_and_rejects_nearby_grammar(self):
        from quorune.compiler.spell_cast_predicates import fixed_spell_cast_binding_spec

        registry = load_default_capability_registry()
        for relation, operator, result in (
            ("you cast", "eq", "copy that spell. You may choose new targets for the copy."),
            ("an opponent casts", "ne", "counter that spell."),
        ):
            with self.subTest(relation=relation):
                prefix = f"Whenever {relation} an artifact, instant, or sorcery spell, "
                binding = fixed_spell_cast_binding_spec(prefix + result)
                self.assertIsNotNone(binding)
                self.assertEqual(
                    {"all": [
                        {"field": "controller", "op": operator, "value": "$source.controller"},
                        {"any": [
                            {"field": "types", "op": "contains_any", "value": [kind]}
                            for kind in ("artifact", "instant", "sorcery")
                        ]},
                    ]},
                    binding.subject.event_condition,
                )
                text = prefix + result + " This ability triggers only once each turn."
                self.assertEqual("exact", compile_oracle_card(query_record(text), capability_registry=registry,
                    capability_profile="commander_review").status)
                for unsupported in (
                    text.replace("artifact, instant, or", "artifact, instant, and"),
                    text.replace("artifact, instant, or", "artifact, or instant, or"),
                    text.replace("an artifact, instant, or", "a creature, artifact, or"),
                ):
                    self.assertIsNone(fixed_spell_cast_binding_spec(unsupported))
                    self.assertNotEqual("exact", compile_oracle_card(query_record(unsupported), capability_registry=registry).status)


class EventSpellCopyNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.ActivationPublicStateNativeTests.session
    add=native.ActivationPublicStateNativeTests.add
    prepare=native.ActivationPublicStateNativeTests.prepare
    cast=native.ActivationPublicStateNativeTests.cast
    cast_for=native.ActivationPublicStateNativeTests.cast_for
    action=native.ActivationPublicStateNativeTests.action
    priority=native.ActivationPublicStateNativeTests.priority
    checkpoint=native.ActivationPublicStateNativeTests.checkpoint
    resolve=native.ActivationPublicStateNativeTests.resolve
    replay=native.ActivationPublicStateNativeTests.replay
    assert_closed=native.ActivationPublicStateNativeTests.assert_closed
    until=native.ActivationPublicStateNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'event-spell-copy.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/event-spell-copy-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Event copy',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def finish(self,session):
        for _ in range(10):
            decision=self.resolve(session)
            if decision is None:return
            if decision.kind=='trigger.order':self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='priority')
            else:self.fail('Unexpected copy decision: '+decision.kind)
        self.fail('Event copy did not finish')

    def test_actual_jin_copies_departed_spell_and_counters_only_first_opponent(self):
        session = self.session(603638201)
        source = self.add(session.engine, "Jin-Gitaxias, Progress Tyrant", zone="hand")
        creature = self.add(session.engine, "Grizzly Bears", zone="hand")
        spell = self.add(session.engine, "Blaze", zone="hand")
        repeat = self.add(session.engine, "Lightning Bolt", zone="hand", ref="jin-controller-repeat")
        opposed = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand", ref="jin-first-opponent")
        counter = self.add(session.engine, "Counterspell", seat="C", zone="hand")
        self.assert_closed(session, source)

        def triggers(game):
            return [
                item for item in game.state.stack
                if item.kind == "triggered_ability" and item.source_object_id == source.object_id
            ]

        self.prepare(session, mana={"C": 9, "U": 2, "G": 1, "R": 2})
        session.state.players["B"].mana_pool["R"] = 1
        session.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(session)
        self.cast_for(session, "A", source, reserve={"C": 4, "G": 1, "R": 2})
        self.assertEqual([], triggers(session))
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "A", creature, reserve={"C": 3, "R": 2})
        self.assertEqual([], triggers(session))
        self.assertIsNone(self.resolve(session))

        self.priority(session, "A")
        result = session.act("pilot:A", {
            "action_id": self.action(session, "A", spell)["id"],
            "targets": ["B"], "x": 3, "pay": "auto", "reserve": {"R": 1},
        })
        self.assertTrue(result.ok, result.summary)
        original = next(item for item in session.state.stack if item.card_object_id == spell.object_id)
        first_copy, = triggers(session)
        self.assertEqual(original.ref, first_copy.context["stack"])
        self.assertEqual(session.state.cards[spell.object_id].logical_object_id, first_copy.context["logical_object_id"])

        self.cast_for(session, "A", repeat, ["D"])
        self.assertEqual([first_copy.ref], [item.ref for item in triggers(session)])
        self.cast_for(session, "B", opposed, ["D"])
        opposing_item = next(item for item in session.state.stack if item.card_object_id == opposed.object_id)
        copy_trigger, counter_trigger = triggers(session)
        self.assertEqual(first_copy.ref, copy_trigger.ref)
        self.assertEqual(opposing_item.ref, counter_trigger.context["stack"])
        self.assertEqual(session.state.cards[opposed.object_id].logical_object_id, counter_trigger.context["logical_object_id"])
        self.assertEqual([], counter_trigger.targets)
        self.cast_for(session, "C", counter, [original.ref])
        self.assertEqual([copy_trigger.ref, counter_trigger.ref], [item.ref for item in triggers(session)])

        session = self.replay(session, load=True)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        for card in (spell, opposed, counter):
            self.assertEqual("graveyard", session.state.cards[card.object_id].zone)
        self.assertEqual(40, session.state.players["B"].life)
        self.assertEqual(40, session.state.players["C"].life)
        self.assertEqual(37, session.state.players["D"].life)
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        response = {"action_id": "choose", "targets": ["C"]}
        self.assertFalse(session.act("pilot:B", response).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", response)
        self.assertTrue(accepted.ok, accepted.summary)
        copied, = [item for item in session.state.stack if item.kind == "spell_copy"]
        self.assertEqual((3, ["C"]), (copied.x_value, copied.targets))
        self.assertEqual([], triggers(session))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(37, session.state.players["C"].life)
        self.assertEqual(40, session.state.players["B"].life)
        self.replay(session, load=True)

    def test_actual_jin_artifact_and_counter_limits_reset_on_next_turn(self):
        session = self.session(603638202)
        source = self.add(session.engine, "Jin-Gitaxias, Progress Tyrant")
        artifact = self.add(session.engine, "Vedalken Orrery", zone="hand")
        opposing_instant = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand", ref="jin-first-turn-bolt")
        opposing_creature = self.add(session.engine, "Grizzly Bears", seat="B", zone="hand")
        opposing_artifact = self.add(session.engine, "Mendicant Core, Guidelight", seat="B", zone="hand")
        next_instant = self.add(session.engine, "Lightning Bolt", zone="hand", ref="jin-next-turn-bolt")
        for index in range(4):
            self.add(session.engine, "Mountain", seat="B", ref=f"jin-b-mountain-{index}")
        self.add(session.engine, "Forest", seat="B", ref="jin-b-forest")
        for index in range(2):
            self.add(session.engine, "Generic Bound Plains", seat="B", ref=f"jin-b-plains-{index}")
            self.add(session.engine, "Skyline Cascade", seat="B", ref=f"jin-b-blue-land-{index}")
        self.add(session.engine, "Mountain", ref="jin-a-mountain")
        for card in (source, artifact, opposing_creature, opposing_artifact):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 4})
        session.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(session)

        self.cast_for(session, "A", artifact)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[artifact.object_id].zone)
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual("Vedalken Orrery", token.printed_name)
        self.assertEqual("A", token.controller)
        self.cast_for(session, "B", opposing_instant, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[opposing_instant.object_id].zone)
        self.assertEqual(40, session.state.players["D"].life)

        previous = session.state.turn_sequence
        self.until(session, lambda game: game.state.turn_sequence > previous and game.state.phase == "precombat_main")
        self.cast_for(session, "B", opposing_creature, reserve={"W": 1, "U": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[opposing_creature.object_id].zone)
        self.cast_for(session, "B", opposing_artifact)
        self.cast_for(session, "A", next_instant, ["D"])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        session = self.replay(session, load=True)
        decision = session.act("pilot:A", {"action_id": "choose"})
        self.assertTrue(decision.ok, decision.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[opposing_artifact.object_id].zone)
        self.assertEqual(34, session.state.players["D"].life)
        self.replay(session, load=True)

    def next_cast_registrations(self, session):
        return [row for row in session.state.delayed_triggers if row.active and "next_cast_copy" in row.condition]

    def test_next_cast_copy_closed_shapes_and_independent_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.compiler.fixed_stack_spell_copy import next_cast_copy_template
        from quorune.event_spell_copy_model import NEXT_CAST_COPY_CAPABILITY, NextCastCopySpec

        session = self.session(603638080)
        for name in ("Doublecast", "Dual Strike", "Galvanic Iteration", "Teach by Example"):
            self.assert_closed(session, self.add(session.engine, name, zone="hand"))
        card = self.db.lookup("Dual Strike")
        program, = [p for p in session.engine.semantics.programs_for_oracle(card.oracle_id)
                    if NEXT_CAST_COPY_CAPABILITY in p.capability_dependencies]
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != NEXT_CAST_COPY_CAPABILITY)
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + NEXT_CAST_COPY_CAPABILITY, result["blockers"])

        spec = NextCastCopySpec("instant_or_sorcery", 4, 2)
        self.assertEqual(spec, NextCastCopySpec.from_dict(spec.to_dict()))
        for change in ({"copies": True}, {"scope": "arbitrary"}, {"maximum_mana_value": -1}, {"schema_version": 2}):
            with self.assertRaises(ValueError):
                NextCastCopySpec.from_dict({**spec.to_dict(), **change})
        self.assertIsNone(next_cast_copy_template(
            "When you next cast a spell this turn, copy it except it isn't legendary. You may choose new targets for the copy."
        ))
        mana = replace(query_record(
            "{T}, Exile this artifact: Add {U}. When you next cast an instant or sorcery spell this turn, "
            "copy that spell. You may choose new targets for the copy."
        ), type_line="Artifact")
        self.assertNotEqual("exact", compile_oracle_card(mana, capability_registry=self.registry).status)

    def test_dual_strike_copies_only_the_first_eligible_cast_even_after_countering(self):
        session = self.session(603638081)
        strike = self.add(session.engine, "Dual Strike", zone="hand")
        expensive = self.add(session.engine, "Blaze", zone="hand", ref="expensive-blaze")
        eligible = self.add(session.engine, "Blaze", zone="hand", ref="eligible-blaze")
        creature = self.add(session.engine, "Grizzly Bears", zone="hand")
        counter = self.add(session.engine, "Counterspell", seat="B", zone="hand")
        opposing = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand", ref="opposing-bolt")
        later = self.add(session.engine, "Lightning Bolt", zone="hand", ref="later-bolt")
        self.assert_closed(session, strike)
        self.prepare(session, mana={"C": 10, "R": 5, "G": 1})
        session.state.players["B"].mana_pool.update({"R": 1, "U": 2})
        self.checkpoint(session)
        self.cast_for(session, "A", strike)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(self.next_cast_registrations(session)))
        self.cast_for(session, "B", opposing, ["D"], reserve={"U": 2})
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "A", creature, reserve={"R": 3})
        self.assertIsNone(self.resolve(session))
        for spell, x in ((expensive, 4), (eligible, 3)):
            self.priority(session, "A")
            action = self.action(session, "A", spell)
            before = authoritative_state_hash(session.state)
            rejected = session.act("pilot:A", {"action_id": action["id"], "targets": ["invalid-target"], "x": x})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            self.assertEqual(1, len(self.next_cast_registrations(session)))
            accepted = session.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "x": x, "pay": "auto",
                                               "reserve": {"R": 2 if x == 4 else 1}})
            self.assertTrue(accepted.ok, accepted.summary)
            if x == 4:
                self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.next_cast_registrations(session))
        original = next(item for item in session.state.stack if item.card_object_id == eligible.object_id)
        self.cast_for(session, "B", counter, [original.ref])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(result.ok, result.summary)
        copied = next(item for item in session.state.stack if item.kind == "spell_copy")
        self.assertEqual(3, copied.x_value)
        self.finish(session)
        self.assertEqual((36, 37, 37), tuple(session.state.players[seat].life for seat in "BCD"))
        self.cast_for(session, "A", later, ["C"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(34, session.state.players["C"].life)
        self.replay(session)

    def test_two_independent_next_cast_copy_registrations_share_trigger_order(self):
        session = self.session(603638082)
        first = self.add(session.engine, "Doublecast", zone="hand")
        second = self.add(session.engine, "Teach by Example", zone="hand")
        spell = self.add(session.engine, "Blaze", zone="hand")
        self.assert_closed(session, first)
        self.prepare(session, mana={"C": 1, "R": 5})
        self.checkpoint(session)
        for source in (first, second):
            self.cast_for(session, "A", source, reserve={"C": 1})
            self.finish(session)
        waiting = self.next_cast_registrations(session)
        self.assertEqual(2, len(waiting))
        self.assertNotEqual(waiting[0].source_logical_object_id, waiting[1].source_logical_object_id)

        self.priority(session, "A")
        offered = self.action(session, "A", spell)
        result = session.act("pilot:A", {"action_id": offered["id"], "targets": ["B"], "x": 1, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("trigger.order", session.state.pending_decision.kind)
        group = next(group for group in session.state.pending_trigger_batches[0].groups if group.controller == "A")
        refs = [item.ref for item in reversed(group.items)]
        self.assertEqual(2, len(refs))
        result = session.act("pilot:A", {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)
        for seat in "CD":
            self.assertEqual("semantic.choice", self.resolve(session).kind)
            result = session.act("pilot:A", {"action_id": "choose", "targets": [seat]})
            self.assertTrue(result.ok, result.summary)
        self.finish(session)
        self.assertEqual((39, 39, 39), tuple(session.state.players[seat].life for seat in "BCD"))
        self.replay(session, load=True)

    def test_unconsumed_next_cast_copy_expires_at_the_turn_boundary(self):
        session = self.session(603638083)
        source = self.add(session.engine, "Doublecast", zone="hand")
        spell = self.add(session.engine, "Lightning Bolt", zone="hand")
        self.add(session.engine, "Mountain", ref="later-turn-mountain")
        self.prepare(session, mana={"R": 2})
        self.checkpoint(session)
        offered = self.action(session, "A", source)
        result = session.act("pilot:A", {"action_id": offered["id"], "pay": "manual", "payment": {"R": 2}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(self.next_cast_registrations(session)))
        previous_turn = session.state.turn_sequence
        self.until(session, lambda game: game.state.turn_sequence > previous_turn and game.state.pending_decision.kind == "priority")
        self.assertEqual([], self.next_cast_registrations(session))
        self.cast_for(session, "A", spell, ["C"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(37, session.state.players["C"].life)
        self.replay(session)

    def test_chapter_three_delayed_occurrence_copies_twice_with_independent_targets(self):
        session = self.session(603638085)
        source = self.add(session.engine, "Summon: G.F. Cerberus")
        source.counters["lore"] = 2
        bastion = self.add(session.engine, "Karn's Bastion")
        spell = self.add(session.engine, "Blaze", zone="hand")
        for card in (source, bastion):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 5, "R": 1})
        self.checkpoint(session)
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in rows if row["id"].startswith("activate:" + bastion.ref + ":")
                    and "proliferate" in row["label"].casefold()]
        result = session.act("pilot:A", {"action_id": offered["id"], "pay": "manual", "payment": {"C": 4}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "objects": [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.finish(session)
        waiting, = self.next_cast_registrations(session)
        self.assertEqual(2, waiting.condition["next_cast_copy"]["copies"])
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.priority(session, "A")
        offered = self.action(session, "A", spell)
        result = session.act("pilot:A", {"action_id": offered["id"], "targets": ["B"], "x": 1, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        triggers = [item for item in session.state.stack if item.kind == "triggered_ability"]
        self.assertEqual(1, len(triggers))
        for seat in "CD":
            self.assertEqual("semantic.choice", self.resolve(session).kind)
            result = session.act("pilot:A", {"action_id": "choose", "targets": [seat]})
            self.assertTrue(result.ok, result.summary)
        self.finish(session)
        self.assertEqual((39, 39, 39), tuple(session.state.players[seat].life for seat in "BCD"))
        self.replay(session)

    def test_flamehold_registration_copies_a_permanent_after_its_source_leaves(self):
        session = self.session(603638086)
        source = self.add(session.engine, "Flamehold Grappler", zone="hand")
        creature = self.add(session.engine, "Grizzly Bears", zone="hand")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 1, "U": 1, "R": 1, "W": 1, "G": 1})
        session.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(session)
        self.cast_for(session, "A", source, reserve={"C": 1, "G": 1})
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "B", bounce, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(self.next_cast_registrations(session)))
        self.cast_for(session, "A", creature)
        self.finish(session)
        copies = [card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token]
        copied, = copies
        self.assertEqual("Grizzly Bears", copied.printed_name)
        self.assertEqual("battlefield", session.state.cards[creature.object_id].zone)
        self.replay(session, load=True)

    def test_next_cast_copy_producer_omission_is_killed(self):
        from unittest.mock import patch

        session = self.session(603638084)
        source = self.add(session.engine, "Doublecast", zone="hand")
        def witness():
            ir = compile_oracle_card(self.db.lookup(source.printed_name), capability_registry=self.registry,
                                     capability_profile="commander_review")
            self.assertEqual("exact", ir.status)
        witness()
        with patch("quorune.compiler.fixed_stack_spell_copy.next_cast_copy_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()

    def test_runtime_event_copy_dependency_is_inferred_without_declaration(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603638001);source=self.add(session.engine,'Double Vision');checked=0
        for p in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in p.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in p.capability_dependencies if c!=CAPABILITY)
            for coverage in (p.coverage,tuple(k for k in p.coverage if k!='cast-event-spell-copy')):
                changed=replace(p,coverage=coverage,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def test_actual_double_vision_untargeted_copy_draws_twice_without_casting_the_copy(self):
        session=self.session(603638010);source=self.add(session.engine,'Double Vision',zone='hand');spell=self.add(session.engine,'Divination',zone='hand')
        prowess=self.add(session.engine,'Monastery Swiftspear');self.assert_closed(session,source);self.assert_closed(session,spell)
        self.prepare(session,mana={'C':5,'R':2,'U':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':2,'U':1});self.finish(session);self.assertEqual(2,session.engine._numeric_stat(prowess.object_id,'power'))
        self.cast(session,'A',spell);hand=len(session.state.players['A'].zones['hand']);expected=[session.state.cards[oid].ref for oid in session.state.players['A'].zones['library'][-4:]]
        session=self.replay(session,load=True);self.finish(session);self.assertEqual(hand+4,len(session.state.players['A'].zones['hand']));self.assertEqual(3,session.engine._numeric_stat(prowess.object_id,'power'))
        for seat in 'BCD':
            for ref in expected:self.assertNotIn(ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_countered_x_spell_is_copied_and_retargeted_after_observer_destruction(self):
        session=self.session(603638020);source=self.add(session.engine,'Double Vision',zone='hand');spell=self.add(session.engine,'Blaze',zone='hand')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand');destroy=self.add(session.engine,'Disenchant',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':6,'R':3});session.state.players['B'].mana_pool.update({'U':2,'C':1,'W':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':3,'R':1});self.assertIsNone(self.resolve(session));life={s:session.state.players[s].life for s in 'BC'}
        self.priority(session,'A');offered=self.action(session,'A',spell)
        result=session.act('pilot:A',{'action_id':offered['id'],'x':3,'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        original=next(i for i in session.state.stack if i.card_object_id==spell.object_id)
        self.cast_for(session,'B',counter,[original.ref],reserve={'C':1,'W':1});self.cast(session,'B',destroy,[source.ref])
        session=self.replay(session,load=True);decision=self.resolve(session);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:D',{'action_id':'choose','targets':['C']}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','targets':['C']});self.assertTrue(result.ok,result.summary)
        copied=next(i for i in session.state.stack if i.kind=='spell_copy');self.assertEqual(3,copied.x_value);self.assertEqual((),copied.mana_colors_spent)
        session=self.replay(session,load=True);self.finish(session);self.assertEqual(life['B'],session.state.players['B'].life);self.assertEqual(life['C']-3,session.state.players['C'].life);self.replay(session,load=True)

    def test_actual_creature_spell_copy_uses_core_permanent_resolution_and_becomes_token(self):
        session=self.session(603638030);source=self.add(session.engine,'Thurid, Mare of Destiny',zone='hand');spell=self.add(session.engine,'Concordia Pegasus',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,spell);self.prepare(session,mana={'C':3,'W':3});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',spell);session=self.replay(session,load=True);self.finish(session)
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));self.assertEqual('token',tokens[0].object_kind);self.assertEqual(2,session.engine._numeric_stat(tokens[0].object_id,'power'));self.assertEqual(4,session.engine._numeric_stat(tokens[0].object_id,'toughness'))
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone);self.replay(session,load=True)

    def test_actual_self_cast_copy_survives_countering_the_original_and_keeps_equipment_actions(self):
        session=self.session(603638035);source=self.add(session.engine,'Brass Knuckles',zone='hand');body=self.add(session.engine,'Grizzly Bears')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand');self.assert_closed(session,source)
        self.prepare(session,mana={'C':5});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1});original=next(i for i in session.state.stack if i.card_object_id==source.object_id)
        self.cast(session,'B',counter,[original.ref]);session=self.replay(session,load=True);self.finish(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token];self.assertEqual(1,len(tokens));copy=tokens[0]
        self.assertEqual('token',copy.object_kind);self.priority(session,'A')
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:'+copy.ref+':'))
        result=session.act('pilot:A',{'action_id':offered['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.finish(session)
        self.assertEqual(body.object_id,session.state.cards[copy.object_id].attached_to);self.assertNotIn('double strike',session.engine._combat_keywords(session.state.cards[body.object_id]));self.replay(session,load=True)

    def test_actual_cast_copy_reads_current_retargeted_spell_choices(self):
        from quorune.semantic_runtime.intents import RetargetStackItemIntent
        from quorune.replacement.immutable import FrozenMap
        session=self.session(603638040);source=self.add(session.engine,'Double Vision',zone='hand');spell=self.add(session.engine,'Blaze',zone='hand')
        self.prepare(session,mana={'C':6,'R':3});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':3,'R':1});self.assertIsNone(self.resolve(session));self.priority(session,'A')
        result=session.act('pilot:A',{'action_id':self.action(session,'A',spell)['id'],'x':3,'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        original=next(i for i in session.state.stack if i.card_object_id==spell.object_id)
        # Exercise the existing typed retarget owner before the public copy action.
        # The checkpoint scopes replay to this prepared owner-interaction state.
        session.engine.retarget_stack_item_intent(RetargetStackItemIntent(actor='B',target_stack_ref=original.ref,targets=('C',),target_groups=FrozenMap({key:['C'] for key in original.context['target_groups']}),source_stack_ref='retarget-owner-boundary'))
        self.checkpoint(session);self.assertEqual('semantic.choice',self.resolve(session).kind)
        session=self.replay(session,load=True);result=session.act('pilot:A',{'action_id':'choose'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(['C'],next(i for i in session.state.stack if i.kind=='spell_copy').targets);life=session.state.players['C'].life;self.finish(session)
        self.assertEqual(life-6,session.state.players['C'].life);self.replay(session,load=True)

    def test_actual_blinked_target_retention_and_current_selection_use_distinct_incarnations(self):
        for change in (False,True):
            with self.subTest(change=change):
                session=self.session(603638050+change);source=self.add(session.engine,'Double Vision',zone='hand');growth=self.add(session.engine,'Giant Growth',zone='hand')
                target=self.add(session.engine,'Grizzly Bears',seat='B');blink=self.add(session.engine,'Ephemerate',seat='B',zone='hand')
                self.assert_closed(session,blink);self.prepare(session,mana={'C':3,'R':2,'G':1});session.state.players['B'].mana_pool['W']=1;self.checkpoint(session)
                self.cast_for(session,'A',source,reserve={'G':1});self.assertIsNone(self.resolve(session));self.cast(session,'A',growth,[target.ref])
                original=next(i for i in session.state.stack if i.card_object_id==growth.object_id);snapshot=deepcopy(original.context['target_snapshots'][target.ref])
                self.cast(session,'B',blink,[target.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertNotEqual(snapshot['zone_change_counter'],session.state.cards[target.object_id].zone_change_counter)
                session=self.replay(session,load=True);response={'action_id':'choose',**({'targets':[{'ref':target.ref}]} if change else {})}
                result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary);copied=next(i for i in session.state.stack if i.kind=='spell_copy')
                self.assertEqual(not change,snapshot==copied.context['target_snapshots'][target.ref]);self.finish(session)
                self.assertEqual(5 if change else 2,session.engine._numeric_stat(target.object_id,'power'));self.replay(session,load=True)

    def test_actual_copied_counterspell_can_choose_its_original_spell_as_new_target(self):
        session=self.session(603638060);source=self.add(session.engine,'Double Vision',zone='hand');counter=self.add(session.engine,'Counterspell',zone='hand')
        opposing=self.add(session.engine,'Divination',seat='B',zone='hand');self.add(session.engine,'Vedalken Orrery',seat='B')
        self.prepare(session,mana={'C':3,'R':2,'U':2});session.state.players['B'].mana_pool.update({'C':2,'U':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'U':2});self.assertIsNone(self.resolve(session));self.cast(session,'B',opposing)
        opposing_item=next(i for i in session.state.stack if i.card_object_id==opposing.object_id);self.cast(session,'A',counter,[opposing_item.ref])
        original=next(i for i in session.state.stack if i.card_object_id==counter.object_id);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','targets':[original.ref]});self.assertTrue(result.ok,result.summary)
        copied=next(i for i in session.state.stack if i.kind=='spell_copy');self.assertEqual([original.ref],copied.targets);self.assertNotIn(copied.ref,copied.targets)
        hand=len(session.state.players['B'].zones['hand']);session=self.replay(session,load=True);self.finish(session)
        self.assertEqual('graveyard',session.state.cards[counter.object_id].zone);self.assertEqual(hand+2,len(session.state.players['B'].zones['hand']));self.replay(session,load=True)

    def test_complete_real_permanent_copy_exceptions_bind_exact_roles(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.stack_spell_copy_model import (
            PERMANENT_SPELL_COPY_CAPABILITY, PERMANENT_SPELL_COPY_FIELD, PermanentSpellCopySpec,
            beast_or_bird_creature_cast_condition,
        )
        from test_controller_creature_keyword_absence import register_card

        for name, kind in (("Double Major", "nonlegendary"), ("Tawnos, the Toymaker", "artifact")):
            with self.subTest(name=name):
                source = self.db.lookup(name, fuzzy=False)
                result, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(result["strict_capability_ready"], result["blockers"])
                self.assertEqual("capability_closed", result["trust_basis"])
                program, = semantics.programs_for_oracle(source.oracle_id)
                spec = PermanentSpellCopySpec(kind)
                self.assertEqual(spec.target_schema, program.target_schema)
                self.assertIn(PERMANENT_SPELL_COPY_CAPABILITY, program.capability_dependencies)
                if kind == "artifact":
                    self.assertEqual("spell.cast", program.event)
                    self.assertEqual(beast_or_bird_creature_cast_condition(), program.event_condition)
                    parent, = program.effects
                    self.assertEqual(("offer_optional_effect", "$controller"), (parent["op"], parent["player"]))
                    original, = parent["effects"]
                    invalid_scope = replace(program, event_condition={"field": "types", "op": "contains_any", "value": ["creature"]})
                else:
                    self.assertEqual("resolve", program.event)
                    original, = program.effects
                    invalid_scope = replace(program, target_schema={**program.target_schema, "controller_relation": "opponent"})
                self.assertEqual(spec.effects[0], original)
                self.assertFalse(bind_semantic_program_runtime(
                    invalid_scope, capability_registry=self.registry, profile="commander_review",
                )["strict"])
                altered = {**original, PERMANENT_SPELL_COPY_FIELD: {"schema_version": True, "kind": kind}}
                wrong = replace(program, effects=[{**parent, "effects": [altered]}] if kind == "artifact" else [altered])
                self.assertFalse(bind_semantic_program_runtime(
                    wrong, capability_registry=self.registry, profile="commander_review",
                )["strict"])
        with patch("quorune.compiler.fixed_stack_spell_copy.cast_event_spell_copy_template", return_value=None):
            withheld, _ = register_card(self.db, self.registry, "Tawnos, the Toymaker")
            self.assertFalse(withheld["strict_capability_ready"], withheld)

    def test_actual_bird_decline_and_kicked_beast_keep_original_choice_and_become_one_artifact(self):
        game = self.session(7071001)
        source = self.add(game.engine, "Tawnos, the Toymaker")
        bear = self.add(game.engine, "Grizzly Bears", zone="hand")
        bird = self.add(game.engine, "Trial Wing Scout", zone="hand")
        beast = self.add(game.engine, "Baloth Gorger", zone="hand")
        doubler = self.add(game.engine, "Parallel Lives")
        game.state.config.semantic_policy = "trusted_only"
        for card in (source, beast, bird, doubler):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 3, "U": 1, "C": 8})
        self.checkpoint(game)
        self.cast(game, "A", bear)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", bird)
        decline = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decline.kind, decline.actors))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "apply"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        cast, = (row for row in actions if row["id"] == f"cast:{beast.ref}")
        result = game.act("pilot:A", {"action_id": cast["id"], "cost_option": "kicked", "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        original, = (item for item in game.state.stack if item.card_object_id == beast.object_id)
        self.assertIs(True, original.context["kicked"])
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        casts = len(game.engine._current_turn_history("spell_cast"))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(accepted.ok, accepted.summary)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertIs(True, copied.context["kicked"])
        self.assertEqual((), copied.mana_colors_spent)
        card = game.state.cards[copied.card_object_id]
        self.assertEqual("stack", card.zone)
        self.assertIn("Artifact", game.engine._spell_copy_characteristics(card)["type_line"])
        self.assertFalse(any(item.is_token for item in game.state.cards.values()))
        game = self.replay(game, load=True)
        self.finish(game)
        token, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertEqual(3, token.counters.get("+1/+1"))
        self.assertEqual(3, game.state.cards[beast.object_id].counters.get("+1/+1"))
        self.assertIn("Artifact", game.engine._copyable_characteristics(token)["type_line"])
        self.assertNotIn("Artifact", game.engine._copyable_characteristics(game.state.cards[beast.object_id])["type_line"])
        self.assertEqual(casts, len(game.engine._current_turn_history("spell_cast")))
        self.assertFalse(any(event.code == "token.create" for event in game.state.events))
        self.replay(game, load=True)

    def test_actual_tawnos_can_copy_the_retained_countered_bird_spell(self):
        game = self.session(7071002)
        source = self.add(game.engine, "Tawnos, the Toymaker")
        bird = self.add(game.engine, "Trial Wing Scout", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.assert_closed(game, counter)
        game.state.players["B"].mana_pool.update({"U": 2})
        self.prepare(game, mana={"U": 1, "C": 1})
        self.checkpoint(game)
        self.cast(game, "A", bird)
        spell, = (item for item in game.state.stack if item.card_object_id == bird.object_id)
        self.cast(game, "B", counter, [spell.ref])
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        self.assertEqual("graveyard", game.state.cards[bird.object_id].zone)
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(accepted.ok, accepted.summary)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertIsNotNone(copied.card_object_id)
        self.finish(game)
        token, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertEqual("Trial Wing Scout", game.engine._effective_card_data(token)["name"])
        self.assertIn("Artifact", game.engine._effective_card_data(token)["type_line"])
        self.assertEqual("graveyard", game.state.cards[bird.object_id].zone)
        self.replay(game, load=True)

    def test_actual_double_major_targets_own_spell_and_the_copy_can_be_countered(self):
        for counter_copy in (False, True):
            with self.subTest(counter_copy=counter_copy):
                game = self.session(7071010 + int(counter_copy))
                source = self.add(game.engine, "Double Major", zone="hand")
                legend = self.add(game.engine, "Isamaru, Hound of Konda", zone="hand")
                foreign = self.add(game.engine, "Ambush Viper", seat="B", zone="hand")
                counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
                game.state.config.semantic_policy = "trusted_only"
                for card in (source, legend, foreign, counter):
                    self.assert_closed(game, card)
                game.state.players["B"].mana_pool.update({"G": 1, "C": 1, "U": 2})
                self.prepare(game, mana={"G": 1, "U": 1, "W": 1})
                self.checkpoint(game)
                self.cast(game, "A", legend)
                self.cast_for(game, "B", foreign, reserve={"U": 2})
                self.assertEqual(2, game.state.players["B"].mana_pool["U"])
                original, = (item for item in game.state.stack if item.card_object_id == legend.object_id)
                foreign_spell, = (item for item in game.state.stack if item.card_object_id == foreign.object_id)
                self.priority(game, "A")
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action, = (row for row in actions if row["id"] == f"cast:{source.ref}")
                before = authoritative_state_hash(game.state)
                rejected = game.act("pilot:A", {"action_id": action["id"], "targets": [foreign_spell.ref], "pay": "auto"})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.cast(game, "A", source, [original.ref])
                self.until(game, lambda current: any(item.kind == "spell_copy" for item in current.state.stack))
                copy, = (item for item in game.state.stack if item.kind == "spell_copy")
                card = game.state.cards[copy.card_object_id]
                self.assertEqual("stack", card.zone)
                self.assertNotIn("Legendary", game.engine._spell_copy_characteristics(card)["type_line"])
                if counter_copy:
                    self.cast(game, "B", counter, [copy.ref])
                game = self.replay(game, load=True)
                self.finish(game)
                self.assertEqual("battlefield", game.state.cards[legend.object_id].zone)
                self.assertIn("Legendary", game.engine._copyable_characteristics(game.state.cards[legend.object_id])["type_line"])
                tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
                self.assertEqual(0 if counter_copy else 1, len(tokens))
                if tokens:
                    self.assertEqual(("A", "Isamaru, Hound of Konda"), (
                        tokens[0].controller, game.engine._effective_card_data(tokens[0])["name"],
                    ))
                    self.assertNotIn("Legendary", game.engine._copyable_characteristics(tokens[0])["type_line"])
                self.replay(game, load=True)


    def test_actual_double_major_keeps_a_face_down_creature_spells_identity_private(self):
        game = self.session(7071020)
        source = self.add(game.engine, "Double Major", zone="hand")
        hidden = self.add(game.engine, "Lumithread Field", zone="hand", ref="permanent-copy-hidden")
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.assert_closed(game, hidden)
        self.prepare(game, mana={"C": 3, "G": 1, "U": 1})
        self.checkpoint(game)
        morph = game.act("pilot:A", {"action_id": f"cast-morph:{hidden.ref}", "pay": "auto",
                                    "reserve": {"G": 1, "U": 1}})
        self.assertTrue(morph.ok, morph.summary)
        original, = (item for item in game.state.stack if item.card_object_id == hidden.object_id)
        self.cast(game, "A", source, [original.ref])
        self.until(game, lambda current: any(item.kind == "spell_copy" for item in current.state.stack))
        for seat in "BCD":
            state = json.dumps(game.packet(f"pilot:{seat}", full=True)["state"])
            self.assertNotIn("Lumithread Field", state)
            self.assertNotIn(hidden.oracle_id, state)
        game = self.replay(game, load=True)
        self.finish(game)
        token, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        values = game.engine._effective_card_data(token)
        self.assertEqual(("", "Creature", 0, [], []),
                         (values["name"], values["type_line"], values["mana_value"], values["colors"], values["keywords"]))
        self.assertFalse(token.face_down)
        self.assertTrue(game.state.cards[hidden.object_id].face_down)
        self.assertEqual((2, 2), (game.engine._numeric_stat(token.object_id, "power"),
                                  game.engine._numeric_stat(token.object_id, "toughness")))
        for seat in "BCD":
            state = json.dumps(game.packet(f"pilot:{seat}", full=True)["state"])
            self.assertNotIn("Lumithread Field", state)
            self.assertNotIn(hidden.oracle_id, state)
        self.replay(game, load=True)

    def test_verified_permanent_copy_exception_withdrawal_preserves_ordinary_cast_copies(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.stack_spell_copy_model import PERMANENT_SPELL_COPY_CAPABILITY
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_controller_creature_keyword_absence import register_card

        capability = PERMANENT_SPELL_COPY_CAPABILITY
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == capability).update(
            status="blocked", blockers=["Scoped permanent spell copy exception withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((capability,), profile="commander_review")
        self.assertIn(f"status:{capability}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ("Double Major", "Tawnos, the Toymaker"):
            rejected, _ = register_card(self.db, registry, name)
            self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, "Reflections of Littjara")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


class DirectCastSpellTokenReferenceTests(unittest.TestCase):
    def test_new_recipe_and_old_spelled_origins_have_distinct_closed_codecs(self):
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec
        effect=TokenCopyRecipeSpec("event_spell",TokenCopyExceptionSpec(remove_legendary=True),schema_version=4)
        self.assertEqual(effect,TokenCopyRecipeSpec.from_dict(effect.to_dict()))
        for change in ({"schema_version":3},{"origin":"event_object"},{"cleanup":"sacrifice_next_end_step"},
                       {"temporary_keywords":["Haste"]},{"exception":TokenCopyExceptionSpec().to_dict()}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                TokenCopyRecipeSpec.from_dict({**effect.to_dict(),**change})
        older=TokenCopyRecipeSpec("event_object").to_dict()
        self.assertEqual(older,TokenCopyRecipeSpec.from_dict(older).to_dict())

    def test_exact_spell_adapts_current_then_departure_values_without_cast_choices(self):
        from quorune.semantics import SemanticProgram
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD
        from quorune.event_spell_copy_model import CAST_SPELL_TOKEN_ORACLE_CONTEXT, cast_spell_token_condition
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec
        from quorune.token_copy_references import copy_reference_snapshot

        host,card,spell=copy_host()
        host.characteristics={
            "name":"Fixture","type_line":"Legendary Creature — Human","power":"1","toughness":"3",
        }
        recipe=TokenCopyRecipeSpec("event_spell",TokenCopyExceptionSpec(remove_legendary=True),schema_version=4)
        effect={"op":"create_token","controller":"$controller","quantity":1,"copy_spec":recipe.to_dict()}
        program = SemanticProgram(
            key="fixture:cast-token-copy", label="Fixture cast token copy",
            effects=[effect], event="spell.cast", active_zone="stack",
            event_condition=cast_spell_token_condition(),
        )
        occurrence={"stack":spell.ref,"logical_object_id":card.logical_object_id}
        captured=event_spell_copy_trigger_context(host,program,"spell.cast",occurrence)
        self.assertEqual(card.oracle_id,captured[CAST_SPELL_TOKEN_ORACLE_CONTEXT])
        self.assertNotIn(COPIABLE_ORACLE_FIELD,EventSpellCopySource.from_dict(captured[KEY]).characteristics)
        legacy=SemanticProgram(key='fixture:legacy-spell-copy',label='Fixture Legacy Spell Copy',
            effects=[event_spell_copy_effect()],event='spell.cast')
        self.assertEqual({KEY},set(event_spell_copy_trigger_context(host,legacy,"spell.cast",occurrence)))
        trigger=StackItem("id:trigger","TRIGGER","triggered_ability","A","Create a copy token",
                          source_object_id=card.object_id,context={**captured,**occurrence})
        host.state.stack.append(trigger)
        host.characteristics["power"]="4"
        live=copy_reference_snapshot(host,trigger,recipe)
        self.assertEqual(("stack",spell.ref,card.logical_object_id),
                         (live["zone"],live["ref"],live["logical_object_id"]))
        self.assertEqual({"characteristics","oracle_id","printed_name","annotations"},set(live["snapshot"]))
        self.assertEqual("4",live["snapshot"]["characteristics"]["power"])
        self.assertEqual(card.oracle_id,live["snapshot"]["oracle_id"])
        pin_event_spell_copy_departure(host,spell)
        host.state.stack.remove(spell)
        card.zone="graveyard"
        card.zone_change_counter+=1
        card.oracle_id="unrelated-later-identity"
        host.characteristics.update(power="7",type_line="Creature — Goblin")
        self.assertEqual(live,copy_reference_snapshot(host,trigger,recipe))
        self.assertNotIn("targets",live["snapshot"]["characteristics"])
        self.assertNotIn("x_value",live["snapshot"]["characteristics"])
        wrong=replace(trigger,context={**trigger.context,"stack":"different-spell"})
        with self.assertRaises(ValueError):
            copy_reference_snapshot(host,wrong,recipe)

    def test_raw_direct_copy_requires_the_new_owner_without_coverage_labels(self):
        from quorune.rules.token_creation_capability_shapes import (
            CAST_PERMANENT_TOKEN_COPY_CAPABILITY, token_definition_payload_dependencies,
        )
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec

        recipe=TokenCopyRecipeSpec("event_spell",TokenCopyExceptionSpec(remove_legendary=True),schema_version=4)
        effect={"op":"create_token","controller":"$controller","quantity":1,"copy_spec":recipe.to_dict()}
        required=token_definition_payload_dependencies(effect)
        self.assertEqual({CAST_PERMANENT_TOKEN_COPY_CAPABILITY,"token.creation.fixed_copy",CAPABILITY},set(required))
        for changed in ({"quantity":True},{"quantity":2},{"tapped":True},{"controller":"$context.player"},
                        {"copy_spec":{**effect["copy_spec"],"origin":"event_object"}}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                token_definition_payload_dependencies({**effect,**changed})

    def test_direct_token_capture_requires_the_exact_self_cast_subscription(self):
        from quorune.event_spell_copy_model import cast_spell_token_condition
        from quorune.rules.event_spell_copy import event_spell_copy_runtime_dependencies
        from quorune.rules.token_creation_capability_shapes import CAST_PERMANENT_TOKEN_COPY_CAPABILITY
        from quorune.semantics import SemanticProgram
        from quorune.token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec

        recipe=TokenCopyRecipeSpec("event_spell",TokenCopyExceptionSpec(remove_legendary=True),schema_version=4)
        effect={"op":"create_token","controller":"$controller","quantity":1,"copy_spec":recipe.to_dict()}
        host,card,spell=copy_host()
        host.characteristics.update(type_line="Creature")
        program = SemanticProgram(
            key="fixture:cast-token-subscription", label="Fixture cast token subscription",
            effects=[effect], event="spell.cast", active_zone="stack",
            event_condition=cast_spell_token_condition(),
        )
        self.assertIn(CAST_PERMANENT_TOKEN_COPY_CAPABILITY,event_spell_copy_runtime_dependencies(program))
        actual=event_spell_copy_trigger_context(host,program,"spell.cast",
            {"stack":spell.ref,"logical_object_id":card.logical_object_id})
        self.assertEqual(spell.ref,EventSpellCopySource.from_dict(actual[KEY]).item().ref)
        for earlier in ({"event":"resolve"},{"active_zone":"battlefield"},{"event_condition":None},
                        {"target_schema":{}},{"effects":[event_spell_copy_effect(),effect]},
                        {"effects":[{"op":"offer_optional_effect","effects":[effect]}]}):
            with self.subTest(earlier=earlier),self.assertRaises(ValueError):
                event_spell_copy_runtime_dependencies(replace(program, **earlier))

    def test_compiler_lowers_one_complete_self_cast_declaration_with_explicit_authority(self):
        from quorune.rules.token_creation_capability_shapes import CAST_PERMANENT_TOKEN_COPY_CAPABILITY

        printed = "When you cast this spell, create a token that's a copy of it, except the token isn't legendary."
        registry=load_default_capability_registry()
        ir=compile_oracle_card(query_record(printed),capability_registry=registry,capability_profile="commander_review")
        node, = (node for face in ir.faces for node in face.nodes
                 if node.template_id == "self-cast-permanent-token-copy-v1")
        self.assertEqual(("stack","spell.cast",None),(node.active_zone,node.event,node.target_schema))
        self.assertEqual(printed,printed[node.span.start:node.span.end])
        self.assertEqual(4,node.effects[0]["copy_spec"]["schema_version"])
        self.assertIn(CAST_PERMANENT_TOKEN_COPY_CAPABILITY,node.capability_dependencies)
        self.assertEqual(registry.closure(node.capability_dependencies,profile="commander_review").trusted,node.exact)
        for record in (replace(query_record(printed),type_line="Instant"),
                       query_record(printed.replace("When you cast this spell","Whenever you cast a creature spell")),
                       query_record(printed+" Draw a card.")):
            with self.subTest(text=record.oracle_text,kind=record.type_line):
                boundary=compile_oracle_card(record,capability_registry=registry,capability_profile="commander_review")
                self.assertFalse(any(n.template_id=="self-cast-permanent-token-copy-v1"
                                     for face in boundary.faces for n in face.nodes))

    def test_stack_token_source_requires_a_pinned_snapshot(self):
        from quorune.token_creation import TokenCreationError, create_tokens

        host = SimpleNamespace(_require_seat=lambda *_args, **_kwargs: None)
        with self.assertRaisesRegex(TokenCreationError, "Stack token-copy source requires a pinned snapshot"):
            create_tokens(host, "A", name="", quantity=1, copy_of="S1", copy_source_zone="stack")

    def test_direct_token_detects_omission_of_its_departure_receipt(self):
        self.test_exact_spell_adapts_current_then_departure_values_without_cast_choices()
        with patch(f"{__name__}.pin_event_spell_copy_departure",return_value=None):
            with self.assertRaisesRegex(ValueError,"departure information is unavailable"):
                self.test_exact_spell_adapts_current_then_departure_values_without_cast_choices()


from inherited_composition_fixture import InheritedNativeFixture


class DirectCastSpellTokenNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        data = json.loads((ROOT / "tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        data["cards"] = [card for card in data["cards"] if card["name"] == "Generic Bound Plains"]
        data["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(data), encoding="utf-8")
        db = folder / "direct-cast-token.sqlite3"
        build_fixture_database([basic, ROOT / "tests/fixtures/self-cast-permanent-token-copy-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Direct cast token", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def finish(self, session):
        EventSpellCopyNativeTests.finish(self, session)

    def cast_source(self, session, source, *, reserve=None):
        self.cast_for(session, "A", source, reserve=reserve)
        spell, trigger = session.state.stack
        self.assertEqual((source.object_id,"spell"), (spell.card_object_id, spell.kind))
        self.assertEqual((source.object_id,"triggered_ability"), (trigger.source_object_id, trigger.kind))
        context = EventSpellCopySource.from_dict(trigger.context[KEY])
        self.assertEqual(spell.ref,context.item().ref)
        self.assertEqual(session.state.cards[source.object_id].logical_object_id,context.logical_object_id)
        return spell, trigger

    def copy_tokens(self, session, source):
        return [card for card in session.state.cards.values() if card.zone=="battlefield" and card.is_token
                and card.annotations.get("copied_from")==source.object_id]

    def assert_copy(self, session, source, token):
        self.assertEqual(("A","A","token"),(token.owner,token.controller,token.object_kind))
        expected = self.db.lookup(source.printed_name)
        self.assertEqual((expected.oracle_id,expected.name),(token.oracle_id,token.printed_name))
        effective = session.engine._effective_card_data(token)
        self.assertNotIn("Legendary",effective["type_line"])
        self.assertTrue({"human","alien","shapeshifter"}.issubset(session.engine._type_parts(effective["type_line"])[1]))
        self.assertEqual((2,2),tuple(session.engine._numeric_stat(token.object_id,key) for key in ("power","toughness")))

    def test_paid_original_creates_direct_token_and_retains_restricted_mana_and_paradox(self):
        from quorune.mana_restrictions import (
            ability_mana_spend_context, mana_restriction_allows, spell_mana_spend_context,
        )

        session = self.session(707100401)
        engine = session.engine
        source = self.add(engine,"Osgood, Operation Double",zone="hand")
        self.add(engine,"Fervor")
        plunder = self.add(engine,"Memory Plunder",zone="hand")
        borrowed = self.add(engine,"Shock",seat="B",zone="graveyard",ref="direct-copy-borrowed-spell")
        artifact = self.add(engine,"Sol Ring",zone="hand")
        creature = self.add(engine,"Grizzly Bears",zone="hand")
        private = self.add(engine,"Counterspell",seat="D",zone="hand",ref="direct-copy-private")
        for card in (source, plunder, borrowed, artifact, creature):
            self.assert_closed(session,card)
        self.prepare(session,mana={"C":2,"U":6,"G":1})
        self.checkpoint(session)

        spell, _trigger = self.cast_source(session,source,reserve={"U":4,"G":1})
        self.resolve_top(session)
        only_original, = session.state.stack
        self.assertEqual((spell.ref,"spell"),(only_original.ref,only_original.kind))
        self.assertEqual("stack",session.state.cards[source.object_id].zone)
        token, = self.copy_tokens(session,source)
        self.assert_copy(session,source,token)
        self.assertIsNone(self.resolve(session))
        self.assertIn("Legendary",session.engine._effective_card_data(session.state.cards[source.object_id])["type_line"])
        self.assertEqual(1,sum(card.is_token for card in session.state.cards.values() if card.zone=="battlefield"))

        self.priority(session,"A")
        offer = self.action(session,"A",plunder)
        command={"action_id":offer["id"],"targets":[borrowed.ref],"pay":"auto","reserve":{"G":1}}
        if offer.get("cost_options"):
            command["cost_option"]=offer["cost_options"][0]["id"]
        cast=session.act("pilot:A",command)
        self.assertTrue(cast.ok,cast.summary)
        self.assertEqual("semantic.choice",self.resolve(session).kind)
        choices=session.packet("pilot:A",full=True)["decision"]["ctx"]["cast_options"]
        command={"action_id":"choose","choice":"cast","cast":{"cost_option":choices[0]["id"],"targets":["C"]}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D",command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        accepted=session.act("pilot:A",command)
        self.assertTrue(accepted.ok,accepted.summary)
        self.finish(session)
        self.assertEqual(38,session.state.players["C"].life)
        clues=[card for card in session.state.cards.values() if card.zone=="battlefield" and card.is_token
               and card.printed_name=="Clue Token"]
        self.assertEqual([("A","A"),("A","A")],[(card.owner,card.controller) for card in clues])

        token=session.state.cards[token.object_id]
        self.priority(session,"A")
        actions=lambda: session.packet("pilot:A",full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (row for row in actions() if row["id"].startswith("activate:"+token.ref+":"))
        accepted=session.act("pilot:A",{"action_id":offered["id"],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[token.object_id].tapped)
        (restriction, lot), = session.state.players["A"].stats["restricted_mana"].items()
        self.assertEqual({"C":1},lot)
        self.assertTrue(mana_restriction_allows(restriction,spell_mana_spend_context("Artifact")))
        self.assertTrue(mana_restriction_allows(restriction,ability_mana_spend_context("Artifact")))
        self.assertFalse(mana_restriction_allows(restriction,spell_mana_spend_context("Creature")))
        self.assertFalse(mana_restriction_allows(restriction,ability_mana_spend_context("Creature")))
        self.assertFalse(any(row.get("card")==creature.ref for row in actions()))
        self.cast_for(session,"A",artifact,reserve={"G":1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.state.players["A"].mana_pool.get("C",0))
        self.assertEqual(1,session.state.players["A"].mana_pool["G"])
        for seat in "ABC":
            self.assertNotIn(private.ref,json.dumps(session.packet("pilot:"+seat,full=True)))
        self.replay(session,load=True)

    def test_countered_source_uses_departure_values_and_creators_token_replacement(self):
        session = self.session(707100402)
        engine = session.engine
        source = self.add(engine,"Osgood, Operation Double",zone="hand")
        counter = self.add(engine,"Counterspell",seat="B",zone="hand")
        self.add(engine,"Parallel Lives",ref="direct-copy-own-replacement")
        self.add(engine,"Parallel Lives",seat="C",ref="direct-copy-foreign-replacement")
        self.assert_closed(session,source)
        self.assert_closed(session,counter)
        self.prepare(session,mana={"C":2,"U":2})
        session.state.players["B"].mana_pool["U"]=2
        self.checkpoint(session)
        spell, trigger = self.cast_source(session,source)
        self.cast_for(session,"B",counter,[spell.ref])
        self.resolve_top(session)
        self.assertEqual("graveyard",session.state.cards[source.object_id].zone)
        pending, = session.state.stack
        self.assertEqual(trigger.ref,pending.ref)
        receipt=EventSpellCopySource.from_dict(pending.context[KEY])
        self.assertTrue(receipt.departed)
        self.assertEqual(spell.ref,receipt.item().ref)
        self.assertFalse(self.copy_tokens(session,source))
        session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        tokens=self.copy_tokens(session,source)
        self.assertEqual(2,len(tokens))
        for token in tokens:
            self.assert_copy(session,source,token)
        self.assertEqual("graveyard",session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_genuine_cast_token_owner_and_shared_dependency_withdrawal(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.rules.token_creation_capability_shapes import CAST_PERMANENT_TOKEN_COPY_CAPABILITY
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        profile = "commander_review"
        card = self.db.lookup("Osgood, Operation Double", fuzzy=False)
        targeted_control = replace(
            query_record("Copy target instant or sorcery spell. You may choose new targets for the copy."),
            type_line="Instant",
        )
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())

        def binding(registry):
            semantics = SemanticRegistry()
            register_generated_programs(
                self.db, semantics, [card], trust_level="provisional", capability_registry=registry,
                capability_profile=profile, promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True,
            )
            program = compile_best_available_card_program(
                self.db, card, semantic_registry=semantics, capability_registry=registry, capability_profile=profile,
            )
            return bind_card_program_runtime(program, capability_registry=registry, profile=profile)

        self.assertTrue(binding(load_default_capability_registry())["strict_capability_ready"])
        for capability in (CAST_PERMANENT_TOKEN_COPY_CAPABILITY, CAPABILITY):
            with self.subTest(capability=capability):
                blocked = deepcopy(raw)
                next(row for row in blocked["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Test withdrawal of the actual owner"],
                )
                registry = CapabilityRegistry(blocked)
                index = build_index(registry_value=blocked, declaration_source=declarations, discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertFalse(binding(registry)["strict_capability_ready"])
                self.assertEqual("exact", compile_oracle_card(
                    targeted_control, capability_registry=registry, capability_profile=profile,
                ).status)

    def test_actual_token_creation_fault_rolls_back_exactly_and_replays_on_retry(self):
        from quorune import token_creation
        from quorune.errors import GameRuleError

        session=self.session(707100403)
        source=self.add(session.engine,"Osgood, Operation Double",zone="hand")
        self.prepare(session,mana={"C":2,"U":2})
        self.checkpoint(session)
        self.cast_source(session,source)
        original=token_creation.create_tokens
        committed=[]
        def fail_after_creation(*args,**kwargs):
            created=original(*args,**kwargs)
            committed.extend(created)
            raise GameRuleError("Injected direct cast token creation fault")
        with patch.object(token_creation,"create_tokens",side_effect=fail_after_creation):
            for _ in range(12):
                actor=session.pending_principals()[0]
                before=authoritative_state_hash(session.state)
                result=session.act(actor,{"action_id":"pass"})
                if not result.ok:
                    self.assertIn("Injected direct cast token creation fault",result.summary)
                    break
            else:
                self.fail("The actual token creation transaction did not reach the fault")
        self.assertEqual(1,len(committed))
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(self.copy_tokens(session,source))
        session=self.replay(session,load=True)
        self.assertTrue(session.act(actor,{"action_id":"pass"}).ok)
        self.assertIsNone(self.resolve(session))
        token, = self.copy_tokens(session,source)
        self.assert_copy(session,source,token)
        self.replay(session,load=True)
