"""Scratchpad template for linting the notebook."""

from __future__ import annotations


def build_lint_template() -> str:
    """Build the scratchpad code template for linting.

    Returns:
        Python code string that runs in the scratchpad.
    """
    return """
import json
import marimo._code_mode as cm
from marimo._lint.diagnostic import Severity
from marimo._lint.rule_engine import RuleEngine

async def lint_notebook():
    async with cm.get_context() as ctx:
        # Get notebook IR through code mode
        # The lint engine checks for breaking, runtime, and formatting issues
        try:
            rule_engine = RuleEngine.create_default()
            diagnostics = await rule_engine.check_notebook(ctx.notebook)
        except Exception as e:
            return json.dumps({{
                "summary": {{
                    "total_issues": 0,
                    "breaking_issues": 0,
                    "runtime_issues": 0,
                    "formatting_issues": 0,
                }},
                "diagnostics": [],
                "error": str(e),
            }})

        # Count by severity
        breaking = 0
        runtime = 0
        formatting = 0

        results = []
        for diagnostic in diagnostics:
            severity = diagnostic.severity
            if severity == Severity.BREAKING:
                breaking += 1
            elif severity == Severity.RUNTIME:
                runtime += 1
            elif severity == Severity.FORMATTING:
                formatting += 1

            results.append({{
                "rule": diagnostic.rule,
                "severity": str(severity),
                "message": diagnostic.message,
                "cell_id": diagnostic.cell_id if hasattr(diagnostic, 'cell_id') else None,
                "line": diagnostic.line if hasattr(diagnostic, 'line') else None,
                "column": diagnostic.column if hasattr(diagnostic, 'column') else None,
            }})

        total = breaking + runtime + formatting

        return json.dumps({{
            "summary": {{
                "total_issues": total,
                "breaking_issues": breaking,
                "runtime_issues": runtime,
                "formatting_issues": formatting,
            }},
            "diagnostics": results,
        }})

print(await lint_notebook())
"""


# Pre-built default template
TEMPLATE_LINT = build_lint_template()
