"""Verify the original non-Human anthem and Human-damage response."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE, ability_fragment_from_dict, ability_fragment_to_dict,
)
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.death_return import DeathReturnSpec
from quorune.event_counter_reference import (
    DAMAGE_DEALER_PERMANENT, DAMAGE_DEALER_PERMANENT_CONTEXT,
    event_counter_recipient_context, resolve_sealed_event_object_value,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.nonhuman_undying_anthem import NONHUMAN_UNDYING_ANTHEM_CAPABILITY
from quorune.rules.occurrence_object_results import HUMAN_DAMAGE_DEALER_CAPABILITY, damage_dealer_result_condition
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


MIKAEUS = "Mikaeus, the Unhallowed"
HUMAN_SPELL = "Generic Human Spark Fixture"
DAMAGE = "Whenever a Human deals damage to you, destroy it."
ANTHEM = "Other non-Human creatures you control get +1/+1 and have undying."
TEXT = "\n".join(("Intimidate", DAMAGE, ANTHEM))
PROFILE = "commander_review"


def compiler_record(text):
    return CardRecord(
        oracle_id="compiler:human-damage-and-undying-anthem", name="Undying Anthem Fixture",
        mana_cost="{3}{B}{B}{B}", mana_value=6, type_line="Legendary Creature — Zombie Cleric",
        oracle_text=text, power="5", toughness="5", loyalty=None, defense=None, colors=("B",),
        color_identity=("B",), keywords=("Intimidate",), produced_mana=(), layout="normal",
        released_at="2026-01-01", legalities={"commander":"legal"}, faces=(), raw={},
    )


class MikaeusCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text=TEXT):
        return compile_oracle_card(compiler_record(text), capability_registry=self.registry, capability_profile=PROFILE)

    def test_exact_human_dealer_and_combined_nonhuman_anthem_keep_their_source_frames(self):
        ir = self.compile()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        keyword, response, combined, child = ir.faces[0].nodes
        self.assertIn("combat.block.intimidate", keyword.capability_dependencies)
        self.assertEqual(DAMAGE, TEXT[response.span.start:response.span.end])
        self.assertEqual(("battlefield", "damage.dealt"), (response.active_zone, response.event))
        self.assertEqual(damage_dealer_result_condition("damage_human_destroy"), response.event_condition)
        self.assertEqual(({"op":"destroy","card":DAMAGE_DEALER_PERMANENT},), response.effects)
        self.assertIsNone(response.target_schema)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, response.runtime_coverage)
        self.assertIn(HUMAN_DAMAGE_DEALER_CAPABILITY, response.capability_dependencies)
        self.assertEqual(ANTHEM, TEXT[combined.span.start:combined.span.end])
        grant, anthem = combined.handlers
        self.assertEqual(grant["condition"], anthem["condition"])
        self.assertTrue(grant["condition"]["exclude_source"])
        self.assertEqual("source_controller", grant["condition"]["target_controller"])
        self.assertEqual(["human"], grant["condition"]["predicate"]["excluded_subtypes"])
        self.assertEqual({"power":1,"toughness":1}, anthem["modifier"])
        self.assertEqual(combined.span, child.span)
        self.assertEqual(("permanent.graveyard.self", "+1/+1"), (child.event, child.effects[0]["entry_counter"]))
        self.assertIn(NONHUMAN_UNDYING_ANTHEM_CAPABILITY, combined.capability_dependencies)

    def test_nearby_damage_recipients_and_nonhuman_anthem_wording_stay_outside_the_cases(self):
        for replacement in (
            DAMAGE.replace("to you", "to an opponent"),
            DAMAGE.replace("deals damage", "deals combat damage"),
            DAMAGE.replace("destroy it", "destroy target creature"),
        ):
            with self.subTest(text=replacement):
                ir = self.compile(TEXT.replace(DAMAGE, replacement))
                self.assertFalse(any(HUMAN_DAMAGE_DEALER_CAPABILITY in node.capability_dependencies
                                     for face in ir.faces for node in face.nodes if node.exact))
        for replacement in (
            ANTHEM.replace("Other ", ""), ANTHEM.replace("+1/+1", "+2/+2"), ANTHEM.replace("undying", "persist"),
        ):
            with self.subTest(text=replacement):
                ir = self.compile(TEXT.replace(ANTHEM, replacement))
                self.assertFalse(any(NONHUMAN_UNDYING_ANTHEM_CAPABILITY in node.capability_dependencies
                                     for face in ir.faces for node in face.nodes if node.exact))

    def program(self, node):
        required = list(node.capability_dependencies)
        kind = "static" if node.kind == "static_ability" else "trigger"
        return SemanticProgram(
            key="fixture:" + node.node_id, label=node.text, oracle_id=compiler_record(TEXT).oracle_id,
            ability_id=f"{kind}:{node.node_id}",
            event=node.event, active_zone=node.active_zone,
            event_condition=node.event_condition, target_schema=node.target_schema,
            coverage=[*node.mechanics, *node.runtime_coverage], effects=list(node.effects), handlers=list(node.handlers),
            provenance={"template_id":node.template_id,"face_id":"front",
                        "source_span":{"line":node.span.line,"start":node.span.start,"end":node.span.end}},
            capability_dependencies=required,
            capability_closure=self.registry.closure(required, profile=PROFILE).to_dict(),
        )

    def bind(self, program):
        return bind_semantic_program_runtime(program, capability_registry=self.registry, profile=PROFILE)

    def test_materialized_recipient_and_shared_receivers_cannot_be_forged(self):
        _, human, compound, child_node = self.compile().faces[0].nodes
        response = self.program(human)
        anthem = self.program(compound)
        for program, capability in ((response, HUMAN_DAMAGE_DEALER_CAPABILITY),
                                    (anthem, NONHUMAN_UNDYING_ANTHEM_CAPABILITY)):
            with self.subTest(capability=capability):
                result = self.bind(program)
                self.assertTrue(result["strict"], result)
                remaining = [value for value in program.capability_dependencies if value != capability]
                stripped = replace(program, capability_dependencies=remaining,
                                   capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
                self.assertIn("capability:undeclared_runtime_dependency:" + capability, self.bind(stripped)["blockers"])
        invalid_response = replace(response, event_condition={"field":"target","op":"eq","value":"$source.controller"})
        self.assertFalse(self.bind(invalid_response)["strict"])
        grant, bonus = anthem.handlers
        invalid_bonus = {**bonus, "condition":{**bonus["condition"], "exclude_source":False}}
        self.assertFalse(self.bind(replace(anthem, handlers=[grant, invalid_bonus]))["strict"])
        raw_fragment, = grant["modifier"]["add_ability_fragments"]
        fragment = ability_fragment_from_dict(raw_fragment)
        child = self.program(child_node)
        child = replace(child,key=fragment.semantic_key,ability_id=fragment.ability_id,
                        provenance={**child.provenance,"granted_only":True})

        def card_binding(abilities):
            card = SimpleNamespace(abilities=abilities, fingerprint="fixture:combined", oracle_id=anthem.oracle_id,
                                   trust_closure={"trust_basis":"capability_closed"})
            return bind_card_program_runtime(card,capability_registry=self.registry,profile=PROFILE)

        linked = card_binding((anthem,child))
        self.assertTrue(linked["strict_capability_ready"],linked)
        other = DeathReturnSpec.for_keyword("persist")
        same_key_wrong_child = replace(child,event_condition=other.event_condition(),effects=[other.effect_descriptor()])
        for abilities in ((anthem,), (anthem,same_key_wrong_child)):
            result = card_binding(abilities)
            self.assertIn(f"ability:{anthem.ability_id}:runtime_nonhuman_undying_anthem:invalid_granted_program",
                          result["blockers"])
        wrong_key = ability_fragment_to_dict(replace(fragment, semantic_key="fixture:unrelated"))
        wrong_grant = {**grant,"modifier":{**grant["modifier"],"add_ability_fragments":[wrong_key]}}
        self.assertFalse(self.bind(replace(anthem,handlers=[wrong_grant,bonus]))["strict"])

    def test_missing_spell_or_reentered_permanent_is_never_the_destructible_dealer(self):
        dealer = SimpleNamespace(ref="dealer", zone="battlefield", phased_out=False, logical_object_id="dealer@2")
        host = SimpleNamespace(state=SimpleNamespace(cards={"dealer":dealer}))
        program = SimpleNamespace(effects=[{"op":"destroy","card":DAMAGE_DEALER_PERMANENT}])
        stack = {"event":"damage.dealt","card":"dealer","source_logical_object_id":"dealer@2","source_zone":"stack"}
        sealed = event_counter_recipient_context(program, "damage.dealt", stack)
        self.assertEqual({DAMAGE_DEALER_PERMANENT_CONTEXT:None}, sealed)
        observer = SimpleNamespace(context={**stack, **sealed, "source_zone":"battlefield"})
        self.assertEqual((True,None), resolve_sealed_event_object_value(host, observer, DAMAGE_DEALER_PERMANENT))
        battlefield = {**stack, "source_zone":"battlefield"}
        battlefield.update(event_counter_recipient_context(program, "damage.dealt", battlefield))
        item = SimpleNamespace(context=battlefield)
        self.assertEqual((True,"dealer"), resolve_sealed_event_object_value(host,item,DAMAGE_DEALER_PERMANENT))
        dealer.logical_object_id = "dealer@3"
        self.assertEqual((True,None), resolve_sealed_event_object_value(host,item,DAMAGE_DEALER_PERMANENT))

    def test_omitting_each_source_production_leaves_a_material_residual(self):
        self.assertEqual("exact", self.compile().status)
        with patch("quorune.compiler.granted_keyword_event_nodes.fixed_power_toughness_anthem_handler", return_value=None):
            self.assertTrue(self.compile().material_residuals)
        with patch("quorune.compiler.damage_permanent_event_bindings._damage_dealer_result_binding_spec", return_value=None):
            self.assertTrue(self.compile().material_residuals)


class MikaeusNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "mikaeus-unhallowed.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/damage-dealer-permanent-responses-cards.json",
            ROOT / "tests/fixtures/mikaeus-unhallowed-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mikaeus linked rules", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @staticmethod
    def current(game, card):
        return game.state.cards[card.object_id]

    def activate_human_damage(self, game, dealer, *, actor, recipient):
        self.priority(game, actor)
        actions = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in actions if row["id"].startswith("activate:" + dealer.ref + ":")]
        result = game.act("pilot:" + actor, {"action_id":action,"targets":[recipient],"pay":"auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    def test_simultaneous_mikaeus_departure_retains_last_known_undying_and_excludes_the_source(self):
        game = self.session(87917001)
        source = self.add(game.engine, MIKAEUS)
        borrowed = self.add(game.engine, "Grizzly Bears", seat="B", ref="borrowed-receiver")
        counted = self.add(game.engine, "Grizzly Bears", ref="already-counted")
        human = self.add(game.engine, "Prodigal Sorcerer", ref="human-neighbor")
        opposing = self.add(game.engine, "Grizzly Bears", seat="C", ref="opposing-nonhuman")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        game.engine.change_control(borrowed.object_id,"A",reason="Prepare borrowed recipient")
        counted.counters["+1/+1"] = 1
        for card in (source,borrowed,counted,human,opposing,wrath):
            self.assert_closed(game,card)
        self.assertEqual([5,3,4,1,2], [
            game.engine._numeric_stat(card.object_id,"power")
            for card in (source,borrowed,counted,human,opposing)
        ])
        self.assertEqual({}, borrowed.counters)
        self.prepare(game, mana={"W":2,"C":2})
        self.checkpoint(game)
        self.cast_for(game,"A",wrath,[])
        self.resolve_top(game)
        waiting, = [item for item in game.state.stack if item.kind == "triggered_ability"]
        self.assertEqual((borrowed.object_id,"A",[]),(waiting.source_object_id,waiting.controller,waiting.targets))
        self.assertTrue(all(self.current(game,card).zone=="graveyard" for card in (source,borrowed,counted,human,opposing)))
        game = self.replay(game,load=True)
        self.resolve_top(game)
        returned = self.current(game,borrowed)
        self.assertEqual(("battlefield","B","B",{"+1/+1":1}),(returned.zone,returned.owner,returned.controller,returned.counters))
        for card in (source,counted,human,opposing):
            self.assertEqual("graveyard",self.current(game,card).zone)
        self.replay(game,load=True)

    def test_actual_human_control_is_irrelevant_but_the_damage_recipient_must_be_you(self):
        game = self.session(87917002)
        source = self.add(game.engine, MIKAEUS)
        wrong_recipient = self.add(game.engine, "Prodigal Sorcerer", ref="wrong-recipient-human")
        correct_recipient = self.add(game.engine, "Prodigal Sorcerer", ref="own-human-dealer")
        for card in (source,wrong_recipient,correct_recipient):
            self.assert_closed(game,card)
        wrong_recipient.acquired_control_turn_count = -1
        correct_recipient.acquired_control_turn_count = -1
        self.prepare(game)
        self.checkpoint(game)
        before = game.state.players["C"].life
        self.activate_human_damage(game, wrong_recipient, actor="A", recipient="C")
        self.assertEqual(before-1,game.state.players["C"].life)
        self.assertEqual(0,self.trigger_count(game,source))
        before = game.state.players["A"].life
        self.activate_human_damage(game, correct_recipient, actor="A", recipient="A")
        self.assertEqual(before-1,game.state.players["A"].life)
        queued, = [item for item in game.state.stack if item.source_object_id==source.object_id]
        self.assertEqual([],queued.targets)
        self.resolve_top(game)
        self.assertEqual("graveyard",self.current(game,correct_recipient).zone)
        self.assertEqual("battlefield",self.current(game,wrong_recipient).zone)
        self.replay(game,load=True)

    def test_actual_response_survives_loss_of_mikaeus_and_does_not_destroy_a_blinked_dealer(self):
        for blink in (False,True):
            with self.subTest(blink=blink):
                game = self.session(87917003+int(blink))
                source = self.add(game.engine, MIKAEUS)
                human = self.add(game.engine, "Prodigal Sorcerer", seat="B")
                removal = self.add(game.engine, "Murder", seat="D", zone="hand")
                blink_spell = self.add(game.engine, "Cloudshift", seat="B", zone="hand") if blink else None
                for card in (source,human,removal,blink_spell):
                    if card is not None:
                        self.assert_closed(game,card)
                human.acquired_control_turn_count = -1
                self.prepare(game)
                game.state.players["D"].mana_pool.update(B=2,C=1)
                game.state.players["B"].mana_pool["W"] = 1
                self.checkpoint(game)
                lost_identity = human.logical_object_id
                self.activate_human_damage(game,human,actor="B",recipient="A")
                self.assertEqual(1,self.trigger_count(game,source))
                self.cast_for(game,"D",removal,[source.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard",self.current(game,source).zone)
                game = self.replay(game,load=True)
                if blink_spell is not None:
                    self.cast_for(game,"B",blink_spell,[human.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(lost_identity,self.current(game,human).logical_object_id)
                self.resolve_top(game)
                self.assertEqual("battlefield" if blink else "graveyard",self.current(game,human).zone)
                self.replay(game,load=True)

    def test_human_kindred_spell_triggers_but_cannot_be_destroyed(self):
        game = self.session(87917005)
        source = self.add(game.engine,MIKAEUS)
        spark = self.add(game.engine,HUMAN_SPELL,seat="B",zone="hand")
        self.assert_closed(game,source)
        self.assert_closed(game,spark)
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game,"B",spark,["A"])
        original_stack_identity = self.current(game,spark).logical_object_id
        self.assertEqual("stack",self.current(game,spark).zone)
        self.resolve_top(game)
        queued, = [item for item in game.state.stack if item.source_object_id==source.object_id]
        self.assertEqual(f"{original_stack_identity}|stack",queued.context["source_identity_key"])
        self.assertIsNone(queued.context[DAMAGE_DEALER_PERMANENT_CONTEXT])
        self.assertIn("human",queued.context["source_subtypes"])
        self.assertEqual([],queued.targets)
        ordinary_destination = self.current(game,spark).logical_object_id
        self.resolve_top(game)
        self.assertEqual(("graveyard",ordinary_destination),(self.current(game,spark).zone,self.current(game,spark).logical_object_id))
        self.assertEqual("battlefield",self.current(game,source).zone)
        self.replay(game,load=True)
