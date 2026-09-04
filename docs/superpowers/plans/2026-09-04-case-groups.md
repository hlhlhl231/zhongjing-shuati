# Case Group Questioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Treat each original case as one top-level question while preserving per-subquestion IDs for grading, wrong-answer memory, and knowledge-point review.

**Architecture:** Group filtered questions before sampling so a case cannot be split by `--n`. Render case material once and subquestions as `N.1`, `N.2`, etc. Keep `answers`, `logs`, SRS state, and knowledge blocks keyed by the existing subquestion IDs.

**Tech Stack:** Python 3.8+ standard library, `unittest`, existing JSONL bank format.

## Global Constraints

- A case group counts as one requested question.
- Every selected case includes its original material and all subquestions in that group.
- Grading and logging remain keyed by subquestion `题目ID`.
- `wrong` expands a wrong subquestion to its full original case group for practice.
- No new runtime dependencies.

---

### Task 1: Regression Tests

**Files:**
- Create: `tests/test_pick.py`

**Interfaces:**
- Consumes: `scripts/pick.py` functions `cmd_pick`, `cmd_wrong`, and `cmd_logs`.
- Produces: deterministic temporary JSONL fixtures for one two-subquestion case and one standalone question.

- [x] **Step 1: Write failing tests**
- [x] **Step 2: Run `python -m unittest discover -s tests -v` and confirm both grouping tests fail for the expected reason**

### Task 2: Case-Aware Selection and Rendering

**Files:**
- Modify: `scripts/pick.py`

**Interfaces:**
- Produces: `case_key(q)`, `group_case_units(qs)`, `unit_weight(unit, state)`, `fmt_unit(unit, ...)`, and `id_map(units)`.

- [x] **Step 1: Group questions into top-level units before weighted sampling**
- [x] **Step 2: Render case material once and all subquestions as `N.1`, `N.2`, ...**
- [x] **Step 3: Emit one top-level mapping entry containing all subquestion IDs**
- [x] **Step 4: Run unit tests and confirm they pass**

### Task 3: Wrong-Question Expansion and Documentation

**Files:**
- Modify: `scripts/pick.py`
- Modify: `README.md`
- Modify: `SKILL.md`

**Interfaces:**
- Consumes: the Task 2 unit helpers.
- Produces: full case groups in `wrong`, with unchanged per-subquestion grading and ledger IDs.

- [x] **Step 1: Expand a wrong case subquestion to its complete case group**
- [x] **Step 2: Keep ledger and knowledge-point processing keyed by subquestion IDs**
- [x] **Step 3: Update user/agent documentation**
- [x] **Step 4: Run full unit tests and smoke-test real-bank commands**
- [x] **Step 5: Commit all changes**
