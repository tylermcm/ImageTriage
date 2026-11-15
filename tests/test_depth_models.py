"""The depth models Light Depth can use: where they are kept, how a map is asked for, and how long it took."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from image_triage import ai_model, depth_maps, depth_worker, light_depth


def test_the_variants_are_the_same_in_the_model_catalogue_and_in_light_depth():
    assert ai_model.DEPTH_VARIANTS == light_depth.DEPTH_MODELS
    assert len(light_depth.DEPTH_MODEL_LABELS) == len(light_depth.DEPTH_MODELS)
    assert set(ai_model.DEPTH_VARIANT_REPOS) == set(ai_model.DEPTH_VARIANTS)


def test_each_variant_has_its_own_folder_beside_the_small_one():
    small, base, large = (ai_model.depth_variant_installation(v) for v in ai_model.DEPTH_VARIANTS)
    assert small.install_dir == ai_model.resolve_depth_model_installation().install_dir
    assert base.install_dir.name == "Depth-Anything-V2-Base-hf" and large.install_dir.name == "Depth-Anything-V2-Large-hf"
    assert base.install_dir.parent == small.install_dir.parent == large.install_dir.parent
    assert base.required_filenames == ai_model.DEPTH_MODEL_REQUIRED_FILENAMES
    with pytest.raises(ValueError):
        ai_model.depth_variant_installation("huge")


def test_each_model_keeps_its_own_map_and_the_small_ones_name_is_unchanged(tmp_path):
    photo = tmp_path / "a.NEF"
    photo.write_bytes(b"raw")
    names = {m: depth_maps.light_depth_cache_file(photo, cache_root=tmp_path / "cache", model=m).name for m in light_depth.DEPTH_MODELS}
    assert names == {"small": "light_depth.png", "base": "light_depth_base.png", "large": "light_depth_large.png"}
    assert depth_maps.light_depth_cache_file(photo, cache_root=tmp_path / "cache").name == "light_depth.png"


def test_the_model_input_size_keeps_the_proportions_in_multiples_of_14():
    assert depth_worker.model_input_size(1600, 1066, 518) == (784, 518)
    width, height = depth_worker.model_input_size(1600, 1066, 1036)
    assert width % 14 == height % 14 == 0 and height == 1036 and abs(width / height - 1600 / 1066) < 0.01
    assert depth_worker.model_input_size(10, 10, 1) == (14, 14)


def test_the_effect_builds_from_the_map_of_the_model_it_uses(tmp_path, monkeypatch):
    photo = tmp_path / "a.NEF"
    photo.write_bytes(b"raw")
    maps = {}
    for key, value in (("small", 40), ("base", 120), ("large", 220)):
        maps[key] = tmp_path / f"{key}.png"
        Image.fromarray(np.full((20, 30), value, dtype=np.uint8)).save(maps[key])
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, model="small", **kw: maps[model])
    for key, value in (("small", 40), ("base", 120), ("large", 220)):
        assert light_depth.load_depth(str(photo), key).mean() == value
    base = light_depth.normalized({"amount": 50, "depthModel": 1})
    assert light_depth.model_key(base) == "base" and light_depth.model_key(None) == "small"
    result = light_depth.with_masks({}, base, str(photo))
    assert "masks" in result
    maps["base"].unlink()
    with pytest.raises(ValueError, match="depth map is missing"):
        light_depth.with_masks({}, base, str(photo))                      # the small model's map is not a stand-in
    assert light_depth.with_masks({}, {**base, "depthModel": 0.0}, str(photo))["masks"]


def test_settings_saved_before_there_was_a_choice_of_model_still_load(tmp_path):
    recipe = tmp_path / "IMG_1.NEF.cameraraw.json"
    old = {k: v for k, v in light_depth.normalized({"amount": 40}).items() if k != "depthModel"}
    light_depth.sidecar_path(recipe).write_text('{"version": 2, "settings": %s}' % __import__("json").dumps(old))
    settings, legacy = light_depth.read_sidecar(recipe)
    assert settings["amount"] == 40 and settings["depthModel"] == 0 and legacy is False


def test_run_depth_map_asks_the_host_for_the_chosen_model_and_size(tmp_path, monkeypatch):
    from image_triage import scene_mask_jobs

    asked = {}
    monkeypatch.setattr("image_triage.ai_model.depth_variant_installation",
                        lambda variant: SimpleNamespace(is_installed=variant != "large", install_dir=Path("models") / variant))

    class Service:
        def infer_depth(self, **kwargs):
            asked.update(kwargs)
            return {"device": "cuda", "inferenceSeconds": 0.4}

    monkeypatch.setattr("image_triage.mask_engine_service.default_mask_engine_service", lambda: Service())
    result = scene_mask_jobs.run_depth_map(tmp_path / "in.png", tmp_path / "out.png", None, None, variant="base", input_size=1036)
    assert result["device"] == "cuda" and asked["model_dir"] == Path("models") / "base" and asked["input_size"] == 1036
    with pytest.raises(RuntimeError, match="large depth model is not installed"):
        scene_mask_jobs.run_depth_map(tmp_path / "in.png", tmp_path / "out.png", None, None, variant="large")


def test_the_host_request_carries_the_input_size_only_when_there_is_one(tmp_path):
    from image_triage.mask_engine_service import MaskEngineService

    sent = []
    service = SimpleNamespace(infer=lambda engine, model_dir, payload, progress: sent.append(payload) or {})
    MaskEngineService.infer_depth(service, model_dir=tmp_path, input_path=tmp_path / "a.png", output_path=tmp_path / "b.png",
                                  progress_callback=None, input_size=1036)
    MaskEngineService.infer_depth(service, model_dir=tmp_path, input_path=tmp_path / "a.png", output_path=tmp_path / "b.png",
                                  progress_callback=None)
    assert sent[0]["inputSize"] == 1036 and "inputSize" not in sent[1]


def test_a_depth_job_keeps_each_models_map_and_logs_how_long_it_took(tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage

    from image_triage.qml_shell import diagnostics, raw_light_depth

    photo = tmp_path / "a.NEF"
    photo.write_bytes(b"raw")
    monkeypatch.setattr(diagnostics, "log_path", lambda: tmp_path / "qml-shell.log")
    monkeypatch.setattr(depth_maps, "default_depth_cache_root", lambda: tmp_path / "cache")
    calls = []

    def fake(source, output, progress, cancel, *, variant, input_size):
        calls.append((variant, input_size))
        Image.fromarray(np.tile(np.arange(60, dtype=np.uint8)[:, None], (1, 90))).save(output)
        return {"device": "cuda", "modelLoadSeconds": 0.5, "inferenceSeconds": 0.25, "modelInput": [1554, 1036]}

    monkeypatch.setattr("image_triage.scene_mask_jobs.run_depth_map", fake)
    image = QImage(90, 60, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    values, info = raw_light_depth.run_depth_job(image, str(photo), None, None, 1)
    assert values.shape == (60, 90) and info["model"] == "base" and not info["cached"]
    assert info["load"] == 0.5 and info["inference"] == 0.25 and info["input"] == "1554x1036"
    assert calls == [("base", raw_light_depth.DEPTH_INPUT_SIZE)]
    again, info = raw_light_depth.run_depth_job(image, str(photo), None, None, 1)
    assert info["cached"] and calls == [("base", raw_light_depth.DEPTH_INPUT_SIZE)] and np.array_equal(again, values)
    raw_light_depth.run_depth_job(image, str(photo), None, None, 0)           # another model: made again, kept separately
    assert [c[0] for c in calls] == ["base", "small"]
    lines = (tmp_path / "light-depth-timing.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and "base" in lines[0] and "made" in lines[0] and "cached" in lines[1] and "a.NEF" in lines[2]
