import wandb
import pandas as pd
from loguru import logger
from tqdm import tqdm
from pathlib import Path
from itertools import product
from tqdm.contrib.concurrent import process_map
import numpy as np
import math
from functools import reduce, partial

from histaug.utils import cached_df, RunningStats

INDEX_COLS = [
    "magnification",
    "target",
    "train_dataset",
    "test_dataset",
    "model",
    "feature_extractor",
    "augmentations",
    "seed",
]
RESULTS_DIR = Path("/app/results")


def filter_runs(runs, filters: dict):
    return [run for run in runs if all(getattr(run, key, None) == value for key, value in filters.items())]


def format_dataset_name(name: str) -> str:
    return name.replace("_mpp0.5", "")


def summarize_run(run):
    history = run.history().groupby("epoch").first()
    best = history[~history.index.isna()].sort_values("val/loss", ascending=True).iloc[0]
    column = run.config["dataset"]["targets"][0]["column"]
    if f"test/{column}/auroc" in run.summary:
        test_auroc = run.summary[f"test/{column}/auroc"]["best"]
    else:
        test_auroc = history[f"test/{column}/auroc"].max()
    try:
        return dict(
            wandb_id=run.id,
            magnification=run.config["settings"].get("magnification", "low"),
            target=column,
            train_dataset=format_dataset_name(run.config["dataset"]["name"]),
            test_dataset=format_dataset_name(run.config["test"]["dataset"]["name"]),
            model=run.config["model"]["_target_"].split(".")[-1],
            feature_extractor=run.config["settings"]["feature_extractor"],
            augmentations=run.config["dataset"]["augmentations"]["name"],
            seed=run.config["seed"],
            train_auroc=best[f"train/{column}/auroc"],
            val_auroc=best[f"val/{column}/auroc"],
            test_auroc=test_auroc,
            runtime=run.summary.get("_runtime", None),
        )
    except Exception as e:
        raise Exception(f"Error summarizing run {run.id}") from e


@cached_df(lambda: "results")
def load_results():
    logger.info("Loading runs")

    api = wandb.Api()
    runs = [
        run
        for run in tqdm(api.runs("histaug", order="+created_at", per_page=1000), desc="Loading runs")
        if run.state == "finished"
    ]
    runs = [summarize_run(run) for run in tqdm(runs, desc="Loading run data")]
    runs = [run for run in runs if run is not None]
    df = pd.DataFrame(runs)
    df = df.set_index(INDEX_COLS).sort_index().drop_duplicates()
    return df


def compute_norm_diff_auroc(sub_df, show_progress: bool = False, compare_across: str = "feature_extractor"):
    """Function to compute average offset from best for a given subset of data."""
    pivot_data = sub_df.pivot(index="seed", columns=compare_across, values="test_auroc")
    feature_extractors = pivot_data.columns.values
    seeds = pivot_data.index.values
    combinations = product(*pivot_data.values.T)
    n_combinations = int(len(seeds) ** len(feature_extractors))
    stats_by_feature_extractor = {fe: RunningStats() for fe in feature_extractors}

    for auroc_values in tqdm(combinations, total=n_combinations) if show_progress else combinations:
        diffs = np.array(auroc_values).max() - np.array(auroc_values)
        for fe, diff in zip(feature_extractors, diffs):
            stats_by_feature_extractor[fe].update(diff)

    return {fe: stats.compute() for fe, stats in stats_by_feature_extractor.items()}


def compute_norm_diff_auroc_worker(args, compare_across: str = "feature_extractor"):
    config, sub_data = args
    result = compute_norm_diff_auroc(sub_data, compare_across=compare_across)
    logger.debug(f"Computed results for {config}")
    return config, result


@cached_df(
    lambda *args, keep_fixed=(
        "magnification",
        "augmentations",
        "model",
        "target",
    ), vary="feature_extractor", **kwargs: f"norm_diff__{vary}__{'_'.join(keep_fixed)}"
)
def compute_results_table(
    test_aurocs: pd.Series,
    keep_fixed=("magnification", "augmentations", "model", "target"),
    vary="feature_extractor",
    n_workers: int = 32,
):
    """Compute average offsets from best for each (target, model, augmentation) pair using multiprocessing."""
    keep_fixed = list(keep_fixed)
    d = test_aurocs.reset_index()

    unique_pairs = d[keep_fixed].drop_duplicates().values

    # Create a tuple of arguments for each unique pair
    args_list = [
        (
            tuple(config.tolist()),
            d[reduce(lambda a, b: a & b, [d[col] == value for col, value in zip(keep_fixed, config)])],
        )
        for config in unique_pairs
    ]

    # Use multiprocessing Pool to compute results in parallel
    worker = partial(compute_norm_diff_auroc_worker, compare_across=vary)
    results_list = process_map(worker, args_list, max_workers=n_workers, tqdm_class=tqdm, desc="Computing results")

    # Convert list of results into dictionary
    results = {config: result for config, result in results_list}

    r = pd.DataFrame(results).map(
        lambda x: {"mean": float("nan"), "std": float("nan")} if isinstance(x, float) and math.isnan(x) else x._asdict()
    )
    r.index.name = vary
    r.columns.names = keep_fixed
    r = r.stack(keep_fixed).apply(pd.Series)
    r.columns.names = ["stats"]
    r = r.pivot_table(index=keep_fixed, columns=vary).reorder_levels([1, 0], axis=1).sort_index(axis=1)
    return r


if __name__ == "__main__":
    df = load_results()
    r = compute_results_table(
        df["test_auroc"], keep_fixed=("magnification", "augmentations", "model", "target"), vary="feature_extractor"
    )
    r = compute_results_table(
        df["test_auroc"], keep_fixed=("magnification", "augmentations", "feature_extractor", "target"), vary="model"
    )
    # # Compute magnification results (we need to filter because we didn't run high magnification for all configs)
    # r = compute_results_table(
    #     df["test_auroc"],  # .query("model == 'AttentionMIL' and augmentations in ['none', 'Macenko_patchwise']"),
    #     keep_fixed=("augmentations", "model", "feature_extractor", "target"),
    #     vary="magnification",
    # )
