"""CR 608.2b and 701.24c. One legal target fixes the library owner."""

from copy import deepcopy
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from common import ROOT, keep_all
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


FIXTURES = ROOT / "tests" / "fixtures"
SOURCE = "Chaos Warp"
PERMANENT_SEED = 170100113
NONPERMANENT_SEED = 170100115
LIBRARY_NAMES = ("Generic Bound Mentor", "Generic Bound Draw Loss", "Generic Bound Counter Adder")
REVEAL = {"library.top.reveal", "library.reveal"}


class TargetOwnerShuffleTopNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / "owner-shuffle-top.sqlite3"
        build_fixture_database(
            [
                FIXTURES / "bound-effect-program-cards.json",
                FIXTURES / "mutate-casting-cards.json",
                FIXTURES / "reveal-entry-cards.json",
                FIXTURES / "target-owner-shuffle-top-cards.json",
            ],
            path,
        )
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Target owner shuffle witness",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def scene(self, seed, *, target_kind="card", names=LIBRARY_NAMES, response=False, aura_recipients=False):
        game = CommanderSession.create(
            self.db,
            {seat: deepcopy(self.deck) for seat in "ABCD"},
            first_player="A",
            seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        keep_all(game)
        game.state.config.semantic_policy = "trusted_only"
        engine = game.engine
        owner = game.state.players["B"]
        for oid in tuple(owner.zones["library"]):
            engine.move_card(oid, "outside", log=False, semantic_events=False)
        library = []
        for index, name in enumerate(names):
            card = self.add(engine, name, seat="B", zone="library", ref=f"warp-library-{index}")
            card.known_to = []
            card.revealed_to = []
            library.append(card)
        if target_kind == "commander":
            oid, = owner.zones["command"]
            target = game.state.cards[oid]
            engine.move_card(oid, "battlefield", log=False, semantic_events=False)
            self.assertTrue(target.is_commander)
        elif target_kind == "token":
            engine.create_token(
                "B", name="Owner Shuffle Wolf", reason="fixture target",
                characteristics={"type_line": "Token Creature — Wolf", "power": "1", "toughness": "1"},
            )
            target, = [card for card in game.state.cards.values() if card.printed_name == "Owner Shuffle Wolf"]
            self.assertTrue(target.is_token)
        elif target_kind == "composition":
            target = self.compose_target(game)
        else:
            target = self.add(engine, "Generic Bound Body", seat="B", ref="warp-stolen-target")
        engine.change_control(target.object_id, "C", reason="fixture different controller")
        self.assertEqual(("B", "C", "battlefield"), (target.owner, target.controller, target.zone))
        spell = self.add(engine, SOURCE, seat="A", zone="hand", ref="warp-normal-cast")
        bounce = self.add(engine, "Unsummon", seat="C", zone="hand", ref="warp-target-removal") if response else None
        if bounce is not None:
            game.state.players["C"].mana_pool["U"] = 1
            self.assert_closed(game, bounce)
        if aura_recipients:
            for seat in ("A", "C"):
                self.add(engine, "Generic Bound Body", seat=seat, ref=f"warp-aura-recipient-{seat}")
        self.prepare(game, actor="A", active="D", mana={"C": 2, "R": 1})
        self.checkpoint(game)
        return game, spell, target, library, bounce

    def compose_target(self, game):
        engine = game.engine
        target = self.add(engine, "Grizzly Bears", seat="B", ref="warp-composition-root")
        part = self.add(engine, "Gemrazer", seat="B", zone="hand", ref="warp-composition-member")
        self.assert_closed(game, part)
        self.prepare(game, actor="B", active="B", mana={"G": 2, "C": 1})
        action = self.action(game, "B", part)
        cost, = [row["id"] for row in action["cost_options"] if row["id"].startswith("mutate")]
        result = game.act("pilot:B", {
            "action_id": action["id"], "cost_option": cost, "targets": [target.ref], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ("B",)), (pending.kind, tuple(pending.actors)))
        result = game.act("pilot:B", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        target = game.state.cards[target.object_id]
        self.assertEqual(
            (target.object_id, part.object_id), tuple(target.permanent_composition.topology.physical_object_ids),
        )
        self.assertNotIn(part.object_id, game.state.cards)
        return target

    @staticmethod
    def counts(game):
        return {seat: int(game.state.players[seat].stats.get("shuffle_count", 0)) for seat in "ABCD"}

    def shuffle_control(self, game, library, target, *, expected_index, arrivals=None):
        # Derive the expected permutation from the fixture library and shuffle seed.
        ids = [card.object_id for card in library]
        ids.extend((target.object_id,) if arrivals is None else arrivals)
        expected_top = ids[expected_index]
        count = self.counts(game)["B"] + 1
        Random(f"{game.state.config.seed}|B|shuffle|{count}").shuffle(ids)
        self.assertEqual(expected_top, ids[-1])
        self.assertNotEqual(library[-1].object_id, ids[-1])
        return ids

    def announce(self, game, spell, target):
        ir = compile_oracle_card(self.db.lookup(SOURCE, fuzzy=False), capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assert_closed(game, spell)
        program, = [
            row for row in game.engine.semantics.programs_for_oracle(spell.oracle_id)
            if row.key == f"{spell.oracle_id}:spell:front"
        ]
        self.assertEqual("target-permanent-shuffle-reveal-v1", program.provenance.get("template_id"))
        self.assertEqual(
            ["shuffle_into_library", "target_permanent_shuffle_reveal"],
            [effect["op"] for effect in program.effects],
        )
        self.assertTrue(game.engine.semantic_program_is_current_trusted(program))
        self.cast_at_target(game, spell, target)

    def cast_at_target(self, game, spell, target):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [action for action in actions if action.get("card") == spell.ref]
        self.assertIn(target.ref, offered["target_schema"]["legal_refs"])
        self.assert_rejects(game, "A", {"action_id": offered["id"], "targets": ["B"], "pay": "auto"})
        accepted = game.act("pilot:A", {"action_id": offered["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(target.ref, game.state.stack[-1].targets[0])

    def assert_rejects(self, game, actor, action):
        before = authoritative_state_hash(game.state)
        commands = len(game.commands)
        result = game.act("pilot:" + actor, action)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(commands, len(game.commands))

    def pass_until(self, game, seat):
        desired = "pilot:" + seat
        for _ in range(len(game.state.turn_order)):
            self.assertEqual("priority", game.state.pending_decision.kind)
            actor, = game.pending_principals()
            if actor == desired:
                return
            reply = game.act(actor, {"action_id": "pass"})
            self.assertTrue(reply.ok, reply.summary)
        self.fail(f"Priority did not reach {desired}")

    @staticmethod
    def new_events(game, start):
        return [(event.event_id, event) for event in game.state.events if event.event_id > start]

    def assert_shuffle_and_reveal(self, game, *, before, start, revealed=None, private=()):
        expected = {**before, "B": before["B"] + 1}
        self.assertEqual(expected, self.counts(game))
        shuffles = [(index, event) for index, event in self.new_events(game, start) if event.code == "library.shuffle"]
        self.assertEqual(1, len(shuffles))
        shuffle_index, shuffle = shuffles[0]
        self.assertEqual("B", shuffle.actor)
        reveals = [(index, event) for index, event in self.new_events(game, start) if event.code in REVEAL]
        if revealed is None:
            self.assertEqual([], reveals)
            return
        self.assertEqual(1, len(reveals))
        reveal_index, event = reveals[0]
        self.assertLess(shuffle_index, reveal_index)
        self.assertEqual("B", event.actor)
        self.assertIn(revealed.printed_name, event.summary)
        self.assert_public_reveal(game, revealed, private)

    def assert_public_reveal(self, game, revealed, private):
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            cursor = "warp-audit-" + principal
            game.drop_projection_cursor(cursor)
            packet = game.packet(principal, full=True, cursor_key=cursor)
            public, = [row for row in packet.get("events", []) if row["c"] in REVEAL]
            self.assertIn(revealed.printed_name, public["s"])
            for hidden in private:
                self.assertNotIn(hidden.ref, json.dumps(packet))

    def assert_no_resolution_effects(self, game, *, spell, before, start):
        self.assertEqual(before, self.counts(game))
        self.assertFalse(any(event.code == "library.shuffle" or event.code in REVEAL
                             for _, event in self.new_events(game, start)))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_normal_cast_reveals_owners_current_permanent_instead_of_previous_top(self):
        game, spell, target, library, _ = self.scene(PERMANENT_SEED)
        order = self.shuffle_control(game, library, target, expected_index=0)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        revealed = library[0]
        self.assertEqual(order[:-1], game.state.players["B"].zones["library"])
        self.assertEqual("library", game.state.cards[target.object_id].zone)
        current = game.state.cards[revealed.object_id]
        self.assertEqual(("battlefield", "B", "B"), (current.zone, current.owner, current.controller))
        self.assert_shuffle_and_reveal(game, before=counts, start=start, revealed=revealed, private=library[1:])
        self.replay(game, load=True)

    def test_shuffled_nonpermanent_is_public_and_stays_current_top(self):
        game, spell, target, library, _ = self.scene(NONPERMANENT_SEED)
        order = self.shuffle_control(game, library, target, expected_index=1)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        self.assertIsNone(self.resolve(game))
        revealed = library[1]
        self.assertEqual(order, game.state.players["B"].zones["library"])
        self.assertEqual("library", game.state.cards[revealed.object_id].zone)
        self.assert_shuffle_and_reveal(
            game, before=counts, start=start, revealed=revealed, private=[library[0], library[2]],
        )
        self.replay(game, load=True)

    def test_normal_response_removes_the_target_and_prevents_any_owner_shuffle(self):
        game, spell, target, library, bounce = self.scene(170100126, response=True)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        game = self.replay(game, load=True)
        self.pass_until(game, "C")
        actions = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [action for action in actions if action.get("card") == bounce.ref]
        accepted = game.act("pilot:C", {"action_id": offered["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        returned = game.state.cards[target.object_id]
        self.assertEqual(("hand", "B"), (returned.zone, returned.owner))
        self.assertEqual([card.object_id for card in library], game.state.players["B"].zones["library"])
        self.assert_no_resolution_effects(game, spell=spell, before=counts, start=start)

    def test_owner_departure_removes_the_only_target_before_resolution(self):
        game, spell, target, _, _ = self.scene(170100127)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        self.pass_until(game, "B")
        gone = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(gone.ok, gone.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertNotIn(target.object_id, game.state.players["C"].zones["battlefield"])
        self.assertIsNone(self.resolve(game))
        self.assert_no_resolution_effects(game, spell=spell, before=counts, start=start)

    def test_owner_commander_replacement_still_shuffles_an_empty_library(self):
        game, spell, target, library, _ = self.scene(170100128, target_kind="commander", names=())
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ("B",)), (pending.kind, tuple(pending.actors)))
        self.assertEqual(counts, self.counts(game))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        packet = game.packet("pilot:B", full=True)
        replacement, = [option for option in packet["decision"]["ctx"]["options"] if not option.get("decline")]
        choose = {"action_id": "choose", "replacement": replacement["id"]}
        self.assert_rejects(game, "C", choose)
        accepted = game.act("pilot:B", choose)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("command", game.state.cards[target.object_id].zone)
        self.assertEqual([card.object_id for card in library], game.state.players["B"].zones["library"])
        self.assert_shuffle_and_reveal(game, before=counts, start=start)
        self.replay(game, load=True)

    def test_token_joins_shuffle_but_cannot_return_from_library(self):
        game, spell, target, library, _ = self.scene(PERMANENT_SEED, target_kind="token")
        order = self.shuffle_control(game, library, target, expected_index=0)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        self.assertIsNone(self.resolve(game))
        remaining = [oid for oid in order[:-1] if oid != target.object_id]
        self.assertEqual(remaining, game.state.players["B"].zones["library"])
        self.assertEqual("battlefield", game.state.cards[library[0].object_id].zone)
        self.assertTrue(all(target.object_id not in player.zones["battlefield"] for player in game.state.players.values()))
        self.assert_shuffle_and_reveal(game, before=counts, start=start, revealed=library[0], private=library[1:])
        self.replay(game, load=True)

    def test_token_on_exact_top_is_never_a_permanent_card_entry(self):
        game, spell, target, library, _ = self.scene(170100110, target_kind="token")
        order = self.shuffle_control(game, library, target, expected_index=3)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(order[:-1], game.state.players["B"].zones["library"])
        for card in library:
            self.assertEqual("library", game.state.cards[card.object_id].zone)
        self.assertTrue(all(target.object_id not in player.zones["battlefield"] for player in game.state.players.values()))
        self.assert_shuffle_and_reveal(game, before=counts, start=start, revealed=target, private=library)
        self.replay(game, load=True)

    def test_composed_permanent_orders_both_owner_cards_privately_before_shuffle(self):
        game, spell, target, library, _ = self.scene(170100122, target_kind="composition")
        first, second = target.permanent_composition.parts
        members = (second, first)
        refs = [part.card_state["ref"] for part in members]
        ids = [part.identity.object_id for part in members]
        self.assertEqual(["B", "B"], [part.card_state["owner"] for part in members])
        order = self.shuffle_control(game, library, target, expected_index=0, arrivals=ids)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ("B",)), (pending.kind, tuple(pending.actors)))
        remaining, = pending.continuation["remaining"]
        self.assertEqual(("target_permanent_shuffle_reveal", "B"), (remaining["op"], remaining["player"]))
        context = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual("composition_departure_order", context["operation"])
        choice, = context["legal_actions"]
        self.assertEqual("bottom_to_top", choice["choice_schema"]["order"])
        self.assertEqual(set(refs), set(choice["choice_schema"]["legal_refs"]))
        for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertIsNone(game.packet(principal, full=True)["decision"])
        self.assertEqual(counts, self.counts(game))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assertFalse(any(event.code in REVEAL for _, event in self.new_events(game, start)))
        game = self.replay(game, load=True)
        self.assertEqual([remaining], game.state.pending_decision.continuation["remaining"])
        submit = {"action_id": choice["id"], "cards": refs}
        self.assert_rejects(game, "C", submit)
        self.assert_rejects(game, "B", {**submit, "cards": [refs[0], refs[0]]})
        result = game.act("pilot:B", submit)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(order[:-1], game.state.players["B"].zones["library"])
        self.assertEqual("battlefield", game.state.cards[library[0].object_id].zone)
        for oid in ids:
            current = game.state.cards[oid]
            self.assertEqual(("library", "B", "B"), (current.zone, current.owner, current.controller))
            self.assertIsNone(current.permanent_composition)
        self.assert_shuffle_and_reveal(game, before=counts, start=start, revealed=library[0], private=library[1:])
        self.replay(game, load=True)

    def test_revealed_game_trail_preserves_owners_private_entry_replacement(self):
        game, spell, target, library, _ = self.scene(
            PERMANENT_SEED, names=("Game Trail", *LIBRARY_NAMES[1:]),
        )
        land = library[0]
        mountain = self.add(game.engine, "Mountain", seat="B", zone="hand", ref="warp-entry-eligible")
        island = self.add(game.engine, "Island", seat="B", zone="hand", ref="warp-entry-ineligible")
        foreign = self.add(game.engine, "Forest", seat="C", zone="hand", ref="warp-entry-other-seat")
        self.checkpoint(game)
        order = self.shuffle_control(game, library, target, expected_index=0)
        start, counts = game.state.event_sequence, self.counts(game)
        self.announce(game, spell, target)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ("B",)), (pending.kind, tuple(pending.actors)))
        self.assertEqual(order, game.state.players["B"].zones["library"])
        choices = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        selected, = [row["id"] for row in choices if mountain.ref in row["id"]]
        self.assertFalse(any(island.ref in row["id"] or foreign.ref in row["id"] for row in choices))
        for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(principal, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(mountain.ref, json.dumps(packet))
        self.assert_shuffle_and_reveal(game, before=counts, start=start, revealed=land, private=library[1:])
        game = self.replay(game, load=True)
        submit = {"action_id": "choose", "replacement": selected}
        self.assert_rejects(game, "A", submit)
        self.assert_rejects(game, "B", {**submit, "replacement": "unknown-reveal"})
        entry_start = game.state.event_sequence
        result = game.act("pilot:B", submit)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", "B", "B", False), (current.zone, current.owner, current.controller, current.tapped))
        self.assertEqual(order[:-1], game.state.players["B"].zones["library"])
        revealed = game.state.cards[mountain.object_id]
        self.assertEqual("hand", revealed.zone)
        self.assertEqual(set("ABCD"), set(revealed.known_to))
        for principal in ("pilot:A", "pilot:D"):
            packet = json.dumps(game.packet(principal, full=True))
            self.assertIn(mountain.ref, packet)
            self.assertNotIn(island.ref, packet)
            self.assertNotIn(foreign.ref, packet)
        spectator = game.packet("spectator", full=True, cursor_key="warp-entry-reveal-spectator")
        public, = [
            row for row in spectator.get("events", [])
            if row["c"] == "card.reveal" and row["id"] > entry_start
        ]
        self.assertEqual("B", public["a"])
        self.assertNotIn(island.ref, json.dumps(spectator))
        self.assertNotIn(foreign.ref, json.dumps(spectator))
        self.assertEqual({**counts, "B": counts["B"] + 1}, self.counts(game))
        self.replay(game, load=True)

    def test_aura_uses_owner_attachment_choice_or_stays_if_no_recipient(self):
        for can_enter in (False, True):
            with self.subTest(can_enter=can_enter):
                game, spell, target, library, _ = self.scene(
                    PERMANENT_SEED,
                    names=("Control Magic", *LIBRARY_NAMES[1:]),
                    aura_recipients=can_enter,
                )
                order = self.shuffle_control(game, library, target, expected_index=0)
                start, counts = game.state.event_sequence, self.counts(game)
                self.announce(game, spell, target)
                pending = self.resolve(game)
                aura = library[0]
                if can_enter:
                    self.assertEqual(("aura.entry", ("B",)), (pending.kind, tuple(pending.actors)))
                    self.assertEqual(order, game.state.players["B"].zones["library"])
                    target_card, = [card for card in game.state.cards.values() if card.ref == "warp-aura-recipient-C"]
                    self.assert_shuffle_and_reveal(game, before=counts, start=start, revealed=aura, private=library[1:])
                    game = self.replay(game, load=True)
                    if any(event.code in REVEAL for _, event in self.new_events(game, start)):
                        self.assert_public_reveal(game, aura, library[1:])
                    attach = {"action_id": "choose", "aura_target": target_card.ref}
                    self.assert_rejects(game, "C", attach)
                    result = game.act("pilot:B", attach)
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                    current = game.state.cards[aura.object_id]
                    self.assertEqual(("battlefield", "B", target_card.object_id),
                                     (current.zone, current.controller, current.attached_to))
                    self.assertEqual(order[:-1], game.state.players["B"].zones["library"])
                    self.assertEqual({**counts, "B": counts["B"] + 1}, self.counts(game))
                else:
                    self.assertIsNone(pending)
                    self.assertEqual(order, game.state.players["B"].zones["library"])
                    self.assertEqual("library", game.state.cards[aura.object_id].zone)
                    self.assert_shuffle_and_reveal(game, before=counts, start=start, revealed=aura, private=library[1:])
                self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main()
