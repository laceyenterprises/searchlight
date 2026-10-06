"""Optional reference regeneration; not imported by the offline SEW suite.

Run in a disposable environment with scipy==1.18.1, statsmodels==0.15.0.
Published algorithms:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html
https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html

Use identical task indices to compare estimators rather than RNG algorithms.
The patch supplies indices only; SciPy computes the statistic and interval.
"""

import random
from unittest.mock import patch

import numpy as np
from scipy.stats import bootstrap, _resampling
from statsmodels.stats.contingency_tables import mcnemar


def resample(sample, n_resamples, rng=None, **kwargs):
    generator = random.Random(42)
    indices = np.array(
        [generator.choices(range(sample.shape[-1]), k=sample.shape[-1]) for _ in range(n_resamples)]
    )
    return sample[..., indices]


if __name__ == "__main__":
    triples = [(a, 0, 1) for a in [0, 0.25, 0.5, 0.75, 1]]
    variable = [(0.8, 0.1, 1), (0.6, 0.2, 0.9), (0.4, 0, 0.7), (0.9, 0.1, 0.8), (0.3, 0.1, 0.9)]
    with patch.object(_resampling, "_bootstrap_resample", resample):
        for data in (triples, variable):
            result = bootstrap(
                tuple(np.array(data).T),
                lambda a, f, c: np.sum(a - f) / np.sum(c - f),
                paired=True,
                method="percentile",
                n_resamples=10_000,
                batch=10_000,
                vectorized=False,
            )
            print(result.confidence_interval)
    for wins, losses in [(11, 1), (25, 15), (0, 0)]:
        print(wins, losses, mcnemar([[0, wins], [losses, 0]], exact=True).pvalue)
