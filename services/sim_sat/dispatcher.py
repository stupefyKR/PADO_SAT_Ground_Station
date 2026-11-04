"""Small, explicit command registry for the private virtual spacecraft.

Handlers return actions; the link sends the correlated acceptance result before
applying an action. Registering a handler never enables a real spacecraft uplink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from tools.pado_packet_codec import PacketError, decode, encode_tm


MAX_TC_AGE_MS = 120_000
MAX_TC_REPLAY_ENTRIES = 64
SIM_READ_ONLY_COMMANDS = frozenset({
    "hk_get", "mm_get_mode", "eps_get_hk", "aocs_get_hk", "cs_get_hk",
    "siren_get_hk", "crest_get_hk",
})
RESULT_OK, RESULT_REJECTED = 0, 1
REASON_OK, REASON_UNSUPPORTED, REASON_SCHEDULED = 0, 1, 2
REASON_REPLAY, REASON_STALE, REASON_PARAMETERS, REASON_HANDLER = 3, 4, 5, 6


@dataclass
class TcReplayState:
    seen: dict[tuple[int, int], int] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandContext:
    received_ms: int
    pending_first_s: int | None = None
    pending_last_s: int | None = None
    structural_only: bool = False


@dataclass(frozen=True)
class CommandAction:
    # Inclusive sample bounds captured at command receipt. Newer samples are
    # retained for the next request, even when they arrive during this dump.
    dump_window: tuple[int, int] | None = None
    dump_requested: bool = False
    validation_dump: dict | None = None


@dataclass(frozen=True)
class DispatchResult:
    ack: bytes
    action: CommandAction | None


CommandHandler = Callable[[dict, CommandContext], CommandAction]


def query_handler(_command: dict, _context: CommandContext) -> CommandAction:
    """Existing query commands only acknowledge receipt in this simulator."""
    return CommandAction()


def dump_handler(command: dict, context: CommandContext) -> CommandAction:
    parameters = command["fields"]
    start, end = parameters["start_utc_ms"], parameters["end_utc_ms"]
    if (start == 0) != (end == 0):
        raise ValueError("both dump bounds must be zero or positive")
    if start or end:
        if (not 0 < start < end <= context.received_ms or
                start < context.received_ms - 86_400_000):
            raise ValueError("dump bounds must be ordered, past and within 24 hours")
        first, last = (start + 999) // 1000, end // 1000
    else:
        first, last = context.pending_first_s, context.pending_last_s
    if context.pending_first_s is None or context.pending_last_s is None:
        return CommandAction(dump_requested=True)
    first = max(first, context.pending_first_s)
    last = min(last, context.pending_last_s)
    return CommandAction((first, last) if first <= last else None, dump_requested=True)


def validation_handler(command: dict, context: CommandContext) -> CommandAction:
    fields=command['fields']
    if not 100<=fields['count']<=10000 or not 0<fields['base_utc_ms']<=context.received_ms or fields['base_utc_ms']%1000:
        raise ValueError('invalid validation corpus bounds')
    return CommandAction(validation_dump=fields)


class TcDispatcher:
    """Validate once, reject replays, then dispatch by catalog command name.

    Add a catalog command and register its handler explicitly. A handler must
    return a CommandAction and should describe work rather than perform I/O.
    """

    def __init__(self, replay: TcReplayState | None = None, *, validation: bool = False):
        self.replay = replay if replay is not None else TcReplayState()
        self.catalog = None
        self.max_replays = 2048 if validation else MAX_TC_REPLAY_ENTRIES
        self.handlers: dict[str, CommandHandler] = {
            name: query_handler for name in SIM_READ_ONLY_COMMANDS
        }
        self.handlers["hk_dump"] = dump_handler
        if validation:
            from services.validation.corpus import catalog, TRIGGER
            self.catalog = catalog()
            # Structural FSW stand-in: acknowledge each defined command without
            # pretending to implement a vendor subsystem's behaviour.
            self.handlers.update({entry['name']:query_handler for entry in self.catalog['commands']
                                  if entry['fields'] is not None})
            self.handlers['hk_dump'] = dump_handler
            self.handlers[TRIGGER] = validation_handler

    def register(self, name: str, handler: CommandHandler) -> None:
        if not isinstance(name, str) or not name or not callable(handler):
            raise ValueError("command name and callable handler required")
        if name in self.handlers:
            raise ValueError("command handler already registered")
        self.handlers[name] = handler

    def dispatch(self, packet: bytes, tm_sequence: int,
                 context: CommandContext) -> DispatchResult:
        received_ms = context.received_ms
        if type(tm_sequence) is not int or not 0 <= tm_sequence <= 65535 or \
                type(received_ms) is not int or received_ms <= 0:
            raise ValueError("invalid simulator command context")
        decoded = decode(packet, **({"catalog":self.catalog} if self.catalog else {}))
        if decoded["kind"] != "TC" or decoded["operational"] is not False:
            raise PacketError("draft TC required on simulator uplink")
        issue_ms, sequence = decoded["utc_ms"], decoded["sequence"]
        self.replay.seen = {key: when for key, when in self.replay.seen.items()
                            if 0 <= received_ms - when <= MAX_TC_AGE_MS}
        key = (issue_ms, sequence)
        action = None
        if not 0 <= received_ms - issue_ms <= MAX_TC_AGE_MS:
            reason = REASON_STALE
        elif key in self.replay.seen or len(self.replay.seen) >= self.max_replays:
            reason = REASON_REPLAY
        else:
            self.replay.seen[key] = received_ms
            if decoded["execute_at_utc_ms"]:
                reason = REASON_SCHEDULED
            elif decoded["name"] not in self.handlers:
                reason = REASON_UNSUPPORTED
            else:
                try:
                    handler=self.handlers[decoded['name']]
                    if self.catalog is not None and context.structural_only and decoded['name']!='validation_tm_dump':
                        handler=query_handler
                    action = handler(decoded, context)
                    if not isinstance(action, CommandAction):
                        raise TypeError("command handler must return CommandAction")
                    reason = REASON_OK
                except (KeyError, ValueError):
                    reason = REASON_PARAMETERS
                    action = None
                except Exception:
                    reason = REASON_HANDLER
                    action = None
        ack = encode_tm("COMMAND_RESULT", tm_sequence, received_ms // 1000 * 1000,
                        {"command_id": decoded["id"], "command_sequence": sequence,
                         "result": RESULT_OK if reason == REASON_OK else RESULT_REJECTED,
                         "reason": reason,
                         "executed_at_utc_ms": received_ms if reason == REASON_OK else 0})
        return DispatchResult(ack, action)
