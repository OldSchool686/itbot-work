#!/usr/bin/env python3
"""Performance budget validation script for CI.

Checks that response times stay within acceptable bounds.
Run with: python scripts/check_performance_budget.py

Returns exit code 0 if all budgets pass, 1 if any fail.
"""
import sys
import json


def check_budgets(performance_stats: dict) -> list[str]:
    """Check performance metrics against budget thresholds.
    
    Args:
        performance_stats: Dict with avg_ms, p95_ms, max_ms from /api/v1/performance/stats
        
    Returns:
        List of failed budget checks (empty if all pass)
    """
    failures = []
    
    budgets = {
        "avg_response_time_ms": 200,
        "p95_response_time_ms": 800, 
        "max_response_time_ms": 3000,
    }
    
    actuals = {
        "avg_response_time_ms": performance_stats.get("avg_ms", 0),
        "p95_response_time_ms": performance_stats.get("p95_ms", 0),
        "max_response_time_ms": performance_stats.get("max_ms", 0),
    }
    
    for metric, budget in budgets.items():
        actual = actuals[metric]
        if actual > budget:
            failures.append(
                f"BUDGET EXCEEDED: {metric} = {actual:.1f}ms (budget: {budget}ms)"
            )
            
    return failures


def main():
    """Main entry point for CI script."""
    print("Performance Budget Check")
    print("=" * 50)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("""Usage: python check_performance_budget.py [stats.json]

Checks performance metrics against budget thresholds.

Budgets:
- Average response time: < 200ms
- P95 response time: < 800ms  
- Max response time: < 3000ms

Examples:
  python check_performance_budget.py                    # Use mock data (always passes)
  python check_performance_budget.py stats.json         # Check actual metrics
  
CI Integration:
  curl -s http://localhost:8000/api/v1/performance/stats > stats.json
  python check_performance_budget.py stats.json
""")
        sys.exit(0)
        
    if len(sys.argv) > 1:
        try:
            with open(sys.argv[1], 'r') as f:
                stats = json.load(f)
        except FileNotFoundError:
            print(f"Error: Stats file '{sys.argv[1]}' not found")
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON in '{sys.argv[1]}'")
            sys.exit(1)
    else:
        stats = {"avg_ms": 0, "p95_ms": 0, "max_ms": 0}
        print("No stats file provided. Using mock data (all budgets pass).")
        
    failures = check_budgets(stats)
    
    if not failures:
        print("\n✅ All performance budgets passed!")
        sys.exit(0)
    else:
        print("\n❌ Performance budget violations:")
        for failure in failures:
            print(f"   {failure}")
        sys.exit(1)


if __name__ == "__main__":
    main()
