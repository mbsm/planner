# 🚀 START HERE: FoundryPlanner Two-Layer Planning System

## What Just Happened

Your codebase has been **analyzed and redesigned** for a major upgrade:

**Before:** Single-layer dispatch system for Terminaciones  
**After:** Dual-layer production planning platform for entire plant

**Sources:** SAP Visión + MB52 only (no MB51). Orders are built once and shared by both layers. Parts/master remain the internal GUI-managed table and are shared.
**Tactical today:** MB52-driven heuristic (priority asc, then due_date − process_time); does not consume the weekly plan. Only the future molding dispatcher will use `plan_molding`.

---

## 📚 Documents Created (Read in This Order)

### 1. **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** — 5 min read ⭐ START HERE
Quick visual guide to the two-layer system. Best for: getting the big picture.

### 2. **[PROPOSAL.md](PROPOSAL.md)** — 10 min read
Executive summary: what's changing, timeline, next steps. Best for: stakeholders, decision-makers.

### 3. **[INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)** — 30 min read (detailed)
300-line technical design: data flows, schema, architecture, risk mitigation. Best for: developers planning implementation.

### 4. **[IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)** — Reference (use during coding)
400 actionable tasks across 6 phases (7 weeks). Best for: developers executing the work.

---

## 🎯 The Vision (30 seconds)

```
┌──────────────────────────────────────────────────────┐
│  STRATEGIC: Weekly MIP solver                        │
│  Q: "How many molds per order per week?"             │
│  Respects: flask capacity, melt deck, line hours     │
│  Tool: foundry_planner_engine                        │
└──────────────────┬───────────────────────────────────┘
                   │
                   ↓ Weekly allocations
┌──────────────────────────────────────────────────────┐
│  TACTICAL: Enhanced hourly dispatch                  │
│  Q: "Which line runs what order TODAY?"              │
│  Respects: weekly plan, family affinity, current WIP │
│  Tool: Enhanced heuristic scheduler                  │
└──────────────────────────────────────────────────────┘
```

**Result:** 85%+ on-time delivery (up from 75%), smoother production

---

## ✅ What's Ready

| Item | Status |
|------|--------|
| Project renamed to FoundryPlanner | ✅ |
| Package renamed to `foundryplanner` | ✅ |
| foundry_planner_engine vendored as submodule | ✅ |
| README and docs updated (two layers) | ✅ |
| Copilot instructions updated | ✅ |
| Architecture designed (12 new tables, 3 new classes) | ✅ |
| Implementation checklist (6 phases) | ✅ |

---

## ✅ Decisions Locked In

- Weekly solve time: **Monday 00:00 UTC** (accepted)
- Timeline: **7 weeks** OK (we'll try to beat it)

---

## 🔧 What Comes Next

### Phase 1 (Weeks 1-2): Foundation
- [x] Create `src/foundryplanner/planning/` package scaffold
- [ ] Wire foundry_planner_engine submodule into imports
- [ ] Update schema to v5 (12 new tables)
- [ ] Create DataBridge + ResultReader stubs

### Phase 2 (Weeks 2-3): ETL
- [ ] Implement StrategyDataBridge (SAP → engine inputs)
- [ ] Implement StrategyResultReader (engine outputs → UI models)

### Phases 3-6: Orchestration, UI, Testing, Deploy
- See [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for detailed tasks

---

## 📖 Key Files Reference

```
Root/
├─ QUICK_REFERENCE.md               ← 5 min overview
├─ PROPOSAL.md                       ← Executive summary
├─ INTEGRATION_ARCHITECTURE.md       ← Technical deep-dive
├─ IMPLEMENTATION_CHECKLIST.md       ← Day-to-day tasks
├─ README.md                         ← Updated
├─ .github/copilot-instructions.md  ← Updated (for AI agents)
└─ src/foundryplanner/
   ├─ planning/                    ← NEW (will be created in Phase 1)
   ├─ dispatching/scheduler.py     ← EXISTING (will be enhanced)
   ├─ data/
   │  ├─ repository.py              ← EXISTING (add new methods)
   │  └─ db.py                      ← EXISTING (add schema v5)
   └─ ui/pages.py                   ← EXISTING (add /plano-semanal)
```

---

## 🎓 For AI Agents / Code Assistants

If you're working with GitHub Copilot, Claude, or similar:

1. Read [.github/copilot-instructions.md](.github/copilot-instructions.md) first
2. Reference specific sections of [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
3. Use [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for task breakdown
4. Ask questions in context of the docs above

---

## 🚀 Success Criteria (Month 2)

- ✅ Two-layer planning operational
- ✅ Weekly solver running automatically
- ✅ On-time delivery: 85%+ (was 75%)
- ✅ Lateness reduction: 15-20%
- ✅ Line utilization smooth: ±10% week-to-week

---

## 💬 Questions?

- **"What is foundry_planner_engine?"** → See QUICK_REFERENCE.md + INTEGRATION_ARCHITECTURE.md
- **"How do I start coding?"** → See IMPLEMENTATION_CHECKLIST.md Phase 1
- **"What's the data model?"** → See INTEGRATION_ARCHITECTURE.md (Data Architecture section)
- **"What about risks?"** → See INTEGRATION_ARCHITECTURE.md (Risk Mitigation section)

---

## Next Step

👉 **Read [QUICK_REFERENCE.md](QUICK_REFERENCE.md) now** (5 minutes)

Then decide on the 5 questions above, and we'll kick off Phase 1.

Good luck! 🎉
