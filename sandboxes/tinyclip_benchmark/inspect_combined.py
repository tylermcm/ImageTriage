from pathlib import Path

import numpy as np
import onnxruntime as ort


path = Path(__file__).resolve().parent / "models" / "onnx-community-tinyclip" / "onnx" / "model.onnx"
session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
print("inputs")
for item in session.get_inputs():
    print(item.name, item.type, item.shape)
print("outputs")
for item in session.get_outputs():
    print(item.name, item.type, item.shape)

image = np.zeros((1, 3, 224, 224), dtype=np.float32)
tokens = np.zeros((1, 77), dtype=np.int64)
mask = np.ones((1, 77), dtype=np.int64)
for label, outputs, feeds in (
    ("image only", ["image_embeds"], {"pixel_values": image}),
    ("text only", ["text_embeds"], {"input_ids": tokens, "attention_mask": mask}),
    ("image with dummy text", ["image_embeds"], {"pixel_values": image, "input_ids": tokens, "attention_mask": mask}),
    ("text with dummy image", ["text_embeds"], {"pixel_values": image, "input_ids": tokens, "attention_mask": mask}),
):
    try:
        output = session.run(outputs, feeds)[0]
        print(label, "ok", output.shape)
    except Exception as exc:
        print(label, "failed", " ".join(str(exc).split()))
