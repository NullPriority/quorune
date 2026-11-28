from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session, pass_current


class MulliganAndTurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_multiplayer_first_mulligan_is_free_and_redraws_seven(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=11)
        self.assertTrue(session.act("pilot:A", {"a": "mulligan"}).ok)
        for seat in "BCD":
            self.assertTrue(session.act(f"pilot:{seat}", {"a": "keep"}).ok)
        player = session.state.players["A"]
        self.assertEqual(1, player.mulligans_taken)
        self.assertEqual(7, len(player.zones["hand"]))
        packet = session.packet("pilot:A")
        self.assertEqual(1, packet["decision"]["ctx"]["if_mulligan"]["bottom"])
        self.assertEqual(6, packet["decision"]["ctx"]["if_mulligan"]["resulting_hand_size"])
        self.assertIn("KEEP any functional hand", packet["decision"]["ctx"]["decision_policy"])

    def test_second_hand_can_be_kept_at_seven(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=12)
        session.act("pilot:A", {"a": "mulligan"})
        for seat in "BCD":
            session.act(f"pilot:{seat}", {"a": "keep"})
        self.assertTrue(session.act("pilot:A", {"a": "keep"}).ok)
        self.assertEqual(7, len(session.state.players["A"].zones["hand"]))
        self.assertTrue(session.state.started)

    def test_second_counted_mulligan_bottoms_one(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=13)
        session.act("pilot:A", {"a": "mulligan"})
        for seat in "BCD":
            session.act(f"pilot:{seat}", {"a": "keep"})
        session.act("pilot:A", {"a": "mulligan", "override_reason": "Test the counted-mulligan path"})
        packet = session.packet("pilot:A")
        self.assertEqual("mulligan.bottom", packet["decision"]["kind"])
        self.assertEqual(1, packet["decision"]["ctx"]["count"])
        card = packet["decision"]["ctx"]["hand"][0]["id"]
        self.assertTrue(session.act("pilot:A", {"a": "bottom", "cs": [card]}).ok)
        self.assertEqual(6, len(session.state.players["A"].zones["hand"]))
        self.assertTrue(session.act("pilot:A", {"a": "keep"}).ok)
        self.assertEqual(6, len(session.state.players["A"].zones["hand"]))

    def test_two_counted_mulligans_accept_typed_card_ids(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=131)
        self.assertTrue(session.act("pilot:A", {"a": "keep"}).ok)
        self.assertTrue(session.act("pilot:B", {"a": "keep"}).ok)
        self.assertTrue(session.act("pilot:C", {"a": "mulligan"}).ok)
        self.assertTrue(session.act("pilot:D", {"a": "mulligan"}).ok)

        for seat in "CD":
            self.assertTrue(
                session.act(
                    f"pilot:{seat}",
                    {
                        "a": "mulligan",
                        "override_reason": "Exercise simultaneous bottoming",
                    },
                ).ok
            )

        self.assertEqual(
            ["pilot:C", "pilot:D"],
            session.pending_principals(),
        )
        for seat in "CD":
            packet = session.packet(f"pilot:{seat}")
            card = packet["decision"]["ctx"]["hand"][0]["id"]
            result = session.act(
                f"pilot:{seat}",
                {
                    "action_id": "bottom",
                    "card_ids": [card],
                },
            )
            self.assertTrue(result.ok, result.summary)

        self.assertEqual(6, len(session.state.players["C"].zones["hand"]))
        self.assertEqual(6, len(session.state.players["D"].zones["hand"]))
        self.assertEqual(["pilot:C"], session.pending_principals())


    def test_functional_post_free_hand_requires_override_to_mulligan_again(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=100)
        # Force the first free redraw, then select a seed until the packet marks
        # the new hand functional. This seed is deterministic for the bundled lists.
        session.act("pilot:A", {"a": "mulligan"})
        for seat in "BCD":
            session.act(f"pilot:{seat}", {"a": "keep"})
        packet = session.packet("pilot:A")
        self.assertTrue(packet["decision"]["ctx"]["signals"]["functional_baseline"])
        rejected = session.act("pilot:A", {"a": "mulligan"})
        self.assertFalse(rejected.ok)
        self.assertIn("functional baseline", rejected.summary)
        accepted = session.act("pilot:A", {"a": "mulligan", "override_reason": "No access to a commander color and no turn-two plan"})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_mulligan_declarations_follow_turn_order_then_redraw_together(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=14)
        self.assertEqual(["pilot:A"], session.pending_principals())
        self.assertTrue(session.act("pilot:A", {"a": "mulligan"}).ok)
        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertTrue(session.act("pilot:B", {"a": "keep"}).ok)
        self.assertEqual(["pilot:C"], session.pending_principals())
        self.assertTrue(session.act("pilot:C", {"a": "mulligan"}).ok)
        self.assertEqual(["pilot:D"], session.pending_principals())
        self.assertTrue(session.act("pilot:D", {"a": "keep"}).ok)
        # Redraws happen only after the final declaration in the round.
        self.assertEqual(1, session.state.players["A"].mulligans_taken)
        self.assertEqual(1, session.state.players["C"].mulligans_taken)
        self.assertEqual(7, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(7, len(session.state.players["C"].zones["hand"]))
        self.assertEqual(["pilot:A"], session.pending_principals())
        self.assertFalse(session.state.started)

    def test_first_player_draws_on_first_multiplayer_turn(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=15)
        keep_all(session)
        self.assertEqual(("beginning", "upkeep"), (session.state.phase, session.state.step))
        for _ in range(4):
            pass_current(session)
        self.assertEqual(("beginning", "draw"), (session.state.phase, session.state.step))
        self.assertEqual(8, len(session.state.players["A"].zones["hand"]))

    def test_priority_rotates_all_four_seats(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=16)
        keep_all(session)
        observed = []
        for _ in range(4):
            observed.append(session.pending_principals()[0])
            pass_current(session)
        self.assertEqual(["pilot:A", "pilot:B", "pilot:C", "pilot:D"], observed)

    def test_yields_skip_redundant_priority_windows(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=17)
        keep_all(session)
        pass_current(session)
        pass_current(session, yield_mode="until_my_turn")
        pass_current(session, yield_mode="until_my_turn")
        pass_current(session, yield_mode="until_my_turn")
        # Draw step gives A priority; B/C/D will be auto-passed after A passes.
        self.assertEqual("pilot:A", session.pending_principals()[0])
        pass_current(session)
        self.assertEqual(("precombat_main", "main"), (session.state.phase, session.state.step))
        self.assertEqual(["pilot:A"], session.pending_principals())

    def test_empty_known_priority_windows_are_skipped_without_model_calls(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=171, auto_pass_empty=True)
        engine = session.engine
        # Give every player a lands-only opening hand.  The command-zone cards
        # are not castable during upkeep, so the entire opening upkeep priority
        # round has no action represented by the implemented grammar.
        for seat in "ABCD":
            player = session.state.players[seat]
            hand = list(player.zones["hand"])
            land_candidates = [
                oid for oid in player.zones["library"]
                if (
                    engine.card_record(oid)
                    and engine.card_record(oid).is_land
                    and not any(
                        "hand" in ability.zones and not ability.mana_ability
                        for ability in engine._activated_abilities(engine.state.cards[oid])
                    )
                )
            ][: len(hand)]
            self.assertEqual(len(hand), len(land_candidates))
            for old, new in zip(hand, land_candidates):
                hand_index = player.zones["hand"].index(old)
                lib_index = player.zones["library"].index(new)
                player.zones["hand"][hand_index] = new
                player.zones["library"][lib_index] = old
                session.state.cards[new].zone = "hand"
                session.state.cards[old].zone = "library"
        keep_all(session)
        self.assertNotEqual("upkeep", session.state.step)
        self.assertEqual(1, session.state.turn_sequence)


if __name__ == "__main__":
    unittest.main()


class SerumPowderTests(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from pathlib import Path

        from common import ROOT
        from quorune.carddb import CardDatabase
        from scripts.build_test_database import build_fixture_database

        self.scratch = self.enterContext(tempfile.TemporaryDirectory())
        pinned = os.environ.get("SERUM_POWDER_PINNED_DB")
        path = Path(self.scratch) / "serum.sqlite3"
        if not pinned:
            build_fixture_database([
                ROOT / "tests/fixtures/opening-hand-battlefield-cards.json",
                ROOT / "tests/fixtures/activation-result-views-cards.json",
            ], path)
        self.db = CardDatabase(pinned or path)
        self.addCleanup(self.db.close)

    def _session(self, cards_by_seat, *, size=2, seed=319):
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.model import GameConfig
        from quorune.session import CommanderSession

        decks = {
            seat: DeckDefinition(
                "Mulligan permission " + seat,
                [DeckEntry("Isamaru, Hound of Konda", 1, "commander")]
                + [DeckEntry(name, count) for name, count in cards_by_seat.get(seat, [("Plains", 10)])],
                ["Isamaru, Hound of Konda"],
            )
            for seat in "ABC"
        }
        return CommanderSession.create(
            self.db, decks, first_player="A", seed=seed,
            config=GameConfig(opening_hand_size=size, auto_pass_empty_priority=False,
                              realistic_mulligan_guard=False),
        )

    def _act(self, session, seat, action, **fields):
        result = session.act("pilot:" + seat, {"action_id": action, **fields})
        self.assertTrue(result.ok, result.summary)

    def _assert_replay(self, session):
        from pathlib import Path

        from quorune.record import replay_record
        from quorune.session import CommanderSession

        path = Path(self.scratch) / "record"
        session.save(path)
        packed = {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}
        proof = replay_record(path, self.db)
        self.assertTrue(proof["ok"], proof)
        self.assertEqual(len(session.commands), proof["commands"])
        loaded = CommanderSession.load(self.db, path)
        self.assertEqual(session.state.pending_decision.to_dict(),
                         loaded.state.pending_decision.to_dict())
        self.assertEqual(packed, {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()})

    def test_serum_redraw_private_choice_keeps_interleaved_declarations_and_replays(self):
        import json

        from quorune.rules.mulligan_redraw import MULLIGAN_REDRAW_ACTION, MULLIGAN_REDRAW_OFFER

        session = self._session({"B": [("Serum Powder", 3), ("Plains", 2)]})
        first = session.state.pending_decision
        self.assertEqual(["keep", "mulligan"], first.allowed_actions)
        self.assertEqual({"round", "actors", "index", "mulliganers"}, set(first.continuation))
        self._act(session, "A", "mulligan")
        pending = session.state.pending_decision
        player = session.state.players["B"]
        hand = tuple(player.zones["hand"])
        library = tuple(player.zones["library"])
        source = next(session.state.cards[oid] for oid in hand
                      if session.state.cards[oid].printed_name == "Serum Powder")
        self.assertEqual(["B"], pending.actors)
        self.assertEqual({"keep", "mulligan", MULLIGAN_REDRAW_ACTION}, set(pending.allowed_actions))
        self.assertEqual(source.ref, pending.continuation[MULLIGAN_REDRAW_OFFER]["source_ref"])
        own = json.dumps(session.packet("pilot:B", full=True))
        foreign = json.dumps(session.packet("pilot:C", full=True))
        self.assertIn(MULLIGAN_REDRAW_ACTION, own)
        self.assertNotIn(MULLIGAN_REDRAW_ACTION, foreign)
        self.assertNotIn(source.ref, foreign)
        before = session.state.to_dict()
        rejected = session.act("pilot:A", {"action_id": MULLIGAN_REDRAW_ACTION})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, session.state.to_dict())

        from unittest.mock import patch

        stale_offer = {**pending.continuation[MULLIGAN_REDRAW_OFFER], "source_logical_object_id": "stale"}
        with patch("quorune.rules.mulligan_redraw.hand_redraw_offer", return_value=stale_offer):
            rejected = session.act("pilot:B", {"action_id": MULLIGAN_REDRAW_ACTION})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, session.state.to_dict())
        self._act(session, "B", MULLIGAN_REDRAW_ACTION)
        player = session.state.players["B"]
        self.assertEqual(list(hand), player.zones["exile"])
        self.assertTrue(all(not session.state.cards[oid].face_down for oid in hand))
        self.assertEqual(list(reversed(library[-2:])), player.zones["hand"])
        self.assertEqual(list(library[:-2]), player.zones["library"])
        self.assertTrue(all(session.state.cards[oid].ref not in json.dumps(
            session.packet("pilot:C", full=True)) for oid in player.zones["hand"]))
        self.assertEqual(0, session.state.players["A"].mulligans_taken)
        self.assertEqual(0, player.mulligans_taken)
        declaration = session.state.pending_decision
        self.assertEqual(["B"], declaration.actors)
        for key in ("index", "round", "actors", "mulliganers"):
            self.assertEqual(pending.continuation[key], declaration.continuation[key])
        self._act(session, "B", "keep")
        self._act(session, "C", "keep")
        self.assertEqual(1, session.state.players["A"].mulligans_taken)
        self.assertEqual(["pilot:A"], session.pending_principals())
        self.assertEqual(list(hand), player.zones["exile"])
        self._assert_replay(session)

    def test_serum_redraw_retains_counted_london_penalty(self):
        from quorune.rules.mulligan_redraw import MULLIGAN_REDRAW_ACTION

        session = self._session({"B": [("Serum Powder", 6), ("Plains", 1)]})
        self._act(session, "A", "keep")
        self._act(session, "B", "mulligan")
        self._act(session, "C", "keep")
        player = session.state.players["B"]
        self.assertEqual((1, 0), (player.mulligans_taken, player.mulligan_penalty))
        self._act(session, "B", "mulligan")
        decision = session.state.pending_decision
        self.assertEqual("mulligan.bottom", decision.kind)
        self.assertNotIn(MULLIGAN_REDRAW_ACTION, decision.allowed_actions)
        candidates = [session.state.cards[oid] for oid in player.zones["hand"]]
        bottom = next((card for card in candidates if card.printed_name == "Plains"), candidates[0])
        self._act(session, "B", "bottom", card_ids=[bottom.ref])
        hand = tuple(player.zones["hand"])
        self.assertEqual(1, len(hand))
        self.assertIn(MULLIGAN_REDRAW_ACTION, session.state.pending_decision.allowed_actions)
        self._act(session, "B", MULLIGAN_REDRAW_ACTION)
        self.assertEqual((2, 1), (player.mulligans_taken, player.mulligan_penalty))
        self.assertEqual(list(hand), player.zones["exile"])
        self.assertEqual(1, len(player.zones["hand"]))
        self.assertIn(bottom.object_id, player.zones["library"])

        self._act(session, "B", "mulligan")
        self.assertEqual((3, 2), (player.mulligans_taken, player.mulligan_penalty))
        self.assertEqual("mulligan.bottom", session.state.pending_decision.kind)
        second_bottom = [session.state.cards[oid].ref for oid in player.zones["hand"]]
        self._act(session, "B", "bottom", card_ids=second_bottom)
        self.assertEqual(list(hand), player.zones["exile"])
        self._act(session, "B", "keep")
        self.assertTrue(session.state.started)
        self._assert_replay(session)

    def test_serum_redraw_repeats_and_records_finite_library(self):
        from quorune.rules.mulligan_redraw import MULLIGAN_REDRAW_ACTION

        session = self._session({"A": [("Serum Powder", 4)]})
        player = session.state.players["A"]
        original = tuple(player.zones["hand"])
        remaining = tuple(reversed(player.zones["library"]))
        self._act(session, "A", MULLIGAN_REDRAW_ACTION)
        self.assertEqual(list(remaining), player.zones["hand"])
        self._act(session, "A", MULLIGAN_REDRAW_ACTION)
        self.assertEqual(list(original + remaining), player.zones["exile"])
        self.assertFalse(player.zones["hand"])
        self.assertFalse(player.zones["library"])
        self.assertTrue(player.attempted_empty_draw)
        self.assertEqual(0, player.mulligans_taken)
        self.assertNotIn(MULLIGAN_REDRAW_ACTION, session.state.pending_decision.allowed_actions)
        self._act(session, "A", "keep")
        self._assert_replay(session)

    def test_serum_powder_retains_native_fixed_colorless_mana(self):
        session = self._session({"A": [("Serum Powder", 1), ("Plains", 1)]})
        for seat in "ABC":
            self._act(session, seat, "keep")
        player = session.state.players["A"]
        source = next(session.state.cards[oid] for oid in player.zones["hand"]
                      if session.state.cards[oid].printed_name == "Serum Powder")
        session.engine.move_card(source.object_id, "battlefield")
        current = session.state.cards[source.object_id]
        ability, = session.engine._activated_abilities(current)
        self.assertTrue(ability.mana_ability)
        result = session.act("pilot:A", {"a": "activate", "source": current.ref, "ability": ability.ability_id})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(current.tapped)
        self.assertEqual({"C": 1}, {k: v for k, v in player.mana_pool.items() if v})

    def test_serum_powder_compiler_requires_exact_complete_hand_permission(self):
        import json
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import compile_oracle_card
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.semantic_runtime.action_permissions import (
            MULLIGAN_REDRAW_CAPABILITY, default_action_permission_registry,
        )
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.semantics import SemanticRegistry

        record = self.db.lookup("Serum Powder", fuzzy=False)
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (value for value in ir.faces[0].nodes if value.event == "action.permission")
        self.assertEqual(record.oracle_text[node.span.start:node.span.end], node.text)
        self.assertEqual("hand", node.active_zone)
        self.assertIn(MULLIGAN_REDRAW_CAPABILITY, node.capability_dependencies)
        descriptor, = node.handlers
        for fields in ({"requires_complete_card_program": False}, {"schema_version": True}, {"extra": True}):
            with self.subTest(fields=fields), self.assertRaises(SemanticNodeError):
                default_action_permission_registry().validate({**descriptor, **fields})
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=registry, capability_profile="commander_review",
        )
        binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        for old, new in (("this card", "another card"), ("all the cards", "a card"),
                         ("then draw that many cards", "then draw six cards")):
            malformed = replace(record, oracle_text=record.oracle_text.replace(old, new),
                                faces=tuple({**face, "oracle_text": face["oracle_text"].replace(old, new)}
                                            for face in record.faces))
            self.assertNotEqual("exact", compile_oracle_card(malformed, capability_registry=registry).status)
        with patch("quorune.compiler.runtime_templates.mulligan_redraw_permission_handler", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=registry).status)
        for capability in (MULLIGAN_REDRAW_CAPABILITY, "zone.change.destination_replacement",
                           "zone.draw.library_to_hand"):
            with self.subTest(withdrawn=capability):
                raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
                next(row for row in raw["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Capability withdrawn in admission witness"],
                )
                withdrawn = CapabilityRegistry(raw)
                withdrawn.mark_evidence_verified("0" * 64)
                self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=withdrawn).status)
