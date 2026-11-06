"""The pinned traditional dungeon graphs for CR 309 and 701.49."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class DungeonId(StrEnum):
    LOST_MINE = "lost_mine"
    MAD_MAGE = "mad_mage"
    TOMB = "tomb"
    UNDERCITY = "undercity"


class VentureMode(StrEnum):
    ORDINARY = "ordinary"
    UNDERCITY = "undercity"


@dataclass(frozen=True, slots=True)
class DungeonRoom:
    label: str
    exits: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DungeonDefinition:
    dungeon: DungeonId
    source_name: str
    source_oracle_hash: str
    entry_mode: VentureMode
    rooms: tuple[DungeonRoom, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.dungeon, DungeonId) or not isinstance(self.entry_mode, VentureMode):
            raise ValueError("Dungeon requires a typed identity and entry mode")
        if type(self.source_name) is not str or not self.source_name or (
            type(self.source_oracle_hash) is not str or len(self.source_oracle_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.source_oracle_hash)
        ):
            raise ValueError("Dungeon requires pinned Oracle source data")
        if type(self.rooms) is not tuple or not self.rooms or any(
            not isinstance(room, DungeonRoom) for room in self.rooms
        ):
            raise ValueError("Dungeon requires an ordered room graph")
        if self.rooms[-1].exits or any(not room.exits for room in self.rooms[:-1]):
            raise ValueError("Dungeon requires one bottommost room")
        reachable = {0}
        for index, room in enumerate(self.rooms):
            if type(room.label) is not str or not room.label or index not in reachable:
                raise ValueError("Every dungeon room must be labeled and reachable")
            if type(room.exits) is not tuple or any(
                type(exit_index) is not int or not index < exit_index < len(self.rooms)
                for exit_index in room.exits
            ) or len(set(room.exits)) != len(room.exits):
                raise ValueError("Dungeon exits must name distinct rooms below the current room")
            reachable.update(room.exits)


DUNGEON_CATALOG = MappingProxyType({
    definition.dungeon: definition
    for definition in (
        DungeonDefinition(
            DungeonId.LOST_MINE, "Lost Mine of Phandelver",
            "55879de18ec50882cd575a146ec21bef3f9b9af6889835be4a1d9f15cc0220c5",
            VentureMode.ORDINARY,
            (
                DungeonRoom("Cave Entrance", (1, 2)),
                DungeonRoom("Goblin Lair", (3, 4)),
                DungeonRoom("Mine Tunnels", (4, 5)),
                DungeonRoom("Storeroom", (6,)),
                DungeonRoom("Dark Pool", (6,)),
                DungeonRoom("Fungi Cavern", (6,)),
                DungeonRoom("Temple of Dumathoin"),
            ),
        ),
        DungeonDefinition(
            DungeonId.MAD_MAGE, "Dungeon of the Mad Mage",
            "9d45d5133873200e22cffb9957e6e6162cc8af5f3ebea9499c34a46e7bfbd40e",
            VentureMode.ORDINARY,
            (
                DungeonRoom("Yawning Portal", (1,)),
                DungeonRoom("Dungeon Level", (2, 3)),
                DungeonRoom("Goblin Bazaar", (4,)),
                DungeonRoom("Twisted Caverns", (4,)),
                DungeonRoom("Lost Level", (5, 6)),
                DungeonRoom("Runestone Caverns", (7,)),
                DungeonRoom("Muiral's Graveyard", (7,)),
                DungeonRoom("Deep Mines", (8,)),
                DungeonRoom("Mad Wizard's Lair"),
            ),
        ),
        DungeonDefinition(
            DungeonId.TOMB, "Tomb of Annihilation",
            "7440b5707e9acbe5a3fcf6f6acbe7be3049a10ca36e085cefb76b84d11d399fb",
            VentureMode.ORDINARY,
            (
                DungeonRoom("Trapped Entry", (1, 3)),
                DungeonRoom("Veils of Fear", (2,)),
                DungeonRoom("Sandfall Cell", (4,)),
                DungeonRoom("Oubliette", (4,)),
                DungeonRoom("Cradle of the Death God"),
            ),
        ),
        DungeonDefinition(
            DungeonId.UNDERCITY, "Undercity // The Initiative",
            "7d6fee7a515876c03f8b3e69ecaad666b11ec47d91ebaa7fe1346b69da514ccd",
            VentureMode.UNDERCITY,
            (
                DungeonRoom("Secret Entrance", (1, 2)),
                DungeonRoom("Forge", (3, 4)),
                DungeonRoom("Lost Well", (4, 5)),
                DungeonRoom("Trap!", (6,)),
                DungeonRoom("Arena", (6, 7)),
                DungeonRoom("Stash", (7,)),
                DungeonRoom("Archives", (8,)),
                DungeonRoom("Catacombs", (8,)),
                DungeonRoom("Throne of the Dead Three"),
            ),
        ),
    )
})
