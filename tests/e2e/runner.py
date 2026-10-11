"""Opaque-box E2E Test Runner for wri-kas-bot.

Supports targeted tier execution and comprehensive reporting.
Usage:
    uv run python tests/e2e/runner.py [--tier 1|2|3|4|all] [-v]
"""

import argparse
import sys

import pytest

TIER_PATHS = {
    "1": "tests/e2e/tier1_feature_coverage",
    "2": "tests/e2e/tier2_boundary_corner",
    "3": "tests/e2e/tier3_cross_feature",
    "4": "tests/e2e/tier4_real_world",
    "all": "tests/e2e",
}


def run_e2e_tests(tier: str = "all", verbose: bool = False) -> int:
    """Run E2E tests for the selected tier."""
    target_path = TIER_PATHS.get(str(tier).lower(), "tests/e2e")

    args = [
        target_path,
        "-rA",
    ]
    if verbose:
        args.append("-v")

    print("\n=======================================================")
    print(f"🚀 Running E2E Test Suite [Tier: {tier.upper()}]")
    print(f"📁 Target Directory: {target_path}")
    print("=======================================================\n")

    # Run pytest
    exit_code = pytest.main(args)

    print("\n=======================================================")
    if exit_code == pytest.ExitCode.OK:
        print(f"✅ E2E Suite [Tier: {tier.upper()}] Passed successfully.")
    elif exit_code == pytest.ExitCode.NO_TESTS_COLLECTED:
        print(f"⚠️  No tests collected for Tier: {tier.upper()}.")
    else:
        print(f"❌ E2E Suite [Tier: {tier.upper()}] Finished with exit code: {exit_code}")
    print("=======================================================\n")

    return int(exit_code)


def main():
    parser = argparse.ArgumentParser(description="wri-kas-bot E2E Test Suite Runner")
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "all"],
        default="all",
        help="Specify tier to run (1: Feature, 2: Boundary, 3: Cross, 4: Scenario, all: All)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose pytest output",
    )
    args = parser.parse_args()
    exit_code = run_e2e_tests(tier=args.tier, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
