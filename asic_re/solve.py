"""Solve the recovered puzzle checker with Z3."""

from __future__ import annotations

import argparse
from pathlib import Path

import networkx as nx
import z3

from .extract import extract_netlist
from .logic_sim import CompiledCircuit, initial_state
from .netlist_graph import build_graph
from .verilog import INTERFACES


GRID_SIZE = 11


def success_cone(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Keep exactly the state and logic that can influence success."""

    output = "port:success"
    nodes = nx.ancestors(graph, output) | {output}
    return graph.subgraph(nodes).copy()


def solve_input(graph: nx.MultiDiGraph) -> tuple[list[list[bool]], bool]:
    """Return an accepted 11-by-11 bit image and whether it is unique."""

    graph = success_cone(graph)
    circuit = CompiledCircuit.from_graph(graph)
    pixels = [
        [z3.Bool(f"pixel_{row}_{column}") for column in range(GRID_SIZE)]
        for row in range(GRID_SIZE)
    ]
    solver = z3.Solver()
    inputs = {"clk": True, "rst_n": True, "enable": False, "I": False}
    state = initial_state(graph)

    # The example waveform has one idle rising edge after reset is released.
    state = circuit.step(state, inputs)

    # The serial stream scans an 11-by-11 image row by row.
    inputs["enable"] = True
    for row in pixels:
        for pixel in row:
            inputs["I"] = pixel
            state = circuit.step(state, inputs)

    # The checker publishes success on the first rising edge after enable falls.
    inputs["enable"] = False
    inputs["I"] = False
    state = circuit.step(state, inputs)
    solver.add(circuit.outputs(state, inputs)["success"])

    if solver.check() != z3.sat:
        raise RuntimeError("The recovered checker has no solution")
    model = solver.model()
    image = [
        [z3.is_true(model.eval(pixel, model_completion=True)) for pixel in row]
        for row in pixels
    ]

    solver.add(
        z3.Or(
            *(
                pixel != value
                for row, values in zip(pixels, image)
                for pixel, value in zip(row, values)
            )
        )
    )
    unique = solver.check() == z3.unsat
    return image, unique


def render_image(image: list[list[bool]]) -> str:
    return "\n".join("".join("##" if pixel else "  " for pixel in row) for row in image)


def write_serial_bits(path: Path, image: list[list[bool]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{'1' if pixel else '0'}\n" for row in image for pixel in row),
        encoding="ascii",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds", type=Path, nargs="?", default=Path("puzzle.gds"))
    parser.add_argument(
        "--models",
        type=Path,
        default=Path("third_party/sky130_fd_sc_hd"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/puzzle/solution.mem"),
        help="row-major serial bits for the Verilog testbench",
    )
    args = parser.parse_args()

    extracted = extract_netlist(args.gds)
    graph = build_graph(
        extracted.netlist,
        extracted.top_cell_name,
        INTERFACES[extracted.top_cell_name],
        args.models,
    )
    image, unique = solve_input(graph)
    print("Accepted 11x11 image:")
    print(render_image(image))
    print(f"Unique solution: {unique}")
    write_serial_bits(args.output, image)
    print(f"Serial bits: {args.output}")


if __name__ == "__main__":
    main()
