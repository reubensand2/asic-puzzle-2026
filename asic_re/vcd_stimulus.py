"""Convert the supplied puzzle VCD into cycle-by-cycle replay vectors."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from pathlib import Path

from vcdvcd import VCDVCD


SIGNALS = {
    "rst_n": "puzzle.rst_n",
    "enable": "puzzle.enable",
    "I": "puzzle.I",
    "success": "puzzle.success",
    "O": "puzzle.O[7:0]",
}


def _value_at(transitions: list[tuple[int, str]], time: int) -> str:
    times = [transition_time for transition_time, _value in transitions]
    index = bisect_right(times, time) - 1
    if index < 0:
        raise ValueError(f"Signal has no value at VCD time {time}")
    return transitions[index][1]


def generate_vectors(vcd_path: Path, memory_path: Path, header_path: Path) -> int:
    """Sample inputs and expected outputs at every rising clock edge."""

    vcd = VCDVCD(str(vcd_path))
    clock = vcd["puzzle.clk"].tv
    rising_edges = [time for time, value in clock if value == "1"]

    vectors: list[str] = []
    for time in rising_edges:
        values = {
            name: _value_at(vcd[signal].tv, time)
            for name, signal in SIGNALS.items()
        }
        output = values["O"].zfill(8)
        vectors.append(
            values["rst_n"]
            + values["enable"]
            + values["I"]
            + values["success"]
            + output
        )

    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("\n".join(vectors) + "\n", encoding="ascii")
    header_path.write_text(
        f"`define PUZZLE_EXAMPLE_CYCLES {len(vectors)}\n",
        encoding="ascii",
    )
    return len(vectors)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vcd", type=Path, help="input VCD")
    parser.add_argument("memory", type=Path, help="output readmemb file")
    parser.add_argument("header", type=Path, help="output Verilog cycle-count header")
    args = parser.parse_args()

    count = generate_vectors(args.vcd, args.memory, args.header)
    print(f"Wrote {count} replay cycles to {args.memory}")


if __name__ == "__main__":
    main()
