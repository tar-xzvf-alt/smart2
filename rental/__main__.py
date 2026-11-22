"""Командная строка: python3 -m rental --help."""

import argparse

from .lease import Terms
from .simulation import generate, read_scenario, replay, save_scenario


def show(run):
    print("Время\tВход\tПараметр\tДо\tПосле\tДолг\tВыход")
    for row in run.trace:
        parameter = "—" if row.amount is None else str(row.amount)
        print(f"{row.at}\t{row.event}\t{parameter}\t\t{row.before.value}\t"
              f"{row.after.value}\t{row.debt}\t{row.result}")
    end = run.end
    print(f"Итог в {end.now}: {end.state.value}; долг {end.debt}, залог на хранении {end.held}, "
          f"к возврату {end.refund_due}, возвращено {end.refunded}, удержано {end.retained}.")
    if end.debt_paid_from_deposit:
        print(f"Из залога погашено долга: {end.debt_paid_from_deposit}.")
    if run.broken_at is not None:
        print(f"Договор разорван в момент {run.broken_at}.")
    else:
        print("Разрыв на этой трассе не наблюдался.")


def main():
    parser = argparse.ArgumentParser(description="Модель договора аренды квартиры")
    sub = parser.add_subparsers(dest="source", required=True)
    manual = sub.add_parser("scenario", help="Воспроизвести сценарий из JSON")
    manual.add_argument("file")
    random = sub.add_parser("random", help="Сгенерировать псевдослучайный сценарий")
    random.add_argument("--seed", required=True, type=int)
    random.add_argument("--steps", type=int, default=20)
    random.add_argument("--mode", choices=("valid", "any"), default="valid")
    random.add_argument("--rent", type=int, default=20000)
    random.add_argument("--deposit", type=int, default=30000)
    random.add_argument("--months", type=int, default=3)
    random.add_argument("--month", default="1")
    random.add_argument("--save", metavar="FILE", help="Сохранить события для воспроизведения")
    args = parser.parse_args()
    try:
        if args.source == "scenario":
            terms, batches = read_scenario(args.file)
            run = replay(terms, batches)
        else:
            terms = Terms(args.rent, args.deposit, args.months, args.month)
            run = generate(terms, args.seed, args.steps, args.mode)
            if args.save:
                save_scenario(args.save, terms, run.batches)
            print(f"Seed: {args.seed}; режим: {args.mode}.")
        show(run)
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
