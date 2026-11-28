"""Native rules, privacy and Game Record witnesses for CR 728."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_cost_modifiers import AbilityLocalRadReduction, ability_local_mana_reduction_from_dict
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.counter_state import CounterChange, commit_counter_changes, plan_counter_changes
from quorune.deck import DeckDefinition, DeckEntry
from quorune.declaration_fragments import DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY, DEFENDING_PLAYER_RAD_BLOCK_TEMPLATE
from quorune.errors import GameRuleError
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.rad_counter_model import (
    ENTRY_RAD_CHOICE_CAPABILITY, ENTRY_RAD_CHOICE_HANDLER, RAD_CAPABILITY,
    RAD_EFFECT_FIELD, RAD_PRODUCER_FIELD, RAD_VERSION_FIELD,
)
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.record_state_provenance import _validate_effect_versions, format_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram
from quorune.trigger_participation import WardSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class RadCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "rad.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/committed-milled-creature-tokens-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Radiation", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    @staticmethod
    def counters(game, seat="A"):
        return game.state.players[seat].counters.get("rad", 0)

    @staticmethod
    def set_rad(game, amount, seat="A"):
        # Arrange a public fixture through the counter owner before the recorded journey.
        before = RadCounterTests.counters(game, seat)
        commit_counter_changes(game.engine, plan_counter_changes(
            game.engine, (CounterChange("player", seat, "rad", amount - before),),
        ))

    def staged(self, seed, names, *, amount, other=0):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        for oid in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(oid, "hand", reason="Prepare the bounded fixture library", log=False)
        cards = [self.add(game.engine, name, zone="library", ref=f"rad-hidden-{idx}")
                 for idx, name in enumerate(names)]
        self.add(game.engine, "Plains", zone="library", ref="rad-before-trigger-draw")
        self.set_rad(game, amount)
        self.set_rad(game, other, "B")
        self.assertEqual(("beginning", "upkeep"), (game.state.phase, game.state.step))
        for card in cards:
            card.known_to = []
            card.revealed_to = []
        game.engine._grant_priority("A")
        game.engine.pump()
        return game, cards

    def enter_main(self, game):
        self.checkpoint(game)
        self.until(game, lambda session: (session.state.phase, session.state.step) == ("precombat_main", "main"))

    def trigger(self, game):
        item, = [item for item in game.state.stack if RAD_EFFECT_FIELD in item.context]
        self.assertEqual(("A", "triggered_ability", None, None, []),
                         (item.controller, item.kind, item.source_object_id, item.card_object_id, item.targets))
        return item

    def mothman_act(self, game, seat, action, **fields):
        result = game.act("pilot:" + seat, {"action_id": action, **fields})
        self.assertTrue(result.ok, result.summary)

    def mothman_reject(self, game, seat, action, **fields):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": action, **fields})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_wise_mothman_its_own_entry_and_attack_add_one_rad_to_every_player(self):
        game = self.session(728071)
        source = self.add(game.engine, "The Wise Mothman", zone="hand")
        haste = self.add(game.engine, "Fervor")
        for card in (source, haste):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "B": 1, "G": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([1, 1, 1, 1], [self.counters(game, seat) for seat in "ABCD"])
        self.assertIn("flying", game.engine._combat_keywords(game.state.cards[source.object_id]))
        game = self.replay(game, load=True)
        source = game.state.cards[source.object_id]
        self.attack(game, [(source, "B")])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([2, 2, 2, 2], [self.counters(game, seat) for seat in "ABCD"])
        self.replay(game)

    def test_wise_mothman_raul_batch_counts_public_graveyard_and_exile_then_revalidates(self):
        from quorune.rules.committed_nonland_mill import MILL_COUNT, MILL_EVENT

        game = self.session(728072)
        mothman = self.add(game.engine, "The Wise Mothman")
        raul = self.add(game.engine, "Raul, Trouble Shooter")
        raul.acquired_control_turn_count = -1
        selected_rival = self.add(game.engine, "Grizzly Bears", seat="B", ref="mill-counter-rival")
        anafenza = self.add(game.engine, "Anafenza, the Foremost", seat="D")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand", ref="mill-response")
        for card in (mothman, raul, anafenza, murder):
            self.assert_closed(game, card)
        cards = {}
        for seat, name in zip("ABCD", ("Lightning Bolt", "Plains", "Grizzly Bears", "Plains"), strict=True):
            top = self.add(game.engine, name, seat=seat, zone="library", ref="raul-top-" + seat)
            top.known_to = []
            top.revealed_to = []
            cards[seat] = top
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith("activate:" + raul.ref + ":"))
        self.mothman_act(game, "A", action["id"])
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(["graveyard", "graveyard", "exile", "graveyard"],
                         [game.state.cards[cards[seat].object_id].zone for seat in "ABCD"])
        staged = [*game.state.stack, *(row for batch in game.state.pending_trigger_batches for row in batch.items)]
        matching = {row.ref: row for row in staged if row.context.get("event") == MILL_EVENT}
        occurrence, = matching.values()
        self.assertEqual(2, occurrence.context[MILL_COUNT])
        self.assertEqual(2, occurrence.context["target_schema_override"]["up_to"])
        refs = [mothman.ref, selected_rival.ref]
        for invalid in ([mothman.ref, mothman.ref], [*refs, raul.ref]):
            self.mothman_reject(game, "A", "choose", targets=invalid)
        self.mothman_reject(game, "B", "choose", targets=refs)
        game = self.replay(game, load=True)
        self.mothman_act(game, "A", "choose", targets=refs)
        self.cast(game, "B", murder, [selected_rival.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[mothman.object_id].counters.get("+1/+1"))
        self.assertEqual("exile", game.state.cards[selected_rival.object_id].zone)
        self.assertFalse(game.state.cards[selected_rival.object_id].counters)
        self.replay(game)

    def test_wise_mothman_rad_mill_can_choose_none_and_hidden_stops_do_not_trigger(self):
        for names, visible in ((("Grizzly Bears", "Plains"), True),
                               (("Blightsteel Colossus", "Plains"), False),
                               (("Plains",), False)):
            with self.subTest(names=names):
                game, cards = self.staged(728075 + len(names) + visible, names, amount=2)
                mothman = self.add(game.engine, "The Wise Mothman")
                self.assert_closed(game, mothman)
                self.enter_main(game)
                self.trigger(game)
                pending = self.resolve(game)
                self.assertEqual((1 if visible else 2, 39 if visible else 40),
                                 (self.counters(game), game.state.players["A"].life))
                if visible:
                    self.assertEqual("semantic.target", pending.kind)
                    game = self.replay(game, load=True)
                    self.mothman_act(game, "A", "choose", targets=[])
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("graveyard", game.state.cards[cards[0].object_id].zone)
                else:
                    self.assertIsNone(pending)
                    if "Blightsteel Colossus" in names:
                        self.assertEqual("library", game.state.cards[cards[0].object_id].zone)
                self.assertEqual(0, game.state.cards[mothman.object_id].counters.get("+1/+1", 0))
                self.replay(game)

    def test_wise_mothman_exact_mill_counter_and_rad_trigger_shapes_are_required(self):
        from quorune.rules.committed_nonland_mill import (
            committed_mill_counter_dependencies, committed_mill_counter_effect, committed_mill_counter_target_schema,
        )

        source = self.db.lookup("The Wise Mothman", fuzzy=False)
        result = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        for helper in ("committed_nonland_mill_binding_spec", "source_enters_attacks_rad_result"):
            with self.subTest(helper=helper), patch("quorune.compiler.committed_nonland_mill." + helper, return_value=None):
                absent = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", absent.status)
        effect = committed_mill_counter_effect()
        schema = committed_mill_counter_target_schema()
        self.assertFalse(committed_mill_counter_dependencies(({**effect, "amount": 2},), schema))
        self.assertFalse(committed_mill_counter_dependencies((effect,), {**schema, "up_to": True}))

    def _mariposa_game(self, seed, *, rad=0, mana=5, prohibit=False):
        game = self.session(seed)
        engine = game.engine
        land = self.add(engine, "Mariposa Military Base", zone="hand", ref="played-base")
        reserve = self.add(engine, "Mariposa Military Base", seat="B", ref="borrowed-base")
        engine.change_control(reserve.object_id, "A", reason="Fixture gives A control of B's land")
        self.set_rad(game, rad)
        self.set_rad(game, 8, "B")
        if prohibit:
            restriction = self.add(engine, "Solemnity", seat="C")
            self.assert_closed(game, restriction)
        self.assert_closed(game, land)
        self.prepare(game, mana={"C": mana})
        self.checkpoint(game)
        return game, land, reserve

    def _play_mariposa(self, game, land, *, accept):
        result = game.act("pilot:A", {"action_id": f"play-land:{land.ref}"})
        self.assertTrue(result.ok, result.summary)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        choice, = [row for row in options if ("Enter tapped and get" in row["label"]) == accept]
        change = {"action_id": "choose", "replacement": choice["id"]}
        unchanged = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", change).ok)
        self.assertEqual(unchanged, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", change)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.assertEqual(accept, game.state.cards[land.object_id].tapped)
        return game

    def _activate_mariposa_draw(self, game, reserve):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"] == f"activate:{reserve.ref}:ab3"]
        before = len(game.state.players["A"].zones["hand"])
        result = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[reserve.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, len(game.state.players["A"].zones["hand"]))

    def test_mariposa_native_entry_choice_and_live_borrowed_activation_price_replay(self):
        for accept in (True, False):
            with self.subTest(accept=accept):
                game, land, reserve = self._mariposa_game(7286100 + accept)
                game = self._play_mariposa(game, land, accept=accept)
                self.assertEqual((2 if accept else 0, 8), (self.counters(game), self.counters(game, "B")))
                self._activate_mariposa_draw(game, reserve)
                self.assertEqual(2 if accept else 0, game.state.players["A"].mana_pool["C"])
                if not accept:
                    self.priority(game, "A")
                    current = game.state.cards[land.object_id]
                    mana = game.act("pilot:A", {"action_id": f"activate:{current.ref}:ab2", "pay": "auto"})
                    self.assertTrue(mana.ok, mana.summary)
                    self.assertTrue(game.state.cards[land.object_id].tapped)
                    self.assertEqual(1, game.state.players["A"].mana_pool["C"])
                self.replay(game, load=True)

    def test_mariposa_counter_prohibition_still_taps_and_large_rad_reduces_to_zero(self):
        game, land, reserve = self._mariposa_game(7286104, rad=6, mana=0, prohibit=True)
        game = self._play_mariposa(game, land, accept=True)
        self.assertEqual((6, 8), (self.counters(game), self.counters(game, "B")))
        self._activate_mariposa_draw(game, reserve)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_mariposa_compiler_pins_the_rad_choice_and_live_reduction(self):
        record = self.db.lookup("Mariposa Military Base", fuzzy=False)
        kwargs = {"capability_registry": self.registry, "capability_profile": "commander_review"}

        def exact(source):
            ir = compile_oracle_card(source, **kwargs)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            nodes = ir.faces[0].nodes
            entry, = [node for node in nodes if node.event == "zone.change"]
            self.assertIn(ENTRY_RAD_CHOICE_CAPABILITY, entry.capability_dependencies)
            self.assertTrue(any(handler["handler_id"] == ENTRY_RAD_CHOICE_HANDLER for handler in entry.handlers))

        exact(record)
        for old, new in (
            ("you get two rad counters", "you get two poison counters"),
            ("two rad counters", "three rad counters"),
            ("rad counter you have", "poison counter you have"),
        ):
            changed = replace(record, oracle_text=record.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(changed, **kwargs).status)
        spec = AbilityLocalRadReduction().to_dict()
        self.assertEqual(AbilityLocalRadReduction(), ability_local_mana_reduction_from_dict(spec))
        with self.assertRaises(ValueError):
            ability_local_mana_reduction_from_dict({**spec, "per_player_counter": "poison"})
        with patch("quorune.compiler.entry_state_templates.entry_rad_choice_descriptor", return_value={}):
            with self.assertRaises((AssertionError, KeyError, ValueError)):
                exact(record)

    def test_precombat_trigger_is_for_active_player_and_counts_committed_nonlands(self):
        game, cards = self.staged(728001, ("Grizzly Bears", "Plains", "Dread Summons"), amount=3, other=2)
        self.enter_main(game)
        item = self.trigger(game)
        self.assertIn(item.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.assertEqual((3, 2, 40), (self.counters(game), self.counters(game, "B"), game.state.players["A"].life))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard",) * 3, tuple(game.state.cards[c.object_id].zone for c in cards))
        self.assertEqual((1, 2, 38), (self.counters(game), self.counters(game, "B"), game.state.players["A"].life))
        life, = [event for event in game.state.events if event.code == "effect.life" and event.details.get("cause") == "radiation"]
        self.assertEqual(-2, life.details["delta"])
        self.until(game, lambda session: session.state.phase == "postcombat_main")
        self.assertFalse(any(RAD_EFFECT_FIELD in entry.context for entry in game.state.stack))
        self.replay(game, load=True)

    def test_public_exile_replacements_resume_the_same_private_mill(self):
        game, (top,) = self.staged(728002, ("Grizzly Bears",), amount=2)
        self.add(game.engine, "Leyline of the Void", seat="B")
        self.add(game.engine, "Rest in Peace", seat="D")
        self.enter_main(game)
        self.trigger(game)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual((2, 40, "library"),
                         (self.counters(game), game.state.players["A"].life, game.state.cards[top.object_id].zone))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(top.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        response = {"action_id": "choose", "replacement": options[0]["id"]}
        prior = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", response).ok)
        self.assertFalse(game.act("pilot:A", {**response, "replacement": "not offered"}).ok)
        self.assertEqual(prior, authoritative_state_hash(game.state))
        resolution = game.act("pilot:A", response)
        self.assertTrue(resolution.ok, resolution.summary)
        self.assertEqual(("exile", 1, 39),
                         (game.state.cards[top.object_id].zone, self.counters(game), game.state.players["A"].life))
        self.assertIn(top.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_short_empty_and_private_replacement_use_only_identifiable_results(self):
        for names, amount, expected_rad, expected_life in (
            ((), 4, 4, 40), (("Plains", "Grizzly Bears"), 4, 3, 39),
            (("Blightsteel Colossus",), 4, 4, 40),
        ):
            with self.subTest(names=names):
                game, cards = self.staged(728003 + len(names), names, amount=amount)
                self.enter_main(game)
                self.trigger(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((expected_rad, expected_life), (self.counters(game), game.state.players["A"].life))
                self.assertTrue(game.state.players["A"].in_game)
                if names == ("Blightsteel Colossus",):
                    self.assertEqual("library", game.state.cards[cards[0].object_id].zone)
                self.replay(game, load=True)

    def test_intervening_condition_and_count_are_live_at_resolution(self):
        for new_count in (0, 1, 3):
            with self.subTest(new_count=new_count):
                game, cards = self.staged(728010 + new_count, ("Grizzly Bears",) * 3, amount=2)
                self.enter_main(game)
                self.trigger(game)
                self.set_rad(game, new_count)
                self.checkpoint(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(new_count, sum(game.state.cards[card.object_id].zone == "graveyard" for card in cards))
                self.assertEqual((0, 40 - new_count), (self.counters(game), game.state.players["A"].life))
                self.replay(game, load=True)

    def test_legacy_checkpoint_without_marker_preserves_passive_counters(self):
        game, (top,) = self.staged(728020, ("Grizzly Bears",), amount=2)
        game.state.rad_trigger_version = None
        self.enter_main(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(("library", 2, 40),
                         (game.state.cards[top.object_id].zone, self.counters(game), game.state.players["A"].life))
        after = self.replay(game, load=True)
        self.assertNotIn(RAD_VERSION_FIELD, after.state.to_dict())
        self.assertEqual(authoritative_state_hash(game.state), authoritative_state_hash(after.state))


    def test_rad_provenance_accepts_omission_and_rejects_explicit_invalid_versions(self):
        state = self.session(728032).state
        current = state.to_dict()
        self.assertEqual(1, current[RAD_VERSION_FIELD])
        self.assertEqual(1, format_state_versions(state)[RAD_VERSION_FIELD])
        historical = {key: value for key, value in current.items() if key != RAD_VERSION_FIELD}
        resumed = GameState.from_dict(historical)
        self.assertIsNone(resumed.rad_trigger_version)
        self.assertNotIn(RAD_VERSION_FIELD, resumed.to_dict())
        self.assertNotIn(RAD_VERSION_FIELD, format_state_versions(resumed))
        legacy_format = format_state_versions(resumed)
        _validate_effect_versions(legacy_format, resumed)
        for invalid in (None, False, True, 0, 2, "1"):
            with self.subTest(version=invalid):
                with self.assertRaisesRegex(ValueError, "Unsupported inherent rad trigger version"):
                    GameState.from_dict({**historical, RAD_VERSION_FIELD: invalid})
                with self.assertRaisesRegex(ValueError, "Unsupported inherent rad trigger version"):
                    _validate_effect_versions({**legacy_format, RAD_VERSION_FIELD: invalid}, resumed)
        with self.assertRaisesRegex(ValueError, "provenance does not match"):
            _validate_effect_versions(legacy_format, state)
        with self.assertRaisesRegex(ValueError, "provenance does not match"):
            _validate_effect_versions(format_state_versions(state), resumed)

    def test_exact_rad_producer_and_forged_radiation_fail_closed(self):
        profile = "commander_review"
        record = replace(self.db.lookup("Dread Summons"), oracle_text="Each player gets four rad counters.", mana_cost="{2}{B}", keywords=())
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=profile)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        (node,) = ir.faces[0].nodes
        self.assertIn(RAD_CAPABILITY, node.capability_dependencies)
        dependencies = tuple(key for key in node.capability_dependencies if key != RAD_CAPABILITY)
        program = SemanticProgram(
            key="test-rad-producer", label="Dependency witness", effects=node.effects,
            capability_dependencies=dependencies, capability_closure=self.registry.closure(dependencies, profile=profile).to_dict(),
        )
        binding = bind_semantic_program_runtime(program, capability_registry=self.registry, profile=profile)
        self.assertIn(f"capability:undeclared_runtime_dependency:{RAD_CAPABILITY}", binding["blockers"])
        historic = {key: value for key, value in node.effects[0].items() if key != RAD_PRODUCER_FIELD}
        prior = bind_semantic_program_runtime(
            replace(program, effects=(historic,)), capability_registry=self.registry, profile=profile,
        )
        self.assertNotIn(f"capability:undeclared_runtime_dependency:{RAD_CAPABILITY}", prior["blockers"])
        game, _ = self.staged(728030, (), amount=2)
        self.enter_main(game)
        self.trigger(game)
        before = authoritative_state_hash(game.state)
        with self.assertRaisesRegex(GameRuleError, "missing its resolving stack"):
            game.engine.apply_effect(
                {"op": "life", "player": "A", "delta": -1, "cause": "radiation", RAD_EFFECT_FIELD: 1},
                actor="A", as_cost=False,
            )
        self.assertEqual(before, authoritative_state_hash(game.state))
        with patch("quorune.rad_counters._milled_nonland", return_value=False):
            mutated, cards = self.staged(728031, ("Grizzly Bears",), amount=1)
            self.enter_main(mutated)
            self.assertIsNone(self.resolve(mutated))
            self.assertEqual("graveyard", mutated.state.cards[cards[0].object_id].zone)
            self.assertEqual((1, 40), (self.counters(mutated), mutated.state.players["A"].life))
        confirmed, _ = self.staged(728031, ("Grizzly Bears",), amount=1)
        self.enter_main(confirmed)
        self.assertIsNone(self.resolve(confirmed))
        self.assertEqual((0, 39), (self.counters(confirmed), confirmed.state.players["A"].life))


class DefendingPlayerRadBlockTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Nightkin Ambusher"
    counters = staticmethod(RadCounterTests.counters)
    set_rad = staticmethod(RadCounterTests.set_rad)

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "defending-player-rad.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "committed-milled-creature-tokens-cards.json",
            fixtures / "source-attack-alone-cards.json",
            fixtures / "attacking-card-entry-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Blocking with rad counters", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def _act(self, game, actor="A", **command):
        response = game.act("pilot:" + actor, command)
        self.assertTrue(response.ok, response.summary)

    def _add_flash_mana(self, game):
        flash = self.add(game.engine, "Vedalken Orrery")
        self.assert_closed(game, flash)
        for index, name in enumerate(("Island", "Swamp", "Plains", "Plains", "Plains")):
            self.add(game.engine, name, ref=f"nightkin-flash-land-{index}")

    def _cast_and_target_for_rad(self, game, source, target):
        self.cast_for(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        before = self.counters(game, target)
        self._act(game, action_id="choose", targets=[target])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 4, self.counters(game, target))
        return game

    def _blocking_options(self, game, seat="B"):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        decision = game.packet("pilot:" + seat, full=True)["decision"]
        self.assertEqual("combat.blockers", decision["kind"])
        return decision["ctx"]["legal_blocks"]

    def _reject_block(self, game, blocker, target):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: target.ref}})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_original_static_restriction_keeps_siblings_and_rejects_wrong_role(self):
        original = self.db.lookup(self.source_name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, original.name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        restriction, = [node for face in ir.faces for node in face.nodes
                        if DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY in node.capability_dependencies]
        self.assertEqual(restriction.text, original.oracle_text[restriction.span.start:restriction.span.end])
        self.assertEqual(DEFENDING_PLAYER_RAD_BLOCK_TEMPLATE, restriction.template_id)
        self.assertEqual({"kind": "player_state", "player": "defending_player", "state": "rad_counter"},
                         restriction.handlers[0]["fragment"]["value"]["condition"])
        self.assertTrue(any(node.event == "permanent.enter.self" for face in ir.faces for node in face.nodes))
        self.assertTrue(any(node.exact and node.text.startswith("Ward {2}")
                            for face in ir.faces for node in face.nodes))
        program, = [program for program in semantics.programs_for_oracle(original.oracle_id)
                    if DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY in program.capability_dependencies]
        descriptor, = [deepcopy(thaw_value(handler)) for handler in program.handlers]
        descriptor["fragment"]["value"]["condition"]["player"] = "source_controller"
        with self.assertRaisesRegex(ValueError, "requires the actual defending player"):
            replace(program, handlers=(descriptor,))
        dependencies = tuple(value for value in program.capability_dependencies
                             if value != DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY)
        withdrawn = replace(program, capability_dependencies=dependencies,
                            capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        blockers = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry,
                                                 profile="commander_review")["blockers"]
        self.assertIn(f"capability:undeclared_runtime_dependency:{DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY}", blockers)
        for wording in ("attacking player has a rad counter", "defending player has exactly one rad counter"):
            corrupted = replace(original, oracle_text=original.oracle_text.replace(
                "defending player has a rad counter", wording))
            other = compile_oracle_card(corrupted, capability_registry=self.registry,
                                        capability_profile="commander_review")
            self.assertNotEqual("exact", other.status)

    def test_actual_player_planeswalker_and_battle_use_the_declared_defending_player(self):
        for kind in ("player", "planeswalker", "battle"):
            with self.subTest(kind=kind):
                game = self.session(72850900 + len(kind))
                source = self.add(game.engine, self.source_name)
                other = self.add(game.engine, "Grizzly Bears")
                blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="defending-blocker")
                for attacker in (source, other):
                    attacker.acquired_control_turn_count = -1
                self.assert_closed(game, source)
                self.set_rad(game, 1, "B")
                if kind == "player":
                    target = "B"
                elif kind == "planeswalker":
                    target, = game.engine.create_token("B", name="Test planeswalker", characteristics={
                        "type_line": "Token Planeswalker — Test", "loyalty": "5",
                    })
                else:
                    target, = game.engine.create_token("C", name="Test Siege", battle_protector="B", characteristics={
                        "type_line": "Token Battle — Siege", "defense": "5",
                    })
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(source, target), (other, "B")])
                context = game.state.combat.attack_target_context[source.object_id]
                self.assertEqual((kind, "B"), (context["kind"], context["defending_player"]))
                legal = self._blocking_options(game)
                self.assertEqual([other.ref], legal[blocker.ref])
                game = self.replay(game, load=True)
                self._reject_block(game, blocker, source)
                self._act(game, "B", action_id="block", blocks={blocker.ref: other.ref})
                self.assertNotIn(source.object_id, game.state.combat.blockers)
                self.assertEqual([blocker.object_id], game.state.combat.blockers[other.object_id])
                self.replay(game, load=True)

    def test_rad_gained_after_attack_changes_the_live_offer_and_command(self):
        game = self.session(72850911)
        source = self.add(game.engine, self.source_name)
        entrant = self.add(game.engine, self.source_name, zone="hand", ref="flashed-rad-entrant")
        other = self.add(game.engine, "Grizzly Bears")
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="live-blocker")
        for attacker in (source, other):
            attacker.acquired_control_turn_count = -1
        self._add_flash_mana(game)
        self.set_rad(game, 1, "C")
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B"), (other, "B")])
        self.assertEqual(0, self.counters(game, "B"))
        self.assertTrue(game.engine._can_block(source, blocker)[0])
        game = self._cast_and_target_for_rad(game, entrant, "B")
        self.assertEqual([other.ref], self._blocking_options(game)[blocker.ref])
        self.assertFalse(game.engine._can_block(source, blocker)[0])
        with patch("quorune.declaration_condition_runtime.defending_player_has_rad_counter", return_value=False):
            with self.assertRaises(AssertionError):
                self.assertFalse(game.engine._can_block(source, blocker)[0])
        self._reject_block(game, blocker, source)
        self._act(game, "B", action_id="block", blocks={blocker.ref: other.ref})
        self.replay(game, load=True)

    def test_native_ward_and_entry_then_late_rad_preserves_the_committed_block(self):
        game = self.session(72850912)
        source = self.add(game.engine, self.source_name, zone="hand")
        later = self.add(game.engine, self.source_name, zone="hand", ref="late-rad-entrant")
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="committed-blocker")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        fervor = self.add(game.engine, "Fervor")
        self._add_flash_mana(game)
        for card in (source, bounce, fervor):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 1, "B": 1})
        game.state.players["B"].mana_pool.update(U=1, C=2)
        self.checkpoint(game)
        game = self._cast_and_target_for_rad(game, source, "C")
        self.cast_for(game, "B", bounce, [source.ref], reserve={"C": 2})
        ward, = [item for item in game.state.stack if item.semantic_key == "builtin:ward"]
        self.assertEqual(2, WardSpec.from_dict(ward.context["ward_spec"]).generic_cost)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("ward_counter_unless_pay", pending.payload_by_actor["B"]["operation"])
        game = self.replay(game, load=True)
        self._act(game, "B", action_id="choose", pay=False)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[bounce.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)

        self.attack(game, [(game.state.cards[source.object_id], "B")])
        self.assertEqual([source.ref], self._blocking_options(game)[blocker.ref])
        self._act(game, "B", action_id="block", blocks={blocker.ref: source.ref})
        expected = {source.object_id: [blocker.object_id]}
        self.assertEqual(expected, game.state.combat.blockers)
        game = self._cast_and_target_for_rad(game, later, "B")
        self.assertEqual(expected, game.state.combat.blockers)
        self.assertTrue(game.state.combat.blockers_declared)
        self.replay(game, load=True)
