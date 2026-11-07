"""Warm CPU/GPU timings and pixel comparisons for the isolated backend."""
import argparse
import json
from pathlib import Path
import statistics
import time

from spike import Backend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('raw', type=Path)
    parser.add_argument('--backend', required=True, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--edge', type=int, default=1200)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    backend = Backend(args.backend, args.output / 'benchmark-backend.log')
    samples, revision = [], 0
    try:
        started = time.perf_counter()
        header, original = backend.request(dict(op='open', path=str(args.raw.resolve()), edge=args.edge, revision=0))
        opened_ms = (time.perf_counter()-started)*1000
        for shadows in (0, 40):
            pairs = {}
            for mode in ('cpu', 'auto'):
                elapsed, worker, develop = [], [], []
                for exposure in (.5, 1.0, -.5, .6, .3, .7):
                    revision += 1
                    started = time.perf_counter()
                    result, pixels = backend.request(dict(op='render', exposure=exposure, shadows=shadows,
                                                          backend=mode, revision=revision))
                    elapsed.append((time.perf_counter()-started)*1000)
                    worker.append(result['workerMs'])
                    develop.append(result['developMs'])
                    assert result['revision'] == revision and result['opens'] == 1
                    assert pixels != original
                    assert len(result['histogram']) == 3
                    assert all(sum(c) == result['width']*result['height'] for c in result['histogram'])
                    pairs[(mode, exposure)] = pixels
                samples.append(dict(mode=mode, actualBackend=result['backend'], shadows=shadows,
                                    roundTripMedianMs=statistics.median(elapsed),
                                    roundTripMaxMs=max(elapsed), workerMedianMs=statistics.median(worker),
                                    developMedianMs=statistics.median(develop), dimensions=[result['width'],result['height']]))
            # GPU and CPU pass use the same source and complete recipe.
            differences = [abs(a-b) for a,b in zip(pairs[('cpu', .7)], pairs[('auto', .7)])]
            samples[-1]['cpuGpuMaxCodeDifference'] = max(differences)
            samples[-1]['cpuGpuMeanCodeDifference'] = statistics.mean(differences)
            assert max(differences) <= 2, samples[-1]
        # A malformed request must produce a recoverable error, not kill the session.
        try:
            backend.request(dict(op='render', exposure=999, revision=revision+1))
            raise AssertionError('Out-of-range exposure accepted')
        except RuntimeError as error:
            assert 'range' in str(error)
        restored, pixels = backend.request(dict(op='render', exposure=0, shadows=0, backend='cpu', revision=revision+2))
        assert pixels == original and restored['opens'] == 1
    finally:
        backend.close()
    assert backend.proc.poll() == 0
    report = dict(openAndGpuInitializationMs=opened_ms, samples=samples, cleanExit=True)
    (args.output / 'benchmark.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
