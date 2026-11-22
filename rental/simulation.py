"""Оба источника команд используют один и тот же переход договора."""

import json
import random
from dataclasses import dataclass
from fractions import Fraction
from itertools import groupby
from pathlib import Path

from .lease import Action, Command, Entry, Lease, State, Terms, timestamp


@dataclass(frozen=True)
class Batch:
    at: Fraction
    commands: tuple[Command, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "at", timestamp(self.at))


@dataclass(frozen=True)
class Run:
    end: Lease
    batches: tuple[Batch, ...]
    trace: tuple[Entry, ...]

    @property
    def broken_at(self):
        return next((row.at for row in self.trace if row.result == "РД"), None)


def replay(terms, batches):
    """Соседние события с одной меткой обрабатываются как единый пакет."""
    lease = Lease(terms)
    trace = []
    merged = []
    for at, group in groupby(batches, key=lambda batch: batch.at):
        batch = Batch(at, tuple(cmd for part in group for cmd in part.commands))
        lease, rows = lease.step(at, batch.commands)
        merged.append(batch)
        trace.extend(rows)
    return Run(lease, tuple(merged), tuple(trace))


def _candidates(terms):
    yield Command(Action.START, terms.rent + terms.deposit)
    yield Command(Action.PAY, terms.rent)
    yield Command(Action.PAY, 2 * terms.rent)
    yield Command(Action.EXTEND, 1)
    yield Command(Action.EXTEND, 2)
    yield Command(Action.INSPECT, 0)
    yield Command(Action.INSPECT, terms.deposit // 2)
    yield Command(Action.SETTLE)


def generate(terms, seed, steps, mode="valid"):
    """Выбор команды или ожидания после явного продвижения времени.

    valid фильтрует кандидатов через те же правила, что replay. any не
    фильтрует: может предлагать действия не вовремя и с неверной суммой.
    Ни один режим не пытается оптимизировать исход.
    """
    if mode not in ("valid", "any"):
        raise ValueError("Режим должен быть valid или any.")
    if type(seed) is not int or type(steps) is not int or steps < 0:
        raise ValueError("Seed — целое число, число шагов — неотрицательное целое.")
    rng = random.Random(seed)
    lease = Lease(terms)
    batches = []
    trace = []
    candidates = list(dict.fromkeys(_candidates(terms)))
    if mode == "any":
        candidates.extend((Command(Action.START, 0), Command(Action.PAY, 0),
                           Command(Action.EXTEND, 0), Command(Action.INSPECT, -1)))
    for index in range(steps):
        if mode == "valid" and lease.state in (State.BROKEN, State.CLOSED):
            break
        at = lease.now if index == 0 else lease.now + terms.month * rng.randint(1, 4) / 4
        # None означает ожидание; часы продвигаются независимо от команды.
        options = [None]
        for command in candidates:
            if mode == "any" or any(
                row.amount is not None and row.result != "НД"
                for row in lease.step(at, (command,))[1]
            ):
                options.append(command)
        choice = rng.choice(options)
        batch = Batch(at, () if choice is None else (choice,))
        lease, rows = lease.step(at, batch.commands)
        batches.append(batch)
        trace.extend(rows)
    return Run(lease, tuple(batches), tuple(trace))


def read_scenario(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"terms", "batches"}:
        raise ValueError("Сценарию нужны поля terms и batches.")
    terms = data["terms"]
    if not isinstance(terms, dict) or set(terms) != {"rent", "deposit", "months", "month"}:
        raise ValueError("В terms нужны rent, deposit, months, month.")
    if not isinstance(data["batches"], list):
        raise ValueError("batches должен быть списком.")
    batches = []
    for batch in data["batches"]:
        if (not isinstance(batch, dict) or set(batch) != {"at", "commands"}
                or not isinstance(batch["commands"], list)):
            raise ValueError("В пакете нужны метка at и список commands.")
        commands = []
        for command in batch["commands"]:
            if (not isinstance(command, dict) or set(command) - {"action", "amount"}
                    or "action" not in command or type(command.get("amount", 0)) is not int):
                raise ValueError("Команде нужны action и, при необходимости, целое amount.")
            commands.append(Command(Action(command["action"]), command.get("amount", 0)))
        batches.append(Batch(batch["at"], tuple(commands)))
    return Terms(**terms), tuple(batches)


def save_scenario(path, terms, batches):
    data = {
        "terms": {"rent": terms.rent, "deposit": terms.deposit,
                  "months": terms.months, "month": str(terms.month)},
        "batches": [
            {"at": str(batch.at), "commands": [
                {"action": cmd.action.value, "amount": cmd.amount} for cmd in batch.commands
            ]}
            for batch in batches
        ],
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
