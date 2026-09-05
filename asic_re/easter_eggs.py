"""Generate diagnostic 11x11 inputs for probing hidden output messages."""

from __future__ import annotations

from pathlib import Path

import z3

from .solve import GRID_SIZE


REGION_MAP = (
    "AAAAABBCDDE",
    "AAFAABCCDDE",
    "AAFBBBBCCDE",
    "AAFBGGGECCE",
    "FAFBGEEEEEE",
    "FFFBGGGEHHH",
    "BBBBBBGEHII",
    "BJJJGGGEHII",
    "BJJKEEEEHII",
    "BBJKKEEEHHH",
    "BJJKEEEEEEE",
)


def write_bits(path: Path, image: list[list[bool]]) -> None:
    path.write_text(
        "".join(f"{'1' if pixel else '0'}\n" for row in image for pixel in row),
        encoding="ascii",
    )


def board_with_valid_counts_but_touching_stars() -> list[list[bool]]:
    pixels = [
        [z3.Bool(f"touch_{row}_{column}") for column in range(GRID_SIZE)]
        for row in range(GRID_SIZE)
    ]
    solver = z3.Solver()
    for row in pixels:
        solver.add(z3.Sum([z3.If(pixel, 1, 0) for pixel in row]) == 2)
    for column in range(GRID_SIZE):
        solver.add(
            z3.Sum([z3.If(pixels[row][column], 1, 0) for row in range(GRID_SIZE)]) == 2
        )
    for region in sorted(set("".join(REGION_MAP))):
        members = [
            pixels[row][column]
            for row in range(GRID_SIZE)
            for column in range(GRID_SIZE)
            if REGION_MAP[row][column] == region
        ]
        solver.add(z3.Sum([z3.If(pixel, 1, 0) for pixel in members]) == 2)

    touching = []
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE):
            for row_delta, column_delta in ((0, 1), (1, -1), (1, 0), (1, 1)):
                other_row = row + row_delta
                other_column = column + column_delta
                if 0 <= other_row < GRID_SIZE and 0 <= other_column < GRID_SIZE:
                    touching.append(
                        z3.And(pixels[row][column], pixels[other_row][other_column])
                    )
    solver.add(z3.Or(*touching))
    if solver.check() != z3.sat:
        raise RuntimeError("Could not construct the touching-stars diagnostic board")
    model = solver.model()
    return [
        [z3.is_true(model.eval(pixel, model_completion=True)) for pixel in row]
        for row in pixels
    ]


def main() -> None:
    output = Path("artifacts/puzzle/easter_eggs")
    output.mkdir(parents=True, exist_ok=True)

    empty = [[False] * GRID_SIZE for _ in range(GRID_SIZE)]
    full = [[True] * GRID_SIZE for _ in range(GRID_SIZE)]
    touching_pair = [[False] * GRID_SIZE for _ in range(GRID_SIZE)]
    touching_pair[5][5] = True
    touching_pair[5][6] = True

    solution_bits = [
        line == "1"
        for line in Path("artifacts/puzzle/solution.mem").read_text(encoding="ascii").splitlines()
    ]
    solution = [
        solution_bits[start : start + GRID_SIZE]
        for start in range(0, len(solution_bits), GRID_SIZE)
    ]
    solution_with_touch = [row.copy() for row in solution]
    solution_with_touch[0][8] = True
    valid_counts_but_touching = board_with_valid_counts_but_touching_stars()

    for name, image in {
        "empty": empty,
        "full": full,
        "touching_pair": touching_pair,
        "solution_with_touch": solution_with_touch,
        "valid_counts_but_touching": valid_counts_but_touching,
        "solution": solution,
    }.items():
        write_bits(output / f"{name}.mem", image)


if __name__ == "__main__":
    main()
