"""A departing artifact's previous controller owns the trigger qualification."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Kibo, Uktabi Prince"
PROFILE = "commander_review"
DEPARTURE = "artifact.graveyard"


class OpponentArtifactCounterTriggerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-artifact-counter.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "opponent-artifact-counter-cards.json",
            fixtures / "activated-self-transform-cards.json",
            fixtures / "additional-beginning-originals-cards.json",
            fixtures / "live-modifier-quantity-cards.json",
            fixtures / "activation-paid-creature-subtype-cards.json",
            fixtures / "fixed-player-life-cards.json",
            fixtures / "assist-payment-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Opponent artifact departure", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def lowering(self, record):
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        return tuple(node for node in ir.faces[0].nodes if node.event == DEPARTURE and node.exact)

    def activate(self, game, seat, source, **extra):
        self.priority(game, seat)
        options = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in options if row.get("source") == source.ref and row.get("ability") == "ab1")
        return game.act(f"pilot:{seat}", {"action_id": action["id"], **extra})

    def resolve_bananas(self, game, source):
        result = self.activate(game, "A", source)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield"
                  and card.is_token and game.engine.display_name(card.object_id) == "Banana"]
        self.assertEqual(list("ABCD"), sorted(card.controller for card in tokens))
        return {card.controller: card for card in tokens}

    def test_complete_original_creates_one_banana_for_each_player_with_paid_mana_and_life(self):
        game = self.session(60310745)
        kibo = self.add(game.engine, ORIGINAL)
        spell = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.assert_closed(game, kibo)
        self.assert_closed(game, spell)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)

        bananas = self.resolve_bananas(game, kibo)
        before = authoritative_state_hash(game.state)
        denied = self.activate(game, "A", bananas["A"], mana_output={"U": 1})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        action = self.activate(game, "A", bananas["A"], mana_output={"R": 1})
        self.assertTrue(action.ok, action.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])
        self.assertEqual(42, game.state.players["A"].life)

        self.cast(game, "B", spell, [bananas["C"].ref])
        self.assertEqual(42, game.state.players["B"].life)
        self.assertNotIn(bananas["B"].object_id, game.state.players["B"].zones["battlefield"])
        self.assertEqual(40, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_mana_life_replacement_is_private_to_the_payer_and_replays_the_whole_activation(self):
        game = self.session(60310746)
        kibo = self.add(game.engine, ORIGINAL)
        for name in ("boon-one", "boon-two"):
            reflection = self.add(game.engine, "Boon Reflection", ref=name)
            self.assert_closed(game, reflection)
        self.assert_closed(game, kibo)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        token = self.resolve_bananas(game, kibo)["A"]
        started = self.activate(game, "A", token, mana_output={"G": 1})
        self.assertTrue(started.ok, started.summary)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual("battlefield", game.state.cards[token.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.assertEqual(40, game.state.players["A"].life)

        game = self.replay(game, load=True)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        command = {"action_id": "choose", "replacement": context["options"][0]["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertNotIn(token.object_id, game.state.players["A"].zones["battlefield"])
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertEqual(48, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_automatic_banana_payment_replays_replacement_then_casts_once(self):
        game = self.session(60310747)
        kibo = self.add(game.engine, ORIGINAL)
        spell = self.add(game.engine, "Disenchant", zone="hand")
        for ref in ("auto-boon-one", "auto-boon-two"):
            self.add(game.engine, "Boon Reflection", ref=ref)
        self.assert_closed(game, kibo)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"W": 1})
        self.checkpoint(game)
        bananas = self.resolve_bananas(game, kibo)

        self.cast(game, "A", spell, [bananas["C"].ref])
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual("hand", game.state.cards[spell.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[bananas["A"].object_id].zone)
        self.assertEqual(40, game.state.players["A"].life)

        game = self.replay(game, load=True)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        result = game.act("pilot:A", {"action_id": "choose", "replacement": context["options"][0]["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(48, game.state.players["A"].life)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.assertEqual(1, sum(item.card_object_id == spell.object_id for item in game.state.stack))
        self.assertNotIn(bananas["A"].object_id, game.state.players["A"].zones["battlefield"])
        self.resolve_top(game)
        self.assertNotIn(bananas["C"].object_id, game.state.players["C"].zones["battlefield"])
        self.replay(game, load=True)

    def test_assisting_player_owns_banana_life_replacement_until_cast_commits(self):
        from test_assist_payment import AssistRuntimeTests

        game = self.session(60310748)
        kibo = self.add(game.engine, ORIGINAL)
        spell = self.add(game.engine, "Bring Down", zone="hand")
        victim = self.add(game.engine, "Charging Binox", seat="C")
        for ref in ("assist-boon-one", "assist-boon-two"):
            self.add(game.engine, "Boon Reflection", seat="B", ref=ref)
        for card in (kibo, spell):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"W": 1, "C": 2})
        self.checkpoint(game)
        token = self.resolve_bananas(game, kibo)["B"]
        offer = AssistRuntimeTests._cast_offer(self, game, spell)
        AssistRuntimeTests._act(self, game, "A", offer["id"], targets=[victim.ref], pay="auto", assist_helper="B")
        context = game.packet("pilot:B", full=True)["decision"]["ctx"]
        mode = next(row for row in context["legal_actions"]
                    if row.get("assist_step", {}).get("source") == token.ref
                    and row["assist_step"]["bundle"] == {"R": 1})
        AssistRuntimeTests._act(self, game, "B", mode["id"])
        game = self.replay(game, load=True)
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        AssistRuntimeTests._act(self, game, "B", "choose", replacement=options[0]["id"])
        AssistRuntimeTests._act(self, game, "B", "assist:finish-mana")
        AssistRuntimeTests._act(self, game, "A", "assist:finish-mana")
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual("battlefield", game.state.cards[token.object_id].zone)
        AssistRuntimeTests._act(self, game, "B", "assist:contribute", assist_amount=1)
        game = self.replay(game, load=True)
        AssistRuntimeTests._act(self, game, "A", "assist:pay")
        self.assertEqual(48, game.state.players["B"].life)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertNotIn(token.object_id, game.state.players["B"].zones["battlefield"])
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_life_gain_changes_the_actual_original_result(self):
        with patch("quorune.rules.mana_life_result.apply_mana_life_gain", return_value=None) as skipped:
            with self.assertRaises(AssertionError):
                self.test_complete_original_creates_one_banana_for_each_player_with_paid_mana_and_life()
        skipped.assert_called_once()

    def test_withdrawing_the_mana_life_owner_reopens_only_the_token_ability(self):
        from quorune.fixed_mana_results import FIXED_MANA_LIFE_GAIN_CAPABILITY
        from quorune.rules.capabilities import CapabilityRegistry

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        entry = next(row for row in raw["capabilities"] if row["id"] == FIXED_MANA_LIFE_GAIN_CAPABILITY)
        entry.update(status="blocked", blockers=["Mana life result withdrawn"])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified("0" * 64)
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=withdrawn, capability_profile=PROFILE)
        self.assertNotEqual("exact", ir.status)
        self.assertEqual(1, len(ir.material_residuals))
        self.assertTrue(any(FIXED_MANA_LIFE_GAIN_CAPABILITY in blocker for blocker in ir.material_residuals[0].blockers))
        self.assertEqual([False, True, True], [node.exact for node in ir.faces[0].nodes])

    def test_printed_artifact_trigger_binds_previous_controller_and_a_creature_subtype_union(self):
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        node, = self.lowering(record)
        self.assertIn("counter.producer.fixed_public_set_event", node.capability_dependencies)
        self.assertIsNone(node.target_schema)
        effect, = node.effects
        self.assertEqual("place_counters_on_set", effect["op"])
        self.assertEqual(["creature"], effect["set"]["query"]["types_all"])
        self.assertEqual({"ape", "monkey"}, set(effect["set"]["query"]["subtypes_any"]))
        for invalid in (
            record.oracle_text.replace("a graveyard", "an opponent's graveyard"),
            record.oracle_text.replace("an Ape or a Monkey", "an Ape and a Monkey"),
            record.oracle_text.replace("an Ape or a Monkey", "an Ape or an unknown creature type"),
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.lowering(replace(record, oracle_text=invalid)))

    def test_stolen_artifact_uses_control_at_departure_and_each_current_creature_once(self):
        for index, thief in enumerate(("B", "A")):
            with self.subTest(thief=thief):
                victim_owner = "A" if thief == "B" else "B"
                game = self.session(60310740 + index)
                source = self.add(game.engine, ORIGINAL)
                ape = self.add(game.engine, "Kird Ape")
                changeling = self.add(game.engine, "Universal Automaton", ref="both-subtypes")
                outsider = self.add(game.engine, "Universal Automaton", seat="B", ref="foreign-subtypes")
                victim = self.add(game.engine, "Universal Automaton", seat=victim_owner, ref="stolen-artifact")
                steal = self.add(game.engine, "Act of Treason", seat=thief, zone="hand")
                destroy = self.add(game.engine, "Disenchant", seat=thief, zone="hand")
                game.state.config.semantic_policy = "trusted_only"
                self.prepare(game, actor=thief, active=thief, mana={"C": 3, "R": 1, "W": 1})
                self.checkpoint(game)

                self.cast_for(game, thief, steal, [victim.ref], reserve={"W": 1})
                self.resolve_top(game)
                self.assertEqual((victim_owner, thief), (game.state.cards[victim.object_id].owner,
                                                       game.state.cards[victim.object_id].controller))
                self.cast(game, thief, destroy, [victim.ref])
                self.resolve_top(game)

                self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
                if thief == "B":
                    trigger, = game.state.stack
                    self.assertEqual(("A", "B"), (trigger.controller, trigger.context["previous_controller"]))
                    self.assertEqual(source.object_id, trigger.source_object_id)
                    game = self.replay(game, load=True)
                    self.resolve_top(game)
                else:
                    self.assertFalse(game.state.stack)
                self.assertEqual([int(thief == "B")] * 3 + [0], [
                    game.state.cards[card.object_id].counters.get("+1/+1", 0)
                    for card in (source, ape, changeling, outsider)
                ])
                self.replay(game, load=True)
