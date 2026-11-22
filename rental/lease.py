"""Правила одного договора. Суммы целые, метки времени рациональные."""

from dataclasses import dataclass, replace
from enum import Enum
from fractions import Fraction


class State(Enum):
    WAITING = "ОН"
    CURRENT = "БД"
    OVERDUE = "СД"
    BROKEN = "РД"
    INSPECTION = "ОС"
    SETTLEMENT = "РЗ"
    CLOSED = "ЗК"


class Action(Enum):
    START = "start"
    PAY = "pay"
    EXTEND = "extend"
    INSPECT = "inspect"
    SETTLE = "settle"

    @property
    def code(self):
        return {
            Action.START: "ВН", Action.PAY: "ОП", Action.EXTEND: "ПР",
            Action.INSPECT: "РО", Action.SETTLE: "ИР",
        }[self]


def timestamp(value):
    if type(value) not in (int, str, Fraction):
        raise ValueError("Время задаётся целым числом или строкой с дробью.")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("Неверная метка времени: {}".format(value)) from error
    if result < 0:
        raise ValueError("Время не может быть отрицательным.")
    return result


@dataclass(frozen=True)
class Terms:
    rent: int
    deposit: int
    months: int
    month: Fraction

    def __post_init__(self):
        if any(type(n) is not int or n <= 0 for n in (self.rent, self.deposit, self.months)):
            raise ValueError("Плата, залог и длина аренды в месяцах — положительные целые.")
        period = timestamp(self.month)
        if period == 0:
            raise ValueError("Длительность месяца должна быть положительной.")
        object.__setattr__(self, "month", period)


@dataclass(frozen=True)
class Command:
    action: Action
    amount: int = 0


@dataclass(frozen=True)
class Entry:
    at: Fraction
    event: str
    before: State
    after: State
    debt: int
    result: str
    amount: int | None = None


@dataclass(frozen=True)
class Lease:
    terms: Terms
    state: State = State.WAITING
    now: Fraction = Fraction(0)
    start: Fraction | None = None
    total_months: int = 0
    billed: int = 0
    paid: int = 0
    held: int = 0
    refund_due: int = 0
    refunded: int = 0
    retained: int = 0
    debt_paid_from_deposit: int = 0

    @property
    def debt(self):
        unpaid = (self.billed - self.paid) * self.terms.rent
        return max(0, unpaid - self.debt_paid_from_deposit)

    @property
    def end(self):
        return None if self.start is None else self.start + self.total_months * self.terms.month

    @property
    def next_boundary(self):
        if self.state in (State.CURRENT, State.OVERDUE):
            return self.start + self.billed * self.terms.month
        return None

    def _apply(self, command):
        action, amount = command.action, command.amount
        t = self.terms
        if type(amount) is not int or amount < 0:
            return self, "НД"
        if action is Action.START and self.state is State.WAITING and amount == t.rent + t.deposit:
            return replace(self, state=State.CURRENT, start=self.now, total_months=t.months,
                           billed=1, paid=1, held=t.deposit), "АК"
        if action is Action.PAY and self.state in (State.CURRENT, State.OVERDUE):
            months, remainder = divmod(amount, t.rent)
            limit = self.billed if self.now == self.end else self.total_months
            if months and not remainder and self.paid + months <= limit:
                paid = self.paid + months
                state = State.OVERDUE if paid < self.billed else State.CURRENT
                return replace(self, state=state, paid=paid), "УП"
        if action is Action.EXTEND and self.state is State.CURRENT and self.now < self.end and amount > 0:
            return replace(self, total_months=self.total_months + amount), "ПД"
        if action is Action.INSPECT and self.state is State.INSPECTION:
            refund = max(0, self.held - self.debt - amount)
            return replace(self, state=State.SETTLEMENT, refund_due=refund), "СР"
        if action is Action.SETTLE and self.state is State.SETTLEMENT and amount == 0:
            return replace(self, state=State.CLOSED, held=0, refunded=self.refund_due,
                           retained=self.held - self.refund_due, refund_due=0,
                           debt_paid_from_deposit=min(self.debt, self.held)), "ЗВ"
        return self, "НД"

    def _boundary(self):
        if self.billed == self.total_months:
            return replace(self, state=State.INSPECTION), "КА", "НО"
        after = replace(self, billed=self.billed + 1)
        if after.billed - after.paid >= 2:
            return replace(after, state=State.BROKEN, retained=self.held, held=0), "МГ", "РД"
        state = State.OVERDUE if after.debt else State.CURRENT
        return replace(after, state=state), "МГ", "НП"

    def step(self, at, commands=()):
        """Выполнить все команды одной метки, включая наступившие границы.

        Сценарий передаёт каждую метку только одним пакетом. На границе
        платежи пакета идут первыми; иные команды — после начисления.
        """
        at = timestamp(at)
        if at < self.now:
            raise ValueError("Нельзя перевести время назад.")
        commands = tuple(commands)
        trace = []
        lease = self

        def record_boundary():
            nonlocal lease
            before = replace(lease, now=lease.next_boundary)
            lease, event, result = before._boundary()
            trace.append(Entry(lease.now, event, before.state, lease.state, lease.debt, result))

        def record_command(command):
            nonlocal lease
            before = lease.state
            lease, result = lease._apply(command)
            trace.append(Entry(at, command.action.code, before, lease.state, lease.debt,
                               result, command.amount))

        while lease.next_boundary is not None and lease.next_boundary < at:
            record_boundary()
        lease = replace(lease, now=at)
        if lease.next_boundary == at:
            for command in commands:
                if command.action is Action.PAY:
                    record_command(command)
            record_boundary()
            commands = tuple(command for command in commands if command.action is not Action.PAY)
        for command in commands:
            record_command(command)
        return lease, tuple(trace)
