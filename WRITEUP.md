# Reverse-engineering the Jane Street ASIC puzzle

## Motivation

I'd come across Jane Street puzzles before, but none of them had really enticed me. I
told myself they were purely software challenges that I, as an electrical engineering
student, would lose interest in pretty quickly. When I came across a post with ASIC in
the title, though, I knew that excuse wouldn't hold up anymore and I had to at least
try it.

I also happened to have a week between the end of my internship and the start of
school, so it seemed like the perfect time. As it turns out, though, I'm much more
motivated to go hiking with my friends than to solve a puzzle in my free time. That
week came and went with a cloned repository being about all I had to show for it.

Once school started, I kept procrastinating on the puzzle so I could enjoy the
back-to-school fun. During the second week, one of my professors kept telling
the class that he no longer thought it was appropriate to spend time teaching all the
derivations and highly theoretical topics that a graduate DSP course might be known
for. Instead, he'd jump up and down to reinforce the idea that he wanted us to develop
a framework for understanding a problem, formulating it, and using the basic toolkit
we already had to find a solution. As for the theoretical proofs, he said that if we
didn't understand one, "ChatGPT will help." He also emphasized one more thing: "It is
important that you are not the slave to AI, but the master."

I figured this puzzle would be a good way to find out which one I was. I wanted to
treat Codex like I was its manager: I would choose the direction, question its
decisions, and make sure I understood and verified what it produced. But, much like
those DSP proofs, most of the code and finer implementation details would be delegated
to Codex.

## Puzzle Overview

The puzzle provides the final GDS layout of a small SkyWater SKY130 ASIC and an
example VCD. The task is to recover a usable netlist, identify what the circuit does,
find an input that makes `success` assert, and then let the circuit reveal
the final string through `O[7:0]`.

The final approach was:

1. Use KLayout's Python API, specifically `klayout.db.LayoutToNetlist`, to
   extract connectivity from the routing layers in the GDS.
2. Use a custom Python exporter, `asic_re/verilog.py`, to walk KLayout's
   `db.Netlist` object and write structural Verilog instances and named-pin
   connections.
3. Use `asic_re/fetch_sky130_models.py` to download the official wrappers,
   functional models, and UDPs for the exact SKY130 cells instantiated by the
   recovered Verilog.
4. Use `vcdvcd` to convert the supplied waveform into replay vectors, Icarus
   Verilog to simulate the original/recovered designs, and `pytest` to automate
   equivalence and integrity checks.
5. Use NetworkX from Python to represent cells and nets as a directed graph and
   examine output cones, state dependencies, feedback groups, and placement.
6. Use a custom Python cell evaluator, `asic_re/logic_sim.py`, so that the same
   recovered gates could operate on either concrete Boolean values or symbolic
   Z3 expressions.
7. Use the Z3 SMT solver to symbolically execute the 79-flop checker cone over
   121 unknown serial input bits and require the recovered `success` signal.
8. Use Icarus Verilog again to apply the solved bitmap to the complete structural
   netlist and read the final message from the separate output generator.
9. Use concrete Python simulations and NetworkX state/placement information to
   characterize Z3's bitmap, recover the hardwired region map, and identify the
   circuit as a Star Battle validator.

## Initial ideas

Right off the bat, I had Codex look through the repository to see what we had to
work with. It found two key pieces of information:

- Both sample attempts shift exactly 121 bits while `enable` is high. Afterward,
  `O` emits the null-terminated ASCII string `TRY AGAIN`.
- The GDS still contains the SKY130 standard-cell master names.

Knowing the input size, I was sure some kind of solver could've been devised to
brute-force a solution. Jumping straight to that, however, felt like it would skip the
most interesting part of the puzzle. Since the standard cells were still named, I
considered tracing their connectivity in KLayout and gradually reconstructing blocks
such as counters, registers, and comparators. That would've put my ECE skills to use,
but manually following hundreds of nets through a placed-and-routed design sounded
really tedious.

Codex helped me settle on a hardware-oriented automated flow. KLayout would handle
the geometric connectivity, Python would turn the result into structural Verilog,
and NetworkX would help me make sense of the recovered circuit. Once I trusted the
netlist and understood enough of the timing and state structure, an SMT solver could
find the input.

Working entirely on Windows was another constraint. I wanted the pipeline to run
from Git Bash without requiring WSL, so every part of the approach needed a native
Windows option. Fortunately, all of the tools above did.

## Stage 1: Recovering connectivity from GDS

### KLayout `LayoutToNetlist`

A GDS contains geometry, hierarchy, and labels, but it does not directly tell us
which standard-cell pins are connected to each other. The goal of the first stage
was to turn that physical information into a netlist containing the cell instances
and the wires between them. The layout also contains physical-only cells such as
decaps, fillers, and taps, although those would not be needed for logic simulation.

Rather than reinventing the wheel, I chose to use KLayout's `LayoutToNetlist` API. The
extractor written by Codex in `asic_re/extract.py` does the following:

1. Load the layout and select its single top-level cell.
2. Traverse the hierarchy with `RecursiveShapeIterator`.
3. Register the SKY130 routing conductors from `li1` through `met5`.
4. Register `mcon` and `via` through `via4`.
5. Connect each conductor internally and connect adjacent conductors through
   their cut layers.
6. Attach datatype-5 text layers so that labeled conductors retain useful net
   and pin names.
7. Run `extract_netlist()`.

The important layer numbers were taken from the SKY130 layer documentation.
For instance, SKY130 `met1` is GDS layer/datatype `68/20`, while `met2` is `69/20`.

Together, the extraction command and Verilog exporter write three useful views:

- An `.l2n` database for KLayout's Netlist Browser.
- A textual dump of KLayout's complete extracted hierarchy.
- Logic-only structural Verilog for simulation.

The `.l2n` file is more than an ordinary text netlist: it preserves the
association between electrical nets and layout geometry, which could be useful
in analyzing the layout later in the KLayout GUI.

### Turning the KLayout netlist into Verilog

KLayout's `db.Netlist` object already contained most of what was needed for
structural Verilog: the top-level circuit, its nets, each standard-cell instance,
and the net attached to each named pin. Codex wrote a custom exporter in
`asic_re/verilog.py` which mostly translates that representation rather than trying to
infer any logic.

First, it finds the extracted top-level circuit and checks that the expected ports
(`clk`, `rst_n`, `enable`, `I`, `success`, and `O[7:0]`) are present. It then gives
every internal KLayout net a legal and unique Verilog identifier. For example,
KLayout's generated net name `$1447` becomes `n_1447`.

Next, the exporter walks through every subcircuit instantiated by the top-level
circuit. Physical-only cells such as fillers, taps, and decaps are skipped. For
each functional SKY130 cell, the script keeps the exact cell type and drive
strength, creates an instance name, and asks KLayout which net is connected to
each pin. A recovered connection is emitted in ordinary named-port form:

```verilog
sky130_fd_sc_hd__and2_2 u_123 (
    .A(n_10),
    .B(n_20),
    .X(n_30)
);
```

Power pins are left out of the simulation version because the functional cell
models can provide their own supplies. The complete power connectivity is still
preserved in the `.l2n` database. The result is a structural description of the
recovered topology: it says which cells exist and how they are wired, but the
Boolean behavior of those cells still has to come from the SKY130 models in the
next stage.

### A couple of hiccups

One cell exposed an extra unnamed physical terminal that was not part of its
official SKY130 interface, so the exporter had to ignore unnamed pins instead of
producing an invalid Verilog connection. A later graph check also found one net,
`$1447`, with loads but no recovered driver. It had no path to `success`, so it did
not affect the solve, although it caused a couple of `X/Z` bits while probing one
Easter-egg message. Codex kept both oddities visible in the extracted `.l2n` data
instead of quietly hiding them, and the later equivalence tests gave me confidence
that they were not signs of a larger extraction problem.

## Stage 2: Adding executable cell behavior

At this point, the structural Verilog described the topology, but it did not yet
define what an `and2`, `a31oi`, or `dfrtp` cell actually did. Without those module
definitions, a simulator would know that the instances existed but would have no
way to evaluate them. I could've written my own models—or had Codex write them—but
that would have meant recreating behavior already published by the PDK authors.
Sticking with the theme of not reinventing the wheel, we decided to use Google's
open-source `sky130_fd_sc_hd` library.

There are a few layers to the SKY130 Verilog models. A drive-strength-specific
module such as `sky130_fd_sc_hd__and2_2` is mostly a wrapper around the common
`and2` implementation. That implementation selects a functional, behavioral, or
timing model depending on the simulator options. The functional models describe
combinational cells with Verilog primitives, while flip-flops also include
user-defined primitives, or UDPs, to describe their sequential behavior.

Downloading the entire standard-cell repository would have worked, but it was
more than I needed. Codex created a Python script `asic_re/fetch_sky130_models.py`
to scan the recovered structural Verilog for every instantiated SKY130 module.
For each one, the script downloads the exact drive-strength wrapper, the shared
family wrapper, the functional model, and any UDP files included by that model.
The source commit is pinned so that rerunning the pipeline cannot silently pick
up a different version of the library.

This stage turns the recovered Verilog from a list of connected black boxes into
an executable gate-level model. It still looks like a standard-cell netlist, but
Icarus Verilog can now evaluate every gate and flip-flop in it.

## Stage 3: Verifying the recovered warm-up

Before trying to figure out what the real puzzle did, I wanted to know whether the
GDS-to-Verilog flow worked at all. The warm-up was essentially a free end-to-end
test because it included both the original RTL and the final GDS generated from it.

I had Codex create an exhaustive testbench for the warm-up circuit,
but Icarus compiled the same testbench twice. The first version used the original
RTL, while the second used the structural Verilog recovered from its GDS along
with the SKY130 functional models. Each run checked all 65,536 input pairs and
confirmed the same condition:

```text
A + B == 496
```

Passing the exhaustive comparison did not prove that the real puzzle would be
extracted perfectly, but it gave me a lot more confidence in the pipeline before
moving on to a design with no source RTL.

## Stage 4: Replaying the supplied puzzle waveform

The VCD supplied with the real puzzle provided a smaller behavioral reference. It
recorded the top-level inputs and outputs for two complete attempts, so the next
reasonable test was to feed those same inputs into the recovered netlist and check
whether it responded identically.

Codex created `asic_re/vcd_stimulus.py`, which uses `vcdvcd` to find every rising
clock edge in the supplied waveform. At each edge, it samples `rst_n`, `enable`,
and `I`, along with the expected `success` and `O[7:0]`, and writes one compact
test vector. The Verilog testbench then replays those vectors against the recovered
circuit and compares its outputs after each clock edge.

The recovered netlist matched all 312 recorded cycles, including both `TRY AGAIN`
messages. Unlike the warm-up test, this was not exhaustive, but it checked the real
puzzle's reset behavior, sequential timing, checker output, and message generator all at once.

With that, it was finally time to figure out what the circuit actually did.

## Stage 5: Structural analysis with NetworkX

Structural Verilog was useful for simulation, but it was not a convenient format
for asking questions like "Which registers can affect `success`?" or "Are these
flip-flops part of the same feedback loop?" Yosys could have converted the Verilog
into another intermediate representation, but at this point the circuit was already
available as a KLayout `db.Netlist` inside Python. I had Codex build a NetworkX view
of that object instead.

The script `asic_re/netlist_graph.py` reads the official functional models to learn
which pins are inputs and outputs. It then creates three kinds of graph nodes—ports,
electrical nets, and cells—and connects them in the direction that information flows:

```text
input port -> electrical net -> cell -> electrical net -> ... -> output port
```

Each cell node is annotated with its family, whether it is sequential, and its
physical `(x, y)` placement. For a flip-flop, an edge enters through its `D` net and
another leaves through its `Q` net. That does not mean the graph is treating a
flip-flop as combinational logic; it only records which state can influence which
other state on a later clock edge.

This representation did not replace the structural Verilog. Icarus remained the
source of truth for clock-accurate simulation, while NetworkX made structural
questions easier. The script could walk backward from an output to find its entire
logic cone, check nets for missing or multiple drivers, remove the flip-flops and
look for combinational cycles, and collapse the gates between registers into a
smaller state-dependency graph. Those operations set up the analysis in the next
stage.

## Stage 6: Make the success flag high!

### Separating checker and output generator

The first useful question was which parts of the chip were actually involved in
deciding `success`. Codex walked backward through the graph from that output and
found a checker cone containing 484 functional cells and 79 of the 92 flip-flops.
The remaining 13 flip-flops affected only `O[7:0]`, placing them in the output
generator that the puzzle said could be ignored initially.

Codex then collapsed each flip-flop's combinational `D` logic into dependencies
between state bits and looked for strongly connected groups. A strongly connected
group means that every register in the group can eventually feed back into every
other register, which is the kind of pattern expected from a counter or state
machine. Codex presented several useful structures:

- A nine-FF feedback group implementing position and completion control.
- A twelve-stage history of the serial `I` signal.
- An eight-FF group in the output-generator area.
- A four-FF unreset group involved in output sequencing.

The nine control bits were especially helpful. Four bits behaved like an inner
counter that ran from 0 through 10 and then wrapped. Four more bits incremented
whenever that inner counter wrapped, also running from 0 through 10. The final bit
marked completion. In other words, the hardware was not merely counting to 121;
it was maintaining a pair of nested 11-position counters. Combined with the 121
enabled clocks in the supplied VCD, that strongly suggested two-dimensional
coordinates:

```text
inner position: 0..10
outer position: 0..10

11 * 11 = 121 input positions
```

The twelve other flip-flops behaved as exact delayed copies of `I`, covering delays
from zero through eleven clocks. Before knowing the final purpose, this looked like
a short history window for a row-major, two-dimensional stream. It meant the
circuit could compare the current input with recently visited positions in the same
row and the previous row.

After accounting for the control, input history, and final success/control
state, 56 checker state bits remained. These were not a single 56-bit register;
they were simply the part of the distributed checker state that had not yet been
classified. At this point, I still did not know what high-level puzzle the hardware
was checking.

### Making the recovered graph executable

NetworkX knew how the circuit was connected, but it did not calculate values. To
use Z3 without bringing in another synthesis tool, Codex proposed a small evaluator
in `asic_re/logic_sim.py`. It implements the Boolean equation for every
combinational cell family found in the puzzle. The same functions can operate on
ordinary Python `bool` values or on Z3 Boolean expressions.

With normal Boolean inputs, the evaluator acts like a simple two-state gate-level
simulator: it evaluates the combinational cells in topological order and captures
each flip-flop's `D` value on a simulated rising edge. Codex compared those state
updates against a detailed Icarus waveform. Every resettable flip-flop agreed on
every recorded cycle. The four unreset `dfxtp` cells were excluded because they
begin as unknowns in four-state Verilog and were part of the output-side logic, not
the `success` cone.

With Z3 expressions instead of ordinary Booleans, the evaluator does something
different. Rather than choosing whether an input is zero or one, it carries a
symbolic variable through each gate. After many clock cycles, `success` becomes one
large Boolean expression involving all the unknown input bits. Z3's job is to find
an assignment to those bits that makes the expression true. This is closer to
solving a compiled set of constraints than trying all `2^121` possibilities one at
a time.

### Interpreting the example input

I knew from the sample VCD that the serial input was exactly 121 clocks. Once
the nested counters suggested an 11-by-11 scan, Codex split each attempt into eleven
groups of eleven bits. It noticed that the first seven bits of each group, interpreted
as least-significant-bit-first ASCII, read:

```text
"The night s"
"ky awaits  "
```

Together they form:

```text
The night sky awaits
```

Codex initially interpreted the remaining four positions in each group as protocol
padding and tried constraining the symbolic input to eleven printable seven-bit
characters followed by four zeros each. That was Codex's assumption, and
it turned out to be wrong: Z3 reported that those constraints were unsatisfiable.
That failure showed that the VCD's text was a clue about the circuit's purpose,
not the required input format.

### What was understood before using Z3?

Before the successful solve, Codex had identified the nested counters, recent-input
history, and separation between the checker and output generator. The supplied VCD
had also revealed both the 121-position input window and the hidden phrase. What I
did not yet have was a high-level explanation for the 56 remaining state bits or
the condition that would make `success` true.

So Z3 did ultimately find the answer directly from the recovered gate-level
checker. That was not quite the satisfying, block-by-block hardware reversal I had
originally imagined. On the other hand, it was not a black-box attack on the input
and output pins either: the solver was evaluating the connectivity and Boolean
behavior recovered from the physical layout. The later analysis would still be
needed to explain what the solution meant and what the unidentified state was
doing.

### Solving the gate-level checker

For the successful attempt, Codex removed the printable-character and padding
assumptions. All 121 serial positions became independent symbolic bits. The solver
did not receive any handwritten rules for the unknown puzzle; it only executed the
recovered gates:

1. Initialize the resettable state.
2. Execute the idle rising edge after reset is released.
3. Create 121 independent Z3 Boolean variables, one per serial input position.
4. Execute 121 enabled clock transitions through the recovered checker cone.
5. Execute the first rising edge after `enable` falls.
6. Constrain the recovered `success` output to be true.

The nested 11-by-11 counters gave a natural way to arrange the returned bits. Z3
produced this map:

```text
.......*.*.
*....*.....
.......*.*.
*.*........
....*.*....
..*.....*..
....*.....*
.*....*....
...*......*
.....*..*..
.*.*.......
```

The solver was run again requiring an accepted bitmap different from the first.
It was unsatisfiable, showing that the accepted bitmap is unique.

### Reading the final answer from the circuit

Codex wrote the solved row-major bitmap to `artifacts/puzzle/solution.mem` and
applied it to the complete recovered structural Verilog, including the 13-flop
output generator that had been excluded from the Z3 checker cone.

On the first rising edge after `enable` fell, `success` asserted. The output
generator then emitted:

```text
(* TWO STARS *)
```

## Stage 7: Identifying Star Battle and recovering its regions

The solved bitmap contains:

- Exactly two stars in every row.
- Exactly two stars in every column.
- No horizontally, vertically, or diagonally adjacent stars.

These are the characteristic rules of a two-star Star Battle puzzle, something
I'd never heard of before. Looking up the rules filled in the missing piece: a
Star Battle board is also divided into irregular regions, each of which must
contain exactly two stars.

The bitmap alone did not show where those regions were, so Codex proposed probing
the checker one input position at a time. The script first simulated an all-zero
board to obtain a baseline state. It then ran 121 more simulations, each with a
single marked position, and recorded which persistent state bits could be changed
by each coordinate.

This produced two recognizable classes of footprints:

- Eleven vertical sets, each covering one complete column: the column counters.
- Eleven irregular, disjoint sets that together cover the complete board: the
  region counters selected by hardwired decode logic.

The recovered regions are:

```text
A A A A A B B C D D E
A A F A A B C C D D E
A A F B B B B C C D E
A A F B G G G E C C E
F A F B G E E E E E E
F F F B G G G E H H H
B B B B B B G E H I I
B J J J G G G E H I I
B J J K E E E E H I I
B B J K K E E E H H H
B J J K E E E E E E E
```

Overlaying the solved bitmap on these regions showed exactly two stars in each
one. That accounted for the last major piece of checker state and completed the
identification of the circuit as a two-star Star Battle validator.

## Looking back at the layout

The puzzle post says that the physical arrangement hints at the circuit's
functionality. I did not understand what that meant while solving it, so afterward
I asked Codex to compare the placement of the recovered state with the blocks we
had identified. The layout does separate the major parts of the design:

- The nine-flop row/column position controller is grouped toward the left.
- The twelve-stage serial-input history used for neighbor checks occupies another
  narrow strip closer to the center.
- The region counters and column counters form two distinct banks near one another.
- The output generator is isolated on the right, matching the boxed area in the
  supplied annotated image.

![Puzzle layout](layout.png)

Put together, those groups roughly follow this path across the floorplan:

```text
position control -> recent-pixel history -> region/column accumulators -> result -> message output
```

So the physical arrangement really does reflect the circuit's data flow, even if
it did not immediately reveal "Star Battle" to me. Of course, those groups are much
easier to recognize after already knowing what the circuit does.

## Stage 8: Exercising the hidden message ROM

The output generator contains several messages in addition to `TRY AGAIN` and
the successful result. Conceptually, it acts like a small ROM addressed by an
error/result classification and a four-bit character position.

Codex then generated several diagnostic boards and ran them through the complete
recovered circuit. They revealed:

- An empty board produces `EMPTY SKY`.
- A completely full board produces `BIG BANG`.
- A board with two stars in every row, column, and region but with touching
  stars produces `TWO NOT TOUCH`.
- An ordinary invalid board produces `TRY AGAIN`.
- The unique valid board produces `(* TWO STARS *)`.

The third case is deliberately selective. A board containing only one touching
pair still produces `TRY AGAIN`, because it also fails the count constraints.
The parentheses and asterisks around the successful message also make it look
like an OCaml comment.

## Other Easter eggs

A friend pointed out two quieter details elsewhere in the supplied files.

### VCD metadata

The supplied VCD is dated:

```text
Sat Dec 31 23:59:60 2016
```

The `23:59:60` timestamp denotes the leap second that occurred at the end of
2016. It also loosely fits the night-sky theme, since leap seconds exist to keep
civil time aligned with the Earth's rotation.

The VCD's version field normally identifies the program that generated the file.
Instead, this one reads:

```text
Leave no stone unturned! But for this file, consider looking at it in a waveform viewer instead.
```

That message is both the Easter egg and a hint. Opening the file in a waveform
viewer makes the two 121-bit input attempts visible, which is what eventually
exposes the hidden `THE NIGHT SKY AWAITS` phrase.

### The warm-up constant

The number 496 does not point toward Star Battle either. It is a mathematical
Easter egg hidden in what otherwise looks like an arbitrary warm-up comparison
constant. A perfect number equals the sum of all its positive divisors other than
itself. The first three are 6, 28, and 496, and in this case:

```text
496 = 1 + 2 + 4 + 8 + 16 + 31 + 62 + 124 + 248
```

The comparison still gives the warm-up a concrete target, but the authors chose
a number with a little more personality than a random constant.

## Reproducing the work

`PIPELINE.md` contains the complete Windows/Bash commands.

The generated `artifacts` directory contains the KLayout database, textual
netlist, structural Verilog, replay vectors, simulation bytecode and logs,
solved bitmap, recovered region report, and diagnostic Easter-egg inputs.

## Final thoughts

When I started, I thought I was going to approach this project like a micromanager.
I wanted to inspect every step, understand every line of code, and test everything.
I quickly realized that the project was too large for me to understand every detail
in the time I had left, especially after all my procrastination. I couldn't review
every line Codex wrote or follow every small debugging decision it made.

I did make sure I not only understood but drove the important decisions:
why each tool was needed, what information passed between stages, what the tests
actually proved, and how the final constraints related to the recovered hardware.
As the project went on, I became more comfortable giving Codex higher-level goals
and judging its work by the artifacts, explanations, and independent checks it produced.
That let me explore and accomplish something I probably wouldn't have managed on my own,
but it also left me with a fairly large codebase that I don't understand line by line.
That may be acceptable for a puzzle, but understanding every part becomes much more
important in a career designing chips. A mistake found after fabrication could leave
you with a very expensive paperweight, and given its size, not even a very good one.

That brings me back to my professor's question of whether I was the master or the
slave to AI. I don't think the answer is as simple as I hoped when I started. I
directed the approach, questioned the choices, and reran the pipeline myself, but I
also relied heavily on Codex for both the implementation and several of the key
discoveries. At the very least, this project reinforced something any good engineer
already knows: using a tool well requires knowing what to question, what to verify,
and when an answer does not make sense.
