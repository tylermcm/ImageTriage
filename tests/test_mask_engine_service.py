from __future__ import annotations

import json
import unittest
from pathlib import Path

from image_triage.mask_engine_service import MaskEngineService, SemanticWorkerResult
from image_triage.semantic_mask_service import _WorkerTransportError


class _FakeStdout:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def readline(self) -> str:
        return self.lines.pop(0) if self.lines else ""

    def close(self) -> None:
        pass


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self.process = process

    def write(self, raw_request: str) -> int:
        request = json.loads(raw_request)
        command = str(request["command"])
        engine = request.get("engine")
        self.process.commands.append((command, engine))
        if self.process.write_error is not None:
            error = self.process.write_error
            self.process.write_error = None
            raise error
        if command == "infer" and engine == "semantic":
            result: dict = {"device": "cuda", "sourceSize": [8, 6],
                            "categoryStats": {"sky": {"coverage": 0.5}}, "timingsMs": {"inference": 11.0}}
        elif command == "infer" and engine == "subject":
            result = {"device": "cuda", "components": [{"id": "subject-01"}]}
        elif command == "embed":
            result = {"device": "cuda", "sourceSize": [8, 6]}
        elif command == "segment":
            result = {"device": "cuda", "maskPath": "m.png", "sourceSize": [8, 6],
                      "bounds": [1, 2, 3, 4], "coverage": 0.1, "iou": 0.9,
                      "chosenMask": 0, "suppressed": False}
        elif command == "shutdown":
            result = {"stage": "shutdown"}
        else:
            result = {"device": "cuda", "stage": command, "engine": engine or "all"}
        response_id = request["id"] if not self.process.force_bad_id else request["id"] + 999
        self.process.stdout.lines.append(
            "RESPONSE " + json.dumps({"id": response_id, "ok": True, "result": result, "error": ""}) + "\n"
        )
        return len(raw_request)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, *, pid: int = 5150) -> None:
        self.pid = pid
        self.stdout = _FakeStdout()
        self.stdin = _FakeStdin(self)
        self.stopped = False
        self.commands: list[tuple[str, object]] = []
        self.write_error: Exception | None = None
        self.force_bad_id = False

    def poll(self):
        return 0 if self.stopped else None

    def wait(self, timeout=None):
        self.stopped = True
        return 0

    def terminate(self) -> None:
        self.stopped = True

    def kill(self) -> None:
        self.stopped = True


class MaskEngineServiceTests(unittest.TestCase):
    def _service(self) -> tuple[MaskEngineService, _FakeProcess]:
        service = MaskEngineService(idle_timeout_seconds=3600)
        process = _FakeProcess()
        service._process = process  # type: ignore[assignment]
        return service, process

    def test_one_host_serves_both_engines_and_reuses_state(self) -> None:
        service, process = self._service()
        service.warm_imports()
        semantic_dir = Path("m/of").resolve()
        subject_dir = Path("m/bi").resolve()
        sem = service.infer_semantic(
            model_dir=semantic_dir, input_path=Path("in.png"), output_dir=Path("masks"),
            categories=("sky", "water"), minimum_coverage=0.0005, progress_callback=None,
        )
        device, components = service.infer_subject(
            model_dir=subject_dir, input_path=Path("in.png"), output_path=Path("out.png"),
            components_dir=Path("comps"), progress_callback=None,
        )
        service.shutdown()

        self.assertIsInstance(sem, SemanticWorkerResult)
        self.assertEqual((8, 6), sem.source_size)
        self.assertEqual(11.0, sem.timings_ms["inference"])
        self.assertEqual("cuda", device)
        self.assertEqual([{"id": "subject-01"}], components)
        # One process, both engines: warm once, load each engine once, two infers.
        self.assertEqual(
            [
                ("warm-imports", None),
                ("load-model", "semantic"),
                ("infer", "semantic"),
                ("load-model", "subject"),
                ("infer", "subject"),
                ("shutdown", None),
            ],
            process.commands,
        )

    def test_same_engine_model_dir_is_not_reloaded(self) -> None:
        service, process = self._service()
        d = Path("m/of").resolve()
        service.warm_model("semantic", d)
        service.warm_model("semantic", d)
        self.assertEqual([("warm-imports", None), ("load-model", "semantic")], process.commands)

    def test_prompt_embeds_once_then_segments_per_click(self) -> None:
        service, process = self._service()
        model_dir = Path("m/sam").resolve()
        common = dict(
            model_dir=model_dir, input_path=Path("prev.png"),
            output_path=Path("mask.png"), points=[(5.0, 5.0)], labels=[1],
            minimum_area=0.0005, progress_callback=None,
        )
        r1 = service.infer_prompt(image_key="imgA", **common)
        r2 = service.infer_prompt(image_key="imgA", **common)   # same image -> no re-embed
        r3 = service.infer_prompt(image_key="imgB", **common)   # new image -> re-embed
        service.shutdown()

        self.assertEqual([1, 2, 3, 4], r1["bounds"])
        self.assertEqual("cuda", r2["device"])
        self.assertEqual(
            [
                ("warm-imports", None),
                ("load-model", "prompt"),
                ("embed", "prompt"),
                ("segment", "prompt"),
                ("segment", "prompt"),   # r2 reused the embedding
                ("embed", "prompt"),     # r3 changed image
                ("segment", "prompt"),
                ("shutdown", None),
            ],
            process.commands,
        )

    def test_out_of_sequence_response_is_rejected(self) -> None:
        service, process = self._service()
        process.force_bad_id = True
        with self.assertRaises(_WorkerTransportError):
            service._send_command_locked({"command": "warm-imports"})

    def test_broken_pipe_retries_and_reloads_the_engine_model(self) -> None:
        service = MaskEngineService(idle_timeout_seconds=3600)
        first = _FakeProcess(pid=1)
        first.write_error = BrokenPipeError("pipe closed")
        second = _FakeProcess(pid=2)
        spawned = iter((second,))

        def fake_ensure() -> None:
            if service._process is None or service._process.poll() is not None:
                service._process = next(spawned)  # type: ignore[assignment]

        service._ensure_process_locked = fake_ensure  # type: ignore[assignment]
        service._process = first  # type: ignore[assignment]

        result = service.infer_semantic(
            model_dir=Path("m/of").resolve(), input_path=Path("in.png"), output_dir=Path("masks"),
            categories=("sky",), minimum_coverage=0.0005, progress_callback=None,
        )
        self.assertEqual((8, 6), result.source_size)
        self.assertIn(("load-model", "semantic"), second.commands)
        self.assertIn(("infer", "semantic"), second.commands)


if __name__ == "__main__":
    unittest.main()
