import argparse

from models import Operation
from ops import add, div, mul, sub

OPS = {"add": add, "sub": sub, "mul": mul, "div": div}


def main() -> None:
    p = argparse.ArgumentParser(description="Simple calculator")
    p.add_argument("op", choices=OPS)
    p.add_argument("a", type=float)
    p.add_argument("b", type=float)
    args = p.parse_args()
    op = Operation(args.op, args.a, args.b)
    print(OPS[args.op](op))


if __name__ == "__main__":
    main()
