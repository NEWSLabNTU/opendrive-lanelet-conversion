# Map conversion - OpenDRIVE => Lanelet2

- Autoware uses Lanelet2 format for its ODD testing.
- We want more scenarios.
- ASAM OpenDRIVE dataset is a good option.

So, we need a way to convert OpenDRIVE to Lanelet2. We tried [CommonRoad Scenario Designer conversion](https://commonroad-scenario-designer.readthedocs.io/en/latest/details/open_drive/), the conversion generally worked, but the maps were containing way too many nodes, making it unusable on Autoware.

I'm attempting to fix that with custom downsampling plus Autoware-specific tagging
and topology fixes, layered on a pinned fork of crdesigner.

## Layout

- `convert.py` — single-file OpenDRIVE → Lanelet2 CLI
- `demo_evan.py` — batch conversion over `sample_data/`
- `utils/` — geometry, postprocessing, config, and the `validate.py` requirement checker
- `sample_data/` — input `.xodr` maps; `output/` — generated `.osm` (gitignored)
- `extern/commonroad-scenario-designer/` — **git submodule**: the patched crdesigner
  (`autoware_dev` fork), pinned to an exact commit so results are reproducible

## Reproduce

Requires Python 3.10 and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Clone WITH the crdesigner submodule
git clone --recurse-submodules https://github.com/yande001/opendrive-lanelet-conversion.git
cd opendrive-lanelet-conversion
#    (already cloned without it? run:)
#    git submodule update --init --recursive

# 2. Create the venv and install the *pinned* dependencies
uv venv olconversion --python 3.10
uv pip install -r requirements.lock --python olconversion/bin/python

# 3. Convert a map
uv run --python olconversion/bin/python convert.py sample_data/custom/two_to_three_lanes_rht.xodr output/example.osm

# 4. (optional) Grade the output against the Autoware LL2 requirements
uv run --python olconversion/bin/python -c \
  "from utils.validate import validate_file; [print(r.line()) for r in validate_file('output/example.osm')]"
```

`requirements.lock` pins every package (including crdesigner via the submodule), so
a fresh install resolves identical versions. Do **not** `pip install
commonroad-scenario-designer` from PyPI — that release lacks the autoware_dev changes.

## Working on crdesigner

`extern/commonroad-scenario-designer` is a normal checkout on branch `autoware_dev`.
To change converter behaviour:

```bash
cd extern/commonroad-scenario-designer
# edit, then:
git commit -am "..." && git push          # push to the crdesigner repo
cd ../..
git add extern/commonroad-scenario-designer  # bump the pinned pointer
git commit -m "Bump crdesigner submodule"
```

It is installed editable, so edits take effect immediately without reinstalling.
