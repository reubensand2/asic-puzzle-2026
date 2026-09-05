"""Characterize the recovered checker as an 11x11 Star Battle puzzle."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from .extract import extract_netlist
from .logic_sim import CompiledCircuit, initial_state
from .netlist_graph import build_graph
from .solve import GRID_SIZE, success_cone
from .verilog import INTERFACES


Coordinate = tuple[int, int]


def read_image(path: Path) -> list[list[bool]]:
    bits = [line == "1" for line in path.read_text(encoding="ascii").splitlines()]
    if len(bits) != GRID_SIZE * GRID_SIZE:
        raise ValueError(f"Expected 121 bits in {path}, found {len(bits)}")
    return [bits[start : start + GRID_SIZE] for start in range(0, len(bits), GRID_SIZE)]


def basic_rules(image: list[list[bool]]) -> tuple[list[int], list[int], list[tuple[Coordinate, Coordinate]]]:
    row_counts = [sum(row) for row in image]
    column_counts = [sum(image[row][column] for row in range(GRID_SIZE)) for column in range(GRID_SIZE)]
    adjacent: list[tuple[Coordinate, Coordinate]] = []
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE):
            if not image[row][column]:
                continue
            for row_delta, column_delta in ((0, 1), (1, -1), (1, 0), (1, 1)):
                other = row + row_delta, column + column_delta
                if (
                    0 <= other[0] < GRID_SIZE
                    and 0 <= other[1] < GRID_SIZE
                    and image[other[0]][other[1]]
                ):
                    adjacent.append(((row, column), other))
    return row_counts, column_counts, adjacent


def final_state_for_pixel(
    circuit: CompiledCircuit,
    graph: object,
    marked: Coordinate | None,
) -> dict[str, bool]:
    state = initial_state(graph)
    inputs = {"clk": True, "rst_n": True, "enable": False, "I": False}
    state = circuit.step(state, inputs)
    inputs["enable"] = True
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE):
            inputs["I"] = marked == (row, column)
            state = circuit.step(state, inputs)
    return state


def state_footprints(graph: object) -> dict[frozenset[Coordinate], list[str]]:
    """Find which input coordinates can set each persistent state bit."""

    circuit = CompiledCircuit.from_graph(graph)
    baseline = final_state_for_pixel(circuit, graph, None)
    affected_by: dict[str, set[Coordinate]] = defaultdict(set)
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE):
            coordinate = row, column
            state = final_state_for_pixel(circuit, graph, coordinate)
            for net, value in state.items():
                if value != baseline[net]:
                    affected_by[net].add(coordinate)

    footprints: dict[frozenset[Coordinate], list[str]] = defaultdict(list)
    for net, coordinates in affected_by.items():
        footprints[frozenset(coordinates)].append(net.removeprefix("net:"))
    return footprints


def is_column(coordinates: frozenset[Coordinate]) -> bool:
    return len(coordinates) == GRID_SIZE and len({column for _row, column in coordinates}) == 1


def recover_regions(
    footprints: dict[frozenset[Coordinate], list[str]],
) -> list[tuple[frozenset[Coordinate], list[str]]]:
    """Separate the eleven ROM-selected region counters from column counters."""

    regions = [
        (coordinates, nets)
        for coordinates, nets in footprints.items()
        if 3 <= len(coordinates) < GRID_SIZE * GRID_SIZE and not is_column(coordinates)
    ]
    regions.sort(key=lambda item: min(item[0]))
    covered = set().union(*(coordinates for coordinates, _nets in regions))
    if len(regions) != GRID_SIZE or len(covered) != GRID_SIZE * GRID_SIZE:
        raise RuntimeError("Could not identify an eleven-region partition in checker state")
    if sum(len(coordinates) for coordinates, _nets in regions) != len(covered):
        raise RuntimeError("Candidate checker regions overlap")
    return regions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds", type=Path, nargs="?", default=Path("puzzle.gds"))
    parser.add_argument("--solution", type=Path, default=Path("artifacts/puzzle/solution.mem"))
    parser.add_argument("--models", type=Path, default=Path("third_party/sky130_fd_sc_hd"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/puzzle/star_battle.txt"))
    args = parser.parse_args()

    image = read_image(args.solution)
    rows, columns, adjacent = basic_rules(image)
    lines = [
        f"Stars per row:    {rows}",
        f"Stars per column: {columns}",
        f"Adjacent pairs:   {adjacent}",
    ]

    extracted = extract_netlist(args.gds)
    graph = build_graph(
        extracted.netlist,
        extracted.top_cell_name,
        INTERFACES[extracted.top_cell_name],
        args.models,
    )
    footprints = state_footprints(success_cone(graph))
    regions = recover_regions(footprints)
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    region_for = {
        coordinate: labels[index]
        for index, (coordinates, _nets) in enumerate(regions)
        for coordinate in coordinates
    }
    lines.extend(("", "ROM-selected Star Battle regions:"))
    for row in range(GRID_SIZE):
        lines.append(" ".join(region_for[row, column] for column in range(GRID_SIZE)))
    lines.append("Region state/counts:")
    for index, (coordinates, nets) in enumerate(regions):
        stars = sum(image[row][column] for row, column in coordinates)
        lines.append(
            f"  {labels[index]}: {len(coordinates):2} cells, {stars} stars, state {nets}"
        )
    report = "\n".join(lines) + "\n"
    print(report, end="")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
