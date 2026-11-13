"""Built-in skills and personas seeded on startup (read-only; users duplicate them to customise)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Skill
from app.services.skills import put_file

BUILTIN_SKILLS: list[dict[str, Any]] = [
    {
        "name": "python-testing",
        "description": "How to write and run pytest tests in this repository.",
        "tools_required": ["read_file", "write_file", "run_shell"],
        "content": """# Python testing

1. Find the existing test layout first (`tests/`, `conftest.py`, pytest config in `pyproject.toml`).
2. Write tests before or together with the change. One behaviour per test; name it
   `test_<unit>_<behaviour>`; use the arrange / act / assert layout.
3. Prefer fixtures over setup code; use `tmp_path` for files, `monkeypatch` for env/globals,
   `pytest.raises` for errors and `pytest.mark.parametrize` for tables of cases.
4. Never call the network or a real database from a unit test; fake the boundary.
5. Run `pytest -q` (or the project's own command) and read the *first* failure before fixing anything.
6. Finish with the full suite green and say how many tests ran.

See `/skills/python-testing/patterns.md` for fixture and parametrize patterns.
""",
        "files": {
            "patterns.md": """# pytest patterns

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return make_client()

@pytest.mark.parametrize("raw, expected", [("1", 1), ("2", 2)])
def test_parse(raw, expected):
    assert parse(raw) == expected

def test_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        parse("")
```

Async tests: plain `async def test_x()` works when `asyncio_mode = "auto"` is configured;
otherwise mark with `@pytest.mark.asyncio`.
""",
        },
    },
    {
        "name": "react-component",
        "description": "Conventions for new React + TypeScript components.",
        "tools_required": ["read_file", "write_file", "list_files"],
        "content": """# React component

- One component per file, named export, file name = component name (`UserCard.tsx`).
- Props typed with an explicit `interface Props`; no `any`; avoid `React.FC`.
- Keep state local unless two siblings need it; derive values during render instead of
  mirroring them in state; effects only for syncing with external systems.
- Accessibility: every input has a label, buttons have text or `aria-label`, keyboard works.
- Styling follows the project's existing approach (Tailwind classes here); no inline styles.
- Add a test next to the component (`UserCard.test.tsx`) using Testing Library: render,
  interact with `userEvent`, assert on what the user sees.

Template in `/skills/react-component/template.tsx`.
""",
        "files": {
            "template.tsx": """import { useState } from 'react'

interface Props {
  title: string
  onSelect?: (value: string) => void
}

export function ExampleCard({ title, onSelect }: Props) {
  const [value, setValue] = useState('')
  return (
    <section className="rounded border p-3">
      <h2 className="font-semibold">{title}</h2>
      <label className="block text-xs" htmlFor="example-value">Value</label>
      <input id="example-value" value={value} onChange={(e) => setValue(e.target.value)} />
      <button type="button" onClick={() => onSelect?.(value)}>Select</button>
    </section>
  )
}
""",
        },
    },
    {
        "name": "git-workflow",
        "description": "Small, reviewable commits and clean diffs.",
        "tools_required": ["run_shell", "git_diff"],
        "content": """# Git workflow

- Work on the run's branch only; never rewrite history or force-push.
- Before editing, run `git status` and `git diff` to understand the state of the tree.
- Keep the diff minimal: no unrelated reformatting, no stray debug output, no generated files.
- Group changes into logical commits with messages in the imperative mood:
  `Add retry to webhook delivery`, body explains *why* when it is not obvious.
- Run the project's formatter/linter before finishing and re-check `git diff --stat`.
- Summarise the final diff (files touched, what changed, how it was verified).
""",
        "files": {},
    },
    {
        "name": "code-review-checklist",
        "description": "What to look for when reviewing a diff; how to phrase findings.",
        "tools_required": ["read_file", "git_diff"],
        "content": """# Code review checklist

Review the diff, not the whole repository. For each finding give file:line, the problem,
why it matters and a concrete fix. Order findings by severity.

1. Correctness: edge cases (empty, null, huge, unicode), off-by-one, error paths, concurrency.
2. Behaviour changes that are not covered by a test.
3. Security: input validation, injection, secrets in code, unsafe deserialisation, path traversal.
4. Performance: N+1 queries, unbounded loops, blocking calls on async paths.
5. Readability: naming, dead code, duplicated logic, comments that lie.
6. API/compat: public signatures, migrations, config defaults.

End with exactly one line: `VERDICT: APPROVE` or `VERDICT: REQUEST_CHANGES`.
Full checklist in `/skills/code-review-checklist/checklist.md`.
""",
        "files": {
            "checklist.md": """# Full checklist

## Correctness
- [ ] Inputs validated at the boundary
- [ ] Errors are handled or deliberately propagated
- [ ] No silent catch-all except blocks
- [ ] Time zones / encodings / locale handled explicitly

## Tests
- [ ] New behaviour has a test; the test fails without the change
- [ ] No flaky timing or network dependence

## Security
- [ ] No secrets, tokens or credentials in the diff
- [ ] User input is escaped / parameterised
- [ ] File paths are constrained to the expected directory

## Maintainability
- [ ] Names say what things are
- [ ] Functions do one thing
- [ ] Documentation / README updated when behaviour changed
""",
        },
    },
    {
        "name": "security-audit",
        "description": "Systematic security review of a codebase or change.",
        "tools_required": ["read_file", "list_files", "run_shell"],
        "content": """# Security audit

Work through the OWASP-style list below; report only real, reachable issues with evidence
(file:line and how an attacker reaches it), severity (critical/high/medium/low) and a fix.

1. Secrets: grep for keys, passwords, tokens; check `.env` files and git history hints.
2. Injection: SQL/NoSQL built by string concatenation, shell commands from user input,
   template injection, path traversal in file access.
3. AuthN/AuthZ: endpoints without auth, object-level checks (IDOR), weak password hashing,
   JWT without expiry/signature verification, CSRF on cookie-auth state changes.
4. Data exposure: verbose errors, debug endpoints, secrets in logs, CORS `*` with credentials.
5. Dependencies: known-vulnerable or unpinned packages (`pip audit`, `npm audit` if available).
6. Crypto: home-made crypto, ECB mode, predictable randomness for tokens.
7. Resource limits: unbounded uploads, missing timeouts, regex DoS.

Do not modify files unless the task explicitly asks for fixes. Finish with a table of findings.
Grep hints in `/skills/security-audit/greps.md`.
""",
        "files": {
            "greps.md": """# Useful greps

```
grep -rniE "(api[_-]?key|secret|password|token)\\s*[:=]" --include=*.py --include=*.ts --include=*.env* .
grep -rnE "subprocess\\.(call|run|Popen)\\(.*shell=True" .
grep -rnE "execute\\(.*%|execute\\(.*\\+|f\\\"(SELECT|INSERT|UPDATE|DELETE)" .
grep -rnE "eval\\(|exec\\(|pickle\\.loads|yaml\\.load\\(" .
grep -rnE "allow_origins=\\[\\\"\\*\\\"\\]|Access-Control-Allow-Origin: \\*" .
```
""",
        },
    },
]


# ---------------------------------------------------------------- personas (who the agent is)

BUILTIN_PERSONAS: list[dict[str, Any]] = [
    {
        "name": "researcher",
        "kind": "persona",
        "description": "Careful analyst who separates evidence from inference and always cites sources.",
        "tools_required": [],
        "content": """# Researcher

You are a meticulous research analyst.

- Prefer primary and reputable sources; note the date and author of each source you use.
- Separate **facts** (with a citation like [1]) from **your interpretation**; never invent numbers,
  quotes or references. If something is unknown, say so and suggest how to find out.
- Compare at least two sources on any contested claim and surface disagreements.
- Write in a neutral, precise tone; short paragraphs; headings for structure; a "References"
  list at the end with full URLs.
""",
        "files": {},
    },
    {
        "name": "copywriter",
        "kind": "persona",
        "description": "Brand-aware writer who turns briefs into clear, engaging copy.",
        "tools_required": [],
        "content": """# Copywriter

You are a senior copywriter.

- Start from the reader: what do they need to know or feel after reading? Lead with that.
- Match the brief's tone exactly (friendly / professional / playful); keep sentences short;
  one idea per paragraph; active voice; concrete nouns and verbs over adjectives.
- Headlines promise a benefit; the first sentence earns the second.
- No clichés, no exclamation-mark enthusiasm, no unverifiable superlatives ("best", "#1").
- Finish with a title, the body, and 3 alternative headlines when asked for options.
""",
        "files": {},
    },
    {
        "name": "data-analyst",
        "kind": "persona",
        "description": "Pragmatic analyst who checks the data before drawing conclusions.",
        "tools_required": [],
        "content": """# Data analyst

You are a data analyst who is sceptical of numbers until they are checked.

- Before any conclusion: inspect shape, types, missing values, duplicates, outliers and the
  time range. State the assumptions you had to make.
- Show the computation (SQL / pandas) so it can be re-run; round sensibly; keep units.
- Report findings as: headline number → context (vs. previous period / baseline) → caveats.
- Charts: one message per chart, labelled axes, no 3D, no dual axes unless unavoidable.
- Never present correlation as causation; flag small samples.
""",
        "files": {},
    },
    {
        "name": "support-agent",
        "kind": "persona",
        "description": "Calm, empathetic customer-support voice that resolves or escalates.",
        "tools_required": [],
        "content": """# Support agent

You are a customer-support agent for the company running this workflow.

- Greet by name when known; acknowledge the problem in one sentence before solving it.
- Be concise and warm; plain language; no jargon, no blame, no over-apologising.
- Only promise what you can verify (refund policies, dates, features). If unsure, say what you
  will check and when the customer will hear back.
- Give numbered steps for anything the customer must do; offer one clear next action.
- Escalate (and say so) for: legal threats, security incidents, payments disputes, abuse.
- Never ask for passwords or full card numbers; never share other customers' data.
""",
        "files": {},
    },
    {
        "name": "document-extractor",
        "kind": "persona",
        "description": "Strict structured-data extractor: schema-exact JSON, null when unsure.",
        "tools_required": [],
        "content": """# Document extractor

You extract structured data from documents (invoices, forms, contracts, emails).

- Output **only** JSON that matches the requested schema: no prose, no markdown fences.
- Use `null` for fields you cannot find or read with confidence; never guess values.
- Normalise: dates as `YYYY-MM-DD`, numbers without thousands separators, currency as ISO code,
  strings trimmed. Keep the document's original language for free-text fields.
- Preserve line items in order; include a `confidence` (0-1) per record when the schema allows.
- If the document is not the expected type, return `{"error": "unexpected document", "detected": "..."}`.
""",
        "files": {},
    },
]

# ---------------------------------------------------------------- extra skills for general workflows

BUILTIN_SKILLS_EXTRA: list[dict[str, Any]] = [
    {
        "name": "brand-voice-template",
        "description": "Fill-in template describing a brand's voice, used to review or write copy.",
        "tools_required": [],
        "content": """# Brand voice

Before writing or reviewing copy, read `/skills/brand-voice-template/voice.md` (edit it for your
brand). Check every draft against it:

1. Tone words: does the text sound like the three tone words? Remove anything that does not.
2. Vocabulary: use the preferred terms, avoid the banned ones.
3. Structure: short paragraphs, scannable headings, one call to action.
4. Reviewer output: reply `APPROVED` + one sentence, or `REVISE` + a numbered list of edits that
   quote the offending text.
""",
        "files": {
            "voice.md": """# <Brand> voice guide (template)

**We sound:** friendly, clear, confident (pick three tone words)
**We never sound:** salesy, sarcastic, vague

## Vocabulary
| Prefer | Avoid |
|---|---|
| customers | users |
| plan | subscription tier |
| help | support ticket |

## Style
- Sentences under 20 words; paragraphs under 4 lines.
- Use "you" and "we". Contractions are fine.
- Numbers as digits; no exclamation marks; Oxford comma.

## Examples
- Good: "Your report is ready. Open it to see what changed this week."
- Bad: "Exciting news!!! Our cutting-edge AI has generated your amazing report!"
""",
        },
    },
    {
        "name": "citation-format",
        "description": "How to cite sources inline and in the References list.",
        "tools_required": ["web_search", "scrape"],
        "content": """# Citation format

- Number sources in order of first use: [1], [2]… and reuse the same number for the same source.
- Put the marker right after the claim it supports: "Revenue grew 12% in 2025 [2]."
- Every factual sentence that is not common knowledge needs a marker; quotes need a marker and
  quotation marks.
- End with `## References` listing each source as:
  `[n] Author or Organisation. "Title". Site/Publisher, YYYY-MM-DD. URL`
- If a fact appears in only one weak source, say "according to [n]" and flag it as unverified.
See `/skills/citation-format/examples.md`.
""",
        "files": {
            "examples.md": """# Examples

Inline:
> Lithium-ion recycling capacity in Southeast Asia reached 120 kt/yr in 2025 [1], although
> industry analysts disagree on utilisation rates [2][3].

References:
[1] International Energy Agency. "Global EV Outlook 2025". IEA, 2025-04-23. https://www.iea.org/reports/global-ev-outlook-2025
[2] Nguyen, T. "Battery recycling in Vietnam". VnExpress, 2025-09-02. https://e.vnexpress.net/...
[3] BloombergNEF. "Recycling Market Outlook". BNEF, 2025-07-15. https://about.bnef.com/...
""",
        },
    },
    {
        "name": "data-analysis-checklist",
        "description": "Checklist to run before trusting a dataset or reporting a metric.",
        "tools_required": ["query_sql", "run_python"],
        "content": """# Data analysis checklist

Work through `/skills/data-analysis-checklist/checklist.md` and include a short "Data checks"
section in your report stating what you verified and what you could not.

Key rules:
1. Profile first (rows, columns, types, nulls, duplicates, min/max, time range).
2. Define every metric in one sentence before computing it.
3. Compare against a baseline (previous period, target, or segment); report absolute and relative change.
4. Note data gaps and anomalies explicitly rather than silently dropping them.
""",
        "files": {
            "checklist.md": """# Checklist

## Load
- [ ] Row count matches expectation / previous run
- [ ] Column names and types as documented
- [ ] Time range covers the requested period; timezone known

## Quality
- [ ] Duplicates checked on the natural key
- [ ] Null rate per column; decide drop / impute / flag
- [ ] Outliers inspected (not just removed)
- [ ] Units and currencies consistent

## Analysis
- [ ] Metric definitions written down
- [ ] Baseline chosen and stated
- [ ] Segments large enough (n ≥ 30) before comparing
- [ ] Seasonality / calendar effects considered

## Report
- [ ] Headline number with context and caveats
- [ ] Charts labelled; one message per chart
- [ ] Code / SQL included or linked
""",
        },
    },
    {
        "name": "email-tone-guide",
        "description": "Tone and structure rules for customer-facing email replies.",
        "tools_required": ["send_email"],
        "content": """# Email tone guide

Use for drafting replies (see `/skills/email-tone-guide/examples.md`).

- Subject: keep the original (`Re: …`) unless it is empty.
- Opening: greet by name, one sentence acknowledging the request or problem.
- Body: answer first, details second; numbered steps for actions; bold only for deadlines.
- Length: under 150 words unless the customer asked for detail.
- Tone by category: support → calm and practical; sales → helpful, no pressure; billing →
  precise with amounts and dates; complaint → empathetic, own the issue, state the fix.
- Close: one clear next step and a friendly sign-off; no "Please do not hesitate…".
- Never include internal notes, other customers' data, or promises you cannot keep.
""",
        "files": {
            "examples.md": """# Examples

## Support (calm, practical)
Hi Linh,

Thanks for flagging the export error. It happens when the report has more than 10,000 rows.

1. Open the report and set the date range to one month.
2. Click Export again; you will get one file per month.

We are shipping a fix for larger exports next week; I will email you when it is live.

Best,
Minh

## Billing (precise)
Hi Mr. Tran,

Your invoice #1042 for 1,200,000 VND was paid on 2026-11-03; the duplicate charge of the same
amount on 2026-11-04 has been refunded and should appear within 5 business days.

Regards,
Minh
""",
        },
    },
]


def seed_builtin_skills(db: Session) -> int:
    """Insert built-in skills that are not present yet. Returns the number added."""
    existing = set(db.scalars(select(Skill.key).where(Skill.key.is_not(None))).all())
    added = 0
    for spec in [*BUILTIN_SKILLS, *BUILTIN_SKILLS_EXTRA, *BUILTIN_PERSONAS]:
        if spec["name"] in existing:
            continue
        s = Skill(
            key=spec["name"],
            user_id=None,
            name=spec["name"],
            description=spec["description"],
            content=spec["content"],
            tools_required=list(spec["tools_required"]),
            kind=spec.get("kind", "skill"),
            files=[],
            is_builtin=True,
        )
        for path, text in spec["files"].items():
            put_file(
                s,
                path,
                text.encode("utf-8"),
                "text/markdown" if path.endswith(".md") else "text/plain",
            )
        db.add(s)
        added += 1
    if added:
        db.commit()
    return added
