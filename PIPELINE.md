# Reverse-engineering pipeline

This guide reproduces the project from the supplied GDS and VCD files. Run the
commands from the repository root in Git Bash on Windows.

## Setup

The pipeline requires Python, Make, and Icarus Verilog 12. Both `iverilog` and
`vvp` must be available on `PATH`. Create the project virtual environment and
install the Python dependencies with:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install klayout networkx z3-solver pytest vcdvcd
```

Stage 2 downloads the required standard-cell models from GitHub, so it also
requires an internet connection. Generated results go under `artifacts/`, while
downloaded PDK files go under `third_party/`.

## Stage 1: GDS to KLayout netlist

The extractor:

1. Opens the GDS and preserves its cell hierarchy with a
   `RecursiveShapeIterator`.
2. Imports the SKY130 `li1` through `met5` routing conductors.
3. Imports `mcon` and `via` through `via4` and connects each metal sandwich.
4. Attaches any datatype-5 labels to their corresponding conductors.
5. Calls `extract_netlist()` and exposes the resulting `db.Netlist` object.

Run it on the warm-up from the repository root:

```bash
python -m asic_re.extract warmup/04_final.gds artifacts/warmup/warmup.l2n
```

The real puzzle uses the identical extractor:

```bash
python -m asic_re.extract puzzle.gds artifacts/puzzle/puzzle.l2n
```

For the warm-up, this writes:

- `warmup.l2n`: KLayout's annotated layout/netlist database, suitable for its
  Netlist Browser.
- `warmup.txt`: KLayout's textual representation of the extracted hierarchy,
  circuits, pins, instances, and nets.
- `warmup.v`: structural Verilog containing the functional SKY130 cells and
  their recovered named-pin connections. By default this is a logic-only view:
  physical supply connections remain in the KLayout netlist but are omitted
  from the Verilog used for simulation.

The puzzle command creates the corresponding `puzzle.l2n`, `puzzle.txt`, and
`puzzle.v` files under `artifacts/puzzle`.

At this point, the extraction-only smoke test can run without the downloaded
standard-cell models:

```bash
python -m pytest tests/test_pipeline.py::test_warmup_produces_a_hierarchical_klayout_netlist
```

## Stage 2: Obtain the standard-cell behavior

Download only the official SKY130 wrappers and functional models instantiated
by either recovered netlist:

```bash
python -m asic_re.fetch_sky130_models artifacts/warmup/warmup.v artifacts/puzzle/puzzle.v --revision ac7fb61f06e6470b94e8afdf7c25268f62fbd7b1
```

The selected upstream files are stored under `third_party/sky130_fd_sc_hd`.
`SOURCE.txt` records the exact upstream commit and file list. Sequential cells
also bring in their referenced Verilog UDP model. The original Apache-2.0
license is saved alongside them.

The remaining tests and analysis stages require these models.

## Stage 3: Verify the recovered warm-up

From Git Bash, compile the functional models with the recovered warmup and run
the exhaustive testbench:

```bash
make warmup
```

The Makefile uses `iverilog` and `vvp` from `PATH`. It runs the same
self-checking testbench first against `warmup/00_source.v` and then against the
recovered structural Verilog plus the selected functional cell models. Every
one of the 65,536 pairs of 8-bit operands must produce exactly
`S = (A + B == 496)`. The two logs are written under `artifacts/warmup`.

## Stage 4: Replay the supplied puzzle example

After extracting `puzzle.gds` and fetching its cell models, replay and verify
every cycle from `example_inputs.vcd`:

```bash
make puzzle-example
```

The VCD converter samples its inputs and expected outputs at every rising clock
edge. Icarus then checks the recovered structural netlist for all 312 supplied
cycles.

Run the complete Python test suite with:

```bash
make test
```

`make test` first generates the detailed Icarus waveform needed for the
internal-state equivalence test, then invokes pytest. Running `python -m pytest`
directly is also supported because the test creates that trace on demand when it
is absent.

## Stage 5: Build the directed connectivity graph

```bash
python -m asic_re.netlist_graph puzzle.gds artifacts/puzzle/connectivity.txt
```

This reads official functional-model pin directions and constructs a directed,
bipartite NetworkX graph: input nets point into cells and cell outputs point to
nets. The report checks drivers, loads, combinational cycles, flip-flop clock
groups, and the logic cone feeding each top-level output.

## Stage 6: Solve and verify the recovered checker

```bash
make puzzle-solve
```

`asic_re/logic_sim.py` evaluates the named SKY130 gates represented in the
NetworkX graph. The test suite from Stage 4 checks its concrete Boolean mode
cycle-by-cycle against all resettable flip-flops in the Icarus trace. For the
solve, the same evaluator uses Z3 values in place of Booleans and unrolls the
success cone for the 121 enabled clocks.

The input is an 11-by-11 row-major bitmap. The solver writes its unique accepted
bitmap to `artifacts/puzzle/solution.mem`; the Icarus testbench then applies those
bits to the structural Verilog, requires `success`, and prints the real output
generator's byte stream.

## Stage 7: Recover the circuit's purpose

```bash
make puzzle-analyze
```

`asic_re/star_battle.py` verifies the accepted bitmap's row, column, and
non-adjacency properties. It then injects a single marked cell at each of the
121 positions and groups the persistent state bits by their affected positions.
This exposes eleven column counters and eleven hardwired regions without
using the original RTL. The recovered region map and per-region star counts are
written to `artifacts/puzzle/star_battle.txt`.

## Stage 8: Exercise the hidden message ROM

```bash
make puzzle-easter-eggs
```

This generates diagnostic empty, full, touching, and solved boards and applies
each one to the recovered structural Verilog. In addition to the ordinary
failure and success strings, the output generator reveals its `EMPTY SKY`,
`BIG BANG`, and `TWO NOT TOUCH` messages. The last message requires a board that
has exactly two stars in every row, column, and region but violates only the
non-touching rule.
