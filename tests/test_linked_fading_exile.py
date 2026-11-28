"""Native Fading exile activations with independent source-departure returns."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import LINKED_FADING_EXILE_CAPABILITY, LINKED_FADING_HAND_BINDING
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


class LinkedFadingExileTests(InheritedNativeFixture, unittest.TestCase):
    tide = "Parallax Tide"
    wave = "Parallax Wave"
    nexus = "Parallax Nexus"
    land = "Generic Bound Plains"
    body = "Generic Bound Body"
    flicker = "Flicker of Fate"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "linked-fading-exile.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "fading-cards.json",
            fixtures / "linked-fading-exile-cards.json",
            fixtures / "announced-x-activation-cards.json",
            fixtures / "mana-tapped-land-next-untap-cards.json",
            fixtures / "static-land-animation-cards.json",
            fixtures / "activation-public-state-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Linked Fading", [
            DeckEntry(name, 1, "commander"), DeckEntry(cls.land, 30),
        ], [name])

    def _start(self, game, source, *spells):
        game.state.config.semantic_policy = "trusted_only"
        for card in (source, *spells):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"C": 2, "W": 2})
        self.prepare(game, mana={"C": 5, "U": 2, "W": 2, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(5, game.state.cards[source.object_id].counters.get("fade"))

    def _activation(self, game, source, target):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered = next(action for action in actions if action["id"].startswith("activate:" + source.ref + ":"))
        return {"action_id": offered["id"], "targets": [target], "pay": "auto"}

    def _activate(self, game, source, target):
        command = self._activation(game, source, target)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _assert_zone(self, game, zone, *cards):
        for card in cards:
            self.assertEqual(zone, game.state.cards[card.object_id].zone, card.ref)

    def _farm_action(self, game, farm, action, target=None, *, succeeds=True):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        matches = [row for row in actions if row["id"].startswith("activate:" + farm.ref + ":")]
        description = {"mana": "Add {C}", "exile": "Exile target creature", "return": "Return a creature card"}[action]
        selected, = [row for row in matches if description in row["label"]]
        command = {"action_id": selected["id"], "pay": "auto"}
        if target is not None:
            command["targets"] = [target]
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", command)
        self.assertEqual(succeeds, result.ok, result.summary)
        if not succeeds:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def _untap_farm(self, game, untap, farm):
        self.cast(game, "A", untap, [farm.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[farm.object_id].tapped)

    def test_farm_sacrifices_original_source_and_returns_chosen_creature_then_the_rest(self):
        game = self.session(607_233_01)
        farm = self.add(game.engine, "Northampton Farm")
        other = self.add(game.engine, "Northampton Farm", ref="other-farm")
        first = self.add(game.engine, self.body, ref="farm-first")
        second = self.add(game.engine, self.body, ref="farm-second")
        invalid = self.add(game.engine, self.body, seat="B", ref="farm-other-owner")
        outside = self.add(game.engine, self.body, zone="exile", ref="farm-unlinked")
        untaps = [self.add(game.engine, "Burst of Energy", zone="hand", ref=f"farm-untap-{n}") for n in range(3)]
        game.engine.change_control(first.object_id, "B", reason="B controls A's creature")
        game.engine.change_control(invalid.object_id, "A", reason="A controls B's creature")
        self.assert_closed(game, farm)
        self.prepare(game, mana={"C": 10, "W": 3})
        self.checkpoint(game)
        before = game.state.players["A"].mana_pool["C"]
        self._farm_action(game, farm, "mana")
        self.assertEqual(before + 1, game.state.players["A"].mana_pool["C"])
        self._untap_farm(game, untaps[0], farm)
        self._farm_action(game, farm, "exile", invalid.ref, succeeds=False)
        for index, target in enumerate((first, second), 1):
            self._farm_action(game, farm, "exile", target.ref)
            self.assertIsNone(self.resolve(game))
            self._untap_farm(game, untaps[index], farm)
        self._assert_zone(game, "exile", first, second, outside)
        original = game.state.cards[farm.object_id].logical_object_id
        self._farm_action(game, farm, "return")
        self.assertEqual("graveyard", game.state.cards[farm.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.assertEqual(original, game.state.stack[-1].context["source_logical_object_id"])
        decision = self.resolve(game)
        self.assertEqual("semantic.choice", decision.kind)
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]
        self.assertEqual({first.ref, second.ref}, {row["id"] for row in choices})
        game = self.replay(game, load=True)
        choice = game.act("pilot:A", {"action_id": "choose", "card": first.ref})
        self.assertTrue(choice.ok, choice.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A"), (game.state.cards[first.object_id].zone, game.state.cards[first.object_id].controller))
        self.assertIn(second.object_id, game.state.players["A"].zones["hand"])
        self._assert_zone(game, "exile", outside)
        self.replay(game, load=True)

    def test_farm_reentered_source_does_not_recover_earlier_linked_cards(self):
        game = self.session(607_233_02)
        farm = self.add(game.engine, "Northampton Farm")
        land = self.add(game.engine, self.land)
        old = self.add(game.engine, self.body, ref="farm-old-source-link")
        blink = self.add(game.engine, "Ghostly Flicker", zone="hand")
        self.prepare(game, mana={"C": 6, "U": 1})
        self.checkpoint(game)
        original = farm.logical_object_id
        self._farm_action(game, farm, "exile", old.ref)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", blink, [farm.ref, land.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original, game.state.cards[farm.object_id].logical_object_id)
        self._farm_action(game, farm, "return")
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "graveyard", farm)
        self._assert_zone(game, "exile", old)
        self.replay(game, load=True)

    def test_farm_without_a_creature_card_still_returns_the_original_land_to_hand(self):
        game = self.session(607_233_04)
        farm = self.add(game.engine, "Northampton Farm")
        canopy = self.add(game.engine, "Living Lands")
        forest = self.add(game.engine, "Forest")
        untap = self.add(game.engine, "Burst of Energy", zone="hand")
        self.assert_closed(game, canopy)
        self.prepare(game, mana={"C": 4, "W": 1})
        self.checkpoint(game)
        self._farm_action(game, farm, "exile", forest.ref)
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "exile", forest)
        self._untap_farm(game, untap, farm)
        self._farm_action(game, farm, "return")
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "graveyard", farm)
        self._assert_zone(game, "hand", forest)
        self.replay(game, load=True)

    def test_farm_exiled_token_never_becomes_a_returnable_card(self):
        game = self.session(607_233_03)
        farm = self.add(game.engine, "Northampton Farm")
        make = self.add(game.engine, "Raise the Alarm", zone="hand")
        untap = self.add(game.engine, "Burst of Energy", zone="hand")
        self.prepare(game, mana={"C": 6, "W": 3})
        self.checkpoint(game)
        self.cast(game, "A", make)
        self.assertIsNone(self.resolve(game))
        tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(tokens))
        token = tokens[0]
        self._farm_action(game, farm, "exile", token.ref)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(token.object_id in player.zones["exile"] for player in game.state.players.values()))
        self._untap_farm(game, untap, farm)
        self._farm_action(game, farm, "return")
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "graveyard", farm)
        self.assertFalse(any(token.object_id in player.zones["hand"] or token.object_id in player.zones["battlefield"]
                             for player in game.state.players.values()))
        self.replay(game, load=True)

    def test_linked_fading_land_returns_only_its_original_owner_batch(self):
        game = self.session(607_232_01)
        source = self.add(game.engine, self.tide, zone="hand", ref="TIDE-SOURCE")
        first_blink = self.add(game.engine, self.flicker, seat="B", zone="hand", ref="TIDE-FIRST-BLINK")
        second_blink = self.add(game.engine, self.flicker, seat="B", zone="hand", ref="TIDE-SECOND-BLINK")
        mine = self.add(game.engine, self.land, ref="TIDE-MINE")
        borrowed = self.add(game.engine, self.land, seat="B", ref="TIDE-BORROWED")
        late = self.add(game.engine, self.land, seat="C", ref="TIDE-LATE")
        new = self.add(game.engine, self.land, seat="D", ref="TIDE-NEW")
        unrelated = self.add(game.engine, self.land, seat="B", zone="exile", ref="TIDE-UNRELATED")
        game.engine.change_control(borrowed.object_id, "A", reason="A controls B's land")
        self._start(game, source, first_blink, second_blink)
        original = game.state.cards[source.object_id].logical_object_id
        for land in (mine, borrowed):
            self._activate(game, source, land.ref)
            self.resolve_top(game)
        self._assert_zone(game, "exile", mine, borrowed, unrelated)
        self.assertEqual(3, game.state.cards[source.object_id].counters.get("fade"))
        self._activate(game, source, late.ref)
        self.assertEqual(2, game.state.cards[source.object_id].counters.get("fade"))
        self.cast(game, "B", first_blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        self.assertEqual(5, game.state.cards[source.object_id].counters.get("fade"))
        old_ability, departure = game.state.stack
        self.assertEqual(("activated_ability", "triggered_ability"), (old_ability.kind, departure.kind))
        self.assertEqual(original, old_ability.context["source_logical_object_id"])
        self.assertEqual(original, departure.context["source_logical_object_id"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self._assert_zone(game, "battlefield", mine, borrowed, late, new)
        self.assertEqual("B", game.state.cards[borrowed.object_id].controller)
        self.resolve_top(game)
        self._assert_zone(game, "exile", late, unrelated)

        self._activate(game, source, new.ref)
        self.resolve_top(game)
        self._assert_zone(game, "exile", late, unrelated, new)
        self.cast(game, "B", second_blink, [source.ref])
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "battlefield", new)
        self._assert_zone(game, "exile", late, unrelated)
        self.replay(game, load=True)

    def test_linked_fading_creature_rejects_wrong_type_and_stale_target(self):
        game = self.session(607_232_02)
        source = self.add(game.engine, self.wave, zone="hand", ref="WAVE-SOURCE")
        blink_target = self.add(game.engine, self.flicker, seat="B", zone="hand", ref="WAVE-BLINK-TARGET")
        blink_source = self.add(game.engine, self.flicker, seat="B", zone="hand", ref="WAVE-BLINK-SOURCE")
        illegal = self.add(game.engine, self.land, seat="C", ref="WAVE-LAND")
        first = self.add(game.engine, self.body, seat="B", ref="WAVE-RETURN")
        stale = self.add(game.engine, self.body, seat="D", ref="WAVE-STALE")
        self._start(game, source, blink_target, blink_source)
        command = self._activation(game, source, illegal.ref)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(5, game.state.cards[source.object_id].counters.get("fade"))
        self._activate(game, source, first.ref)
        self.resolve_top(game)
        self._assert_zone(game, "exile", first)
        self._activate(game, source, stale.ref)
        previous = game.state.cards[stale.object_id].logical_object_id
        self.cast(game, "B", blink_target, [stale.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(previous, game.state.cards[stale.object_id].logical_object_id)
        self._assert_zone(game, "battlefield", stale, illegal)
        self.assertEqual(3, game.state.cards[source.object_id].counters.get("fade"))
        self.cast(game, "B", blink_source, [source.ref])
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "battlefield", first, stale, illegal)
        self.replay(game, load=True)

    def test_linked_fading_hand_choice_stays_private_and_returns_to_owner(self):
        game = self.session(607_232_03)
        source = self.add(game.engine, self.nexus, zone="hand", ref="NEXUS-SOURCE")
        blink = self.add(game.engine, self.flicker, seat="B", zone="hand", ref="NEXUS-BLINK")
        selected = self.add(game.engine, self.body, seat="B", zone="hand", ref="NEXUS-PRIVATE-SELECTED")
        other = self.add(game.engine, self.land, seat="B", zone="hand", ref="NEXUS-PRIVATE-OTHER")
        foreign = self.add(game.engine, self.body, seat="D", zone="hand", ref="NEXUS-PRIVATE-FOREIGN")
        owner = ZoneTransitionOwner(game.engine)
        for object_id in tuple(game.state.players["C"].zones["hand"]):
            owner.move_card(object_id, "library")
        self._start(game, source, blink)
        command = self._activation(game, source, "A")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._activate(game, source, "C")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.state.cards[source.object_id].counters.get("fade"))
        self._activate(game, source, "B")
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(action["id"].startswith("activate:" + source.ref + ":") for action in actions))
        choice = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (choice.kind, choice.actors))
        self.assertIn(selected.ref, json.dumps(game.packet("pilot:B", full=True)["decision"]))
        for seat in "ACD":
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(selected.ref, json.dumps(packet))
            self.assertNotIn(other.ref, json.dumps(packet))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        pick = {"action_id": "choose", "cards": [selected.ref]}
        self.assertFalse(game.act("pilot:A", pick).ok)
        self.assertFalse(game.act("pilot:B", {**pick, "cards": [foreign.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", pick)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "exile", selected)
        self._assert_zone(game, "hand", other, foreign)
        self.cast(game, "B", blink, [source.ref])
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "hand", selected, other, foreign)
        self.assertIn(selected.object_id, game.state.players["B"].zones["hand"])
        self.assertNotIn(other.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_linked_fading_compiler_and_runtime_require_exact_pair_and_cost(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == LINKED_FADING_EXILE_CAPABILITY)
        row.update(status="blocked", blockers=["linked Fading authority withdrawn"])
        blocked = CapabilityRegistry(raw)
        game = self.session(607_232_04)
        for name in (self.tide, self.wave, self.nexus):
            with self.subTest(name=name):
                card = self.add(game.engine, name)
                self.assert_closed(game, card)
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=self.registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                phases = [node for node in ir.faces[0].nodes if node.event in {"activate", "permanent.leave.self"}]
                self.assertEqual(2, len(phases))
                activation, departure = [node.effects[0] for node in phases]
                want = departure["binding_id"]
                self.assertEqual(want, activation.get(LINKED_FADING_HAND_BINDING, activation.get("binding_id")))
                for node in phases:
                    self.assertIn(LINKED_FADING_EXILE_CAPABILITY, node.capability_dependencies)
                removed = compile_oracle_card(record, capability_registry=blocked)
                self.assertFalse(any(node.exact for node in removed.faces[0].nodes
                                     if node.event in {"activate", "permanent.leave.self"}))
                with patch("quorune.compiler.linked_exile_return_templates.linked_fading_compilers", side_effect=lambda a, b, **_: (a, b)):
                    unpaired = compile_oracle_card(record, capability_registry=self.registry)
                    self.assertNotEqual("exact", unpaired.status)
                program = next(p for p in game.engine.semantics.programs_for_oracle(record.oracle_id)
                               if p.event == "activate")
                cost = {**thaw_value(program.cost_schema), "source_counter_removal_cost": {
                    **thaw_value(program.cost_schema["source_counter_removal_cost"]), "amount": 2,
                }}
                result = bind_semantic_program_runtime(
                    replace(program, cost_schema=cost), capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(result["strict"], result)


if __name__ == "__main__":
    unittest.main()
