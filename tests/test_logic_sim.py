from bisect import bisect_right
from pathlib import Path
import subprocess

from vcdvcd import VCDVCD

from asic_re.extract import extract_netlist
from asic_re.logic_sim import CompiledCircuit, flops_in_graph, initial_state
from asic_re.netlist_graph import build_graph
from asic_re.verilog import INTERFACES


ROOT = Path(__file__).resolve().parents[1]


def _value_at(transitions: list[tuple[int, str]], time: int) -> str:
    times = [transition_time for transition_time, _value in transitions]
    return transitions[bisect_right(times, time) - 1][1]


def _vcd_net_name(graph_net: str) -> str:
    name = graph_net.removeprefix("net:")
    if name.startswith("$"):
        name = "n_" + name[1:]
    return f"puzzle_example_tb.dut.{name}"


def test_boolean_evaluator_matches_resettable_flops_in_icarus_trace() -> None:
    trace = ROOT / "artifacts" / "puzzle" / "puzzle_example_trace.vcd"
    if not trace.exists():
        # The ordinary example replay avoids writing a large waveform. Build it
        # on demand only for this internal-state equivalence test.
        subprocess.run(["make", "-s", "puzzle-trace"], cwd=ROOT, check=True)

    extracted = extract_netlist(ROOT / "puzzle.gds")
    graph = build_graph(
        extracted.netlist,
        extracted.top_cell_name,
        INTERFACES[extracted.top_cell_name],
        ROOT / "third_party" / "sky130_fd_sc_hd",
    )
    vcd = VCDVCD(str(trace))
    circuit = CompiledCircuit.from_graph(graph)
    vectors = (ROOT / "artifacts" / "puzzle" / "example_inputs.mem").read_text(
        encoding="ascii"
    ).splitlines()

    # The supplied VCD contains two attempts, each preceded by reset.
    state = initial_state(graph)
    resettable_flops = [
        flop for flop in flops_in_graph(graph) if flop.family != "dfxtp"
    ]
    for cycle, vector in enumerate(vectors):
        rst_n, enable, serial_input = (bit == "1" for bit in vector[:3])
        if rst_n:
            state = circuit.step(
                state,
                {"clk": True, "rst_n": rst_n, "enable": enable, "I": serial_input},
            )
        else:
            state = initial_state(graph)
        sample_time = 5_000 + 10_000 * cycle
        for flop in resettable_flops:
            signal = _vcd_net_name(flop.q_net)
            observed = _value_at(vcd[signal].tv, sample_time)
            assert observed in {"0", "1"}
            assert state[flop.q_net] == (observed == "1"), (
                f"cycle {cycle}: {flop.node} ({flop.q_net})"
            )
