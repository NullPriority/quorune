"""Native current-turn damage recovery, spell targeting, and replay."""

from dataclasses import replace
from hashlib import file_digest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import CONTROLLER_DAMAGE_RECEIVED_FACT
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Simulacrum"
PROFILE = "commander_review"


class SimulacrumTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "simulacrum.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / "bound-effect-program-cards.json", fixtures / "simulacrum-turn-damage-cards.json"],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Turn damage recovery",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def _record(self, game, name):
        directory = os.environ.get("QUORUNE_SIMULACRUM_RECORD_PARENT")
        if directory is None:
            self.replay(game, load=True)
            return
        path = Path(directory) / name
        game.save(path)
        result = replay_record(path, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(authoritative_state_hash(game.state), result["final_state_hash"])
        manifest = (path / "manifest.json").read_bytes()
        self.assertEqual(3, json.loads(manifest)["record_schema_version"])
        with (path / "manifest.json").open("rb") as handle:
            digest = file_digest(handle, "sha256").hexdigest()
        receipt = {"manifest_sha256": digest, "commands": len(game.commands),
                   "final_state_sha256": result["final_state_hash"]}
        path.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    def _cast_resolve(self, game, seat, spell, targets=()):
        self.cast_for(game, seat, spell, list(targets))
        self.assertIsNone(self.resolve(game))

    def _advance_turn(self, game, preserve_ref):
        turn = game.state.turn_sequence
        while game.state.turn_sequence == turn:
            self.until(game, lambda current: current.state.turn_sequence != turn or (
                current.state.pending_decision is not None and current.state.pending_decision.kind == "cleanup.discard"
            ))
            if game.state.turn_sequence == turn:
                principal = game.pending_principals()[0]
                decision = game.packet(principal, full=True)["decision"]["ctx"]
                cards = [row["id"] for row in decision["hand"] if row["id"] != preserve_ref][:decision["count"]]
                result = game.act(principal, {"action_id": "discard", "cards": cards})
                self.assertTrue(result.ok, result.summary)

    def test_actual_final_receipts_exclude_prevention_redirection_and_direct_life_loss(self):
        game = self.session(61127001)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        brontodon = self.add(game.engine, "Ancient Brontodon")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C")
        prior_bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="prior-bolt")
        direct_shock = self.add(game.engine, "Shock", zone="hand", ref="prior-shock")
        loss = self.add(game.engine, "Sign in Blood", zone="hand")
        prevented = self.add(game.engine, "Shock", seat="B", zone="hand", ref="prevented-shock")
        palm = self.add(game.engine, "Deflecting Palm", zone="hand")
        giant = self.add(game.engine, "Palisade Giant", zone="hand")
        redirected = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="redirected-bolt")
        for card in (spell, prior_bolt, direct_shock, loss, prevented, palm, giant, redirected):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 10, "W": 3, "R": 2, "B": 3})
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)

        self._cast_resolve(game, "B", prior_bolt, ("A",))
        self._cast_resolve(game, "A", direct_shock, ("A",))
        self._cast_resolve(game, "A", loss, ("A",))
        self.cast_for(game, "B", prevented, ["A"])
        selected_source = game.state.stack[-1].ref
        self.cast_for(game, "A", palm)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        answer = game.act("pilot:A", {"action_id": "choose", "source": selected_source})
        self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(self.resolve(game))

        self._cast_resolve(game, "A", giant)
        self._cast_resolve(game, "B", redirected, ("A",))
        self.assertEqual((33, 38), tuple(game.state.players[seat].life for seat in "AB"))
        self.assertEqual(3, game.state.cards[giant.object_id].marked_damage)
        receipts = [event for event in game.state.turn_history.events if event.kind == "player_damaged"]
        self.assertEqual([("A", 3), ("A", 2), ("B", 2)], [(event.target, event.amount) for event in receipts])

        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        self.assertEqual({brontodon.ref, giant.ref}, set(offer["target_schema"]["legal_refs"]))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": offer["id"], "targets": [foreign.ref], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [brontodon.ref])
        source_incarnation = game.state.cards[spell.object_id].logical_object_id
        redirect_recipient = game.state.cards[giant.object_id].logical_object_id
        start = len(game.state.turn_history.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual(0, game.state.cards[brontodon.object_id].marked_damage)
        self.assertEqual("graveyard", game.state.cards[giant.object_id].zone)
        committed = [event for event in game.state.turn_history.events[start:]
                     if event.kind in {"player_gained_life", "permanent_damaged"}]
        self.assertEqual([("player_gained_life", 5), ("permanent_damaged", 5)],
                         [(event.kind, event.amount) for event in committed])
        self.assertEqual("A", committed[0].target)
        self.assertEqual(source_incarnation, committed[1].object_incarnation)
        self.assertIn("instant", committed[1].types)
        self.assertEqual(redirect_recipient, committed[1].target_object_incarnation)
        self._record(game, "simulacrum-committed-receipts")

    def test_actual_damage_in_response_counts_when_the_spell_resolves(self):
        game = self.session(61127002)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        target = self.add(game.engine, "Ancient Brontodon")
        first = self.add(game.engine, "Shock", zone="hand")
        late = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.prepare(game, mana={"C": 1, "B": 1, "R": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self._cast_resolve(game, "A", first, ("A",))
        self.cast_for(game, "A", spell, [target.ref])
        self.cast_for(game, "B", late, ["A"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(35, game.state.players["A"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual(5, game.state.cards[target.object_id].marked_damage)
        self._record(game, "simulacrum-response")

    def test_losing_the_only_target_stops_both_instructions(self):
        game = self.session(61127003)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        target = self.add(game.engine, "Ancient Brontodon")
        first = self.add(game.engine, "Shock", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.prepare(game, mana={"C": 1, "B": 1, "R": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self._cast_resolve(game, "A", first, ("A",))
        self.cast_for(game, "A", spell, [target.ref])
        self.cast_for(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertFalse(any(event.kind == "permanent_damaged" for event in game.state.turn_history.events))
        self._record(game, "simulacrum-lost-target")

    def test_prior_turn_damage_yields_no_life_or_damage_with_a_legal_target(self):
        game = self.session(61127004)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        target = self.add(game.engine, "Ancient Brontodon")
        first = self.add(game.engine, "Shock", zone="hand")
        self.add(game.engine, "Swamp")
        self.add(game.engine, "Plains")
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self._cast_resolve(game, "A", first, ("A",))
        self.assertEqual(38, game.state.players["A"].life)
        self._advance_turn(game, spell.ref)
        self._cast_resolve(game, "A", spell, (target.ref,))
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual(0, game.state.cards[target.object_id].marked_damage)
        self.assertFalse(any(event.kind in {"player_gained_life", "permanent_damaged"}
                             for event in game.state.turn_history.events))
        self._record(game, "simulacrum-next-turn")

    def test_exact_compilation_rejects_foreign_source_and_requires_public_history_authority(self):
        game = self.session(61127005)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, spell)
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = game.engine.semantics.programs_for_oracle(record.oracle_id)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(record.oracle_text)), (node.span.start, node.span.end))
        gain, damage = program.effects
        self.assertEqual((CONTROLLER_DAMAGE_RECEIVED_FACT, CONTROLLER_DAMAGE_RECEIVED_FACT),
                         (gain["delta"]["history_fact"], damage["amount"]["history_fact"]))
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != PUBLIC_HISTORY_QUERY_CAPABILITY)
        variants = (
            replace(program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict()),
            replace(program, target_schema={**program.target_schema, "controller_relation": "any"}),
            replace(program, effects=(damage, gain)),
        )
        for variant in variants:
            result = bind_semantic_program_runtime(variant, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        foreign = replace(record, oracle_text=record.oracle_text.replace("Simulacrum deals", "Another spell deals"))
        other = compile_oracle_card(foreign, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(other.material_residuals)
