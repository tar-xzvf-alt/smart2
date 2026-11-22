import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from rental.lease import Action, Command, Lease, State, Terms
from rental.simulation import Batch, generate, read_scenario, replay, save_scenario


ROOT = Path(__file__).resolve().parents[1]
TERMS = Terms(20_000, 30_000, 3, "1")
START = Command(Action.START, 50_000)
PAY = Command(Action.PAY, 20_000)
SETTLE = Command(Action.SETTLE)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.lease, _ = Lease(TERMS).step(0, [START])

    def test_start_requires_both_amounts(self):
        lease, rows = Lease(TERMS).step(0, [Command(Action.START, 30_000)])
        self.assertEqual((lease.state, lease.held, rows[0].result), (State.WAITING, 0, "НД"))

    def test_two_missed_months_break_and_retain_deposit(self):
        lease, rows = self.lease.step(10)
        self.assertEqual([(r.at, r.after, r.debt) for r in rows],
                         [(1, State.OVERDUE, 20_000), (2, State.BROKEN, 40_000)])
        self.assertEqual((lease.held, lease.retained, lease.paid), (0, 30_000, 1))
        self.assertEqual(lease.step(11, [PAY])[1][0].result, "НД")

    def test_payment_on_boundary_counts_first(self):
        lease, rows = self.lease.step(2, [PAY])
        self.assertEqual([r.event for r in rows], ["МГ", "ОП", "МГ"])
        self.assertEqual((lease.state, lease.debt, lease.paid), (State.OVERDUE, 20_000, 2))

    def test_waiting_past_boundary_does_not_backdate_payment(self):
        lease, rows = self.lease.step("9/4", [Command(Action.PAY, 40_000)])
        self.assertEqual(lease.state, State.BROKEN)
        self.assertEqual((rows[-2].at, rows[-1].result), (2, "НД"))

    def test_two_months_paid_on_boundary_leave_no_debt(self):
        lease, _ = self.lease.step(2, [Command(Action.PAY, 40_000)])
        self.assertEqual((lease.state, lease.debt), (State.CURRENT, 0))

    def test_extend_keeps_deposit_and_only_if_current(self):
        extend = Command(Action.EXTEND, 2)
        lease, rows = self.lease.step(1, [extend])
        self.assertEqual((lease.end, rows[-1].result), (3, "НД"))
        lease, rows = self.lease.step(1, [extend, PAY])
        self.assertEqual((lease.end, lease.held, rows[-1].result), (5, 30_000, "ПД"))

    def test_cannot_extend_exactly_at_end(self):
        lease, _ = self.lease.step("1/2", [Command(Action.PAY, 40_000)])
        lease, rows = lease.step(3, [Command(Action.EXTEND, 1)])
        self.assertEqual((lease.state, rows[-1].result), (State.INSPECTION, "НД"))

    def test_end_does_not_bill_an_extra_month(self):
        lease, _ = self.lease.step(2, [PAY])
        lease, rows = lease.step(3)
        self.assertEqual((lease.state, lease.billed, lease.debt), (State.INSPECTION, 3, 20_000))
        self.assertEqual(rows[0].event, "КА")

    def test_end_accepts_payment_of_last_debt_at_exact_time(self):
        lease, _ = self.lease.step(2, [PAY])
        lease, rows = lease.step(3, [PAY, Command(Action.INSPECT, 0), SETTLE])
        self.assertEqual([row.event for row in rows], ["ОП", "КА", "РО", "ИР"])
        self.assertEqual((lease.state, lease.refunded, lease.debt), (State.CLOSED, 30_000, 0))

    def test_deposit_settlement_caps_retention_and_prevents_second_refund(self):
        lease, _ = self.lease.step(2, [PAY])
        for damage, refund in ((0, 10_000), (3_000, 7_000), (70_000, 0)):
            with self.subTest(damage=damage):
                calculated, _ = lease.step(3, [Command(Action.INSPECT, damage)])
                self.assertEqual((calculated.held, calculated.refund_due), (30_000, refund))
                closed, rows = calculated.step(4, [SETTLE, SETTLE])
                self.assertEqual((closed.state, closed.refunded, closed.retained, closed.held),
                                 (State.CLOSED, refund, 30_000 - refund, 0))
                self.assertEqual((closed.debt, closed.debt_paid_from_deposit), (0, 20_000))
                self.assertEqual(rows[-1].result, "НД")

    def test_deposit_smaller_than_debt_leaves_uncovered_balance(self):
        terms = Terms(20_000, 10_000, 2, 1)
        lease, _ = Lease(terms).step(0, [Command(Action.START, 30_000)])
        closed, _ = lease.step(2, [Command(Action.INSPECT, 3_000), SETTLE])
        self.assertEqual((closed.state, closed.debt, closed.refunded,
                          closed.debt_paid_from_deposit), (State.CLOSED, 10_000, 0, 10_000))

    def test_invalid_actions_and_time(self):
        snapshot, rows = self.lease.step(0, [Command(Action.PAY, 1), SETTLE,
                                             Command(Action.PAY, 60_000)])
        self.assertEqual(snapshot, self.lease)
        self.assertTrue(all(r.result == "НД" for r in rows))
        with self.assertRaises(ValueError):
            self.lease.step(-1)
        advanced, _ = self.lease.step("2/3")
        with self.assertRaises(ValueError):
            advanced.step("1/3")

    def test_fractional_time_and_delayed_start(self):
        terms = Terms(20_000, 30_000, 3, "1/3")
        run = replay(terms, (Batch("1/3", (START,)), Batch("4/3")))
        self.assertEqual(run.broken_at, Fraction(1))


class SimulationTests(unittest.TestCase):
    def test_examples(self):
        for name, state, refund in (("settlement", State.CLOSED, 7_000),
                                    ("break", State.BROKEN, 0)):
            with self.subTest(name=name):
                run = replay(*read_scenario(ROOT / "examples" / (name + ".json")))
                self.assertEqual((run.end.state, run.end.refunded), (state, refund))

    def test_same_time_is_grouped_and_ordered_at_boundary(self):
        run = replay(TERMS, (Batch(0, (START,)), Batch(1, (Command(Action.EXTEND, 2),)),
                             Batch(1, (PAY,))))
        self.assertEqual(run.end.end, 5)
        self.assertEqual([row.event for row in run.trace], ["ВН", "ОП", "МГ", "ПР"])

    def test_valid_rejects_no_generated_action_including_on_boundaries(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                run = generate(TERMS, seed, 40, "valid")
                self.assertEqual(run, replay(TERMS, run.batches))
                self.assertNotIn("НД", [row.result for row in run.trace])

    def test_arbitrary_is_reproducible_and_covers_invalid_actions(self):
        run = generate(TERMS, 42, 60, "any")
        self.assertEqual(run, generate(TERMS, 42, 60, "any"))
        self.assertNotEqual(run.batches, generate(TERMS, 43, 60, "any").batches)
        self.assertIn("НД", [row.result for row in run.trace])
        self.assertEqual({row.event for row in run.trace if row.amount is not None},
                         {action.code for action in Action})
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "generated.json"
            save_scenario(path, TERMS, run.batches)
            self.assertEqual(run, replay(*read_scenario(path)))


if __name__ == "__main__":
    unittest.main()
