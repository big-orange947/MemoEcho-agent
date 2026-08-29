"""CLI: python -m doppel_eval generate|replay ...

Never contacts QQ; all data is synthetic and deterministic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app.integrations.doppel.config import (
    doppel_api_key,
    doppel_max_tokens_parameter,
    doppel_model,
    doppel_openai_base_url,
    doppel_schema_mode,
    doppel_thinking,
)
from doppel_eval.e2e import run_e2e
from doppel_eval.generators import Tier, TierConfig, generate_dataset
from doppel_eval.graph_e2e import run_graph_e2e
from doppel_eval.load import run_load
from doppel_eval.provider import ProviderBudget
from doppel_eval.replay import _load_doppel, replay_dataset, replay_scenarios


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="doppel_eval")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate a synthetic dataset")
    gen.add_argument(
        "--tier",
        choices=[tier.value for tier in Tier],
        default=Tier.ADAPTER.value,
        help="dataset tier (adapter | quality | load)",
    )
    gen.add_argument("--out", type=Path, required=True, help="output .jsonl path")
    gen.add_argument("--seed", type=int, default=42, help="deterministic seed")
    gen.add_argument(
        "--events",
        type=int,
        default=10_000,
        help="event count for the load tier",
    )
    gen.add_argument(
        "--private-scopes",
        type=int,
        default=50,
        help="private scopes for the load tier",
    )
    gen.add_argument(
        "--group-scopes",
        type=int,
        default=10,
        help="group scopes for the load tier",
    )
    gen.add_argument(
        "--owners",
        type=int,
        default=1,
        help="tenant (selfId) count for the load tier; >1 enables multi-tenant",
    )

    rep = sub.add_parser("replay", help="replay a dataset/scenarios through Doppel")
    rep.add_argument(
        "--scenarios",
        action="store_true",
        help="run built-in knowledge scenes (default when --dataset is absent)",
    )
    rep.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="raw JSONL dataset to replay (adapter/quality/load)",
    )
    rep.add_argument(
        "--replay-twice",
        action="store_true",
        help="ingest twice to verify idempotence",
    )
    rep.add_argument(
        "--out",
        type=Path,
        default=None,
        help="audit JSON output path (default: print summary)",
    )
    rep.add_argument(
        "--strict",
        action="store_true",
        help="deprecated compatibility flag; scenario replay is strict by default",
    )
    rep.add_argument(
        "--allow-quality-failures",
        action="store_true",
        help="exit zero when the contract is safe but quality assertions fail",
    )

    ld = sub.add_parser("load", help="stream a dataset for throughput/isolation")
    ld.add_argument("--dataset", type=Path, required=True, help="JSONL dataset")
    ld.add_argument("--replay-twice", action="store_true", help="verify idempotence")
    ld.add_argument(
        "--isolation-check-scopes", type=int, default=5, help="scopes to sample"
    )
    ld.add_argument("--backend", default="sqlite", help="sqlite | postgres")
    ld.add_argument("--dsn", default=None, help="postgres DSN (backend_kwargs)")
    ld.add_argument("--out", type=Path, default=None, help="report JSON output")

    e2e = sub.add_parser(
        "e2e", help="run synthetic scenes through a real structured-output model"
    )
    e2e.add_argument(
        "--cases",
        default="",
        help="comma-separated case ids; default uses registered order",
    )
    e2e.add_argument("--max-scenes", type=int, default=10)
    e2e.add_argument("--max-calls", type=int, default=10)
    e2e.add_argument("--max-input-tokens", type=int, default=80_000)
    e2e.add_argument("--max-output-tokens", type=int, default=10_240)
    e2e.add_argument("--max-total-tokens", type=int, default=90_240)
    e2e.add_argument("--max-output-per-call", type=int, default=1_024)
    e2e.add_argument(
        "--retrieval-mode",
        choices=("lexical", "hybrid"),
        default="lexical",
        help="lexical baseline or domain-neutral local embedding hybrid",
    )
    e2e.add_argument(
        "--embedding-model",
        default="BAAI/bge-small-zh-v1.5",
        help="FastEmbed model used only by --retrieval-mode hybrid",
    )
    e2e.add_argument(
        "--cache-dir", type=Path, default=Path("data/doppel/e2e-cache")
    )
    e2e.add_argument("--out", type=Path, default=None, help="report JSON output")
    e2e.add_argument(
        "--allow-quality-failures",
        action="store_true",
        help="exit zero for quality misses; budget stops and hard failures still fail",
    )

    graph = sub.add_parser(
        "graph-e2e",
        help="run budget-free temporal Graphiti/Neo4j scenes",
    )
    graph.add_argument(
        "--backend",
        choices=("contract", "neo4j"),
        default="contract",
        help="offline Graphiti contract (default) or live preseeded Neo4j",
    )
    graph.add_argument(
        "--out", type=Path, default=None, help="report JSON output"
    )
    graph.add_argument(
        "--keep-fixture",
        action="store_true",
        help="retain the isolated live Neo4j fixture for manual inspection",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.command == "generate":
        config = TierConfig(
            tier=Tier(args.tier),
            seed=args.seed,
            load_events=args.events,
            load_private_scopes=args.private_scopes,
            load_group_scopes=args.group_scopes,
            load_owners=args.owners,
        )
        dataset = generate_dataset(config)
        out_path = args.out
        if out_path.suffix == "":
            out_path = out_path.with_suffix(".jsonl")
        dataset.write(out_path)
        manifest = dataset.manifest(out_path)
        manifest_path = out_path.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"tier={config.tier.value} events={dataset.events.__len__()} "
            f"out={out_path} manifest={manifest_path}"
        )
        return 0

    if args.command == "replay":
        dm = _load_doppel()
        if dm is None:
            print(
                "doppel_memory not importable. Set DOPPEL_IMPORT_PATH="
                "D:/project/Doppel (or install doppel-memory).",
                file=sys.stderr,
            )
            return 3
        if args.dataset is None and not args.scenarios:
            print("specify --dataset <file.jsonl> or --scenarios", file=sys.stderr)
            return 2
        if args.dataset is not None:
            report = asyncio.run(
                replay_dataset(dm, args.dataset, replay_twice=args.replay_twice)
            )
            exit_code = 0 if report.get("gate", {}).get("ok", False) else 1
        else:
            report = asyncio.run(replay_scenarios(dm, replay_twice=args.replay_twice))
            gate = report.get("gate", {})
            if args.allow_quality_failures:
                exit_code = 0 if gate.get("ok", False) else 1
            else:
                exit_code = 0 if gate.get("strict_passed", False) else 1
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(report.get("summary", report), ensure_ascii=False, indent=2))
        if exit_code:
            print(
                f"[doppel_eval] replay gate FAILED (exit {exit_code}); "
                "see the JSON report for failures.",
                file=sys.stderr,
            )
        return exit_code

    if args.command == "load":
        dm = _load_doppel()
        if dm is None:
            print(
                "doppel_memory not importable. Set DOPPEL_IMPORT_PATH="
                "D:/project/Doppel (or install doppel-memory).",
                file=sys.stderr,
            )
            return 3
        backend_kwargs = {}
        if args.dsn:
            backend_kwargs["dsn"] = args.dsn
        report = asyncio.run(
            run_load(
                dm,
                args.dataset,
                replay_twice=args.replay_twice,
                isolation_check_scopes=args.isolation_check_scopes,
                backend=args.backend,
                **backend_kwargs,
            )
        )
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        gate = report.get("gate", {})
        if not gate.get("ok", False):
            print(
                f"[doppel_eval] load gate FAILED: {gate}",
                file=sys.stderr,
            )
            return 1
        return 0
    if args.command == "graph-e2e":
        dm = _load_doppel()
        if dm is None:
            print(
                "doppel_memory not importable. Set DOPPEL_IMPORT_PATH="
                "D:/project/Doppel (or install doppel-memory).",
                file=sys.stderr,
            )
            return 3
        try:
            report = asyncio.run(
                run_graph_e2e(
                    dm,
                    backend=args.backend,
                    neo4j_uri=os.environ.get(
                        "GRAPHITI_EVAL_NEO4J_URI", ""
                    ).strip(),
                    neo4j_user=os.environ.get(
                        "GRAPHITI_EVAL_NEO4J_USER", ""
                    ).strip(),
                    neo4j_password=os.environ.get(
                        "GRAPHITI_EVAL_NEO4J_PASSWORD", ""
                    ),
                    keep_fixture=args.keep_fixture,
                )
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"[doppel_eval] graph E2E failed: {exc}", file=sys.stderr)
            return 2
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return 0 if report["gate"]["ok"] else 1
    if args.command == "e2e":
        dm = _load_doppel()
        if dm is None:
            print(
                "doppel_memory not importable. Set DOPPEL_IMPORT_PATH="
                "D:/project/Doppel (or install doppel-memory).",
                file=sys.stderr,
            )
            return 3
        try:
            budget = ProviderBudget(
                max_calls=args.max_calls,
                max_input_tokens=args.max_input_tokens,
                max_output_tokens=args.max_output_tokens,
                max_total_tokens=args.max_total_tokens,
                max_output_tokens_per_call=args.max_output_per_call,
            )
            report = asyncio.run(
                run_e2e(
                    dm,
                    model=doppel_model(),
                    base_url=doppel_openai_base_url(),
                    api_key=doppel_api_key(),
                    schema_mode=doppel_schema_mode(),
                    max_tokens_parameter=doppel_max_tokens_parameter(),
                    thinking=doppel_thinking(),
                    budget=budget,
                    cache_dir=args.cache_dir,
                    case_ids=[
                        item.strip() for item in args.cases.split(",") if item.strip()
                    ],
                    max_scenes=args.max_scenes,
                    retrieval_mode=args.retrieval_mode,
                    embedding_model=args.embedding_model,
                )
            )
        except (TypeError, ValueError) as exc:
            print(f"[doppel_eval] invalid E2E configuration: {exc}", file=sys.stderr)
            return 2
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        gate = report["gate"]
        if args.allow_quality_failures:
            return 0 if gate["ok"] else 1
        return 0 if gate["strict_passed"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
