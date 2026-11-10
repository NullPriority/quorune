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
        host,card,original=copy_host();program=SimpleNamespace(effects=(event_spell_copy_effect(),),coverage=(),event='spell.cast')
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
