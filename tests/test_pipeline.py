from pathlib import Path
from collections import Counter
import re

from asic_re.extract import extract_netlist
from asic_re.fetch_sky130_models import cell_modules_in_verilog
from asic_re.netlist_graph import build_graph
from asic_re.vcd_stimulus import generate_vectors
from asic_re.verilog import INTERFACES, emit_structural_verilog


ROOT = Path(__file__).resolve().parents[1]


def test_warmup_produces_a_hierarchical_klayout_netlist() -> None:
    extracted = extract_netlist(ROOT / "warmup" / "04_final.gds")
    circuits = tuple(extracted.netlist.each_circuit())
    netlist_text = str(extracted.netlist)

    assert extracted.top_cell_name == "adder_demo"
    assert extracted.l2n.netlist() is extracted.netlist
    assert len(circuits) > 1
    assert any(circuit.name == "adder_demo" for circuit in circuits)
    assert any(circuit.name.startswith("sky130_fd_sc_hd__") for circuit in circuits)
    assert "RESET_B=rst_n" in netlist_text
    assert "X=S" in netlist_text

    verilog = emit_structural_verilog(
        extracted.netlist,
        extracted.top_cell_name,
        INTERFACES[extracted.top_cell_name],
    )
    assert "module adder_demo" in verilog
    assert "input rst_n;" in verilog
    assert ".RESET_B(rst_n)" in verilog
    assert ".X(S)" in verilog
    assert ".VPWR(" not in verilog
    assert ".VGND(" not in verilog
    assert ".(" not in verilog
    assert "supply1 VPWR" not in verilog

    recovered_cells = Counter(
        re.findall(r"^  (sky130_fd_sc_hd__\S+) u_", verilog, flags=re.MULTILINE)
    )
    assert set(cell_modules_in_verilog(verilog)) == set(recovered_cells)
    golden_text = (ROOT / "warmup" / "01_netlist.v").read_text(encoding="utf-8")
    golden_cells = Counter(
        cell
        for cell in re.findall(
            r"^\s+(sky130_fd_sc_hd__\S+)\s+[^;]+\(",
            golden_text,
            flags=re.MULTILINE,
        )
        if "__decap_" not in cell and "__tap" not in cell
    )
    assert recovered_cells == golden_cells


def test_example_vcd_contains_312_replay_cycles(tmp_path: Path) -> None:
    memory = tmp_path / "example.mem"
    header = tmp_path / "example.vh"

    count = generate_vectors(ROOT / "example_inputs.vcd", memory, header)

    assert count == 312
    assert len(memory.read_text(encoding="ascii").splitlines()) == count
    assert header.read_text(encoding="ascii") == "`define PUZZLE_EXAMPLE_CYCLES 312\n"


def test_warmup_graph_has_expected_state() -> None:
    extracted = extract_netlist(ROOT / "warmup" / "04_final.gds")
    graph = build_graph(
        extracted.netlist,
        extracted.top_cell_name,
        INTERFACES[extracted.top_cell_name],
        ROOT / "third_party" / "sky130_fd_sc_hd",
    )

    cells = [node for node, data in graph.nodes(data=True) if data["kind"] == "cell"]
    flops = [node for node in cells if graph.nodes[node]["sequential"]]
    assert len(cells) == 79
    assert len(flops) == 16
