"""A cast trigger copies the referenced spell through current/LKI information."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
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
            limit = " This ability triggers only once each turn."
            for descriptor, types in (
                ("an artifact, instant, or sorcery", ("artifact", "instant", "sorcery")),
                ("a creature, artifact, or sorcery", ("artifact", "creature", "sorcery")),
            ):
                with self.subTest(relation=relation, descriptor=descriptor):
                    trigger = f"Whenever {relation} {descriptor} spell, {result}"
                    binding = fixed_spell_cast_binding_spec(trigger)
                    self.assertIsNotNone(binding)
                    expected = {"all": [
                        {"field": "controller", "op": operator, "value": "$source.controller"},
                        {"any": [
                            {"field": "types", "op": "contains_any", "value": [kind]}
                            for kind in types
                        ]},
                    ]}
                    self.assertEqual(expected, binding.subject.event_condition)
                    compiled = compile_oracle_card(query_record(trigger + limit), capability_registry=registry,
                                                   capability_profile="commander_review")
                    self.assertEqual("exact", compiled.status, compiled.material_residuals)
                    node, = compiled.faces[0].nodes
                    self.assertEqual(expected, node.event_condition)
            for unsupported in (
                "an artifact, instant, and sorcery",
                "an artifact, or instant, or sorcery",
                "an artifact, artifact, or sorcery",
                "an artifact, instant, or unrepresented",
                "an artifact, creature, instant, or sorcery",
            ):
                with self.subTest(relation=relation, unsupported=unsupported):
                    trigger = f"Whenever {relation} {unsupported} spell, {result}{limit}"
                    self.assertIsNone(fixed_spell_cast_binding_spec(trigger))
                    compiled = compile_oracle_card(query_record(trigger), capability_registry=registry,
                                                   capability_profile="commander_review")
                    self.assertNotEqual("exact", compiled.status)
                    self.assertTrue(compiled.material_residuals)


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
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/event-spell-copy-cards.json',
            ROOT/'tests/fixtures/fixed-resolution-mana-cards.json'],path)
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

    @staticmethod
    def _storm_triggers(game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == source.object_id]

    def _storm_copy_target(self, game, target):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        self.assertEqual("copy_stack_item", game.packet("pilot:A", full=True)["decision"]["ctx"]["operation"])
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "targets": [target]}
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": ["bad-ref"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_thousand_year_storm_counts_only_the_original_casters_earlier_qualified_casts(self):
        from quorune.rules.prior_qualified_cast_copy import PRIOR_CAST_COPY_COUNT

        game = self.session(603638310)
        source = self.add(game.engine, "Thousand-Year Storm", seat="D")
        game.engine.change_control(source.object_id, "A", reason="Seed the original cast controller")
        creature = self.add(game.engine, "Grizzly Bears", zone="hand")
        early = self.add(game.engine, "Dark Ritual", zone="hand", ref="tys-first-cast")
        later = self.add(game.engine, "Dark Ritual", zone="hand", ref="tys-later-response")
        prior_targeted = self.add(game.engine, "Lightning Bolt", zone="hand", ref="tys-earlier-bolt")
        opposing = self.add(game.engine, "Lightning Bolt", zone="hand", seat="B", ref="tys-bolt-by-opponent")
        spell = self.add(game.engine, "Blaze", zone="hand")
        counter = self.add(game.engine, "Counterspell", zone="hand", seat="C")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "G": 1, "R": 2, "B": 2})
        game.state.players["B"].mana_pool["R"] = 1
        game.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(game)

        self.cast_for(game, "A", creature)
        self.assertEqual([], self._storm_triggers(game, source))
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", early)
        first, = self._storm_triggers(game, source)
        self.assertEqual(0, first.context[PRIOR_CAST_COPY_COUNT])
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "B", opposing, ["D"])
        self.assertEqual([], self._storm_triggers(game, source))
        self.assertIsNone(self.resolve(game))

        self.cast_for(game, "A", prior_targeted, ["B"])
        observed, = self._storm_triggers(game, source)
        self.assertEqual(1, observed.context[PRIOR_CAST_COPY_COUNT])
        self._storm_copy_target(game, "B")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(34, game.state.players["B"].life)
        self.assertEqual(37, game.state.players["D"].life)

        self.priority(game, "A")
        action = self.action(game, "A", spell)
        accepted = game.act("pilot:A", {"action_id": action["id"], "x": 3, "targets": ["B"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        original = next(item for item in game.state.stack if item.card_object_id == spell.object_id)
        locked, = self._storm_triggers(game, source)
        self.assertEqual(2, locked.context[PRIOR_CAST_COPY_COUNT])
        self.cast_for(game, "A", later)
        earlier, response = self._storm_triggers(game, source)
        self.assertEqual((locked.ref, 2, 3), (earlier.ref, earlier.context[PRIOR_CAST_COPY_COUNT],
                                               response.context[PRIOR_CAST_COPY_COUNT]))
        self.cast_for(game, "C", counter, [original.ref])
        game = self.replay(game, load=True)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        retained, = self._storm_triggers(game, source)
        self.assertEqual(2, retained.context[PRIOR_CAST_COPY_COUNT])
        self.assertTrue(EventSpellCopySource.from_dict(retained.context[KEY]).departed)
        game = self.replay(game, load=True)
        self._storm_copy_target(game, "C")
        game = self.replay(game, load=True)
        self._storm_copy_target(game, "D")
        copies = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual([(3, ["C"]), (3, ["D"])], [(item.x_value, item.targets) for item in copies])
        self.assertEqual([], self._storm_triggers(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((34, 37, 34), tuple(game.state.players[seat].life for seat in "BCD"))
        casts = game.engine._current_turn_history("spell_cast")
        self.assertEqual(5, sum(event.actor == "A" for event in casts))
        self.replay(game, load=True)

    def test_thousand_year_storm_compiler_requires_the_original_prior_cast_scope(self):
        from quorune.rules.prior_qualified_cast_copy import PRIOR_CAST_COPY_CAPABILITY, PRIOR_CAST_COPY_FIELD

        record = self.db.lookup("Thousand-Year Storm", fuzzy=False)
        def witness():
            ir = compile_oracle_card(record, capability_registry=self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertEqual("spell.cast", node.event)
            self.assertEqual({"schema_version": 1}, node.effects[0][PRIOR_CAST_COPY_FIELD])
            self.assertIn(PRIOR_CAST_COPY_CAPABILITY, node.capability_dependencies)
        witness()
        with patch("quorune.compiler.fixed_stack_spell_copy.cast_event_spell_copy_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        changed = replace(record, oracle_text=record.oracle_text.replace("you've cast before it", "any player has cast"))
        ir = compile_oracle_card(changed, capability_registry=self.registry)
        self.assertNotEqual("exact", ir.status)

    def _codie_source(self, session):
        source = self.add(session.engine, "Codie, Vociferous Codex", ref="codie-source")
        source.acquired_control_turn_count = -1
        self.assert_closed(session, source)
        return source

    def _codie_waiters(self, session):
        from quorune.rules.next_cast_library_access import NEXT_CAST_LIBRARY_ACCESS

        return [trigger for trigger in session.state.delayed_triggers
                if trigger.active and NEXT_CAST_LIBRARY_ACCESS in trigger.condition]

    def _codie_offers(self, session, seat="A"):
        self.priority(session, seat)
        packet = session.packet("pilot:" + seat, full=True)
        return {row["id"]: row for row in packet["decision"]["ctx"]["legal"]["actions"]}

    def _activate_codie(self, session, source):
        offers = self._codie_offers(session)
        action, = [row for key, row in offers.items() if key.startswith(f"activate:{source.ref}:")]
        self.assertTrue(action["mana_ability"])
        response = {"action_id": action["id"], "pay": "manual", "payment": {"C": 4}}
        result = session.act("pilot:A", response)
        self.assertTrue(result.ok, result.summary)

    def test_codie_pays_an_x_spell_then_scans_to_the_first_strictly_lesser_sorcery(self):
        from quorune.rules.next_cast_library_access import NEXT_CAST_LIBRARY_ACCESS

        session = self.session(603638241)
        source = self._codie_source(session)
        spell = self.add(session.engine, "Blaze", zone="hand", ref="codie-announced-x")
        blocked = self.add(session.engine, "Grizzly Bears", zone="hand", ref="codie-blocked-permanent")
        land = self.add(session.engine, "Plains", zone="hand", ref="codie-legal-land")
        candidate = self.add(session.engine, "Night's Whisper", zone="library", ref="codie-candidate")
        split = self.add(session.engine, "Wear // Tear", zone="library", ref="codie-equal-split")
        equal = self.add(session.engine, "Divination", zone="library", ref="codie-equal-sorcery")
        permanent = self.add(session.engine, "Grizzly Bears", zone="library", ref="codie-low-permanent")
        self.prepare(session, mana={"C": 4})
        self.checkpoint(session)

        options = self._codie_offers(session)
        self.assertNotIn(f"cast:{blocked.ref}", options)
        state_before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": f"cast:{blocked.ref}", "pay": "auto"}).ok)
        self.assertEqual(state_before, authoritative_state_hash(session.state))
        land_option, = [row for row in options.values() if row["action"] == "play_land" and land.ref in row["id"]]
        played = session.act("pilot:A", {"action_id": land_option["id"]})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", session.state.cards[land.object_id].zone)
        action = self.action(session, "A", spell)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual([], self._codie_waiters(session))
        self.assertEqual(0, session.state.players["A"].mana_pool.get("R", 0))
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": action["id"], "x": 2, "targets": ["invalid"], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": action["id"], "x": 2, "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        original, delayed = session.state.stack
        self.assertEqual(("spell", "triggered_ability"), (original.kind, delayed.kind))
        self.assertEqual((original.ref, "A", 3.0), (
            delayed.context[NEXT_CAST_LIBRARY_ACCESS]["spell_ref"],
            delayed.context[NEXT_CAST_LIBRARY_ACCESS]["caster"],
            delayed.context[NEXT_CAST_LIBRARY_ACCESS]["exclusive_mana_value"],
        ))
        self.assertEqual([], self._codie_waiters(session))
        session = self.replay(session, load=True)
        self.until(session, lambda game: game.state.cards[candidate.object_id].zone == "exile")
        self.assertEqual([original.ref], [item.ref for item in session.state.stack])
        self.assertNotIn(f"cast:{candidate.ref}", self._codie_offers(session))
        self.assertEqual({split.object_id, equal.object_id, permanent.object_id},
                         set(session.state.players["A"].zones["library"][:3]))
        public = json.dumps(session.packet("spectator", full=True))
        for card in (candidate, split, equal, permanent):
            self.assertIn(card.ref, public)
        self.finish(session)
        option = self._codie_offers(session)[f"cast:{candidate.ref}"]
        self.assertEqual({"without_mana_cost"}, {row["id"] for row in option["cost_options"]})
        mana = dict(session.state.players["A"].mana_pool)
        result = session.act("pilot:A", {
            "action_id": option["id"], "cost_option": "without_mana_cost", "pay": "manual", "payment": {},
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(mana, dict(session.state.players["A"].mana_pool))
        self.finish(session)
        self.assertEqual("graveyard", session.state.cards[candidate.object_id].zone)
        self.assertEqual([], self._codie_waiters(session))
        self.replay(session)

    def test_codie_copied_delayed_ability_keeps_its_sealed_cast_and_original_on_stack(self):
        from quorune.rules.next_cast_library_access import NEXT_CAST_LIBRARY_ACCESS

        session = self.session(603638270)
        source = self._codie_source(session)
        resonator = self.add(session.engine, "Strionic Resonator", ref="codie-resonator")
        spell = self.add(session.engine, "Blaze", zone="hand", ref="codie-copy-cast")
        counter = self.add(session.engine, "Counterspell", seat="B", zone="hand", ref="codie-counter")
        second = self.add(session.engine, "Village Rites", zone="library", ref="codie-copy-second")
        first = self.add(session.engine, "Lightning Bolt", zone="library", ref="codie-copy-first")
        self.assert_closed(session, resonator)
        self.prepare(session, mana={"C": 6})
        session.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(session)
        self._activate_codie(session, source)
        offer = self.action(session, "A", spell)
        cast = session.act("pilot:A", {
            "action_id": offer["id"], "x": 1, "targets": ["B"], "pay": "manual",
            "payment": {"R": 1, "U": 1},
        })
        self.assertTrue(cast.ok, cast.summary)
        original, = [item for item in session.state.stack if NEXT_CAST_LIBRARY_ACCESS in item.context]
        action, = [row for key, row in self._codie_offers(session).items()
                   if key.startswith(f"activate:{resonator.ref}:")]
        copied = session.act("pilot:A", {
            "action_id": action["id"], "targets": [original.ref], "pay": "manual", "payment": {"C": 2},
        })
        self.assertTrue(copied.ok, copied.summary)
        self.until(session, lambda game: sum(NEXT_CAST_LIBRARY_ACCESS in item.context for item in game.state.stack) == 2)
        first_instance, second_instance = [item for item in session.state.stack if NEXT_CAST_LIBRARY_ACCESS in item.context]
        self.assertEqual(original.ref, first_instance.ref)
        self.assertNotEqual(original.ref, second_instance.ref)
        self.assertEqual(first_instance.context[NEXT_CAST_LIBRARY_ACCESS], second_instance.context[NEXT_CAST_LIBRARY_ACCESS])
        self.assertEqual(2.0, second_instance.context[NEXT_CAST_LIBRARY_ACCESS]["exclusive_mana_value"])
        session = self.replay(session, load=True)
        self.cast_for(session, "B", counter, [original.context[NEXT_CAST_LIBRARY_ACCESS]["spell_ref"]])
        self.until(session, lambda game: game.state.cards[spell.object_id].zone == "graveyard")
        self.assertIn(original.ref, [item.ref for item in session.state.stack])
        self.assertIn(second_instance.ref, [item.ref for item in session.state.stack])
        self.until(session, lambda game: game.state.cards[first.object_id].zone == "exile")
        self.assertIn(original.ref, [item.ref for item in session.state.stack])
        self.assertEqual("library", session.state.cards[second.object_id].zone)
        first_grant = session.state.cards[first.object_id].annotations["temporary_play_permission"]
        self.assertEqual(second_instance.ref, first_grant["source"])
        self.finish(session)
        second_grant = session.state.cards[second.object_id].annotations["temporary_play_permission"]
        self.assertEqual(original.ref, second_grant["source"])
        self.replay(session)

    def test_codie_next_cast_survives_spell_copy_and_source_departure_then_returns_no_match(self):
        session = self.session(603638242)
        source = self._codie_source(session)
        copy_source = self.add(session.engine, "Double Vision")
        setup_spell = self.add(session.engine, "Divination", zone="hand", ref="codie-before-activation")
        next_spell = self.add(session.engine, "Blaze", zone="hand", ref="codie-after-departure")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        self.assert_closed(session, copy_source)
        library = session.state.players["A"].zones["library"]
        for object_id in tuple(library[:-6]):
            session.engine.move_card(object_id, "outside", log=False)
        self.prepare(session, mana={"C": 6, "U": 1})
        session.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(session)

        self.cast_for(session, "A", setup_spell, reserve={"C": 4})
        pending_refs = [item.ref for item in session.state.stack]
        self._activate_codie(session, source)
        self.assertEqual(pending_refs, [item.ref for item in session.state.stack])
        pool = {color: amount for color, amount in session.state.players["A"].mana_pool.items() if amount}
        self.assertEqual({color: 1 for color in "WUBRG"}, pool)
        self.assertEqual(1, len(self._codie_waiters(session)))
        self.assertFalse(any(row["action"] == "undo_mana" for row in self._codie_offers(session).values()))
        checkpoint = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"a": "undo_mana", "source": source.ref}).ok)
        self.assertEqual(checkpoint, authoritative_state_hash(session.state))
        self.assertEqual(1, len(self._codie_waiters(session)))
        self.until(session, lambda game: any(item.kind == "spell_copy" for item in game.state.stack))
        self.assertEqual(1, len(self._codie_waiters(session)))
        self.cast_for(session, "B", bounce, [source.ref])
        self.until(session, lambda game: game.state.cards[source.object_id].zone == "hand")
        self.assertEqual(1, len(self._codie_waiters(session)))
        session = self.replay(session, load=True)
        self.finish(session)
        unqualified = set(session.state.players["A"].zones["library"])
        self.assertEqual(2, len(unqualified))
        option = self.action(session, "A", next_spell)
        result = session.act("pilot:A", {"action_id": option["id"], "x": 0, "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([], self._codie_waiters(session))
        self.finish(session)
        self.assertEqual(unqualified, set(session.state.players["A"].zones["library"]))
        self.assertEqual([], session.state.players["A"].zones["exile"])
        self.replay(session)

    def test_codie_free_cast_keeps_the_additional_cost_and_unused_grant_expires(self):
        for use_grant in (False, True):
            with self.subTest(use_grant=use_grant):
                session = self.session(603638243 + use_grant)
                source = self._codie_source(session)
                creature = self.add(session.engine, "Grizzly Bears", ref="codie-sacrifice-cost")
                spell = self.add(session.engine, "Blaze", zone="hand", ref="codie-cost-trigger")
                candidate = self.add(session.engine, "Village Rites", zone="library", ref="codie-rites")
                self.prepare(session, mana={"C": 4})
                self.checkpoint(session)
                offer = self.action(session, "A", spell)
                result = session.act("pilot:A", {"action_id": offer["id"], "x": 1, "targets": ["B"], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.until(session, lambda game: game.state.cards[candidate.object_id].zone == "exile")
                offer = self._codie_offers(session)[f"cast:{candidate.ref}"]
                self.assertEqual({"without_mana_cost"}, {row["id"] for row in offer["cost_options"]})
                response = {"action_id": offer["id"], "cost_option": "without_mana_cost", "pay": "manual", "payment": {}}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", response).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                if use_grant:
                    session = self.replay(session, load=True)
                    result = session.act("pilot:A", {**response, "sacrifice_cards": [creature.ref]})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("graveyard", session.state.cards[creature.object_id].zone)
                    self.finish(session)
                else:
                    self.finish(session)
                    turn = session.state.turn_sequence
                    self.until(session, lambda game: game.state.turn_sequence > turn)
                    self.assertNotIn(f"cast:{candidate.ref}", self._codie_offers(session))
                    self.assertEqual("exile", session.state.cards[candidate.object_id].zone)
                self.assertIsNone(session.engine._temporary_play_permission("A", session.state.cards[candidate.object_id]))
                self.replay(session)

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


class TurnCreatureCastDrawContractTests(unittest.TestCase):
    def delayed_snapshot(self):
        from quorune.turn_creature_cast_draw_model import TURN_CREATURE_CAST_DRAW_FIELD, TURN_CREATURE_CAST_DRAW_LABEL

        return {
            "trigger_id": "witness-delay", "ref": "DT1", "controller": "A",
            "label": TURN_CREATURE_CAST_DRAW_LABEL, "source_object_id": None, "event_kind": "spell.cast",
            "condition": {TURN_CREATURE_CAST_DRAW_FIELD: 1}, "stack_template": {}, "once": False,
            "created_turn_sequence": 4, "expires_turn_sequence": 4,
        }

    def test_repeated_draw_snapshot_rejects_crossed_and_open_delayed_shapes(self):
        from quorune.event_spell_copy_model import NEXT_CAST_COPY_FIELD, NextCastCopySpec
        from quorune.model import DelayedTrigger
        from quorune.turn_creature_cast_draw_model import TURN_CREATURE_CAST_DRAW_FIELD, validate_turn_creature_cast_draw_effect
        from quorune.turn_creature_cast_draw_model import turn_creature_cast_draw_effect

        payload = self.delayed_snapshot()
        trigger = DelayedTrigger.from_dict(payload)
        self.assertEqual(trigger, DelayedTrigger.from_dict(trigger.to_dict()))
        for change in (
            {"once": True}, {"expires_turn_sequence": 5}, {"event_kind": "spell.copy"},
            {"stack_template": {"context": {"dynamic_effects": [{"op": "draw", "player": "B"}]}}},
            {"source_object_id": "unrelated"},
            {"condition": {TURN_CREATURE_CAST_DRAW_FIELD: True}},
            {"condition": {TURN_CREATURE_CAST_DRAW_FIELD: 1, NEXT_CAST_COPY_FIELD: NextCastCopySpec("any").to_dict()}},
        ):
            with self.assertRaises(ValueError):
                DelayedTrigger.from_dict({**payload, **change})
        effect = turn_creature_cast_draw_effect()
        validate_turn_creature_cast_draw_effect(effect)
        for change in (
            {"once": True}, {"controller": "$opponent"}, {TURN_CREATURE_CAST_DRAW_FIELD: True},
            {NEXT_CAST_COPY_FIELD: NextCastCopySpec("any").to_dict()},
        ):
            with self.assertRaises(ValueError):
                validate_turn_creature_cast_draw_effect({**effect, **change})

    def test_repeated_draw_reads_sealed_cast_or_rejects_stale_and_other_occurrences(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.delayed_triggers import turn_creature_cast_draw_occurrence_context
        from quorune.model import DelayedTrigger
        from quorune.rules.spell_cast_events import SpellCastEvent
        from quorune.trigger_processing import TriggerProcessingOwner

        trigger = DelayedTrigger.from_dict(self.delayed_snapshot())
        state = SimpleNamespace(turn_sequence=4, delayed_triggers=[trigger])
        owner = TriggerProcessingOwner(SimpleNamespace(state=state))
        cast = SpellCastEvent("creature", "source", "source@1", "A", "hand", "S1", ("creature",),
                              schema_version=1)
        context = cast.to_context()
        self.assertEqual([trigger], owner.matching_delayed_triggers("spell.cast", context))
        self.assertEqual([trigger], owner.matching_delayed_triggers("spell.cast", context))
        self.assertEqual([], owner.matching_delayed_triggers("spell.copy", context))
        self.assertEqual([], owner.matching_delayed_triggers("spell.cast", replace(cast, controller="B").to_context()))
        self.assertEqual([], owner.matching_delayed_triggers("spell.cast", replace(cast, types=("instant",)).to_context()))
        self.assertEqual({"dynamic_effects": [{"op": "draw", "player": "A", "count": 1, "private": True}]},
                         turn_creature_cast_draw_occurrence_context(state, trigger, context))
        state.turn_sequence += 1
        with self.assertRaises(ValueError):
            turn_creature_cast_draw_occurrence_context(state, trigger, context)
        self.assertEqual([], owner.matching_delayed_triggers("spell.cast", context))
        self.assertFalse(trigger.active)


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


ARCHAIC_FRONT = "Wandering Archaic"
VASTLANDS_BACK = "Explore the Vastlands"
ARCHAIC = f"{ARCHAIC_FRONT} // {VASTLANDS_BACK}"


class ArchaicAndVastlandsNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "event-spell-copy-cards.json": {ARCHAIC, "Counterspell", "Grizzly Bears", "Isamaru, Hound of Konda",
                                            "Lightning Bolt", "Mountain"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
        }
        files = []
        for filename, names in support.items():
            doc = json.loads((ROOT / "tests" / "fixtures" / filename).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, filename
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / filename
            part.write_text(json.dumps(doc), encoding="utf-8")
            files.append(part)
        db = folder / "archaic-vastlands.sqlite3"
        build_fixture_database(files, db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Archaic and Vastlands", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def okay(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def cast_face(self, game, source, name, *, payment=None):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"cast:{source.ref}" if name == ARCHAIC_FRONT else f"cast:{source.ref}:face:{name}"
        self.assertIn(action_id, {row["id"] for row in actions})
        cost = {"pay": "auto"} if payment is None else {"pay": "manual", "payment": payment}
        self.okay(game, "A", {"action_id": action_id, "face": name, **cost})

    def scene(self, seed, *, counter_original=False, payer_lands=False):
        game = self.session(seed)
        source = self.add(game.engine, ARCHAIC, zone="hand", ref="archaic-front-or-back")
        spell = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="archaic-instant-original")
        counter = self.add(game.engine, "Counterspell", zone="hand", ref="archaic-outside-counter") if counter_original else None
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="archaic-hidden-d")
        lands = [self.add(game.engine, "Mountain", seat="B", ref=f"archaic-payer-land-{i}") for i in range(2)] if payer_lands else []
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "U": 2} if counter_original else {"C": 5})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_face(game, source, ARCHAIC_FRONT, payment={"C": 5})
        self.assertIsNone(self.resolve(game))
        resident = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", ARCHAIC_FRONT), (resident.zone, resident.active_face))
        self.cast_for(game, "B", spell, ["A"])
        return game, source, spell, counter, private, lands

    def payment_offer(self, game, *, payable):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (decision.kind, decision.actors))
        context = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual({"GENERIC": 2}, {key: value for key, value in context["cost"].items() if value})
        action, = (row for row in decision.payload_by_actor["B"]["legal_actions"] if row["id"] == "choose")
        self.assertEqual([True, False] if payable else [False], list(action["choice_schema"]["legal_values"]))

    def test_actual_opposing_caster_pays_through_existing_payment_and_denies_copy(self):
        game, source, _spell, _counter, private, lands = self.scene(70710111, payer_lands=True)
        self.assertEqual(1, self.trigger_count(game, source))
        self.payment_offer(game, payable=True)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.reject(game, "A", {"action_id": "choose", "pay": True})
        game = self.replay(game, load=True)
        self.okay(game, "B", {"action_id": "choose", "pay": True})
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self.assertEqual(["spell"], [item.kind for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_declined_copy_uses_departed_spell_and_controller_retargets(self):
        game, source, spell, counter, private, _lands = self.scene(70710112, counter_original=True, payer_lands=True)
        original, trigger = game.state.stack
        self.assertEqual((["A"], "B"), (original.targets, original.controller))
        self.assertEqual([], trigger.targets)
        self.cast_for(game, "A", counter, [original.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertTrue(game.state.stack[-1].context[KEY]["departed"])
        self.payment_offer(game, payable=True)
        self.reject(game, "A", {"action_id": "choose", "pay": True})
        self.okay(game, "B", {"action_id": "choose", "pay": False})
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"]
        self.assertEqual(["apply", "decline"], actions[0]["choice_schema"]["legal_values"])
        command = {"action_id": "choose", "choice": "apply"}
        self.reject(game, "B", command)
        game = self.replay(game, load=True)
        self.okay(game, "A", command)
        self.reject(game, "C", {"action_id": "choose", "targets": ["C"]})
        self.okay(game, "A", {"action_id": "choose", "targets": ["C"]})
        copy, = game.state.stack
        self.assertEqual(("spell_copy", "A", ["C"]), (copy.kind, copy.controller, copy.targets))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 37), (game.state.players["A"].life, game.state.players["C"].life))
        self.replay(game, load=True)

    def library(self, game, seat, names, *, leave_lower=True):
        if not leave_lower:
            for object_id in tuple(game.state.players[seat].zones["library"]):
                game.engine.move_card(object_id, "exile", reason="fixture short library", log=False, semantic_events=False)
        return [self.add(game.engine, name, seat=seat, zone="library", ref=f"vastlands-{seat}-{index}")
                for index, name in enumerate(names)]

    def role(self, game, seat, legal):
        decision = game.state.pending_decision
        self.assertEqual(("choice.apnap", [seat]), (decision.kind, decision.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        schema = context["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 1, True), (schema["minimum"], schema["maximum"], schema["optional"]))
        self.assertEqual(set(legal), set(schema["legal_refs"]))
        return context

    def test_actual_vastlands_reveals_in_apnap_and_commits_private_partitions(self):
        from quorune.rules import library_selection

        game = self.session(10140111)
        source = self.add(game.engine, ARCHAIC, zone="hand", ref="vastlands-resolving-source")
        self.assert_closed(game, source)
        a = self.library(game, "A", ["Grizzly Bears", "Generic Bound Plains", "Generic Bound Growth", "Mountain", "Grizzly Bears"])
        b = self.library(game, "B", ["Lightning Bolt", "Counterspell", "Grizzly Bears"], leave_lower=False)
        c = self.library(game, "C", ["Grizzly Bears", "Generic Bound Plains", "Grizzly Bears"], leave_lower=False)
        self.library(game, "D", [], leave_lower=False)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.cast_face(game, source, VASTLANDS_BACK)
        self.assertEqual(VASTLANDS_BACK, game.state.cards[source.object_id].active_face)
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        ctx = self.role(game, "A", [a[1].ref, a[3].ref])
        self.assertEqual(5, len(ctx["looked"]))
        for other in "BCD":
            self.assertNotIn(a[1].ref, json.dumps(game.packet("pilot:" + other, full=True)))
        self.reject(game, "B", {"action_id": "choose", "cards": [a[1].ref]})
        self.reject(game, "A", {"action_id": "choose", "cards": [a[1].ref, a[3].ref]})
        self.reject(game, "A", {"action_id": "choose", "cards": [a[2].ref]})
        self.okay(game, "A", {"action_id": "choose", "cards": [a[1].ref]})
        self.role(game, "A", [a[2].ref])
        game = self.replay(game, load=True)
        self.okay(game, "A", {"action_id": "choose", "cards": [a[2].ref]})

        ctx = self.role(game, "B", [b[0].ref, b[1].ref])
        self.assertEqual(3, len(ctx["looked"]))
        self.assertEqual({a[1].ref, a[2].ref}, {row["ref"] for row in ctx["previous_reveals"][0]["cards"]})
        self.assertNotIn(a[3].ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertNotIn(b[1].ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in (a[1], a[2])))
        self.assertTrue(all("B" not in game.state.cards[card.object_id].known_to for card in (a[1], a[2])))
        self.okay(game, "B", {"action_id": "choose", "cards": [b[0].ref]})
        ctx = self.role(game, "C", [c[1].ref])
        self.assertEqual(["A", "B"], [row["player"] for row in ctx["previous_reveals"]])
        self.assertTrue(all(game.state.players[seat].life == 40 for seat in "ABCD"))
        game = self.replay(game, load=True)
        with patch.object(library_selection, "_shuffle_bottom_selection", wraps=library_selection._shuffle_bottom_selection) as randomize:
            self.okay(game, "C", {"action_id": "choose", "cards": []})
        self.assertEqual({"A": 3, "B": 2, "C": 3, "D": 0},
                         {call.args[1]: len(call.args[3]) for call in randomize.call_args_list})
        self.assertFalse(game.state.stack)
        self.assertTrue(all(game.state.players[seat].life == 43 for seat in "ABCD"))
        for seat, selected, remainder in (("A", [a[1], a[2]], [a[0], a[3], a[4]]), ("B", [b[0]], b[1:]), ("C", [], c)):
            self.assertTrue(all(card.object_id in game.state.players[seat].zones["hand"] for card in selected))
            self.assertTrue(all(set(game.state.cards[card.object_id].known_to) == set("ABCD") for card in selected))
            self.assertCountEqual([card.object_id for card in remainder], game.state.players[seat].zones["library"][:len(remainder)])
            self.assertTrue(all(not game.state.cards[card.object_id].revealed_to
                                and not game.state.cards[card.object_id].known_to for card in remainder))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_both_faces_exact_and_checked_roles_fail_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.apnap_library_reveals import CAPABILITY as REVEAL_CAPABILITY, library_reveal_round_effects
        from quorune.rules.opponent_cast_copy_payment import OPPONENT_CAST_COPY_PAYMENT_CAPABILITY
        from test_granted_skulk import record

        card = self.db.lookup(ARCHAIC, fuzzy=False)
        oracle = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", oracle.status, oracle.material_residuals)
        front, back = (face.nodes[0] for face in oracle.faces)
        self.assertIn(OPPONENT_CAST_COPY_PAYMENT_CAPABILITY, front.capability_dependencies)
        self.assertIn(REVEAL_CAPABILITY, back.capability_dependencies)
        self.assertEqual(library_reveal_round_effects(), tuple(back.effects))
        for face, bad, kind in ((card.faces[0], ("an opponent", "you"), "Creature"),
                                (card.faces[0], ("you may copy", "copy"), "Creature"),
                                (card.faces[1], ("a land card and/or", "a land card and"), "Sorcery"),
                                (card.faces[1], ("a random order", "any order"), "Sorcery")):
            altered = compile_oracle_card(record(face["oracle_text"].replace(*bad), kind), capability_registry=self.registry)
            caps = {cap for row in altered.faces for node in row.nodes for cap in node.capability_dependencies}
            self.assertTrue({REVEAL_CAPABILITY, OPPONENT_CAST_COPY_PAYMENT_CAPABILITY}.isdisjoint(caps))
        game = self.session(10140112)
        source = self.add(game.engine, ARCHAIC, zone="hand")
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id)
        for cap in (REVEAL_CAPABILITY, OPPONENT_CAST_COPY_PAYMENT_CAPABILITY):
            program, = (entry for entry in programs if cap in entry.capability_dependencies)
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            mutated = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            binding = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", binding["blockers"])


class MagecraftRummageOrdinalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/"magecraft-ordinal.sqlite3"
        fixtures=ROOT/"tests/fixtures"
        build_fixture_database([fixtures/name for name in (
            "bound-effect-program-cards.json","event-spell-copy-cards.json",
            "ability-source-counter-cards.json","earthbend-cards.json",
        )],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition("Magecraft ordinal",[
            DeckEntry("Isamaru, Hound of Konda",1,"commander"),DeckEntry("Generic Bound Plains",30),
        ],["Isamaru, Hound of Konda"])

    def _ashling(self,seed):
        game=self.session(seed)
        source=self.add(game.engine,"Ashling, Flame Dancer")
        self.assert_closed(game,source)
        return game,source

    def _rummage(self,game):
        self.assertEqual("choice.apnap",self.resolve(game).kind)
        self.assertEqual(["pilot:A"],game.pending_principals())
        price=next(game.state.cards[oid] for oid in game.state.players["A"].zones["hand"]
                   if game.state.cards[oid].printed_name=="Generic Bound Plains")
        hand=len(game.state.players["A"].zones["hand"])
        result=game.act("pilot:A",{"action_id":"choose","cards":[price.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(hand,len(game.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard",game.state.cards[price.object_id].zone)

    def test_ashling_counts_actual_copies_and_retains_red_through_the_step(self):
        game,source=self._ashling(603639101)
        bolt=self.add(game.engine,"Lightning Bolt",zone="hand")
        twin=self.add(game.engine,"Twincast",zone="hand")
        enemy=self.add(game.engine,"Grizzly Bears",seat="B")
        friend=self.add(game.engine,"Grizzly Bears",ref="ashling-friendly")
        self.prepare(game,mana={"R":1,"U":2,"G":1})
        self.checkpoint(game)
        self.cast(game,"A",bolt,["D"])
        self._rummage(game)
        original,=[item for item in game.state.stack if item.card_object_id==bolt.object_id]
        self.assertEqual("battlefield",game.state.cards[enemy.object_id].zone)
        self.cast(game,"A",twin,[original.ref])
        game=self.replay(game,load=True)
        self._rummage(game)
        self.assertEqual("graveyard",game.state.cards[enemy.object_id].zone)
        self.assertEqual("battlefield",game.state.cards[friend.object_id].zone)
        self.assertEqual([38,38,38],[game.state.players[seat].life for seat in "BCD"])
        self.assertEqual("semantic.choice",self.resolve(game).kind)
        result=game.act("pilot:A",{"action_id":"choose","targets":["D"]})
        self.assertTrue(result.ok,result.summary)
        self.assertTrue(any(item.kind=="spell_copy" for item in game.state.stack))
        game=self.replay(game,load=True)
        self._rummage(game)
        self.assertEqual(4,game.state.players["A"].mana_pool["R"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40,38,38,32],[game.state.players[seat].life for seat in "ABCD"])
        self.until(game,lambda current:current.state.step=="end_step")
        self.assertEqual(4,game.state.players["A"].mana_pool["R"])
        self.assertEqual(0,game.state.players["A"].mana_pool["G"])
        game=self.replay(game,load=True)
        self.assertEqual(4,game.state.players["A"].mana_pool["R"])

    def test_ashling_second_result_uses_source_lki_and_retention_ends_on_departure(self):
        game,source=self._ashling(603639102)
        first=self.add(game.engine,"Lightning Bolt",zone="hand",ref="ashling-bolt-one")
        second=self.add(game.engine,"Lightning Bolt",zone="hand",ref="ashling-bolt-two")
        doom=self.add(game.engine,"Doom Blade",seat="B",zone="hand")
        self.prepare(game,mana={"R":3})
        game.state.players["B"].mana_pool.update({"C":1,"B":1})
        self.checkpoint(game)
        self.cast(game,"A",first,["D"])
        self._rummage(game)
        self.assertIsNone(self.resolve(game))
        self.cast(game,"A",second,["D"])
        self.cast(game,"B",doom,[source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard",game.state.cards[source.object_id].zone)
        game=self.replay(game,load=True)
        self._rummage(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40,38,38,32],[game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(1,game.state.players["A"].mana_pool["R"])
        self.until(game,lambda current:current.state.step=="end_step")
        self.assertEqual(0,game.state.players["A"].mana_pool["R"])
        self.replay(game,load=True)

    def test_ashling_retained_red_keeps_spend_restrictions_without_reviving_expired_duration(self):
        from quorune.mana_provenance import ManaProvenanceLot, _store_lots, clear_step_mana, mana_provenance_lots

        original=ManaProvenanceLot((("R",2),("G",1)),restriction="creature_spell",retention="end_of_turn")
        player=SimpleNamespace(mana_pool={"G":1,"R":2},stats={})
        _store_lots(player,(original,))
        self.assertEqual({"G":1},clear_step_mana(player,phase="ending",step="cleanup",preserve_colors=("R",)))
        lot,=mana_provenance_lots(player)
        self.assertEqual(((("R",2),),"creature_spell",None),(lot.bundle,lot.restriction,lot.retention))
        self.assertEqual({"R":2},clear_step_mana(player,phase="precombat_main",step="main"))

    def test_ashling_requires_exact_static_and_ordered_magecraft_shapes(self):
        card=self.db.lookup("Ashling, Flame Dancer",fuzzy=False)
        ir=compile_oracle_card(card,capability_registry=self.registry)
        self.assertEqual("exact",ir.status,ir.material_residuals)
        for old,new in (("second time","fourth time"),("each creature they control","each planeswalker they control"),
                        ("unspent red mana","unspent blue mana")):
            changed=replace(card,oracle_text=card.oracle_text.replace(old,new))
            self.assertNotEqual("exact",compile_oracle_card(changed,capability_registry=self.registry).status)
