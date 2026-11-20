"""The fill model's plumbing: fitting a crop to the model, the host's routing, the job runner and the model's registration."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

import sys

# The worker modules import as top-level scripts from their own folder, as the host runs them.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "image_triage"))
import inpaint_worker  # noqa: E402
from image_triage import ai_model, mask_engine_service, scene_mask_jobs  # noqa: E402


class FakeSession:
    """A stand-in for the ONNX model: records what it was given and returns the picture it was given (0..255)."""

    def __init__(self):
        self.calls = []

    def run(self, _outputs, feed):
        self.calls.append(feed)
        return [(feed["image"] * 255).astype(np.float32)]


def engine():
    return SimpleNamespace(np=np, Image=Image, session=FakeSession())


@pytest.mark.parametrize("size", [(300, 200), (200, 300), (512, 512), (1000, 120), (90, 40), (2000, 1500)])
def test_a_crop_is_fitted_to_the_model_with_its_proportions_and_margins_that_continue_it(size):
    w, h = size
    rgb = np.dstack([np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))] * 3)
    hole = np.zeros((h, w), np.uint8)
    hole[h // 3:h // 2, w // 3:w // 2] = 255
    canvas, mask, (top, left, nh, nw) = inpaint_worker.fit_to_model(np, Image, rgb, hole)
    assert canvas.shape == (512, 512, 3) and mask.shape == (512, 512) and max(nh, nw) == 512
    assert abs(nw / nh - w / h) < 0.02 * max(1, w / h)
    assert mask[:top].sum() == 0 and mask[:, :left].sum() == 0 and mask[top + nh:].sum() == 0     # the margins are never filled
    assert mask.sum() > 0 and mask[top:top + nh, left:left + nw].sum() == mask.sum()
    assert np.array_equal(canvas[top:top + nh, left:left + nw].shape[:2], (nh, nw))


def test_filling_returns_the_crop_at_its_own_size_and_hands_the_model_the_hole():
    rgb = np.full((300, 200, 3), 100, np.uint8)
    hole = np.zeros((300, 200), np.uint8)
    hole[100:150, 60:120] = 255
    eng = engine()
    out = inpaint_worker.fill(eng, rgb, hole)
    assert out.shape == rgb.shape and out.dtype == np.uint8
    assert np.abs(out.astype(int) - 100).max() <= 1                         # the stand-in model gives the picture back
    feed = eng.session.calls[0]
    assert feed["image"].shape == (1, 3, 512, 512) and feed["image"].dtype == np.float32
    assert feed["mask"].shape == (1, 1, 512, 512) and set(np.unique(feed["mask"])) == {0.0, 1.0}
    assert abs(float(feed["image"].mean()) - 100 / 255) < 0.02                   # 0..1 in, as the model expects


def test_generating_a_fill_reads_the_files_and_writes_the_result(tmp_path, monkeypatch):
    Image.fromarray(np.full((120, 160, 3), 80, np.uint8)).save(tmp_path / "in.png")
    mask = np.zeros((120, 160), np.uint8)
    mask[40:60, 50:90] = 255
    Image.fromarray(mask).save(tmp_path / "mask.png")
    eng = engine()
    eng.load_model = lambda model_dir: "cpu"
    result = inpaint_worker.generate_inpaint(model_dir=tmp_path, input_path=tmp_path / "in.png", mask_path=tmp_path / "mask.png",
                                             output_path=tmp_path / "out" / "o.png", requested_device="cpu", engine=eng, emit_result=False)
    assert result["device"] == "cpu" and (result["width"], result["height"]) == (160, 120) and result["inferenceSeconds"] >= 0
    assert np.asarray(Image.open(tmp_path / "out" / "o.png")).shape == (120, 160, 3)
    Image.fromarray(mask[:50]).save(tmp_path / "bad.png")
    with pytest.raises(ValueError, match="does not match"):
        inpaint_worker.generate_inpaint(model_dir=tmp_path, input_path=tmp_path / "in.png", mask_path=tmp_path / "bad.png",
                                        output_path=tmp_path / "o2.png", requested_device="cpu", engine=eng, emit_result=False)


def test_the_provider_is_gpu_only_when_it_can_be_and_was_not_refused():
    gpu = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    assert inpaint_worker._providers(gpu, "auto", ["CUDAExecutionProvider", "CPUExecutionProvider"])[1] == "cuda"
    assert inpaint_worker._providers(gpu, "cpu", ["CUDAExecutionProvider", "CPUExecutionProvider"])[1] == "cpu"
    assert inpaint_worker._providers(gpu, "auto", ["CPUExecutionProvider"])[1] == "cpu"
    assert inpaint_worker._providers(None, "auto", ["CUDAExecutionProvider"])[1] == "cpu"
    assert inpaint_worker._providers(SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)), "auto", ["CUDAExecutionProvider"])[1] == "cpu"


def test_the_host_routes_a_fill_request_to_its_own_engine(tmp_path, monkeypatch):
    import mask_engine_worker

    got = {}
    monkeypatch.setattr(inpaint_worker, "generate_inpaint", lambda **kwargs: got.update(kwargs) or {"device": "cpu"})
    marker = object()
    host = mask_engine_worker.MaskEngineHost("auto", subject_engine=object(), semantic_engine=object(), prompt_engine=object(),
                                             depth_engine=object(), inpaint_engine=marker)
    assert host._engine(mask_engine_worker.ENGINE_INPAINT) is marker and "inpaint" in mask_engine_worker.ENGINE_NAMES
    result = host.infer("inpaint", {"modelDir": str(tmp_path), "inputPath": "a.png", "maskPath": "m.png", "outputPath": "o.png"})
    assert result == {"device": "cpu"} and got["engine"] is marker and got["emit_result"] is False
    assert got["input_path"].name == "a.png" and got["mask_path"].name == "m.png" and got["output_path"].name == "o.png"
    assert mask_engine_service.ENGINE_INPAINT == mask_engine_worker.ENGINE_INPAINT == "inpaint"


def test_the_service_sends_the_three_paths_to_the_host():
    sent = []
    service = SimpleNamespace(infer=lambda engine_name, model_dir, payload, progress: sent.append((engine_name, payload)) or {"device": "cuda"})
    result = mask_engine_service.MaskEngineService.infer_inpaint(service, model_dir=Path("m"), input_path=Path("a.png"), mask_path=Path("m.png"),
                                                                 output_path=Path("o.png"), progress_callback=None)
    assert result == {"device": "cuda"} and sent[0][0] == "inpaint"
    assert set(sent[0][1]) == {"inputPath", "maskPath", "outputPath"} and sent[0][1]["maskPath"].endswith("m.png")


def test_the_job_runner_uses_the_installed_model_and_says_when_there_is_none(tmp_path, monkeypatch):
    asked = {}
    monkeypatch.setattr("image_triage.ai_model.lama_installation", lambda: SimpleNamespace(is_installed=False, install_dir=tmp_path))
    with pytest.raises(RuntimeError, match="fill model is not installed"):
        scene_mask_jobs.run_inpaint(tmp_path / "a.png", tmp_path / "m.png", tmp_path / "o.png")
    monkeypatch.setattr("image_triage.ai_model.lama_installation", lambda: SimpleNamespace(is_installed=True, install_dir=tmp_path / "model"))

    class Service:
        def infer_inpaint(self, **kwargs):
            asked.update(kwargs)
            return {"device": "cuda"}

    monkeypatch.setattr("image_triage.mask_engine_service.default_mask_engine_service", lambda: Service())
    result = scene_mask_jobs.run_inpaint(tmp_path / "a.png", tmp_path / "m.png", tmp_path / "o.png")
    assert result == {"device": "cuda"} and asked["model_dir"] == tmp_path / "model" and asked["output_path"] == tmp_path / "o.png"
    fake = scene_mask_jobs.run_inpaint(tmp_path / "a.png", tmp_path / "m.png", tmp_path / "o.png", engine=lambda *a: {"device": "fake"})
    assert fake == {"device": "fake"}


def test_the_fill_model_is_registered_beside_the_other_editor_models(tmp_path):
    installation = ai_model.lama_installation()
    assert installation.install_dir.name == "LaMa-ONNX" and installation.install_dir.parent == ai_model.depth_variant_installation("base").install_dir.parent
    assert installation.required_filenames == (ai_model.LAMA_MODEL_FILE,) == (inpaint_worker.MODEL_FILE,)
    assert len(installation.expected_sha256[ai_model.LAMA_MODEL_FILE]) == 64
