"""
BugPredictor Eval Framework
============================
Runs labeled test cases against the live /analyze endpoint and reports:
  - Overall detection accuracy (precision, recall, F1)
  - False positive rate (clean code flagged as buggy)
  - False negative rate (buggy code missed)
  - Severity accuracy (did we get Critical vs Warning right?)
  - Confidence calibration (are high-confidence predictions more accurate?)
  - Per-language breakdown
  - Per-bug-type breakdown

Usage:
  cd ~/bugpredictor
  python evals/run_evals.py

  # Against local server:
  python evals/run_evals.py --api http://127.0.0.1:8000

  # With auth token:
  python evals/run_evals.py --token eyJhbGci...

  # Verbose (show each result):
  python evals/run_evals.py --verbose
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_API = "https://web-production-cb79b.up.railway.app"
EVAL_CASES_PATH = Path(__file__).parent / "eval_cases.json"
RATE_LIMIT_DELAY = 6.5  # seconds between requests (10/min authenticated = 6s min)

# ---------------------------------------------------------------------------
# ANSI colors for terminal output
# ---------------------------------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{RESET}"


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------
def analyze(api_url: str, token: Optional[str], filename: str, code: str) -> Optional[dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.post(
            f"{api_url}/analyze",
            json={"filename": filename, "code": code},
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print(color("  ⚠ Rate limited — waiting 15s...", YELLOW))
            time.sleep(15)
            return analyze(api_url, token, filename, code)
        else:
            print(color(f"  ✗ API error {resp.status_code}: {resp.text[:100]}", RED))
            return None
    except requests.exceptions.Timeout:
        print(color("  ✗ Request timed out", RED))
        return None
    except requests.exceptions.ConnectionError:
        print(color(f"  ✗ Cannot connect to {api_url}", RED))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------
def evaluate_case(case: dict, result: dict) -> dict:
    """
    Compare API result against labeled expectations.
    Returns a dict with pass/fail for each dimension.
    """
    expected_detect  = case["should_detect"]
    expected_sev     = case["expected_severity"]
    expected_range   = case["expected_line_range"]

    actual_sev       = result.get("severity", "None")
    actual_line      = result.get("bug_line", 0)
    actual_ignored   = result.get("ignored", False)
    actual_confidence = result.get("confidence", 0)
    actual_score     = result.get("score", 0)

    # Detection: did we correctly identify whether a bug exists?
    detected = (actual_sev != "None") and not actual_ignored
    detection_correct = (detected == expected_detect)

    # Severity accuracy (only meaningful when both sides agree a bug exists)
    severity_correct = None
    if expected_detect and detected:
        severity_correct = (actual_sev == expected_sev)

    # Line accuracy: is the predicted line within the expected range?
    line_correct = None
    if expected_detect and detected and expected_range:
        line_correct = expected_range[0] <= actual_line <= expected_range[1]

    return {
        "id":                case["id"],
        "description":       case["description"],
        "language":          case["language"],
        "expected_bug_type": case["expected_bug_type"],
        "should_detect":     expected_detect,
        "detected":          detected,
        "detection_correct": detection_correct,
        "expected_severity": expected_sev,
        "actual_severity":   actual_sev,
        "severity_correct":  severity_correct,
        "expected_range":    expected_range,
        "actual_line":       actual_line,
        "line_correct":      line_correct,
        "confidence":        actual_confidence,
        "score":             actual_score,
        "prediction":        result.get("prediction", "")[:120],
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def print_report(results: list[dict]) -> None:
    total = len(results)
    if total == 0:
        print(color("No results to report.", RED))
        return

    # --- Core metrics ---
    detection_correct = sum(1 for r in results if r["detection_correct"])

    # True positives / false positives / false negatives
    tp = sum(1 for r in results if r["should_detect"] and r["detected"])
    fp = sum(1 for r in results if not r["should_detect"] and r["detected"])
    fn = sum(1 for r in results if r["should_detect"] and not r["detected"])
    tn = sum(1 for r in results if not r["should_detect"] and not r["detected"])

    precision  = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall     = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1         = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    fpr        = fp / (fp + tn) if (fp + tn) > 0 else 0.0  # false positive rate

    # Severity accuracy
    sev_results = [r for r in results if r["severity_correct"] is not None]
    sev_correct = sum(1 for r in sev_results if r["severity_correct"])
    sev_acc = sev_correct / len(sev_results) if sev_results else 0.0

    # Line accuracy
    line_results = [r for r in results if r["line_correct"] is not None]
    line_correct_count = sum(1 for r in line_results if r["line_correct"])
    line_acc = line_correct_count / len(line_results) if line_results else 0.0

    # Avg confidence on correct vs incorrect detections
    conf_correct   = [r["confidence"] for r in results if r["detection_correct"]]
    conf_incorrect = [r["confidence"] for r in results if not r["detection_correct"]]
    avg_conf_correct   = sum(conf_correct)   / len(conf_correct)   if conf_correct   else 0
    avg_conf_incorrect = sum(conf_incorrect) / len(conf_incorrect) if conf_incorrect else 0

    # --- Print ---
    sep = "─" * 60

    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  BugPredictor Eval Report{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")

    print(f"{BOLD}Overall Detection Accuracy{RESET}")
    print(sep)
    acc_color = GREEN if detection_correct / total >= 0.75 else YELLOW if detection_correct / total >= 0.5 else RED
    print(f"  Accuracy   : {color(f'{detection_correct}/{total} ({detection_correct/total*100:.1f}%)', acc_color)}")
    print(f"  Precision  : {color(f'{precision*100:.1f}%', GREEN if precision >= 0.8 else YELLOW)}  (of bugs flagged, how many were real?)")
    print(f"  Recall     : {color(f'{recall*100:.1f}%', GREEN if recall >= 0.8 else YELLOW)}  (of real bugs, how many were caught?)")
    print(f"  F1 Score   : {color(f'{f1*100:.1f}%', GREEN if f1 >= 0.8 else YELLOW)}")
    print(f"  False Pos  : {color(f'{fpr*100:.1f}%', GREEN if fpr <= 0.2 else RED)}  (clean code flagged as buggy)")
    print()

    print(f"{BOLD}Confusion Matrix{RESET}")
    print(sep)
    print(f"  TP (bug caught)      : {color(str(tp), GREEN)}")
    print(f"  TN (clean, correct)  : {color(str(tn), GREEN)}")
    print(f"  FP (false alarm)     : {color(str(fp), RED)}")
    print(f"  FN (bug missed)      : {color(str(fn), RED)}")
    print()

    print(f"{BOLD}Severity & Line Accuracy{RESET}")
    print(sep)
    print(f"  Severity accuracy : {color(f'{sev_acc*100:.1f}%', GREEN if sev_acc >= 0.7 else YELLOW)}  ({sev_correct}/{len(sev_results)} when bug detected)")
    print(f"  Line accuracy     : {color(f'{line_acc*100:.1f}%', GREEN if line_acc >= 0.6 else YELLOW)}  ({line_correct_count}/{len(line_results)} within expected range)")
    print()

    print(f"{BOLD}Confidence Calibration{RESET}")
    print(sep)
    print(f"  Avg confidence (correct predictions)   : {avg_conf_correct:.1f}%")
    print(f"  Avg confidence (incorrect predictions) : {avg_conf_incorrect:.1f}%")
    calibrated = avg_conf_correct > avg_conf_incorrect
    print(f"  Calibrated : {color('YES ✓', GREEN) if calibrated else color('NO ✗', RED)}  (higher confidence should correlate with correctness)")
    print()

    # --- Per-language breakdown ---
    print(f"{BOLD}Per-Language Breakdown{RESET}")
    print(sep)
    languages = sorted(set(r["language"] for r in results))
    for lang in languages:
        lang_results = [r for r in results if r["language"] == lang]
        lang_correct = sum(1 for r in lang_results if r["detection_correct"])
        print(f"  {lang:<12} : {lang_correct}/{len(lang_results)} correct  ({lang_correct/len(lang_results)*100:.0f}%)")
    print()

    # --- Per-bug-type breakdown ---
    print(f"{BOLD}Per-Bug-Type Breakdown{RESET}")
    print(sep)
    bug_types = sorted(set(r["expected_bug_type"] for r in results if r["should_detect"]))
    for bt in bug_types:
        bt_results = [r for r in results if r["expected_bug_type"] == bt]
        bt_correct = sum(1 for r in bt_results if r["detection_correct"])
        status = color("✓", GREEN) if bt_correct == len(bt_results) else color("✗", RED)
        print(f"  {status} {bt:<25} : {bt_correct}/{len(bt_results)}")
    print()

    # --- Failures ---
    failures = [r for r in results if not r["detection_correct"]]
    if failures:
        print(f"{BOLD}{RED}Failed Cases{RESET}")
        print(sep)
        for r in failures:
            expected = "detect" if r["should_detect"] else "clean"
            actual   = "detected" if r["detected"] else "missed"
            print(f"  ✗ [{r['id']}] {r['description']}")
            print(f"    Expected: {expected} | Got: {actual} | Confidence: {r['confidence']}%")
            print(f"    Prediction: {r['prediction'][:80]}")
            print()

    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="BugPredictor Eval Runner")
    parser.add_argument("--api",     default=DEFAULT_API, help="API base URL")
    parser.add_argument("--token",   default=os.getenv("BUGPREDICTOR_TOKEN", ""), help="JWT auth token")
    parser.add_argument("--verbose", action="store_true", help="Print each result as it runs")
    parser.add_argument("--cases",   default=str(EVAL_CASES_PATH), help="Path to eval_cases.json")
    parser.add_argument("--filter",  default="", help="Filter cases by id prefix (e.g. 'py_' or 'js_')")
    args = parser.parse_args()

    # Load cases
    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(color(f"Eval cases file not found: {cases_path}", RED))
        sys.exit(1)

    with open(cases_path) as f:
        cases = json.load(f)

    if args.filter:
        cases = [c for c in cases if c["id"].startswith(args.filter)]
        print(color(f"Filtered to {len(cases)} cases matching '{args.filter}'", CYAN))

    print(f"\n{BOLD}BugPredictor Eval Runner{RESET}")
    print(f"API     : {args.api}")
    print(f"Token   : {'set ✓' if args.token else 'not set (anonymous)'}")
    print(f"Cases   : {len(cases)}")
    print(f"Delay   : {RATE_LIMIT_DELAY}s between requests\n")

    results = []

    for i, case in enumerate(cases):
        print(f"[{i+1:02d}/{len(cases)}] {case['id']:<30} ", end="", flush=True)

        result = analyze(args.api, args.token or None, case["filename"], case["code"])

        if result is None:
            print(color("SKIP (API error)", YELLOW))
            continue

        eval_result = evaluate_case(case, result)
        results.append(eval_result)

        # One-line status
        detect_ok  = color("✓ detect", GREEN)  if eval_result["detection_correct"] and eval_result["should_detect"]  else ""
        clean_ok   = color("✓ clean",  GREEN)  if eval_result["detection_correct"] and not eval_result["should_detect"] else ""
        detect_fail= color("✗ missed", RED)    if not eval_result["detection_correct"] and eval_result["should_detect"]  else ""
        fp_fail    = color("✗ false+", RED)    if not eval_result["detection_correct"] and not eval_result["should_detect"] else ""
        sev_note   = color(f" sev={eval_result['actual_severity']}", CYAN) if eval_result["detected"] else ""
        conf_note  = f" conf={eval_result['confidence']}%"

        status = detect_ok or clean_ok or detect_fail or fp_fail
        print(f"{status}{sev_note}{conf_note}")

        if args.verbose and result:
            print(f"       prediction: {eval_result['prediction'][:100]}")

        # Rate limit delay (skip after last case)
        if i < len(cases) - 1:
            time.sleep(RATE_LIMIT_DELAY)

    print()
    print_report(results)

    # Exit code: 0 if F1 >= 0.7, else 1 (useful for CI)
    tp = sum(1 for r in results if r["should_detect"] and r["detected"])
    fp = sum(1 for r in results if not r["should_detect"] and r["detected"])
    fn = sum(1 for r in results if r["should_detect"] and not r["detected"])
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    sys.exit(0 if f1 >= 0.7 else 1)


if __name__ == "__main__":
    main()
