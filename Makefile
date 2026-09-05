SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

IVERILOG ?= iverilog
VVP ?= vvp
PYTHON ?= ./.venv/Scripts/python.exe

WARMUP_ARTIFACTS := artifacts/warmup
WARMUP_TB := tests/verilog/warmup_tb.v
SKY130_ROOT := third_party/sky130_fd_sc_hd

SKY130_WRAPPERS := $(shell find $(SKY130_ROOT)/cells -type f \
	-name 'sky130_fd_sc_hd__*_[0-9]*.v' | sort)
SKY130_MODELS := $(shell find $(SKY130_ROOT)/cells $(SKY130_ROOT)/models \
	-type f -name '*.v' | sort)
SKY130_INCLUDE_FLAGS := $(foreach directory,$(sort $(dir $(SKY130_WRAPPERS))),-I$(directory))

REFERENCE_VVP := $(WARMUP_ARTIFACTS)/warmup_reference.vvp
EXTRACTED_VVP := $(WARMUP_ARTIFACTS)/warmup_extracted.vvp
PASS_MESSAGE := PASS: checked all 65536 operand pairs

PUZZLE_ARTIFACTS := artifacts/puzzle
PUZZLE_TB := tests/verilog/puzzle_example_tb.v
PUZZLE_MEMORY := $(PUZZLE_ARTIFACTS)/example_inputs.mem
PUZZLE_HEADER := $(PUZZLE_ARTIFACTS)/example_inputs.vh
PUZZLE_VVP := $(PUZZLE_ARTIFACTS)/puzzle_example.vvp
PUZZLE_PASS_MESSAGE := PASS: recovered puzzle matches all 312 example cycles
PUZZLE_SOLUTION_TB := tests/verilog/puzzle_solution_tb.v
PUZZLE_SOLUTION_VVP := $(PUZZLE_ARTIFACTS)/puzzle_solution.vvp
PUZZLE_PROBE_TB := tests/verilog/puzzle_probe_tb.v
PUZZLE_PROBE_VVP := $(PUZZLE_ARTIFACTS)/puzzle_probe.vvp

.PHONY: warmup test FORCE
warmup: $(REFERENCE_VVP) $(EXTRACTED_VVP)
	@echo "Checking the original RTL..."
	@$(VVP) $(REFERENCE_VVP) | tee $(WARMUP_ARTIFACTS)/warmup_reference.log
	@grep -Fxq '$(PASS_MESSAGE)' $(WARMUP_ARTIFACTS)/warmup_reference.log
	@echo "Checking the recovered gate-level netlist..."
	@$(VVP) $(EXTRACTED_VVP) | tee $(WARMUP_ARTIFACTS)/warmup_extracted.log
	@grep -Fxq '$(PASS_MESSAGE)' $(WARMUP_ARTIFACTS)/warmup_extracted.log
	@echo "Warmup RTL and recovered netlist passed the same exhaustive test."

# The Python equivalence test consumes the internal waveform produced by
# puzzle-trace, so make sure that generated prerequisite exists first.
test: puzzle-trace
	$(PYTHON) -m pytest -q

# Icarus's compiled .vvp format is not compatible across all releases. FORCE
# prevents a simulator upgrade from reusing bytecode produced by an older one.
FORCE:

$(REFERENCE_VVP): FORCE warmup/00_source.v $(WARMUP_TB)
	@mkdir -p $(WARMUP_ARTIFACTS)
	$(IVERILOG) -g2005 -s warmup_tb -o $@ warmup/00_source.v $(WARMUP_TB)

$(EXTRACTED_VVP): FORCE $(WARMUP_ARTIFACTS)/warmup.v $(WARMUP_TB) $(SKY130_MODELS)
	@mkdir -p $(WARMUP_ARTIFACTS)
	$(IVERILOG) -g2005 -grelative-include -DFUNCTIONAL '-DUNIT_DELAY=#0' \
		-s warmup_tb -o $@ $(SKY130_INCLUDE_FLAGS) \
		$(SKY130_WRAPPERS) $(WARMUP_ARTIFACTS)/warmup.v $(WARMUP_TB)

.PHONY: puzzle-example puzzle-trace puzzle-solve puzzle-analyze puzzle-easter-eggs
puzzle-example: $(PUZZLE_VVP)
	@echo "Replaying and checking example_inputs.vcd..."
	@$(VVP) $(PUZZLE_VVP) | tee $(PUZZLE_ARTIFACTS)/puzzle_example.log
	@grep -Fxq '$(PUZZLE_PASS_MESSAGE)' $(PUZZLE_ARTIFACTS)/puzzle_example.log

puzzle-trace: $(PUZZLE_VVP)
	@$(VVP) $(PUZZLE_VVP) +dump

puzzle-solve:
	$(PYTHON) -m asic_re.solve
	$(IVERILOG) -g2005 -grelative-include -DFUNCTIONAL '-DUNIT_DELAY=#0' \
		-s puzzle_solution_tb -o $(PUZZLE_SOLUTION_VVP) \
		$(SKY130_INCLUDE_FLAGS) $(SKY130_WRAPPERS) \
		$(PUZZLE_ARTIFACTS)/puzzle.v $(PUZZLE_SOLUTION_TB)
	$(VVP) $(PUZZLE_SOLUTION_VVP)

puzzle-analyze: puzzle-solve
	$(PYTHON) -m asic_re.star_battle

puzzle-easter-eggs: puzzle-solve
	$(PYTHON) -m asic_re.easter_eggs
	$(IVERILOG) -g2005 -grelative-include -DFUNCTIONAL '-DUNIT_DELAY=#0' \
		-s puzzle_probe_tb -o $(PUZZLE_PROBE_VVP) \
		$(SKY130_INCLUDE_FLAGS) $(SKY130_WRAPPERS) \
		$(PUZZLE_ARTIFACTS)/puzzle.v $(PUZZLE_PROBE_TB)
	@for case_name in empty full touching_pair solution_with_touch valid_counts_but_touching solution; do \
		$(VVP) $(PUZZLE_PROBE_VVP) \
			+input=$(PUZZLE_ARTIFACTS)/easter_eggs/$$case_name.mem \
			+name=$$case_name; \
	done

$(PUZZLE_MEMORY) $(PUZZLE_HEADER) &: example_inputs.vcd asic_re/vcd_stimulus.py
	@mkdir -p $(PUZZLE_ARTIFACTS)
	$(PYTHON) -m asic_re.vcd_stimulus example_inputs.vcd \
		$(PUZZLE_MEMORY) $(PUZZLE_HEADER)

$(PUZZLE_VVP): FORCE $(PUZZLE_ARTIFACTS)/puzzle.v $(PUZZLE_TB) \
		$(PUZZLE_MEMORY) $(PUZZLE_HEADER) $(SKY130_MODELS)
	@mkdir -p $(PUZZLE_ARTIFACTS)
	$(IVERILOG) -g2005 -grelative-include -DFUNCTIONAL '-DUNIT_DELAY=#0' \
		-I$(PUZZLE_ARTIFACTS) -s puzzle_example_tb -o $@ \
		$(SKY130_INCLUDE_FLAGS) $(SKY130_WRAPPERS) \
		$(PUZZLE_ARTIFACTS)/puzzle.v $(PUZZLE_TB)
