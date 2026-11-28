"""CR110.2/611.2a/613.1b: resolving actor and control recipient differ."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch
from common import ROOT
import test_fixed_creature_power_damage as records
import test_target_recipient_results as recipient_fixtures
import optional_result_followup_fixture as turn_fixtures
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.directional_control import CAPABILITY as DIRECTIONAL_CONTROL_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from quorune.continuous_effect_state import ResolutionEffectSource
from quorune.semantic_runtime.context import ReadOnlyHandlerContext,SemanticSourceContext
from test_controller_creature_keyword_absence import register_card


AMINATOU = "Aminatou, the Fateshifter"


class AminatouLoyaltyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "aminatou.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "recipient-control-cards.json",
            fixtures / "nontoken-target-cards.json",
            fixtures / "source-attack-alone-cards.json",
            fixtures / "ability-source-counter-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Ordered loyalty effects", [
            DeckEntry("Sliver Hivelord", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Sliver Hivelord"])

    def _activate(self, game, source, number, targets=(), *, valid=True):
        self.priority(game, "A")
        command = {"action_id": f"activate:{source.ref}:ab{number}", "targets": list(targets), "pay": "auto"}
        self._act(game, valid=valid, **command)

    def _act(self, game, actor="A", *, valid=True, **command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def _choose_direction(self, game, direction):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]
        values = tuple(decision["legal_actions"][0]["choice_schema"]["legal_values"])
        self.assertEqual(("left", "right"), values)
        self._act(game, "B", valid=False, action_id="choose", choice=direction)
        self._act(game, valid=False, action_id="choose", choice="across")
        game = self.replay(game, load=True)
        self._act(game, action_id="choose", choice=direction)
        return game

    def _board(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, AMINATOU)
        source.counters["loyalty"] = 7
        creatures = {seat: self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"rotating-{seat}")
                     for seat in "ABCD"}
        lands = {seat: self.add(game.engine, "Generic Bound Plains", seat=seat, ref=f"land-{seat}")
                 for seat in "ABCD"}
        self.assert_closed(game, source)
        return game, source, creatures, lands

    def _concede(self, game, actor):
        self.priority(game, actor)
        self._act(game, actor, action_id="concede", choices={"confirm_concede": True})

    def test_original_binding_closes_both_loyalty_bodies_and_rejects_wrong_scope(self):
        original = self.db.lookup(AMINATOU, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, AMINATOU)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        blink, = [program for program in programs if program.cost_schema and program.cost_schema.get("loyalty_delta") == -1]
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                          "source_exclusion": True, "owner_relation": "you"}, blink.target_schema)
        rotate, = [program for program in programs if DIRECTIONAL_CONTROL_CAPABILITY in program.capability_dependencies]
        self.assertEqual(-6, rotate.cost_schema["loyalty_delta"])
        self.assertIsNone(rotate.target_schema)
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        for node in (node for face in ir.faces for node in face.nodes):
            self.assertEqual(node.text, original.oracle_text[node.span.start:node.span.end])
        mutant = replace(rotate, effects=({"op": "choose_option", "options": ["left"], "player": "$controller"},))
        self.assertFalse(bind_semantic_program_runtime(
            mutant, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.directional_control.directional_control_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_plus_one_draws_then_privately_puts_the_drawn_card_back(self):
        game = self.session(60631010)
        source = self.add(game.engine, AMINATOU)
        source.counters["loyalty"] = 3
        top = self.add(game.engine, "Grizzly Bears", zone="library", ref="aminatou-private-draw")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self._activate(game, source, 1)
        self.assertEqual(4, game.state.cards[source.object_id].counters["loyalty"])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("hand", game.state.cards[top.object_id].zone)
        self.assertIn(top.ref, json.dumps(game.packet("pilot:A", full=True)["decision"]))
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        self._act(game, "B", valid=False, action_id="pass")
        game = self.replay(game, load=True)
        self._act(game, action_id="choose", cards=[top.ref])
        self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_minus_one_exiles_a_borrowed_owned_permanent_and_returns_a_new_object(self):
        game = self.session(60631011)
        source = self.add(game.engine, AMINATOU)
        source.counters["loyalty"] = 3
        owned = self.add(game.engine, "Grizzly Bears", ref="owned-by-A")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="owned-by-B")
        game.engine.change_control(owned.object_id, "B", reason="Borrowed owned creature")
        game.engine.change_control(other.object_id, "A", reason="Borrowed opposing creature")
        owned.counters["charge"] = 1
        original = owned.logical_object_id
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self._activate(game, source, 2, [source.ref], valid=False)
        self._activate(game, source, 2, [other.ref], valid=False)
        self._activate(game, source, 2, [owned.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        returned = game.state.cards[owned.object_id]
        self.assertEqual(("battlefield", "A", "A", {}), (returned.zone, returned.owner, returned.controller, returned.counters))
        self.assertNotEqual(original, returned.logical_object_id)
        self.assertEqual(("B", "A"), (game.state.cards[other.object_id].owner, game.state.cards[other.object_id].controller))
        self.replay(game, load=True)

    def test_both_directions_commit_one_snapshot_and_departed_recipient_restores_control(self):
        for direction, receivers in (("left", "DABC"), ("right", "BCDA")):
            with self.subTest(direction=direction):
                game, source, creatures, lands = self._board(60631012 + len(direction))
                loan = self.add(game.engine, "Grizzly Bears", ref="loan-from-A-to-B")
                game.engine.change_control(loan.object_id, "B", reason="Borrowed before directional control")
                token_ref, = game.engine.create_token(
                    "C", name="Rotation token", characteristics={"type_line": "Token Artifact — Treasure"},
                )
                token = game.engine._resolve_object("C", token_ref, zones={"battlefield"})
                self.prepare(game)
                self.checkpoint(game)
                self._activate(game, source, 3)
                game = self._choose_direction(game, direction)
                self.assertEqual(tuple(receivers), tuple(game.state.cards[creatures[seat].object_id].controller for seat in "ABCD"))
                self.assertEqual(tuple("ABCD"), tuple(game.state.cards[lands[seat].object_id].controller for seat in "ABCD"))
                self.assertEqual(("A", receivers[1]), (game.state.cards[loan.object_id].owner,
                                                       game.state.cards[loan.object_id].controller))
                self.assertEqual(receivers[2], game.state.cards[token.object_id].controller)
                self.assertEqual("A", game.state.cards[source.object_id].controller)
                entries = [event for event in game.state.continuous_effects if event.source_id == source.object_id]
                self.assertEqual(1, len({event.timestamp for event in entries}))
                self.assertEqual(tuple(game.engine.apnap_order()), tuple(event.operations[0].value for event in entries))
                self._concede(game, "B")
                donor = next(seat for seat, receiver in zip("ABCD", receivers, strict=True) if receiver == "B")
                self.assertEqual(donor, game.state.cards[creatures[donor].object_id].controller)
                self.assertNotEqual("battlefield", game.state.cards[creatures["B"].object_id].zone)
                survivor, controller = ("D", "C") if direction == "left" else ("C", "D")
                self._concede(game, "A")
                remaining = game.state.cards[creatures[survivor].object_id]
                self.assertEqual(("battlefield", controller), (remaining.zone, remaining.controller))
                self.replay(game, load=True)

    def test_blinked_source_is_rotated_and_the_live_ring_skips_a_pre_resolution_departure(self):
        game, source, creatures, lands = self._board(60631018)
        spell = self.add(game.engine, "Flicker", zone="hand")
        flash = self.add(game.engine, "Vedalken Orrery")
        for card in (spell, flash):
            self.assert_closed(game, card)
        original = source.logical_object_id
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self._activate(game, source, 3)
        self._concede(game, "B")
        self.cast_for(game, "A", spell, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        game = self._choose_direction(game, "left")
        self.assertEqual("D", game.state.cards[source.object_id].controller)
        self.assertEqual(("D", "A", "C"), tuple(game.state.cards[creatures[seat].object_id].controller for seat in "ACD"))
        self.assertEqual(("A", "C", "D"), tuple(game.state.cards[lands[seat].object_id].controller for seat in "ACD"))
        self.replay(game, load=True)

    def test_ultimate_survives_its_own_source_dying_at_zero_loyalty(self):
        game, source, creatures, _lands = self._board(60631019)
        source.counters["loyalty"] = 6
        self.prepare(game)
        self.checkpoint(game)
        self._activate(game, source, 3)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self._choose_direction(game, "right")
        self.assertEqual(tuple("BCDA"), tuple(game.state.cards[creatures[seat].object_id].controller for seat in "ABCD"))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)


class RecipientControlModelTests(unittest.TestCase):
    def context(self):
        return ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='resolved gift',
            seats=tuple('ABCD'),active_seats=tuple('ABCD'),apnap_order=tuple('ABCD'),
            source=SemanticSourceContext(stack_ref='stack',object_id='source',logical_object_id='source@1',card_ref='source-ref'))

    def test_new_recipient_intent_keeps_the_old_acquiring_actor_contract_strict(self):
        from quorune.semantic_runtime.control_intents import GainControlIntent,GiveControlIntent
        from quorune.continuous_effect_model import ContinuousEffectDuration
        source=ResolutionEffectSource('stack','source','source@1','source-ref')
        intent=GiveControlIntent('A','gift','C',source,'resolved gift')
        self.assertEqual(('A','C',ContinuousEffectDuration.ZONE_OBJECT),(intent.actor,intent.controller,intent.duration))
        with self.assertRaises(FrozenInstanceError):intent.controller='B'
        with self.assertRaises(ValueError):GainControlIntent('A','gift','C',ContinuousEffectDuration.ZONE_OBJECT,source,'resolved gift')

    def test_registered_recipient_handler_is_closed_and_null_target_is_a_noop(self):
        from quorune.semantic_runtime.control_handlers import RecipientControlHandler
        effect={'op':'give_control','schema_version':1,'card':'gift','controller':'C','source':'source-ref'}
        plan=RecipientControlHandler().lower(effect,self.context())
        self.assertEqual(('A','C','gift'),(plan.intents[0].actor,plan.intents[0].controller,plan.intents[0].object_ref))
        for change in ({'schema_version':True},{'controller':'Z'},{'extra':1},{'duration':'until_end_of_turn'}):
            with self.subTest(change=change),self.assertRaises(ValueError):RecipientControlHandler().lower({**effect,**change},self.context())
        for change in ({'card':None},{'controller':None}):
            self.assertEqual((),RecipientControlHandler().lower({**effect,**change},self.context()).intents)

    def test_untargeted_choice_is_controller_owned_live_opponent_scoped_and_incarnation_bound(self):
        from quorune.semantic_choices.control_recipient import ControlRecipientChoiceHandler
        from quorune.replacement.immutable import FrozenMap
        holder={'logical':'gift@1'}
        query=SimpleNamespace(seats=tuple('ABCD'),active_seats=tuple('ACD'),
            object=lambda ref,**kw:SimpleNamespace(logical_object_id=holder['logical']))
        effect={'op':'choose_control_recipient','schema_version':1,'player':'A','card':'gift','opponent_only':True}
        context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='stack',query=query)
        handler=ControlRecipientChoiceHandler();prepared=handler.prepare(effect,context)
        self.assertEqual({'field':'recipient','legal_values':['C','D']},prepared.request.choice.choice_schema())
        continuation=SimpleNamespace(effect=prepared.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))
        completed=handler.complete(continuation,{'recipient':'C'},query)
        self.assertEqual(('gift','C'),tuple(completed.prepend_effects[0][k] for k in ('card','controller')))
        for response in ({'recipient':'A'},{'recipient':'B'},{'recipient':'Z'},{'recipient':'C','card':'other'}):
            with self.subTest(response=response),self.assertRaises(ValueError):handler.complete(continuation,response,query)
        continuation.semantic_frame.controller='B'
        with self.assertRaises(ValueError):handler.complete(continuation,{'recipient':'C'},query)
        continuation.semantic_frame.controller='A';holder['logical']='gift@2'
        with self.assertRaises(ValueError):handler.complete(continuation,{'recipient':'C'},query)
        holder['logical']='gift@1'
        for change in ({'schema_version':True},{'opponent_only':1},{'unknown':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):handler.prepare({**effect,**change},context)


class RecipientControlCompilerTests(unittest.TestCase):
    def compile(self,text,*,permanent=False):
        return compile_oracle_card(records.card_record(text,type_line='Creature — Human' if permanent else 'Sorcery'),
            capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_targeted_player_and_permanent_roles_have_distinct_owned_slots(self):
        ir=self.compile('Target opponent gains control of target permanent you control.')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual(('give_control','$target.0','$target.1'),tuple(node.effects[0][k] for k in ('op','controller','card')))
        self.assertEqual(['recipient','permanent'],[g['id'] for g in node.target_schema['groups']])
        self.assertIn('continuous.control.fixed_recipient',node.capability_dependencies)

    def test_source_anaphora_is_bound_only_for_a_source_specific_trigger(self):
        source=self.compile('When this creature enters, target opponent gains control of it.',permanent=True)
        self.assertEqual('exact',source.status,source.material_residuals)
        self.assertEqual('$source.zone_object',source.faces[0].nodes[0].effects[0]['card'])
        other=self.compile('Whenever another creature enters, target opponent gains control of it.',permanent=True)
        self.assertNotEqual('exact',other.status)
        self.assertNotEqual('exact',self.compile('When you cast this spell, target opponent gains control of it.',permanent=True).status)
        choice=self.compile('When this creature enters, an opponent gains control of it.',permanent=True)
        self.assertEqual('exact',choice.status,choice.material_residuals)
        self.assertIsNone(choice.faces[0].nodes[0].target_schema)
        self.assertEqual('choose_control_recipient',choice.faces[0].nodes[0].effects[0]['op'])

    def test_malformed_recipient_subject_and_version_roles_have_no_capability_closure(self):
        from copy import deepcopy
        from quorune.rules.capabilities import capability_dependencies_for_node
        node=self.compile('Target opponent gains control of target permanent you control.').faces[0].nodes[0]
        for change in ({'controller':'$controller'},{'controller':'$target.1'},{'card':'$target.0'},
                {'schema_version':True},{'source':'$target.1'},{'unknown':1}):
            with self.subTest(change=change):
                self.assertEqual((),capability_dependencies_for_node(effects=({**node.effects[0],**change},),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        schema=deepcopy(node.target_schema);schema['groups'][0]['count']=True
        self.assertEqual((),capability_dependencies_for_node(effects=node.effects,target_schema=schema,mechanic_ids=node.mechanics))

    def test_verified_recipient_withdrawal_rejects_gifts_but_keeps_classic_acquisition(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='continuous.control.fixed_recipient').update(status='blocked',blockers=['Withheld recipient contract'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(records.card_record('Target player gains control of target permanent you control.'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(records.card_record('Gain control of target creature.'),capability_registry=registry).status)

    def test_unsupported_untargeted_mana_side_effect_does_not_gain_normal_activation_admission(self):
        self.assertNotEqual('exact',self.compile('{T}: Add {B}{B}{B}{B}. An opponent gains control of this creature.',permanent=True).status)

    def test_removal_followup_binds_the_target_controller_without_a_second_player_target(self):
        ir=self.compile('{T}: Destroy target artifact or creature. That permanent\'s controller gains control of this creature.',permanent=True)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual(1,node.target_schema['count'])
        self.assertEqual(2,node.effects[1]['schema_version'])
        self.assertEqual({'kind':'target_controller_information','schema_version':1,'index':0},node.effects[1]['controller'])
        self.assertIn('resolution.target_controller_information',node.capability_dependencies)
        for text in ('Destroy target creature. Its owner gains control of this creature.',
                'Destroy up to one target creature. Its controller gains control of this creature.',
                'Destroy target creature. That land\'s controller gains control of this creature.',
                'You may exile target artifact. If you do, its controller gains control of this creature.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text,permanent=True).status)

    def test_target_controller_control_payloads_reject_changed_roles_and_keep_v1_strict(self):
        from quorune.recipient_control_model import validate_recipient_control_effect
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.rules.recipient_control_shapes import recipient_control_runtime_dependencies
        node=self.compile('{T}: Destroy target creature. Its controller gains control of this creature.',permanent=True).faces[0].nodes[0]
        for change in ({'schema_version':1},{'schema_version':True},{'controller':'$controller'},
                {'controller':{'kind':'target_controller_information','schema_version':1,'index':1}},
                {'controller':{'kind':'target_owner_information','schema_version':1,'index':0}},{'card':'$target.0'}):
            with self.subTest(change=change):
                effects=(node.effects[0],{**node.effects[1],**change})
                self.assertEqual((),capability_dependencies_for_node(effects=effects,target_schema=node.target_schema,mechanic_ids=node.mechanics))
                with self.assertRaises(ValueError):recipient_control_runtime_dependencies(SimpleNamespace(effects=effects,target_schema=node.target_schema))
        with self.assertRaises(ValueError):validate_recipient_control_effect({**node.effects[1],'schema_version':1})
        resolved={**node.effects[1],'controller':'C'}
        with self.assertRaises(ValueError):validate_recipient_control_effect(resolved)
        validate_recipient_control_effect(resolved,resolved=True)


class RecipientControlNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipient_fixtures.TargetRecipientNativeTests.reject
    finish=recipient_fixtures.TargetRecipientNativeTests.finish
    finish_turn=turn_fixtures.OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        v=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        v['cards']=[r for r in v['cards'] if r['name']=='Generic Bound Plains'];v['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(v),encoding='utf-8')
        path=folder/'recipient.sqlite3';build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/recipient-control-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Control recipient roles',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,zone='hand',before=None):
        s=self.session(2021020+len(name));source=self.add(s.engine,name,zone=zone)
        gift=self.add(s.engine,'Grizzly Bears',ref='foreign-owned-gift');gift.owner='D'
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='gift-private-C')
        self.add(s.engine,'Fervor')
        if before:before(s,source,gift)
        s.state.config.semantic_policy='trusted_only'
        for card in (source,gift):self.assert_closed(s,card)
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,gift,private

    def activate(self,s,source,targets,**extra):
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':') and not r.get('mana_ability'))
        command={'action_id':row['id'],'targets':targets,'pay':'auto',**extra}
        self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def yes_man_scene(self, *, quest=0, owner="A"):
        extras = {}
        def arrange(session, source, _gift):
            source.owner = owner
            if quest:
                source.counters.update({"quest": quest, "wish": 4})
            extras["removal"] = self.add(session.engine, "Murder", zone="hand", ref="yes-man-removal")

        session, source, _gift, private = self.scene("Yes Man, Personal Securitron", zone="battlefield", before=arrange)
        return session, source, extras["removal"], private

    def test_yes_man_actual_control_precedes_private_reflexive_draw_and_owner_tokens(self):
        session, source, removal, private = self.yes_man_scene(quest=1, owner="D")
        hand = set(session.state.players["A"].zones["hand"])
        self.activate(session, source, ["C"])
        parent = session.state.stack[-1].ref
        self.resolve_top(session)
        source = session.state.cards[source.object_id]
        self.assertEqual(("C", "D", 1), (source.controller, source.owner, source.counters["quest"]))
        self.assertEqual(hand, set(session.state.players["A"].zones["hand"]))
        child, = session.state.stack
        self.assertEqual(("A", "triggered_ability", parent), (child.controller, child.kind, child.context["reflexive_parent"]))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        source = session.state.cards[source.object_id]
        self.assertEqual(2, source.counters["quest"])
        drawn = set(session.state.players["A"].zones["hand"]) - hand
        self.assertEqual(2, len(drawn))
        for card_id in drawn:
            for viewer in "BCD":
                self.assertNotIn(session.state.cards[card_id].ref, json.dumps(session.packet("pilot:" + viewer, full=True)))

        self.cast(session, "A", removal, [source.ref])
        self.resolve_top(session)
        leave, = session.state.stack
        self.assertEqual(("C", "D"), (leave.controller, leave.context["owner"]))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        tokens = [c for c in session.state.cards.values() if c.zone == "battlefield" and c.is_token]
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(c.owner == c.controller == "D" and c.tapped for c in tokens))
        for token in tokens:
            data = session.engine._effective_card_data(token)
            self.assertEqual(("1", "1"), (str(data["power"]), str(data["toughness"])))
            self.assertIn("Soldier", data["type_line"])
            self.assertEqual(["W"], list(data["colors"]))
        self.finish(session, private)

    def test_yes_man_original_missing_source_or_departed_target_creates_no_reflexive(self):
        for departure in ("source", "target"):
            with self.subTest(departure=departure):
                session, source, removal, _private = self.yes_man_scene()
                self.activate(session, source, ["C"])
                original = session.state.stack[-1].ref
                if departure == "source":
                    self.cast(session, "A", removal, [source.ref])
                else:
                    self.priority(session, "C")
                    result = session.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                hand = list(session.state.players["A"].zones["hand"])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(hand, session.state.players["A"].zones["hand"])
                self.assertFalse(any(item.context.get("reflexive_parent") == original for item in session.state.stack))
                self.assertFalse(any(card.is_token for card in session.state.cards.values()))
                if departure == "source":
                    self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                else:
                    self.assertEqual("A", session.state.cards[source.object_id].controller)
                self.replay(session)

    def test_yes_man_reflexive_draw_survives_a_later_departure_without_counter(self):
        session, source, removal, _private = self.yes_man_scene(quest=1)
        self.activate(session, source, ["C"])
        self.resolve_top(session)
        source = session.state.cards[source.object_id]
        hand_count = len(session.state.players["A"].zones["hand"])
        self.cast(session, "A", removal, [source.ref])
        self.assertIsNone(self.resolve(session))
        source = session.state.cards[source.object_id]
        self.assertEqual("graveyard", source.zone)
        self.assertNotIn("quest", source.counters)
        tokens = [c for c in session.state.cards.values() if c.zone == "battlefield" and c.is_token]
        self.assertEqual(1, len(tokens))
        self.assertEqual(("A", True), (tokens[0].controller, tokens[0].tapped))
        self.assertEqual(hand_count + 1, len(session.state.players["A"].zones["hand"]))
        self.replay(session)

    def test_yes_man_recipient_departure_restores_custody_and_owner_departure_removes_object(self):
        session, source, _removal, _private = self.yes_man_scene(owner="D")
        self.activate(session, source, ["C"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("C", session.state.cards[source.object_id].controller)
        for leaving in ("C", "D"):
            self.priority(session, leaving)
            result = session.act("pilot:" + leaving, {"action_id": "concede", "choices": {"confirm_concede": True}})
            self.assertTrue(result.ok, result.summary)
            current = session.state.cards.get(source.object_id)
            if leaving == "C":
                self.assertEqual(("A", "D"), (current.controller, current.owner))
            else:
                self.assertTrue(current is None or current.zone != "battlefield")
        self.assertFalse(any(c.is_token for c in session.state.cards.values()))
        self.replay(session)

    def test_yes_man_incomplete_source_roles_restore_residuals(self):
        from unittest.mock import patch
        from quorune.rules.owner_departure_tokens import owner_departure_token_node_capabilities
        from quorune.rules.source_control_reflexive import (
            source_control_reflexive_dependencies, source_control_reflexive_effect, source_control_reflexive_target,
        )

        card = self.db.lookup("Yes Man, Personal Securitron")
        for owner in ("recipient_control_templates._source_control_reflexive",
                      "source_self_effect_templates._owner_departure_counter_token_result"):
            with self.subTest(owner=owner), patch("quorune.compiler." + owner, return_value=None):
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("partial", ir.status, ir.material_residuals)
        effect = source_control_reflexive_effect()
        with self.assertRaises(ValueError):
            source_control_reflexive_dependencies({**effect, "card": "$target.0"}, source_control_reflexive_target())
        with self.assertRaises(ValueError):
            source_control_reflexive_dependencies(effect, {**source_control_reflexive_target(), "count": True})
        from quorune.rules.owner_departure_tokens import owner_departure_token_effect
        self.assertEqual((), owner_departure_token_node_capabilities(
            ({**owner_departure_token_effect(), "controller": "$controller"},), None,
        ))

    def spell_gifts(self,names):
        for name in names:
            s,spell,gift,private=self.scene(name)
            row=self.action(s,'A',spell)
            self.reject(s,'A',{'action_id':row['id'],'targets':[gift.ref,'C'],'pay':'auto'})
            self.cast(s,'A',spell,['C',gift.ref]);s=self.replay(s,load=True)
            self.assertIsNone(self.resolve(s))
            self.assertEqual(('C','D','battlefield'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner,s.state.cards[gift.object_id].zone))
            self.finish(s,private)

    def test_donate_offering_and_wrong_turn_separate_actor_recipient_owner_and_subject(self):
        self.spell_gifts(('Donate','Harmless Offering','Wrong Turn'))

    def test_bazaar_trader_targets_only_its_controlled_permanent_union(self):
        extra={}
        def before(s,source,gift):extra['enchantment']=self.add(s.engine,'Hardened Scales')
        s,source,gift,private=self.scene('Bazaar Trader',zone='battlefield',before=before)
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        self.reject(s,'A',{'action_id':row['id'],'targets':['C',extra['enchantment'].ref],'pay':'auto'})
        self.activate(s,source,['C',gift.ref]);self.assertTrue(s.state.cards[source.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','D'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner))
        self.assertEqual('A',s.state.cards[source.object_id].controller);self.finish(s,private)

    def test_humble_defector_draws_for_the_resolver_before_giving_its_original_incarnation(self):
        s,source,gift,private=self.scene('Humble Defector',zone='battlefield')
        hand=set(s.state.players['A'].zones['hand'])
        self.activate(s,source,['C']);self.assertTrue(s.state.cards[source.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        drawn=set(s.state.players['A'].zones['hand'])-hand;self.assertEqual(2,len(drawn))
        for oid in drawn:
            for viewer in 'BCD':self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.finish(s,private)

    def test_sleeper_entry_target_is_chosen_before_priority_and_uses_the_new_controller(self):
        s,source,gift,private=self.scene('Sleeper Agent')
        self.cast(s,'A',source);self.assertEqual('semantic.target',self.resolve(s).kind)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.target(s,'A',['C']);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        self.finish(s,private)

    def test_horse_uses_an_untargeted_resolution_choice_with_saved_controller_authority(self):
        s,source,gift,private=self.scene('Akroan Horse')
        self.cast(s,'A',source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        for recipient in ('A','Z'):
            self.reject(s,'A',{'action_id':'choose','recipient':recipient})
        self.reject(s,'B',{'action_id':'choose','recipient':'C'})
        s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','recipient':'C'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        self.finish(s,private)

    def test_jinxed_artifact_costs_are_paid_by_the_actor_before_source_control_changes(self):
        for name in ('Jinxed Ring','Jinxed Idol'):
            with self.subTest(name=name):
                s,source,gift,private=self.scene(name,zone='battlefield')
                self.activate(s,source,['C'],cost_cards=[gift.ref])
                self.assertEqual('graveyard',s.state.cards[gift.object_id].zone)
                self.assertIn(gift.object_id,s.state.players['D'].zones['graveyard'])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
                self.assertEqual((40,40,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s,private)

    def test_witch_engine_is_not_a_mana_ability_and_adds_for_the_original_actor_before_gifting(self):
        s,source,gift,private=self.scene('Witch Engine',zone='battlefield')
        before={p:s.state.players[p].mana_pool.get('B',0) for p in 'AC'}
        self.activate(s,source,['C'])
        self.assertTrue(s.state.stack)
        self.assertEqual(before['A'],s.state.players['A'].mana_pool.get('B',0))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(before['A']+4,s.state.players['A'].mana_pool.get('B',0))
        self.assertEqual(before['C'],s.state.players['C'].mana_pool.get('B',0))
        self.assertEqual('C',s.state.cards[source.object_id].controller);self.finish(s,private)

    def test_wishclaw_searches_and_shuffles_for_the_actor_before_choosing_a_control_recipient(self):
        s,source,gift,private=self.scene('Wishclaw Talisman')
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[source.object_id].counters['wish'])
        selected=s.state.cards[s.state.players['A'].zones['library'][-1]].ref
        shuffles=s.state.players['A'].stats.get('shuffle_count',0)
        self.activate(s,source,[])
        self.assertEqual(2,s.state.cards[source.object_id].counters['wish'])
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual('semantic.search',self.resolve(s).kind)
        command={'action_id':'choose','search_cards':[selected]}
        self.reject(s,'C',command);s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual('A',s.state.cards[source.object_id].controller)
        self.assertEqual(shuffles+1,s.state.players['A'].stats.get('shuffle_count',0))
        for viewer in 'BCD':self.assertNotIn(selected,json.dumps(s.packet('pilot:'+viewer,full=True)))
        result=s.act('pilot:A',{'action_id':'choose','recipient':'C'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('C',2),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].counters['wish']))
        self.finish(s,private)

    def test_avarice_amulet_targets_a_recipient_after_its_attached_creature_dies(self):
        extra={}
        def before(s,source,gift):extra['kill']=self.add(s.engine,'Murder',zone='hand')
        s,source,gift,private=self.scene('Avarice Amulet',before=before)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.activate(s,source,[gift.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(gift.object_id,s.state.cards[source.object_id].attached_to)
        self.cast(s,'A',extra['kill'],[gift.ref])
        self.assertEqual('semantic.target',self.resolve(s).kind)
        self.assertEqual('graveyard',s.state.cards[gift.object_id].zone)
        s=self.replay(s,load=True)
        self.target(s,'A',['C'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A',None),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner,s.state.cards[source.object_id].attached_to))
        self.finish(s,private)

    def test_new_controller_upkeeps_apply_sleeper_loss_and_horse_opponent_token_roles(self):
        for name in ('Sleeper Agent','Akroan Horse'):
            with self.subTest(name=name):
                s,source,gift,private=self.scene(name)
                self.cast(s,'A',source);decision=self.resolve(s)
                if name=='Sleeper Agent':self.target(s,'A',['C'])
                else:
                    result=s.act('pilot:A',{'action_id':'choose','recipient':'C'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                self.finish_turn(s);self.finish_turn(s)
                self.until(s,lambda s:s.state.active_player=='C' and s.state.step=='upkeep' and bool(s.state.stack))
                self.assertIsNone(self.resolve(s))
                self.assertEqual('C',s.state.cards[source.object_id].controller)
                if name=='Sleeper Agent':
                    self.assertEqual((40,40,38,40),tuple(s.state.players[p].life for p in 'ABCD'))
                else:
                    tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
                    self.assertEqual(['A','B','D'],sorted(c.controller for c in tokens))
                    for token in tokens:
                        self.assertEqual(token.controller,token.owner)
                        self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
                self.finish(s,private)

    def test_giver_departure_does_not_end_the_gift_but_recipient_departure_restores_base_controller(self):
        for leaving,expected in (('A','C'),('C','A')):
            with self.subTest(leaving=leaving):
                s,source,gift,private=self.scene('Donate')
                self.cast(s,'A',source,['C',gift.ref]);self.assertIsNone(self.resolve(s))
                self.priority(s,leaving)
                result=s.act('pilot:'+leaving,{'action_id':'concede','choices':{'confirm_concede':True}})
                self.assertTrue(result.ok,result.summary)
                self.assertEqual((expected,'D','battlefield'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner,s.state.cards[gift.object_id].zone))
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.replay(s,load=True)

    def test_humble_draws_but_does_not_give_a_returned_source_and_illegal_player_stops_all_results(self):
        for invalidate in ('source','recipient'):
            with self.subTest(invalidate=invalidate):
                extra={}
                def before(s,source,gift):extra['blink']=self.add(s.engine,'Cloudshift',zone='hand')
                s,source,gift,private=self.scene('Humble Defector',zone='battlefield',before=before)
                hand=set(s.state.players['A'].zones['hand']);old=source.logical_object_id
                self.activate(s,source,['C'])
                if invalidate=='source':self.cast(s,'A',extra['blink'],[source.ref])
                else:
                    self.priority(s,'C')
                    result=s.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('A',s.state.cards[source.object_id].controller)
                if invalidate=='source':
                    self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
                    self.assertEqual(2,len(set(s.state.players['A'].zones['hand'])-hand))
                    self.finish(s,private)
                else:
                    self.assertEqual(hand,set(s.state.players['A'].zones['hand']))
                    for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                    self.replay(s,load=True)

    def test_humble_queued_actor_keeps_the_draw_even_after_someone_else_controls_its_source(self):
        extra={}
        def before(s,source,gift):
            extra['theft']=self.add(s.engine,'Turn Against',seat='B',zone='hand')
            s.state.players['B'].mana_pool.update({'C':4,'R':1})
        s,source,gift,private=self.scene('Humble Defector',zone='battlefield',before=before)
        hand={p:set(s.state.players[p].zones['hand']) for p in 'AC'}
        self.activate(s,source,['C']);self.cast(s,'B',extra['theft'],[source.ref]);self.resolve_top(s)
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(set(s.state.players['A'].zones['hand'])-hand['A']))
        self.assertEqual(hand['C'],set(s.state.players['C'].zones['hand']))
        self.assertEqual('C',s.state.cards[source.object_id].controller)
        self.finish(s,private)

    def test_opponent_copy_rebinds_controlled_subject_under_the_copy_actor_and_keeps_the_recipient(self):
        extra={}
        def before(s,source,gift):
            extra['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand')
            extra['gift']=self.add(s.engine,'Grizzly Bears',seat='B',ref='copy-actor-gift');extra['gift'].owner='D'
            extra['wrong']=self.add(s.engine,'Grizzly Bears',ref='new-illegal-copy-subject')
            s.state.players['B'].mana_pool['R']=2
        s,source,gift,private=self.scene('Donate',before=before)
        self.cast(s,'A',source,['C',gift.ref]);self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        command={'action_id':'choose','targets':['C',extra['gift'].ref]}
        self.reject(s,'A',command)
        self.reject(s,'B',{**command,'targets':['C',extra['wrong'].ref]})
        s=self.replay(s,load=True)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('B',s.state.stack[-1].controller)
        self.resolve_top(s)
        self.assertEqual(('C','D'),(s.state.cards[extra['gift'].object_id].controller,s.state.cards[extra['gift'].object_id].owner))
        self.assertIsNone(self.resolve(s));self.assertEqual('C',s.state.cards[gift.object_id].controller)
        self.finish(s,private)

    def test_fresh_donate_witness_kills_the_resolving_actor_as_recipient_mutant(self):
        from unittest.mock import patch
        from quorune.semantic_runtime.control_handlers import RecipientControlHandler
        method='test_donate_offering_and_wrong_turn_separate_actor_recipient_owner_and_subject'
        type(self)(method).spell_gifts(('Donate',))
        original=RecipientControlHandler.lower
        def wrong(handler,effect,context):return original(handler,{**effect,'controller':context.actor},context)
        with patch.object(RecipientControlHandler,'lower',wrong):
            with self.assertRaises(AssertionError):type(self)(method).spell_gifts(('Donate',))

    def test_gift_and_choice_payloads_require_the_recipient_owner_without_coverage(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Donate','Akroan Horse'):
            with self.subTest(name=name):
                s,source,gift,private=self.scene(name)
                program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op') in {'give_control','choose_control_recipient'} for e in p.effects))
                deps=tuple(c for c in program.capability_dependencies if c!='continuous.control.fixed_recipient')
                stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:continuous.control.fixed_recipient',bound['blockers'])

    def starke_departure(self):
        # CR 608.2h: B announces, C controls at departure, D physically owns it.
        extra={}
        def before(s,source,gift):
            gift.controller='B'
            s.state.players['A'].zones['battlefield'].remove(gift.object_id)
            s.state.players['B'].zones['battlefield'].append(gift.object_id)
            extra['theft']=self.add(s.engine,'Turn Against',seat='C',zone='hand')
            s.state.players['C'].mana_pool.update({'C':4,'R':1})
        s,source,gift,private=self.scene('Starke of Rath',zone='battlefield',before=before)
        self.activate(s,source,[gift.ref]);self.cast(s,'C',extra['theft'],[gift.ref]);self.resolve_top(s)
        self.assertEqual('C',s.state.cards[gift.object_id].controller)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('graveyard','D'),(s.state.cards[gift.object_id].zone,s.state.cards[gift.object_id].owner))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        self.assertIn(source.object_id,s.state.players['C'].zones['battlefield'])
        self.finish(s,private)

    def test_starke_uses_the_departure_controller_after_a_response_changes_control(self):
        self.starke_departure()

    def test_starke_does_not_give_a_returned_source_or_resolve_against_a_returned_target(self):
        for blink_source in (True,False):
            with self.subTest(blink_source=blink_source):
                extra={}
                def before(s,source,gift):
                    gift.controller='B'
                    s.state.players['A'].zones['battlefield'].remove(gift.object_id)
                    s.state.players['B'].zones['battlefield'].append(gift.object_id)
                    extra['blink']=self.add(s.engine,'Cloudshift',seat='A' if blink_source else 'B',zone='hand')
                    s.state.players['B'].mana_pool['W']=1
                s,source,gift,private=self.scene('Starke of Rath',zone='battlefield',before=before)
                subject=source if blink_source else gift;old=subject.logical_object_id
                self.activate(s,source,[gift.ref]);self.cast(s,'A' if blink_source else 'B',extra['blink'],[subject.ref])
                self.resolve_top(s);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertNotEqual(old,s.state.cards[subject.object_id].logical_object_id)
                self.assertEqual(('A','battlefield'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].zone))
                self.assertEqual('graveyard' if blink_source else 'battlefield',s.state.cards[gift.object_id].zone)
                self.finish(s,private)

    def test_starke_gives_control_even_when_indestructibility_prevents_destruction(self):
        extra={}
        def before(s,source,gift):
            extra['target']=self.add(s.engine,'Darksteel Myr',seat='B');extra['target'].owner='D'
        s,source,gift,private=self.scene('Starke of Rath',zone='battlefield',before=before)
        target=extra['target'];self.assert_closed(s,target)
        self.activate(s,source,[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('battlefield','B','D'),tuple(getattr(s.state.cards[target.object_id],k) for k in ('zone','controller','owner')))
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        self.finish(s,private)

    def test_starke_payload_requires_target_information_even_when_coverage_is_removed(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,source,gift,private=self.scene('Starke of Rath',zone='battlefield')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')=='give_control' for e in p.effects))
        for cap in ('resolution.target_controller_information','continuous.control.fixed_recipient'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_fresh_starke_witness_kills_reading_the_announcement_controller_as_recipient(self):
        from unittest.mock import patch
        from quorune.target_controller_information import resolve_target_controller_information
        def wrong(host,value,item):
            resolve_target_controller_information(host,value,item)
            return item.context['target_snapshots'][item.targets[value['index']]]['controller']
        method='test_starke_uses_the_departure_controller_after_a_response_changes_control'
        type(self)(method).starke_departure()
        with patch('quorune.target_controller_information.resolve_target_controller_information',wrong):
            with self.assertRaises(AssertionError):type(self)(method).starke_departure()

    def test_goblin_cadets_gifts_the_source_and_removes_it_from_combat_after_becoming_blocked(self):
        extra={}
        def before(s,source,gift):extra['blocker']=self.add(s.engine,'Hill Giant',seat='B')
        s,source,gift,private=self.scene('Goblin Cadets',zone='battlefield',before=before)
        self.attack(s,[(source,'B')]);self.until(s,lambda s:s.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{extra['blocker'].ref:[source.ref]}}
        self.reject(s,'C',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.target',self.resolve(s).kind)
        self.target(s,'A',['C']);self.assertIsNone(self.resolve(s))
        self.assertEqual('C',s.state.cards[source.object_id].controller)
        self.assertNotIn(source.object_id,s.state.combat.attackers)
        self.until(s,lambda s:s.state.phase_index>=9)
        self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual(0,s.state.cards[extra['blocker'].object_id].marked_damage)
        self.finish(s,private)
