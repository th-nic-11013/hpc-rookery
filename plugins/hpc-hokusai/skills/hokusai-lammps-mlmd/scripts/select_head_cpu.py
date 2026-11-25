"""Cut one head out of a (GPU-trained) multihead MACE fine-tune, on a CPU node.

mace.cli.select_head does torch.load(map_location=...) only, which doesn't reach
e3nn's compiled submodules (CUDA tensors, loaded by torch.jit.load with no
map_location: gap G17, "Found no NVIDIA driver"). Making "cpu" the default
map_location of torch.jit.load while the model loads fixes it, as predict.py's
_load_on_cpu does. Used for the UiO-66 round-3 export (HOKUSAI job 9922229).

usage: python select_head_cpu.py IN.model HEAD OUT.model
"""
import sys
import torch
from mace.tools.scripts_utils import remove_pt_head

src, head, out = sys.argv[1:4]
orig = torch.jit.load
torch.jit.load = lambda f, map_location=None, *a, **k: orig(f, map_location or "cpu", *a, **k)
try:
    model = torch.load(src, map_location="cpu", weights_only=False)
finally:
    torch.jit.load = orig
torch.set_default_dtype(next(model.parameters()).dtype)
print("heads in:", list(model.heads), "dtype:", next(model.parameters()).dtype)
if head not in model.heads:
    sys.exit(f"head {head!r} is not in {list(model.heads)}")
single = remove_pt_head(model, head).to("cpu")
torch.save(single, out)
print("heads out:", list(single.heads))
