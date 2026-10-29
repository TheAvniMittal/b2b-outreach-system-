# Narayani Distributors — B2B Outreach System
## Vision & Architecture Document · v1.6 (FIVE AUDITS + FOUR OWNER REVIEWS)

**Status:** Design only. No code until §15, §16 and §19 are signed off.
**Date:** 2026-09-07
**Owner:** Mayank Khandelwal
**Supersedes:** v1.5 (`VISION-v1.5-superseded.md`)
**Reviews:** `AUDIT-v0.4.md` (9) · `AUDIT-v0.5.md` (12) · owner review 1 (3) · `AUDIT-v0.8.md` (10) · `REVIEW-OWNER-2.md` (6) · `AUDIT-v1.0.md` (10) · `REVIEW-OWNER-3.md` (2) · `AUDIT-v1.3.md` (6). **55 closed, 3 deferred to P0 as measurements (§18.1).** See §21 and `REVISION-HISTORY.md`.
**Audit pack:** `AUDIT-PACK.md` — the short version for sign-off.

**Inputs:**
- `Narayani Distributors B2B Outreach System — Master Build Prompt.md`
- NASSCOM Cohort 3 Tech Session 2 — all 7 files; compliance is a standing CI gate (§17.2)
- Owner corrections v0.3–v0.4: real LLM decision authority · five segments as first-class · every mail individually written · house-voice rules derived once from sample emails · seven catalogues routed independently · catalogue as both PDF and hosted link · email verification with bounce reporting · deliverability as engineering

---

## 0. How to read this document

Audit targets: **§15** (Context Risk Register), **§16** (production-readiness checklist), **§19** (pass/fail questions).

A mitigation without a verification method is a wish. A verification method that cannot fail is worse than none — it manufactures false confidence. Both audits found instances; both are closed.

### 0.1 Relationship to the Master Build Prompt

**This document governs architecture, context engineering and AI-layer behaviour. The Master Build Prompt remains the feature specification.** Where they overlap, the Master Prompt wins on *what the system does*; this document wins on *how it is built and bounded*.

Feature requirements are inherited, not restated — dashboard metrics (Master Prompt §21), prospect timeline (§22), human-action-queue fields (§23), the opportunity-signal list (§24), the full 20-item daily report (§25 — only the bounce block is specified here, §12.5), audit-entry fields (§26), worked queue-carryover examples (§33). Their absence here is deferral, not omission.

---

## 1. Executive summary

> **A deterministic email operations spine, with an AI layer that has real authority over targeting, segmentation, catalogue routing and content — bounded by an asymmetry rule, not a blanket prohibition.**

1. **Asymmetric authority (§2).** The LLM may always send *less, slower, to fewer people, or not at all*. Never *more, anyway, or again*.
2. **Segment-first content (§5).** Five buyer types, seven catalogues, routed independently.
3. **A written rulebook, not a learning loop (§6).** Your 15 emails are read once and converted into fixed rules; ~70% compile into validator checks — enforced, not requested.
4. **Deliverability as engineering, not disguise (§13).**
5. **Every validator is decidable without an LLM (§10).** No model judges a model in the send path.
6. **Nothing in a free-text field can authorise a fact (§10.1).**

---

## 2. THE CORE RULE — asymmetric authority

| Direction | Authority | Why safe |
|---|---|---|
| **Less / slower / stop** — skip, defer, lower volume, escalate, suppress, hold, pause | **Full LLM authority** | Every failure lands on "fewer emails sent." |
| **Rank / select / classify / compose** — segment, catalogue, angle, claim, CTA, words, segment mix | **Full LLM authority**, deterministic gate after | The LLM decides what is written and which bucket gets today's slots. Code decides how long the line is. |
| **More / anyway / again** — exceed a cap, send despite suppression, un-suppress, re-send, bypass a hold | **None. Ever.** | Every failure here is irreversible and lands in a real inbox. |

### 2.1 What the LLM decides

| Decision | Worker | Deterministic gate |
|---|---|---|
| Today's volume and segment mix | A7 | `min(llm_target, ramp_target, 1000)`; follow-ups ≤500. Can lower, never raise. |
| Whether to send this one — veto | A7 + A2 | A veto is always honoured (§7.2c). An approval is never sufficient alone. |
| Which segment a company is | A6 | `confidence < 0.75` ⇒ `unknown` ⇒ human review |
| ~~Which catalogue to attach~~ | ~~A8~~ | **Not an LLM decision.** `segment → catalogue` is a 1:1 lookup (§5.2) |
| Whether someone is suppressed | A3 | LLM may **add**. Only a human may remove. |
| What state a prospect moves to | A3 / A7 | Validated against the legal-transition map |
| Whether a supplied address has a typo | A1 | **Proposes**; a human confirms |
| Every word of every email | A2 | Three decidable validators (§10) |

**Not LLM-decided: which individual prospect goes first.** That is the deterministic **FitScore** (§7.4b).

### 2.2 What code owns — and the two invariants the LLM never touches

*(v1.2 — owner finding E1. v1.1 titled this "The only two decisions in code", which was flatly contradicted by the rest of the document and could lead a developer to build safety-critical gates as AI calls.)*

**Most of this system is deterministic code.** The whole of Layer 0 is code: the queue, the cap arithmetic, the state machine, the verification ladder, bounce-code parsing, FitScore, batch quarantine, auto-pause, HOLD backpressure, and **every validator in the send path** — grounding, rulebook, sameness, suppression, eligibility, legal-transition checks. None of those is an AI decision, and none was ever a candidate to be one.

Three categories, not two:

| Category | Examples | Who decides |
|---|---|---|
| **A · Code alone, no AI at any stage** | Cap arithmetic · queue ordering · state-transition legality · MX lookups · DSN parsing · Wilson bounds · duplicate detection · **all four validator groups** · eligibility gates · FitScore · **suppression detection from a one-click unsubscribe or a hard bounce code** | **Code.** These are arithmetic, lookups or rule evaluation. There is a correct answer and no judgement to exercise. |
| **B · LLM proposes, code disposes** | Segment · catalogue · every word of every email · today's volume and segment mix · **suppression detection *from replies*** · proposed state transitions · typo correction | **Both.** The LLM's output is an input to a deterministic gate that enforces the invariant (§2.1). |
| **C · Two invariants the LLM never touches at any stage** | Below | **Code, inviolably.** |

**Category C is the narrow claim this section makes.** These are the two rules where the LLM has no input whatsoever — it cannot decide them, propose them, influence them, or be consulted about them — because no amount of correct model behaviour could be trusted to maintain them, and a violation is irreversible and lands in a real inbox:

| Invariant | Rule | Why it admits no proposal |
|---|---|---|
| **1,000/day system-wide ceiling** | Rule 11: *"must never be exceeded"* | Counting, not judgement. A7 may set today's number at 320; the ceiling only clamps. |
| **Was this already sent?** | Rule 28 | A unique DB constraint on `send_key`. Nothing to reason about. |

**Suppression deliberately is not in category C**, and the distinction is instructive. Its *enforcement* is inviolable (§7.5a), but its *detection* is split: a one-click unsubscribe or a hard bounce code is pure category A, while **reading a reply and judging it an opt-out is category B** — A3 exercises judgement there.

So suppression is a **category-A gate fed by both A and B detection**, which is the shape of most of this system *(v1.4 — audit F5; v1.2 attributed all suppression detection to the LLM)*.

---

## 3. Scope

**In scope:** import → verify → validate → dedupe → segment → route catalogue → store → target → personalise → queue → send → monitor delivery → monitor replies → classify → stop-on-human-reply → follow-up cadence → dashboard → reporting.

**Out of scope (Master Prompt §1):** prospect discovery, Google/LinkedIn search, **email-address discovery or guessing**, scraping, external research. **The AI layer has no internet access and no search tool** — a security property, not a limitation.

**Non-goals v1:** no vector DB (§8.5), no CrewAI (§17.1), no hierarchical agents, no worker-to-worker conversation, no LLM write access to the database, no rework loop between workers, **no LLM approving another LLM's output**.

### 3.1 On "sound human" and Rule 35

The requirement is that mail reads as though a person wrote it: warm, specific, direct. That is the goal, and §6 makes it a mechanism.

This system will not deceive a recipient or provider about the use of automation. Not built: forged headers, fake mail-client strings, typing-mimicking delays, character substitution or invisible text, image-only emails hiding text, false "sent from my iPhone."

Beyond Rule 35, the practical reason (§13.7): **filter evasion is far more detectable than good prose.** Providers have decades of evasion signatures and no reliable prose detector.

### 3.2 Legal inputs
GDPR / CAN-SPAM / CASL exposure at this volume. Schema carries `lawful_basis` and `consent_source`; per-campaign physical postal address footer and working one-click unsubscribe are mandatory (§13.5).

---

## 4. System shape

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  LAYER 0 — DETERMINISTIC SPINE   (Python + PostgreSQL. Enforces invariants.) │
│                                                                              │
│  Import ─► VERIFY (DNS/MX/API) ─► Validate ─► Dedupe                         │
│                │                                                             │
│                ├─► [A1 typo proposal] ──► human confirms                     │
│                ▼                                                             │
│    [A6 segment]* ─► catalogue LOOKUP (§5.2) ─► send preview ─► State Machine │
│                                                    │                         │
│                              eligible pool ────────┤                         │
│                                    │               ▼                         │
│                       [A7] sets today's N + segment mix   (aggregates only)  │
│                                    │                                         │
│                       FitScore orders prospects  (deterministic SQL, §7.4b)  │
│                                    ▼                                         │
│                       Persistent Queue (Postgres, SKIP LOCKED)               │
│                                    │                                         │
│                          [A2 personalise] ─► RULEBOOK + GROUNDING            │
│                                    │         + SAMENESS validators (§10)     │
│                                    ▼                                         │
│                 ┌──────────────────┴──────────────────┐                      │
│                 ▼                                     ▼                      │
│          CAP ENFORCER                          IDEMPOTENCY GUARD             │
│   min(llm_target, ramp, 1000) · f-up ≤500      unique send_key               │
│                 │                                     │                      │
│                 └──────────────────┬──────────────────┘                      │
│                                    ▼                                         │
│                    ►►► SUPPRESSION FINAL GATE ◄◄◄                            │
│                                    │                                         │
│                     ATTACH catalogue PDF + hosted link (§11)                 │
│                                    ▼                                         │
│                      EmailProvider (Gmail│M365│SMTP│MOCK)                    │
│                                    │                                         │
│        Delivery · BOUNCE CLASSIFIER (§12) · Reply ingest ─► [A3 classify]    │
│                                    │                                         │
│                 Audit Log · [A4 score] · [A5 digest] · Dashboard             │
└──────────────────────────────────────────────────────────────────────────────┘

  ► Bracketed [Ax] are AI workers. They propose; Layer 0 disposes.
  ► No arrow goes worker → worker. Every handoff round-trips through PostgreSQL.
  ► An AI KILL SWITCH (§14.6) disables the whole AI layer; Layer 0 keeps running.
```

**The one structural rule:** no worker-to-worker conversation. A3 writes a typed row; hours later A4 reads that row *from the database*. This kills Error Propagation, Compounding Errors, Agent Context Explosion, Context Fragmentation, Reasoning Drift, Goal Drift and Memory Bloat — nothing accumulates, because there is no shared context to accumulate into.

---

## 5. Segments and catalogues

### 5.1 Five segments

| Segment | Reader | Cares about | Wrong move |
|---|---|---|---|
| `distributor_wholesaler` | Owner / purchase head | Margin, territory rights, exclusivity, MOQ, credit terms | A 3-piece sample pack to someone who moves containers |
| `importer` | Import manager / compliance | Certifications, HS codes, Incoterms, shelf life, FCL/LCL, documentation | Vague specs. This reader wants documents |
| `wellness_gym` | Owner / nutrition lead | Nutrition profile, clean label, retail-ready packs, resale margin, small MOQ | Offering a 20ft container |
| `supermarket_retail` | Category buyer | Shelf-ready packaging, EAN, listing terms, category fit, promo support | Bulk pricing to someone who thinks in planograms |
| `horeca_bakery` | Chef / purchase manager | Bulk formats, batch consistency, specs, cost per portion | Consumer-pack pricing. They buy inputs |
| `unknown` | — | — | **Never sent to.** Human review. |

### 5.2 One segment, one catalogue — a mapping table, not a decision

*(v1.5 — owner rule. This supersedes the v0.3 choice of independent classification, and **deletes worker A8 entirely**.)*

**The rule you gave: one segment gets exactly one catalogue. If a single catalogue would serve two segments, the file is duplicated under two names so the mapping stays 1:1.**

That turns catalogue selection from a judgement into a lookup:

```
catalogue = segment_catalogue[ prospect.segment ]     -- a JOIN
```

No model, no confidence score, no fail-closed threshold, no golden set, no escape audit. **The segment arrives as a column in your list; the catalogue follows from it deterministically.**

| Was | Now |
|---|---|
| A8 CatalogueSelector — prompt, evals, 0.75 confidence floor | **Deleted** |
| A8 cross-checking A6's segment | Gone — replaced by the send preview (§5.2c) |
| `catalogue_decision_log`, consistency monitor, progressive hardening | Unnecessary; the mapping is a table you edit |
| Risk #33, cross-geography catalogue error | **Dissolved.** `importer_india` and `importer_uae` are separate *segments* with separate catalogues. No decision exists in which they can be confused. |

#### 5.2a Segments are data you manage, not an enum in the code

You asked to add, remove and edit segments, catalogues and campaigns. So `segment` is a **table**, and adding one is a guided flow:

```
Add a segment  ->  1. name it
                   2. attach its catalogue  (upload, or copy an existing file)
                   3. write its playbook    (§5.3)

  -> The segment is NOT selectable for sending until all three exist.
     Incomplete = invisible. Never partial.
```

That last rule is the same fail-closed shape as `segment = unknown` never being queued. A half-configured segment cannot receive mail.

Validators read the segment set from the database rather than a compiled enum. `unknown` still exists and still blocks sending.

#### 5.2b The duplication rule closes the `wellness_gym` gap

Open since v0.3: wellness/gym had no matching catalogue. Your rule answers it — **copy `organic_gourmet` under a wellness name.** The mapping stays 1:1 and the two files diverge later if you want them to.

#### 5.2c The send preview replaces A8's second opinion

A8 used to return an `implied_segment`; disagreement with A6 routed to a human. With A6 deferred (§18) and A8 deleted, **nothing cross-checks the label your CSV supplies** — a typo sends a supermarket catalogue to an importer.

**The send preview is the cross-check.** Before anything goes out, a human sees company, segment and attached catalogue on one screen, and a mismatch is obvious at a glance. This works because warm-up volume is 15–50/day (§13.3) — it is not a permanent answer, and §18 says what replaces it.

### 5.2d The starting set — seven pairs

Because the mapping is 1:1, the catalogue list *is* the segment list. Your seven catalogues become seven segments:

| Segment | Catalogue | Note |
|---|---|---|
| `importer_india` | Indian importer | Geography is a **segment** distinction, not a catalogue-selection problem |
| `importer_uae` | UAE importer | Different documents, different compliance — so a different segment |
| `distributor_wholesale` | Distributor / wholesaler | |
| `fmcg_distributor` | FMCG distributor | |
| `horeca_bakery` | HoReCa / bakery | |
| `organic_gourmet` | Organic & gourmet stores | |
| `supermarket_retail` | Supermarkets / retail chains | |
| `wellness_gym` *(pending)* | **needs a copied catalogue** | §5.2b — likely a copy of organic_gourmet |
| `unknown` | — | **Never queued.** |

This list is a **starting point, not a fixed enum** — you add, remove and edit it in the admin surface (§5.2a). §5.1's five buyer descriptions still apply; an Indian and a UAE importer read as the same *kind* of buyer, they simply receive different documents, and under a 1:1 rule that makes them different segments.

### 5.3 The segment playbook — config, not code

```
segment_playbook {            # EVERY FIELD IS HUMAN-WRITTEN.
                              # Nothing here is derived from the sample
                              # emails — see §6.6a.  (v1.3, owner finding E2:
                              # register_spec and target_words had no stated
                              # author, so segment voice could have leaked in
                              # from 2-3 samples.)
  segment                enum (PK)
  allowed_angles         enum[]
  allowed_ctas           enum[]      # a CTA outside this set is a VALIDATOR FAILURE
  approved_claim_ids     int[]
  forbidden_topics       text[]      # e.g. "FCL/container" for wellness_gym
  target_words_min/max   int         # human-set, NOT inferred from samples
  register_spec          text(≤300)  # segment tone/voice — human-written, and
                                     # the ONLY home for segment-specific voice
  cadence_override       jsonb
  opportunity_weights    jsonb
}
```

| Segment | CTAs |
|---|---|
| distributor_wholesaler | `price_list` · `territory_discussion` · `catalogue` · `call` |
| importer | `spec_sheet` · `certifications_pack` · `quotation_fob_cif` · `sample_shipment` · `call` |
| wellness_gym | `sample_pack` · `nutrition_sheet` · `retail_pricing` · `call` |
| supermarket_retail | `catalogue` · `listing_proposal` · `category_deck` · `sample_pack` · `meeting` |
| horeca_bakery | `sample_pack` · `bulk_pricing` · `spec_sheet` · `call` |

**Every CTA is an offer to send, never a statement of terms** (§10.8). `price_list` means *"I can send our current price list"*; `bulk_pricing` means *"I can share bulk pricing"*; `quotation_fob_cif` means *"I can prepare a quotation."* **No CTA may carry a number.** A CTA outside the segment's allowed set is a validator failure; a CTA containing a commercial term is also a validator failure.

### 5.4 Cadence differs by segment
Master Prompt §6 defaults (0 / 7 / 21 / 30 / 90) are the fallback; the playbook overrides per segment.

### 5.5 Open gap
**`wellness_gym` has no matching catalogue.** Add an eighth, map it explicitly, or drop the segment. Needs an answer before P2.

---

## 6. THE EMAIL WRITING RULEBOOK

### 6.1 Built by a convergence loop, not from supplied emails

*(v1.6 — owner decision. Earlier versions assumed the owner would supply 12–15 emails they had already written. That premise is withdrawn: **not everyone writes English confidently, and recognition is easier than production.** Correcting a draft asks for business knowledge; authoring one asks for a writing skill that is not the point.)*

```
Stage 0  interview  ──►  RULEBOOK v0  (guidance only, no hard checks)
                              │
              ┌───────────────┴───────────────┐
              │                               │
              ▼                               │
   draft N written from the RULEBOOK ONLY     │
   in a fresh context — no conversation       │
              │                               │
              ▼                               │
   human clicks what is wrong, says why       │
   in any language; rule confirmed            │
              │                               │
              ▼                               │
   three guards run (§6.1c)                   │
              │                               │
              └───────────────────────────────┘
                              │
              SEGMENT SWEEP gates convergence (§6.1d)
                              │
              BASELINE CHECK  draft 1 vs final, blind (§6.1e)
                              │
                              ▼
                    RULEBOOK v1 — hard checks activate
```

**Every draft is written from the rulebook alone, in a clean session.** If corrections accumulated in a running conversation the model would *appear* to improve while learning nothing durable — it would be following a transcript that is discarded when P-B ends. Writing each draft from the rulebook makes *"the model got good"* mean **"the rulebook got good"**, which is the only thing that survives into production. Same principle as §8.1.

**The drafts never enter a production prompt.** They are a construction scaffold; the rules they produce are what ships.

#### 6.1a Stage 0 — the interview seeds guidance, never rules

Seven questions requiring no writing and no English judgment: greeting form, length, whether to mention location, sign-off shape, always-say, never-say, and — the most valuable one — **"paste an email you like the sound of."**

That last question is the cheapest available answer to *"voice you have never expressed"*: everyone can recognise an email they like, even if they could not write one. **Structure only is extracted** — greeting form, length, where the ask sits — **never phrases.** Lifting sentences from someone else's email is both a copyright problem and a grounding failure, so the source text is discarded after extraction.

**No interview answer becomes a hard check.** They are guesses made in the abstract — people routinely choose "200 words" and then correct every draft for being too long. They enter as guidance and harden only if the loop confirms them independently.

#### 6.1b Correcting at line level *and* whole-email level

Click a line and say what is wrong, in any language; the rule is proposed **in both languages** and only exists once confirmed.

But *"too long," "wrong order," "you left out what we actually sell"* have **no line to click.** Absence is a whole class of correction, so whole-email targets exist alongside: `too long · too short · wrong order · something missing · wrong subject`.

#### 6.1c The three guards

| Guard | Catches |
|---|---|
| **Regression check** | Every new rule re-runs the drafts already accepted. If rule 9 breaks draft 3, the conflict surfaces **the moment it is created** — otherwise the loop builds a rulebook that is individually sensible and **collectively unsatisfiable** |
| **Joint pass rate** | Hard checks multiply: 3 validators = 81%, +10 checks at 98% = 66%, +15 = 60%. Below §9.1's 80% gate the cost model in §9.3 is wrong by half. The regression re-runs supply the measurement for free |
| **Fatigue tracking** | Corrections that *taper* are convergence; corrections that **stop abruptly**, or drafts read in a fraction of the earlier time, are fatigue. Without this, a tired reviewer ends the loop and ships the rulebook |

#### 6.1d Convergence is a segment sweep, not "two clean in a row"

Ten drafts across eight segments means most segments see exactly one — so a rule added while looking at a UAE importer **may never be tested against a gym at all.** That is audit F2 again: a rulebook derived from a skewed sample, wearing a global label.

**One draft per segment, using the current rulebook. Converged only if every segment passes.** A failure names the segment and the rule that broke it, and the usual resolution is that the rule belongs in that segment's playbook rather than the global rulebook — exactly the boundary §6.6a draws.

This also closes the fatigue hole: a tired reviewer can produce two clean drafts in a row. **They cannot produce eight clean drafts across eight segments.**

#### 6.1e The baseline check

Draft 1 is kept from the start. Before any rule hardens, draft 1 and the final draft are shown **shuffled and unlabelled**: *"which would you send?"*

Pick the final one and the loop worked. Can't tell, and rules stay guidance. **Pick draft 1 and the loop made it worse** — discard and restart. Without this, "getting better" rests on memory of draft 1 after seven drafts have anchored the reviewer.

#### 6.1f Nothing hardens early

**No rule becomes a hard validator check until the segment sweep and the baseline check both pass.** Until then a wrong rule can only produce a slightly-off email; it cannot reject a good one.

### 6.2 Rules that can be counted become checks

| Rule | Enforced as |
|---|---|
| Greeting form — `Dear <First Name>,` only | Regex on line 1 |
| **Never open with a wellbeing phrase** | Banned-opener denylist |
| Word count within segment target | Word count |
| Max 3 paragraphs, 2–3 sentences each | Structure parse |
| Exactly one CTA, final paragraph, from the segment's allowed set | Enum match + position |
| Sign-off block exact form | Regex |
| Banned phrases ("reaching out", "leverage", "synergy"…) | Denylist |
| No exclamation marks, no ALL-CAPS words | Character check |

The remaining ~30% — tone, how directness reads, when shorter is better, thin-data prospects — stays as prompt guidance because it cannot be counted.

### 6.3 Why this kills the greeting problem
*"Don't say 'I hope this email finds you well'"* in a prompt is the negative-only anti-pattern (NASSCOM §4.4); models drift back to it. As a **denylist check that rejects the draft**, the model cannot ship one.

### 6.4 The rulebook is also the instruction-adherence instrument
Because ~70% is machine-checked on every draft, **rulebook first-pass rate is a continuous measurement of instruction-following.** If A2's prompt grows and adherence degrades, that rate falls visibly, per rule, in production. This is the verification for risk #13.

### 6.5 Rulebook lifecycle

A stale rulebook does not merely produce dated prose — because most of it is hard checks, **it starts rejecting correct emails.** So it gets the same lifecycle controls as `approved_claims`:

```
rulebook {
  version              semver
  derived_from         sample_email_ids[]
  created_at
  last_reviewed_at     -> 180-day review task
  per_rule_stats       -> rejection rate per rule, rolling 7 and 30 days
}
```

**Alarm thresholds** *(v0.6 — audit A12; v0.5 said "an unusual share", which is not a number):*

| Trigger | Action |
|---|---|
| A single rule rejects **>25% of drafts over a rolling 7 days** | Raise a review task naming that rule |
| A single rule's 7-day rate exceeds **2× its own 30-day baseline** | Raise a review task naming that rule |

### 6.6 Honest limitations
- **A2's system prompt grows 900 → ~1,200 tokens.** Costed in §9. Replaces the ~3,000 tokens exemplars would have cost.

### 6.6a The rulebook is global house voice — segment voice is human-written

*(v1.3 — owner finding E2. v1.2 said the rulebook would be "mostly global, segment rules only where samples justify them" — a tendency, with no threshold on "justify". This makes it a rule.)*

**Fifteen emails across five segments is 2–3 each at best, and almost certainly less evenly than that** (§6.6b). That is enough to learn how Narayani writes. It is not enough to learn *"this is exactly how Narayani speaks to supermarket buyers."*

So the boundary is now absolute:

| Source | Contains | Authored by |
|---|---|---|
| **`email_writing_rulebook.md`** | **Global house voice only** — greeting form, banned openers, sentence and paragraph shape, sign-off, banned phrases, punctuation | **Mixed — see §6.6c. Not all of it is derived.** |
| **`segment_playbook`** (§5.3) | **All segment-specific behaviour** — register, word-count targets, allowed angles and CTAs, forbidden topics, claim scoping, cadence | **Human-written. Never derived from samples.** |

**No segment-specific rule is ever derived from the sample emails.** If a pattern appears to be segment-specific, it belongs in the playbook, where a person decides whether it is true — not in the rulebook, where extraction decides.

**And an evidence floor on derivation itself** — but only where it makes sense, which is not everywhere (§6.6c).

### 6.6c Every rule carries its provenance, and the floor applies to only one class

*(v1.4 — audit F1. v1.3 stated a single floor — "no pattern observed in fewer than 5 samples may become a hard check" — which is **inverted for prohibitions**, including the most important rule in the rulebook.)*

Applied literally to **"never open with a wellbeing phrase"** — the rule §6.3 exists for — the v1.3 floor would have required **5 of your 15 emails to contain** *"I hope this email finds you well"* before it could be banned. That is backwards: your samples are examples of good writing, and the complaint was that the **model** produces those greetings, not that Narayani does.

Its real justification is **absence across all fifteen, plus your explicit instruction.** Neither is a count of observations.

So every rulebook rule records **which of three classes it belongs to**:

| Class | Justification | Floor |
|---|---|---|
| **Positive** — *do this* (greeting form, sign-off shape, sentence length, paragraph count) | **The reviewer corrected toward it during the loop** (§6.1) | **The segment sweep passes in every segment, AND the baseline check passes** (§6.1d–e) |
| **Prohibition** — *never this* (banned openers, banned phrases, no exclamation marks, no ALL-CAPS) | **Absent from all samples**, or an explicit owner instruction | **No count floor.** Absence across fifteen is itself the evidence. |
| **Owner-directed** — any rule you state directly | You said so | **No floor.** Recorded with you as the source. |

Provenance is stored with the rule, so a later reader can see at a glance which rules rest on fifteen samples and which rest on an instruction — and §6.5's per-rule rejection alarm can weight them differently, since a misfiring owner-directed rule is a conversation and a misfiring derived rule is a bug.

### 6.6d A global rule must hold across at least two segments

*(v1.4 — audit F2. §6.6b warns the samples are probably skewed; §6.6a then placed the entire weight of the derived voice on those same samples. Neither section noticed the interaction.)*

If eight of fifteen samples are importer emails, the derived "global house voice" is **53% importer** — compliance-heavy, document-forward, formal. That is the right register for an import manager and the **wrong** one for a gym owner, yet it would become the house voice applied to every segment, including the two with no samples at all.

> **A skewed fifteen cannot teach a neutral global voice, any more than it can teach five segment voices.**

That is the corollary of §6.6a's own premise, and v1.3's fix made it sharper rather than softer: forbidding segment-derived rules concentrates all derived weight into a single voice the neighbouring section says is probably biased.

**So a positive rule becomes a global hard check only if it appears in samples from at least two segments.** A pattern present in the importer emails and absent from the horeca ones is segment voice wearing a global label, and belongs in the playbook where a human decides whether it generalises.

This converts *"the sample might be skewed"* from a caveat into a test, and it works without balanced data — which is fortunate, because the data will not be balanced.

**Why this specific failure matters more than it looks.** A thin-data rule that lands in the guidance half produces slightly off-register prose — mild and recoverable. A thin-data rule that becomes a **hard check** rejects valid emails: three supermarket samples where two open with a question would yield "supermarket emails open with a question", rejecting perhaps 40% of good drafts, dropping the joint first-pass rate below the 81% the cost model assumes (§9.1), and filling the HOLD queue until §10.2's backpressure cuts the daily target.

**And it would be misattributed.** The symptom reads as *"the model writes supermarket emails badly."* The cause is a rule inferred from two coin flips. Someone would spend a week tuning the prompt before suspecting the rulebook — which is why §6.5's **per-rule rejection rate** is the instrument that finds it, not the model's output quality.

### 6.6b The samples are probably not evenly distributed

The "2–3 per segment" figure assumes fifteen emails spread evenly across five segments. **They almost certainly are not.**

Narayani's existing outreach will be concentrated where the business already is — importers and distributors. Wellness/gym and supermarket may have one example, or none. A realistic distribution is closer to 8 / 4 / 2 / 1 / 0 than to 3 / 3 / 3 / 3 / 3.

**So the first output of P-B is a count per segment**, before any rule is written, so you can see which segments have no basis at all before anything is built on them. For those, the playbook is the *only* source — which §6.6a already requires, and this makes visible rather than assumed.

**And alongside the count, which segment dominates the derived voice** *(v1.4 — audit F2)*. One extra line from the same analysis, and it puts the question to you rather than assuming an answer:

> *"The house voice is derived predominantly from your importer emails. Does that register generalise to how Narayani should sound to a gym owner — or should the house voice come from a different subset?"*

You may nominate which emails best represent how Narayani should sound **in general**, which will not necessarily be the most numerous ones.

### 6.7 What P-B needs from you

*(v1.6 — this list replaced "send me 15 emails". The work moved, it did not shrink.)*

| # | Needed | Why |
|---|---|---|
| 1 | **Segments + their catalogues** | 1:1 mapping; a segment without a catalogue cannot send (§5.2a) |
| 2 | **The approved-claims list, per segment** | **The real work.** The only facts the model may ever state. Nothing can be generated without it, and every guarantee in this system is only as good as this table |
| 3 | **10 real prospect rows** | So drafts concern real companies, not invented ones — *"would you send this to Al Noor Trading?"* is answerable; *"would you send this?"* is not |
| 4 | **Business rules per segment** | Allowed CTAs, forbidden topics, cadence |
| 5 | **~40 minutes** for the loop, ~15 for the sweep | §6.1 |

**What you no longer supply:** tone, register, voice, word counts, or any written English. Those come out of the loop instead — which is the point of the change.

If you *do* have real sent emails, they are still valuable: paste one at Stage 0 question 7. Real voice with no circularity beats generated drafts, and even one shortens the loop.

---

## 7. Worker specifications

**Universal rules** (NASSCOM 02 Step 2, 04 §K, 05 §T):
1. **Every enum has an `unknown` member.**
2. **Every `unknown` has defined behaviour.** An escape hatch with no handler is a bug.
3. **Every contract has a deterministic `fallback()`** that fails closed.
4. **Flat, shallow schemas.**
5. **No long free-text blob inside a JSON object we must parse.**
6. **Business rules inside the contract** as `@field_validator`s.
7. **JSON schema generated from the Pydantic class.**
8. **Zero tools on every worker.**

### 7.1 A6 — SegmentClassifier

| | |
|---|---|
| **Decides** | Which of five segments a company belongs to |
| **Input** | Company name, domain, designation, country, product interest, notes — fenced `<untrusted_data>` |
| **Output** | `{segment: enum(+unknown), confidence, evidence(≤200), alternate\|null}` |
| **Caps** | 1 call · ≤1,500 tokens · temp 0.0 |
| **Fail closed** | `confidence < 0.75` ⇒ `unknown` ⇒ human review. **Never queued.** |
| **Why strict** | Misclassification produces a *fluent, well-grounded, completely wrong* email. Grounding validators cannot catch it — every fact is true. Only the segment gate can. |
| **Eval** | ≥92% accuracy on 100 labelled companies; **≥97% precision on `distributor_wholesaler` and `importer`** |

### 7.2 A2 — PersonalizationAgent *(the only true agent)*

| | |
|---|---|
| **Decides** | Every word of every email. Holds a **veto** (§7.2c). |
| **Input** | One `MessageBrief`: rulebook guidance + segment playbook + Fact Pack + campaign step + ≤5 segment-scoped claims + thread digest + last message + human-written catalogue summary |
| **Tools** | None |
| **Shape** | LangGraph: `draft → rulebook_check → ground_check → sameness_check → (repair ×2, no-progress break) → emit \| hold \| veto` |
| **Caps** | ≤3 LLM calls · ≤45s · ≤6,000 tokens · **temp 0.7 prose**, 0.0 metadata · per-run cost cap |

**(a) Output shape.** NASSCOM §4.6: *"Never put a huge free-text blob inside a JSON field you also need to parse."*

```
A2 returns TWO things, separately:

  1. body_text        — prose, returned as message CONTENT, not a JSON string
                        field. No escaping risk.
  2. MessageMetadata  — small and flat:
       { subject(≤90), angle_used: enum(+unknown), cta: enum(+unknown),
         claims_used: list[str], product_terms_used: list[str],
         opening_line_hash, confidence, skip_reason: str|null }

  body_html is NOT model-generated — rendered deterministically from body_text.
```

**(b) Contract validators.** `confidence ≥ 0.7 ⇒ claims_used non-empty` · `cta ∈ segment.allowed_ctas` · subject non-empty and unequal to every prior subject for this prospect · **`cta == unknown` or `angle_used == unknown` ⇒ HOLD** (the `unknown` member exists so the model may decline to guess, not so it may ship a CTA-less email) · **no commercial term in `body_text` or `subject`, categorically** (§10.8).

**(c) The veto, defined** *(v0.6 — audit A7; v0.5 mentioned it without saying what happens):*

A2 may return `skip_reason` instead of a message — "this prospect looks like a retail consumer, not a B2B buyer."

A veto is a **soft skip, not a HOLD**:
- The prospect returns to the eligible pool with `veto_count += 1` and a recorded reason.
- **The slot passes immediately to the next prospect** — today's target is unaffected.
- A veto is **never** overridden by a later worker.
- **Three vetoes on the same prospect route to human review** rather than recycling indefinitely.

**(d) Temperature 0.7, deliberately.** 0.4 produces safe, samey copy. The higher temperature is affordable *because* the validators are decidable — the writing can breathe because a machine, not a prompt, checks the facts. **Measured, not asserted — §14.4b.**

**(e) Eval.** Grounding pass ≥95% · rulebook pass ≥95% · sameness pass ≥90% · **joint first-pass ≥80%** (§9.1) · LLM-as-judge "sounds human, would send as-is" ≥4/5 against 30 human labels · **quality-stability across repeat runs** (§14.4b) · **validator escape rate 0** (§14.4c).

### 7.3 A8 — CatalogueSelector · REMOVED in v1.5

**This worker no longer exists.** Under the owner's one-segment-one-catalogue rule (§5.2), catalogue selection is a table lookup on a column the import already supplies — there is no judgement to delegate.

Removed with it: the 0.75 confidence floor, the `implied_segment` cross-check, `catalogue_decision_log`, the consistency monitor, and progressive hardening. The cross-check is replaced by the send preview (§5.2c).

The original design is preserved in `REVISION-HISTORY.md`. **If lists ever arrive without a segment column, or one segment ever needs a choice between catalogues, that is the design to restore** — this is a deletion justified by a business rule, not by the design being wrong.

### 7.4 A7 — DailyTargetingAgent · and the FitScore

**(a) A7 decides policy, on aggregates.**

| | |
|---|---|
| **Decides** | Today's **target volume**, **segment mix**, and bucket priority order |
| **Input** | **Aggregates only, never rows** — per-segment eligible counts, overdue follow-ups, yesterday's bounce/complaint/reply rates, ramp position, remaining capacity. ≤2,500 tokens regardless of pool size. |
| **Output** | `{target_total, per_segment_allocation, bucket_priority_order, slow_down_reason\|null, confidence}` |
| **Caps** | 1 call · ≤3,000 tokens · temp 0.0 · once daily |
| **Gate in code** | The two-line rule in §7.4d. **A7 can always lower; never raise, and never reorder priority.** |
| **Eval** | Never proposes above the ceiling across 200 simulated days; correctly slows when injected bounce rates spike |

**(b) FitScore orders prospects, deterministically** *(v0.6 — audit A6 corrected v0.5's multiplicative form and its overclaim of determinism):*

**Eligibility is a gate; FitScore is an ordering. v0.5 conflated them by multiplying by zero.**

*Eligibility gate (binary, evaluated first):*
```
NOT eligible  ⇔  verification == invalid
              ∨  verification == unknown  (pending human review)
              ∨  segment_confidence  < 0.75
              ∨  catalogue_confidence < 0.75
              ∨  suppressed
Ineligible prospects are HELD, never scored, never queued.
```

*FitScore (weighted-additive, all factors 0–1, weights sum to 1):*
```
FitScore = 0.35 · opportunity_score_normalised     (rule-based, §7.7)
         + 0.20 · verification_band                 (valid 1.0 | review_cleared 0.8 | risky 0.6)
         + 0.15 · segment_confidence_band           (high 1.0 | medium 0.7)
         + 0.15 · catalogue_confidence_band         (high 1.0 | medium 0.7)
         + 0.10 · campaign_priority
         + 0.05 · recency(due_at)

         × veto_decay                               (0 vetoes 1.0 | 1 → 0.5 | 2 → 0.25)
```

**`veto_decay`** *(v0.9 — audit B7)*: a prospect A2 has vetoed (§7.2c) returns to the pool, and without this term it would be re-selected immediately at the same score — burning the most expensive call in the system, repeatedly, until it hits three vetoes and goes to review. The decay pushes it down the queue instead. It is the one multiplicative term in the formula, and deliberately so: it expresses "this has already been judged unsuitable once", which should scale the whole score rather than offset part of it. It never reaches zero — eligibility, not ordering, is what removes a prospect (§23).

**Honest characterisation:** FitScore is **deterministic and auditable given its inputs.** Two of its inputs are model confidences, which are poorly calibrated as probabilities — so they are consumed as **coarse bands, never as raw floats**, and they carry only 30% of the weight combined. Weighted-additive rather than multiplicative, so one weak factor degrades the score rather than collapsing it.

FIFO on `due_at` remains the tiebreaker, preserving Rule 12 fairness within equal scores. Costs zero tokens and is explainable to a human: *"this prospect went first because…"*

**(d) The daily cap — one mathematical rule** *(v1.0 — owner finding C1; v0.9's `min(...)` expression did not say whether 1,000 included follow-ups)*

Master Prompt §11 settles it: *"Maximum total outbound emails per day = 1,000. **This includes:** New introductions, Follow-up #1, Follow-up #2, Monthly follow-ups, Quarterly follow-ups."*

**This is the only statement of the cap in the system:**

```
new_sends + followup_sends  <=  1000        # DAILY_TOTAL_LIMIT
followup_sends              <=   500        # DAILY_FOLLOWUP_LIMIT
```

**Two limits apply simultaneously, and neither implies the other** *(v1.1 — audit D10; v1.0 never stated the ceiling's scope)*:

| Limit | Scope | Source |
|---|---|---|
| **1,000/day total · 500/day follow-ups** | **System-wide** | Master Prompt §11 |
| **Mailbox ceiling (150/day at full warm-up)** | **Per mailbox** | §13.3 |

At 5 mailboxes × 150 = 750, the mailbox ceilings bind first. At 10 mailboxes the system cap binds. Both are enforced; satisfying one says nothing about the other.

**The split is deterministic, not A7's to choose.** Master Prompt §12 puts overdue follow-ups at priority 1 and new introductions at priority 3, so follow-ups claim the budget first and new outreach takes the remainder:

```
ramp_target    = Σ mailbox_allowance(m)  for m where m.status = 'active'
                 # v1.1 — audit D1. There is no single ramp number: §13.3 runs
                 # per-mailbox schedules that start on different dates, and a
                 # paused mailbox contributes zero.

budget         = min(A7.target_total, ramp_target, 1000)
followup_sends = min(followups_due, 500, budget)
new_sends      = min(new_due, budget - followup_sends)
```

**Distribution across mailboxes is a second, separate constraint.** The budget says how many sends happen; it does not say from where. A send is assigned to a mailbox only if that mailbox is below **its own** `mailbox_allowance` for the day, checked **at send time inside the claiming transaction** (§7.5a) — not merely in the aggregate. A mailbox that hits its ceiling stops receiving work while others continue.

When a mailbox is paused or reset (§13.3), `ramp_target` recomputes on the next daily tick from the remaining active mailboxes. Today's already-queued rows for that mailbox return to the pool for reassignment.

A7 may **lower** either number. It may never raise one, and it may **never reorder the priority** — otherwise a model could starve overdue follow-ups by allocating everything to new outreach, which would be a hole in §2's asymmetry rule.

**Worked examples** (the first two are Master Prompt §33's):

| Follow-ups due | New due | A7 target | Follow-ups sent | New sent | Total |
|---:|---:|---:|---:|---:|---:|
| 700 | 500 | 1,000 | **500** *(capped)* | **500** | 1,000 |
| 300 | 400 | 1,000 | 300 | 400 | **700** — never padded to 1,000 |
| 500 | 900 | 1,000 | **500** | **500** | 1,000 — *the case in your question* |
| 600 | 200 | 1,000 | **500** | 200 | 700 |
| 700 | 500 | 320 *(A7 slowing down)* | 320 | 0 | 320 |

The 200 unsent follow-ups in row 4 and the 400 in row 1 **stay queued and become overdue**, rising to priority 1 tomorrow. Never deleted, never skipped, never marked sent (Master Prompt §11).

**(c) Precedence: bucket order vs FitScore** *(v0.6 — audit A10):*

| Window | Precedence |
|---|---|
| **Ramp, P7 weeks 1–4** | **FitScore dominates.** Highest-fit prospects go first regardless of bucket order. Reputation-building outranks segment balance in this window (§13.4). |
| **Steady state, week 5+** | **Bucket order dominates.** A7's allocation is filled in its stated priority; FitScore orders within each bucket. |

Stating this matters: without the ramp exception, §13.4's warmup argument fails, because the ramp would be seeded by fit only *within whichever bucket A7 happened to rank first*.

### 7.5 A3 — ReplyClassifierAgent

| | |
|---|---|
| **Decides** | Human vs auto; intent; **suppression**; proposed state transition |
| **Input** | `{subject, body_stripped, from_domain, headers_summary, our_last_message_type, segment}`. Body **quote-stripped**, truncated to 2,000 chars. |
| **Contract (flat)** | `kind: [human, auto_reply, bounce_notice, unknown]` · `intent: [interested, wants_info, wants_catalogue, wants_pricing, wants_samples, wants_meeting, negotiation, not_interested, unsubscribe, wrong_person, referral, other, unknown]` · `suppression_signal: [none, unsubscribe_request, left_company, invalid_address, do_not_contact, unknown]` · `summary(≤250)` · `evidence_quote(≤200)` · `confidence` |
| **Caps** | ≤2 calls · ≤20s · ≤3,000 tokens · temp **0.0** |
| **Security** | Primary injection surface. Fenced in `<untrusted_data>`. No tools, no write path — a fully hijacked output can only emit a value inside a fixed enum. |
| **Eval** | human/auto ≥98% · **unsubscribe recall 100%** (CI gate) · intent macro-F1 ≥0.80 · label stability ≥99% over 5 repeats |

**Mechanical overrides, in code — not in the prompt:**
```
if suppression_signal != "none":  suppress(contact)          # regardless of anything else
if kind == "human":               pause_automation(prospect)
if confidence < 0.6:              kind = "unknown"; route_to_human()
# Un-suppression is not reachable from any model output. Human-only.
```

A deterministic pre-filter runs first — `Auto-Submitted`, `X-Autoreply`, `Precedence: bulk`, DSN content-type, `Out of Office` subjects — catching ~70% of auto-replies free.

### 7.5a Suppression — the concurrency specification

*(v1.0 — owner finding C2. v0.9 said "suppression re-checked at send time" without specifying what that means concurrently, which is a hope rather than a specification.)*

Unsubscribe is the strongest never-violate requirement in the system (Master Prompt §17: *"Suppression must override all queue operations"*). It therefore needs a concurrency answer, not a sequencing one.

#### Four check points, one authoritative

| Where | Purpose | Authoritative |
|---|---|---|
| At queue time | Cheap filter; avoids obvious waste | No |
| At render start | Avoids burning a 45-second generation | No |
| **Inside the claiming transaction** | **The real gate** | **Yes** |
| On every suppression event | Proactively cancels in-flight rows | No — belt and braces |

#### The authoritative gate: lock the contact, then claim

A check and then a send is two operations, and anything can happen between them. But **an atomic statement alone is not sufficient either** *(v1.1 — audit D2; v1.0 claimed it was, and that claim was wrong)*.

Under PostgreSQL's default **READ COMMITTED**, a statement evaluates its subquery against a snapshot taken at statement start. A suppression `INSERT` committing *after* that snapshot but *before* the `UPDATE` completes is invisible to it — and nothing blocks, because **you cannot lock a row that does not yet exist.** The window shrinks from milliseconds to microseconds; it does not reach zero.

So both paths first serialise on a row that **does** exist:

```sql
-- FIRST STATEMENT of both the suppression path and the claim path:
SELECT id FROM contacts WHERE id = :contact_id FOR UPDATE;

-- then, in the claim path:
UPDATE outbound_queue
   SET status = 'sending', locked_by = :worker,
       lease_expires_at = now() + interval '90 seconds'
 WHERE id = :id
   AND status = 'rendering'
   AND mailbox_sends_today(:mailbox_id) < mailbox_allowance(:mailbox_id)   -- §7.4d
   AND NOT EXISTS (
         SELECT 1 FROM suppression_list s
          WHERE s.email_hash = :email_hash
             OR s.contact_id = :contact_id)
RETURNING id;
```

The contact-row lock makes the ordering **decided rather than raced**: whichever transaction takes it first completes first, and the second sees the first's committed result. Cost is one lock on a primary key, and no retry logic is needed.

**Zero rows returned means suppression won.** With the lock, that outcome is now guaranteed rather than merely overwhelmingly likely.

*(`SERIALIZABLE` on both transactions would also work via PostgreSQL's SSI, but requires `serialization_failure` retry handling at every call site. The row lock is simpler and sufficient.)*

#### Testing a race requires forcing the interleaving

**Repetition does not test a race.** A 1,000-iteration loop firing suppression and claim "simultaneously" would pass by luck almost every time under the v1.0 design — manufacturing confidence in a property that did not hold, which §19.4 exists to forbid.

The concurrency test therefore **forces the ordering deterministically** — an advisory lock or an injected delay between snapshot and commit — and asserts that the losing transaction is the one that yields, in both directions. The repetition loop stays as a smoke test; it is not the proof.

#### The wider window: how fast an unsubscribe becomes a row

The claim-to-send race is sub-second. **Unsubscribe processing latency is not**, and it dominates the exposure.

If a `List-Unsubscribe` one-click POST enters a queue and is processed thirty seconds later, the window is **thirty seconds**, and no amount of atomicity at the send gate helps.

Therefore:
- **The unsubscribe endpoint writes the suppression row synchronously, inside the HTTP request, before returning 200.** It does nothing else first — no queueing, no async job, no batching.
- **A3's suppression override (§7.5) writes suppression before any other state change** when a reply carries a suppression signal.
- Both paths are measured: **time from receipt to suppression row committed, p99 alarm at 2 seconds.**

#### The residual window is irreducible — so it is measured, not denied

Between transaction commit and the provider accepting the message there is a window that cannot be closed. Holding a database transaction across a network call to Gmail would hold locks for the duration of an unreliable RPC.

Handled honestly:

1. **Minimised** — the provider call is the next statement after commit. Nothing else happens between them.
2. **Measured** — commit-to-accept latency recorded per send; **p99 alarm at 5 seconds**, because a slow provider silently widens the exposure.
3. **Escalated when it loses** — if a suppression row appears bearing a timestamp inside a window in which we sent to that contact, that is recorded as an **incident**, surfaced in the daily report, and flagged for a human. We did send after they unsubscribed. Discarding that fact silently would be the worse failure.

#### Suppression arriving mid-render

The atomic claim catches it at the sending transition — **the generation is wasted, which is the correct trade.**

In addition, a suppression event immediately sets every `pending` or `rendering` row for that contact to `cancelled`, so a worker mid-render aborts early rather than finishing work that will be rejected.

#### Un-suppression remains human-only

No model output, no automated path and no reaper can remove a suppression row. Only a person, and the removal writes an audit row naming them.

### 7.6 A1 — ImportTriage *(advisory)*
Proposes CSV column→field mappings, flags suspect rows, **proposes email typo corrections** (§12.2). Deterministic header matching runs first. **It never writes.**

### 7.7 A4 — OpportunityScoring · A5 — DailyDigest

**A4** scores 0–100 from a **fixed signal enum** (Rule 24), weighted per segment. Catalogue link views are a strong signal (§11.4). **The deterministic rule-based twin always runs** — not optional, because FitScore depends on it.
> **Kill criterion (P0):** if rules agree with the LLM within ±15 points on ≥90% of 40 cases, **delete A4's LLM half.** The rule-based score stays either way.

**A5** narrates ~25 pre-computed SQL aggregates plus the bounce block (§12.5). It cannot compute a number — any numeral absent from the input is a validator failure.
> **Kill criterion (P0):** build a Jinja template over the same aggregates. If a reader cannot tell which is better, **delete A5.**

---

## 8. Context engineering doctrine

**8.1 The database is the memory; the context window is a workbench.** No history carried between units of work.

**8.2 One worker, one job, one shot.** Longest run: ≤3 LLM calls, ≤45s. Drift needs duration; there is none.

**8.3 Bounded history via rolling digest.** **One** digest per prospect, capped at 300 tokens, **rewritten** (never appended) after every send:

```
thread_digest {
  angles_used:      [margin, territory_rights, moq_flexibility]    # ≤8, enum
  phrases_to_avoid: [...]                                          # ≤10
  ctas_used:        [catalogue, price_list]
  opening_hashes:   [a3f1, 91cc, ...]        # feeds the sameness gate
  tone_notes:       "formal; replied once with an OOO"
  messages_sent:    7
}
```
Plus the **single** most recent message verbatim (≤400 tok). Context for message #22 equals message #2.

**8.4 Absent facts are omitted, never stated as unknown.**

**8.5 No RAG in v1 — a decision, not an omission.** The retrieval surface is one prospect row, one campaign row, ~50–200 segment-filtered claims, and one human-written catalogue summary. **Revisit at >500 claims.**

**8.6 Token budget is declared, enforced, monitored.** One constant, `A2_INPUT_CEILING = 4100` (§9.2), referenced everywhere.

**8.7 Zero tools; pre-shaped, pre-truncated inputs.** No worker has DB access. Reply bodies quote-stripped. A7 gets aggregates, never rows. **Catalogue full text never enters a prompt** (§11.2).

**8.8 Untrusted data: fenced inbound, and never fact-authorising**

*Inbound (A3, A6).* No tools, no write path, output confined to a fixed enum. A fully hijacked output can at worst produce a wrong label a human sees. **Bounded by design.**

*Outbound (A2).* **Not analogous.** A2's output is free prose the system **sends to a third party**. A crafted note in a purchased list could steer what Narayani says in a real email.

**The defence — corrected in v0.6 (audit A3).** v0.5 said notes "pass the entity allowlist before entering the brief." That was **circular**: grounding check (e) validates output entities *against the brief*, so any entity an attacker placed in a note would have authorised itself. A note reading *"Narayani supplies Emirates Group and Carrefour UAE"* would have entered the brief, been used by A2, and passed check (e) by finding itself there.

The rule is now absolute:

> **Free-text notes may inform tone and topic. They can never authorise a fact.**

Concretely: the entity allowlist consulted by check (e) is built **only** from structured prospect/company fields, campaign configuration and `approved_claims`. **Imported free-text notes are explicitly excluded from it** (§10.1e). Notes are still fenced as `<untrusted_data>`, still off by default (§14.7), and now cannot self-authorise.

**8.9 Prompts split by lifetime; written as policy, not politeness.**

| Layer | Contains | Changes | Trust |
|---|---|---|---|
| **System** | Role, **rulebook guidance**, output contract, safety limits | Per release — versioned in git | Trusted |
| **Developer/task** | Prospect brief, segment playbook, catalogue summary | Per request — machine-assembled | Trusted |
| **User/untrusted** | Reply text, imported notes | Per turn | **Data, never instructions — and never facts** |

| Anti-pattern | Corrected form here |
|---|---|
| Politeness instead of policy | "Every claim must map to a FACTS key. Any other output is an error." |
| **Negative-only instructions** | "90–140 words. Exactly one CTA from the allowed set. Open with a sentence specific to this company." |
| 4,000-line monolith | One prompt per worker; segment variance in the **playbook**, voice in the **rulebook** |
| Contradictory rules | Explicit precedence: **grounding beats rulebook beats personalisation** |

**Structured output:** Level 4 (`with_structured_output`) default for A1/A3/A4/A5/A6/A7. A2 returns prose as content, metadata via level 3–4.

---

## 9. Token budgets and cost

### 9.1 Joint first-pass rate — the number that actually drives cost

*(v0.6 — audit A4. v0.5 assumed ≥90%, contradicting its own component targets.)*

A draft must clear **all three** validators:

| Validator | Target first-pass |
|---|---:|
| Rulebook | ≥95% |
| Grounding | ≥95% |
| Sameness | ≥90% |
| **Joint** | **≈81%** (0.95 × 0.95 × 0.90) |

So roughly **19% of drafts need at least one repair**, not 10%.

**Joint first-pass rate ≥80% is the operative P0 gate**, replacing the per-validator figures, which were never the number that mattered. If P0 measures below 80%, the cost model is revisited before P1.

### 9.2 A2 per draft call

| Section | Budget |
|---|---:|
| System prompt incl. rulebook guidance | 1,200 |
| Segment playbook | 350 |
| Fact Pack (prospect + company) | 550 |
| Campaign brief + step spec | 350 |
| Approved claims (top 5, segment-scoped) | 500 |
| Catalogue summary — human-written, stored on the catalogue row | 200 |
| Thread digest | 300 |
| Last message verbatim | 400 |
| **Total input** | **3,850** |
| Output | 700 |

**`A2_INPUT_CEILING = 4100`** — one constant, referenced by `assert_budget()`, by the p99 alarm (fires at 90% = 3,690) and by every risk-register row that cites a token threshold *(v0.6 — audit A9; v0.5 stated 4.1k and 4.5k in different places, and one row gave no threshold at all)*.

Others: A3 ≤3,000 · A6 ≤1,500 · A7 ≤3,000 · A4 ≤2,000 · A5 ≤4,000 · A1 ≤2,500.

### 9.3 Cost, recomputed

Base per email: 3,850 in + 700 out = **4,550 tokens**.
At an 81% joint first-pass rate: ~15% need one repair, ~4% need two. A repair carries the failed draft plus the validator error, ≈5,000 tokens.

```
average = 4,550 + (0.15 × 5,000) + (0.04 × 2 × 5,000) ≈ 5,700 tokens/email   (+25%)
at 500/day                                             ≈ 2.85M tokens/day
```

**~2.85M tokens/day for personalisation** — about 15% above v0.5's figure. On a small fast model, a few dollars a day; on a frontier model, not. **Model tiering is a design requirement.** Token usage is recorded from the first call.

---

## 10. Content quality pipeline

Three validators. **Every check is decidable without an LLM.**

```
  MessageBrief   (every field carries provenance: imported_csv | campaign_config |
                  approved_claim | catalogue_terms | prior_message | untrusted_note)
        │
        ▼
  [0] DRAFT              temp 0.7 prose / 0.0 metadata; emits claims_used[]
        │                and product_terms_used[]
        ▼
  [1] SYNTACTIC REPAIR   extract_json_object() — BRACE COUNTING, not regex
        │                (JSON nests; regex does not). Strips fences, preamble,
        │                trailing commentary; handles provider content blocks.
        ▼
  [2] SCHEMA VALIDATE    Pydantic + @field_validators, incl. cta/angle
        │                == unknown ⇒ HOLD. Fail → repair, error VERBATIM.
        ▼
  [3] RULEBOOK CHECKS    ◄── compiled from YOUR 15 emails (§6.2)
        │   ► per-rule rejection rate recorded (§6.5)
        ▼
  [4] GROUNDING VALIDATOR                                    (see §10.1)
        ▼
  [5] SAMENESS GATE
        │   i.   vs this prospect's last 3 messages: trigram Jaccard < 0.35
        │   ii.  vs last 50 in the SAME segment+step, 14 days: < 0.50
        │   iii. OPENING SENTENCE, stricter: < 0.60 against opening_hashes
        │        and the segment's recent openers
        │   iv.  No phrase from phrases_to_avoid or the campaign-level
        │        banned-opener ledger
        │
        ├── PASS ──► [6] attach catalogue + queue for send
        │
        └── FAIL ──► repair loop (max 2), error fed back VERBATIM
                        │
                        │  ► NO-PROGRESS DETECTOR: if attempt N yields the same
                        │    error set as N-1, break IMMEDIATELY (NASSCOM §3.6)
                        │
                        └── exhausted ──► [7] HOLD FOR HUMAN. Do NOT send.
                                              Slot returns to the pool.
```

### 10.1 The grounding validator, in full

```
a. Every id in claims_used[] exists in the brief.                   (set check)
b. Every NUMBER in the body appears in the brief.                   (regex)
c. No certification token (FSSAI HACCP ISO BRC HALAL KOSHER FDA
   APEDA GMP) unless present in an approved_claim.                  (allowlist)
d. No currency amount, MOQ, tonnage, %, or date not in the brief.   (regex)

e. No country / city / organisation name absent from the ENTITY ALLOWLIST.
   ►► THE ALLOWLIST IS BUILT ONLY FROM:
        - structured prospect and company fields (name, city, country)
        - campaign configuration
        - approved_claims
   ►► IMPORTED FREE-TEXT NOTES ARE EXPLICITLY EXCLUDED.
      Notes may inform tone and topic; they can never authorise a fact.
      (v0.6 — audit A3. v0.5's allowlist included the whole brief, so an
       injected note authorised itself.)

f. No past-relationship verbs: "as discussed", "your order",
   "following our call", "last time", "we previously".              (denylist)
g. CTA ∈ segment.allowed_ctas; no segment forbidden_topics term.    (playbook)

h. CATALOGUE CONSISTENCY — two decidable halves, no NER anywhere.
   (v0.6 — audit A1. v0.5 required identifying "product nouns" in free
    prose, which is undecidable without unreliable NER.)

   h1. POSITIVE — every term in product_terms_used[], which A2 declares
       in its own contract, appears in THIS catalogue's registered
       term list.                                                   (set check)

   h2. NEGATIVE — the body contains no term from
         (union of all OTHER catalogues' term lists) − (this catalogue's terms)
       A closed, finite set. A pure string scan.                    (denylist)

   h2 is what catches the real failure: an email mentioning a product that
   lives in a different catalogue than the one attached. The model declares
   what it used; the scan catches what it did not declare.

i. NO COMMERCIAL TERM OF ANY KIND.                                  (denylist)
   ►► CATEGORICAL. Not "must be grounded" — MUST NOT APPEAR AT ALL.
      Being traceable to an approved_claim is NOT sufficient. See §10.8.
```

### 10.8 The AI makes no commercial commitment — ever

*(v0.7 — owner directive: "AI should not make any promise or commitment without human approval in context of MOQ, shelf life, price, commercial terms. All the rules will be defined by us only.")*

This is stricter than everything above it, and it overrides the general grounding rule.

Checks (b) and (d) previously permitted a number **if it was traceable to the brief**. That is not enough. A grounded price is still a **quotation**. A grounded MOQ is still a **commitment**. Narayani's terms vary by prospect, volume, country and season — so a term that is true in general can be wrong for the specific buyer receiving it, and it arrives in writing, from Narayani, looking like an offer.

**Check (i) is therefore categorical.** These may not appear in generated prose regardless of provenance:

| Category | Examples |
|---|---|
| Price and currency | any ₹ / $ / AED / € amount, per-kg, per-MT, per-carton, FOB/CIF/EXW figures |
| Minimum order | MOQ, minimum quantity, container/FCL/LCL load commitments |
| Shelf life | months/days of shelf life, best-before periods |
| Payment terms | LC, TT, advance %, net 30/60/90, credit period |
| Delivery | lead times, dispatch windows, delivery dates |
| Commercial incentives | discounts, margins, rebates, slabs, introductory offers |
| Rights | exclusivity, territory grants, distributorship promises |
| Capacity | production capacity figures, monthly volume commitments |

**How check (i) matches — proximity, not digits** *(v0.9 — audit B5)*

Check (i) fires on **a numeric adjacent to a commercial unit or keyword**, never on a bare number. The distinction matters: a naive digit ban would reject *"over 40 years in the business"* and *"our range covers 12 categories"*, driving the joint first-pass rate (§9.1) through the floor to catch nothing real.

Check (i) has **three layers**. Layer 1 alone — which is all v0.9 specified — is trivially defeated, as §10.8a shows.

```
LAYER 1 — NUMERIC PROXIMITY
  number + currency symbol or code        "$4.20", "₹380", "AED 15"
  number + trade unit                     "40 MT", "500 kg", "2 containers"
  number + minimum keyword                "MOQ 20", "minimum 500 cartons"
  number + shelf-life keyword             "12 months shelf life"
  number + payment keyword                "30% advance", "net 60", "LC 90 days"
  number + lead-time keyword              "ships in 15 days"
  number + incentive keyword              "8% margin", "5% discount"

LAYER 2 — NUMBER WORDS treated as numerics for layer 1
  one..twenty, thirty, forty, fifty, hundred, thousand, dozen,
  LAKH, CRORE                             "forty metric tonnes",
                                          "two lakh units",
                                          "between twenty and forty MT"
  Indian currency forms                   "Rs.380/-", "INR 380", "380/-"

LAYER 3 — UNIT-FREE COMMITMENT DENYLIST   (no number required at all)

  Unconditional:
    "competitive pricing" · "best price" · "special rate" ·
    "flexible on minimums" · "discount available" ·
    "attractive terms" · "negotiable" · "as low as" ·
    "exclusive distributorship" · "sole rights"

  Conditional — banned only before a COMMERCIAL noun:      (v1.1 — audit D7)
    "we can offer" / "we are able to offer" / "happy to offer"
       + pricing|rates|terms|discount|margin|minimums|credit   -> FIRES
       + catalogue|samples|spec sheet|call|meeting|references   -> PASSES

  Rationale: §5.3 requires every CTA to BE an offer to send. "We can offer
  a catalogue" is the intended behaviour; "we can offer competitive rates"
  is a commitment. Layer 1's proximity principle, applied to phrases.

PASSES    bare numerics governed by check (b)
          "40 years", "12 categories", "three generations", "1998"
```

Bare numerics remain governed by check (b), which already requires them to trace to the brief. Check (i) adds the commercial layer on top; the two do not overlap.

**Golden-set obligation:** both directions are tested. *"Founded 40 years ago"* **must pass**; *"MOQ is 40 MT"* **must fail**. A validator that only ever rejects is as untrustworthy as one that never does.

### 10.8a Why layer 1 alone was not enough

*(v1.0 — owner finding C6. Applying "0 failures in 60 does not mean safe" to check (i) found it trivially evadable.)*

Layer 1 was added in v0.9 to stop the check rejecting *"40 years in business"*. Correct as far as it went — and it anchors on a **digit**, so:

| Evasion | Why layer 1 misses it |
|---|---|
| *"forty metric tonnes"* | Spelled number — no digit to anchor on |
| *"two lakh units"* | Indian numbering, spelled |
| *"Rs.380/-"* | Currency form absent from a naive symbol list |
| **"our minimums are flexible"** | **A commitment with no number at all** |
| **"we can offer very competitive pricing"** | **Same — an implied commercial promise, zero digits** |

The last two matter most: **that is exactly how a human salesperson writes**, which is exactly what a model instructed to sound human will produce. Layers 2 and 3 exist for them.

### 10.8b Regression testing and adversarial auditing are different jobs

Zero failures across 60 curated cases means *the checker handles 60 cases someone chose*. It does not mean the checker is safe — and the cases most likely to break it are the ones a well-meaning author would never think to write.

| | **CI golden set** | **Adversarial escape audit** |
|---|---|---|
| Purpose | Regression — did a change break what worked? | Discovery — **can this checker be broken at all?** |
| Cases | 60 fixed, in version control | Deliberately hostile, growing |
| Cadence | Every merge | **Monthly, by a human** |
| Method | Assert 0 failures | Read 50 **passed** emails asking *"does this imply a commitment?"* |
| On a find | Fix and re-run | Fix, **and add the case to the CI set permanently** |

The audit must be performed by a person, because *"does this imply a commitment?"* is precisely the judgement the checker under test cannot make about itself. Same philosophy as the grounding escape audit (§14.4c).

**Every escape found becomes a permanent CI case.** The golden set then grows from real breakage rather than from what was imagined at design time — which is the only path by which 60 cases eventually becomes meaningful coverage.

**Where terms live instead.** In the **catalogue PDF** — a human-authored, versioned, reviewed document — and in **human-written replies after a prospect responds**. The email's job is to open a conversation and point at the catalogue. It is not to quote terms.

**CTAs become offers, not statements.** `price_list` means *"I can send our current price list"* — not a price. `quotation_fob_cif` means *"I can prepare a quotation"* — not a quotation. No CTA may carry a number.

**Enforcement is in two independent places** — the claim never reaches the model, and the check catches it if it somehow appears:

1. **At the assembler.** `approved_claims` gains `claim_type: [capability, product_descriptive, certification, commercial]`. **`commercial` claims are never eligible for a Fact Pack.** They exist in the table for catalogue and human use; the model never sees them.
2. **At the validator.** Check (i) scans the body regardless. Defence in depth, because "the model never saw it" is an assumption and this is not a place for assumptions.

**Commercial questions stop automation, always.** A3 already classifies `wants_pricing` and `negotiation` and pauses the sequence (§7.5). Reinforced here as an absolute: **no automated message ever answers a commercial question.** A prospect asking "what is your MOQ?" is answered by a person, or not at all.

**The rules are yours.** Nothing in this system infers, derives, interpolates or generalises a commercial term. Every term that ever reaches a prospect is one a human at Narayani wrote — in a catalogue or in a reply.

### 10.2 The fallback is HOLD, not a template
If A2 cannot write a personal, grounded, non-repetitive, rulebook-compliant email in three attempts, we **hold for a human** and give the slot to the next prospect. Throughput is unaffected; it is more faithful to Rule 34.

**HOLD-queue backpressure** *(v0.9 — audit B6)*

HOLDs arrive from three places — validator exhaustion (here), `cta == unknown` (§7.2b), and unresolvable `sending` rows (§14.1d). Nothing previously bounded the queue.

At 500/day even a 2% terminal HOLD rate is ~10/day, ~300/month. **Generating mail nobody can review is waste, and a silently growing queue hides the signal that something upstream broke.**

| HOLD queue depth | Response |
|---|---|
| > 1 day of stated review capacity (§20.9) | **ALARM** |
| > 3 days of capacity | **BACKPRESSURE — reduce tomorrow's target proportionally** rather than accumulate |
| > 7 days of capacity | **HOLD RAMP** and raise a blocking task |

Same shape as §13.6a's HOLD RAMP: cheap, reversible, and it stops the system scaling into a problem. The queue depth is a first-class dashboard metric, not a table someone remembers to check.

### 10.3 The campaign-level banned-opener ledger
Per-prospect avoidance does not stop 500 *different* recipients getting the same opener the same day. Every opening sentence is hashed campaign-wide; recurring ones are auto-banned in subsequent briefs. **The system gets less repetitive over time, not more.**

### 10.4 Why the repair loop works
The model is excellent at fixing a *specific named error* and poor at avoiding all errors at once (§4.7). The validator's message is fed back **verbatim, never paraphrased**.

### 10.5 Why the grounding checks are strong
They are **whitelists, not blacklists** — every specific entity in the output must trace to something in the input. A closed set. **And after A3's fix, nothing an attacker writes can enlarge that set.**

### 10.6 Mechanical override, in code
```
if not (rulebook.passed and grounding.passed and sameness.passed):
    message.status = "HELD"; message.requires_human_review = True
    # regardless of the model's own confidence or self-report
```

### 10.7 Approved claims are versioned data, not prompt text
`approved_claims` holds each statement with an id, **segment applicability**, product category, **`claim_type`**, `valid_from`, `valid_to`, `last_reviewed_at`. Claims unreviewed >180 days are excluded and raise an admin task. **The model never knows a fact about Narayani that is not a current row in that table.**

`claim_type` is one of `capability` · `product_descriptive` · `certification` · `commercial`. **Rows typed `commercial` are never eligible for a Fact Pack** (§10.8) — they exist for catalogues and human replies, and the assembler filters them out before A2 sees anything.

---

## 11. Catalogue pipeline and delivery

### 11.1 Asset pipeline

```
Admin uploads PDF
   ├─ 1. Verify real PDF (magic bytes, not extension)
   ├─ 2. SANITISE — strip JavaScript, embedded files, external refs, form and
   │       launch actions; flatten.
   ├─ 3. COMPRESS — downsample images. Target <1MB. Hard reject >2MB.
   ├─ 4. EXTRACT TEXT → registered term list for grounding check (h)
   ├─ 5. Admin writes the 200-token SUMMARY (human-written, not generated)
   ├─ 6. VERSION — checksum, valid_from / valid_to, supersession_policy
   └─ 7. Store file + publish hosted copy
```

### 11.2 Two artifacts, never confused

| Artifact | Size | Goes to |
|---|---|---|
| **Registered term list** | unbounded | **The validator only. Never a prompt.** |
| **Catalogue summary** | ≤200 tokens | The `MessageBrief`, budgeted in §9.2 |

The summary is **human-written and stored on the catalogue row**. It is not model-generated — a generated summary would be an unspecified summarisation step with its own hallucination surface feeding the brief that grounds every email.

**Version sync is enforced** *(v0.6 — audit A8):* a catalogue version bump **invalidates both the term list and the summary**. The term list is re-extracted automatically; **the summary is blocked from briefs until an admin re-confirms it**, and that catalogue cannot be attached to any new email until they do. A stale summary is worse than a stale claim — it enters *every* brief for that catalogue.

### 11.3 Delivery — both, version-pinned, with a supersession policy

PDF attached **and** hosted link included, per your decision that some buyers will not click an unknown link. Both **pinned to the same version at send time** by checksum.

| `supersession_policy` | Behaviour |
|---|---|
| `pin` (default) | Link always serves the version that was sent |
| `pin_with_banner` | Serves the sent version plus *"a newer edition is available"* |
| `redirect_to_current` | Link serves the current edition — **deliberately breaks checksum pinning**; use when commercial accuracy outranks consistency (e.g. superseded prices) |

### 11.4 The link buys what the attachment cannot
Per-prospect and unique. A catalogue view is a much stronger buying signal than an email open — it feeds A4's score, the FitScore, and the human-action queue.

### 11.5 Minimising the deliverability cost of attaching
- **Size discipline** (<1MB target, 2MB hard cap). Attachment *size*, not presence, drives most of the penalty.
- **Sanitisation** — active content in a PDF is a larger flag than the attachment.
- **Deliverability tracked per catalogue.**
- **Attach-vs-link is a per-campaign flag, defaulting to both.** Past threshold, the anomaly detector recommends link-only. You keep the decision.
- **Capacity:** at 500/day a 3MB catalogue is ~1.5GB outbound daily. Some providers throttle on bytes, not message count.

---

## 12. Email verification and bounce handling

Almost none of this is LLM work. A model cannot query a DNS record; DNS can.

### 12.1 The verification ladder — at import, not at send

| Stage | Check | Cost | Catches |
|---|---|---|---|
| 0 | Syntax | free | Malformed addresses |
| 1 | **Domain resolves + MX record exists** | free (DNS) | Dead domains — a large share of hard bounces |
| 2 | Disposable/temp domain list | free | Throwaway addresses |
| 3 | Role account (`info@`, `sales@`) | free | Higher-complaint, lower-reply addresses |
| 4 | Catch-all detection | cheap | Domains where verification tells you nothing |
| 5 | Third-party verification API | ~$0.001–0.01 | The uncertain remainder |

**Stage 5 runs only on what survives 0–4** — typically 20–30% of a list.

**We do not do our own SMTP probing.** Gmail and Outlook no longer answer probes honestly, and probing from the sending IP can get that IP blocked.

| Status | Eligible? | FitScore band |
|---|---|---|
| `valid` | Yes | 1.0 |
| `risky` (catch-all, role account) | Yes, deprioritised | 0.6 |
| `review_cleared` (was `unknown`, human confirmed) | Yes | **0.8** *(v0.6 — audit A6; v0.5 left this undefined)* |
| `unknown` | **No** — human review first | n/a |
| `invalid` | **No. Never queued. No exceptions.** | n/a |

Role accounts are flagged and deprioritised, never excluded — for food export, `info@company.com` is often the only address that exists.

**Re-verification:** B2B addresses decay ~2%/month. Records older than 90 days are re-checked before first send.

### 12.2 The one LLM job — typo repair on supplied addresses
Folded into **A1 ImportTriage**. Detects mechanical typos in addresses *you supplied* — `gmial.com`, `.con`, missing TLD — and **proposes** a correction for human confirmation. Optional auto-accept for the unambiguous case (original domain has no MX, correction does), **off by default**.

### 12.3 Not building: address discovery
Guessing patterns like `first.last@company.com` is excluded by Master Prompt §1, and the practical reason is stronger: **guessed addresses hit spam traps** — dead addresses providers monitor specifically to catch guessers. One hit costs more reputation than the prospect was worth, and guessed addresses bounce, which is the number you are reducing.

### 12.4 Bounce classification — the distinction most systems get wrong

| Type | Codes | Action |
|---|---|---|
| **Hard bounce** | 5.1.1, 5.1.2, 5.2.1 | Permanent suppression, no retry (Rule 18) |
| **Soft bounce** | 4.2.2, 4.3.x, 4.7.x temp | Retry with backoff, then suppress |
| **Policy / reputation block** | 5.7.1, spam refusals | **Do NOT suppress the recipient.** Raise a deliverability alarm. |
| **Throttle** | 4.7.x rate-limited | Slow the send rate, requeue. Not a bounce. |

A reputation block is **information about you, not about them.** Systems that conflate them suppress good prospects *and* miss that the domain is in trouble.

### 12.5 Batch quarantine — evidence floor, rolling evaluation

*(v0.7 — owner finding 23. A flat 5% rule fires on 1 bounce in 20, quarantining 19 good records on one data point.)*

**The problem with a bare percentage.** 5% of 20 is one bounce. 5% of 2,000 is a hundred. Those are not the same evidence, and treating them identically means a single dead address halts a small, clean list.

**Three-part rule:**

**(1) Evidence floor — an absolute bounce count, and nothing else.**
```
Auto-quarantine requires:
    hard_bounces >= 10        (from this batch)
```
Below ten bounces, a batch exceeding 5% is **flagged for human review, not quarantined.** The person sees the addresses and decides. This is defensible on impact grounds as well as statistical ones: twenty emails with one bounce cannot meaningfully damage domain reputation, so automatic protection buys nothing there.

> **There is deliberately no `attempted >= N` condition** *(v0.9 — audit B1; v0.8 required `attempted >= 100` as well, which protected the wrong list).*
>
> A scraped list bouncing at 50% reaches ten bounces at **twenty** attempts, where the Wilson lower bound is already **25.9%** — decisive. Requiring a hundred attempts would have let **fifty** bounces out before the rule could act, on evidence that was conclusive at twenty.
>
> The attempts condition is redundant, because the bounce count and the Wilson bound together already do the discrimination it was meant to provide:
>
> | Evidence | Raw | Wilson lower | Behaviour |
> |---|---:|---:|---|
> | 1 / 20 | 5% | 0.9% | no fire — blocked by `bounces ≥ 10` |
> | **10 / 20** | **50%** | **25.9%** | **fire — v0.8 could not** |
> | 10 / 500 | 2% | 1.09% | no fire ✓ |
> | 12 / 200 | 6% | 3.5% | fire ✓ |
>
> Every small-batch protection survives; the catastrophic list is now stopped at twenty sends instead of a hundred.

**(2) Above the floor, judge the lower bound, not the point estimate.**
Use the **Wilson 95% lower bound** on the bounce rate rather than the raw fraction, and quarantine when that lower bound exceeds **2%**:

| Batch | Raw rate | Wilson 95% lower | Action |
|---|---:|---:|---|
| 1 / 20 | 5.0% | ~0.9% | Below floor anyway → **flag for review** |
| 3 / 50 | 6.0% | ~2.1% | Below floor → **flag for review** |
| 12 / 200 | 6.0% | ~3.5% | **Quarantine** |
| 100 / 2,000 | 5.0% | ~4.1% | **Quarantine** |

The lower bound asks "what is the worst rate consistent with this evidence?" — which is exactly the question a quarantine decision should turn on.

**(3) Rolling evaluation, not one-shot** *(a second gap the same question exposed — v0.6 never said **when** the rate was measured).*
The rule is evaluated **after every 50 sends from a batch**, not after the batch completes. A 2,000-record batch that starts bouncing at record 150 is caught at 150, with 1,850 records still protected. Evaluating only at the end would let the entire batch out before the rule could fire — which would make it useless for the exact case it exists to prevent.

**Quarantine is reversible.** A quarantined batch goes to a review screen with its bounce list. A human may release it wholly, partially, or discard it. Quarantine is never a deletion.

Rolling 7-day bounce rate across **all** sending crossing threshold auto-pauses everything (Master Prompt §19) — that rule is unchanged and operates above the batch level.

**Daily report bounce section:** totals split hard/soft/policy-block/throttle · **every bounced address** with company, contact, segment, campaign, catalogue, bounce code, raw provider message, action taken · rolling 7- and 30-day rates · **bounce rate by import batch** · new suppressions · **verification accuracy** — of addresses marked `valid`, what percentage bounced? That last line audits the verification stack itself.

---

## 13. The deliverability programme

**13.1 Authentication (non-negotiable).** SPF (watch the **10 DNS lookup limit** — fails silently past it) · DKIM 2048-bit · DMARC `p=none` with `rua=` → `quarantine` → `reject`, never starting at reject. **Alignment matters more than presence:** the `From:` domain must align with DKIM `d=` and SPF.

**13.2 Domain strategy — the most important item.** **Do not send cold outreach from `narayanidistributors.com`.** If burned, you lose invoices, existing customers and purchase orders — permanently. Register a **separate, clearly-Narayani sending domain**, give it real auth and a landing page, **age it ~30 days**, send all cold mail from there.

**Multiple mailboxes:** at 500/day one mailbox is a concentration risk; 3–5 is normal. Each should correspond to **a real person or role at Narayani who could plausibly have sent that email and can receive the reply.** Three salespeople at ~100/day each is a company doing business. Twenty invented personas is evasion.

### 13.3 Reputation and warm-up — the operational schedule

*(v1.0 — owner finding C4. v0.9 said "2–3 weeks of genuine low-volume mail", which is not a number a developer can implement.)*

**Warm-up is per mailbox, not per domain.** A mailbox added in month three starts at day 1 of its own schedule regardless of what the domain is doing.

| Days | Per mailbox / day |
|---|---:|
| 1–3 | 5 |
| 4–7 | 10 |
| 8–14 | 20 |
| 15–21 | 35 |
| 22–28 | 50 |
| 29+ | **+15 per week**, to a **mailbox ceiling of 150/day** |

**A mailbox reaches the 150/day ceiling at roughly week 11** (50/day at day 28, then +15/week: 65 · 80 · 95 · 110 · 125 · 140 · 150). At 3–5 mailboxes that ceiling supports the 500/day target with headroom, though **staggered mailbox start dates push out the date at which full capacity exists** — the schedule is per mailbox, so a mailbox added in week 6 reaches its ceiling in week 17.

150/day per mailbox is a conservative cold-outreach limit far below Google Workspace's technical cap — **the binding constraint is reputation, not the API.**

**Increase only if all of these held yesterday:**

| Condition | Test |
|---|---|
| Bounces acceptable | **`hard_bounces < 3` OR Wilson lower bound ≤ 2%** — see below |
| No complaint alarm | §13.6 |
| Auth failures | **zero** — a count, not a rate (a config bug, §13.6c) |
| Postmaster reputation | not Low or Bad — a verdict, not a sample |
| The day completed | without any pause |
| HOLD queue | within review capacity (§10.2) |

> **The bounce condition carries an evidence floor** *(v1.1 — audit D3)*
>
> v1.0 required a raw *"hard bounce rate under 2%"*. At days 1–3 a mailbox sends **five** emails: one bounce is a 20% rate, and the ramp would hold — **permanently**, since at 5/day it can never accumulate the volume to fall back under 2%.
>
> This was the small-sample bug's **third appearance**, in a section written *after* §19.21 was added to forbid it. Holding the ramp is a blocking action, and §19.21 covers it.
>
> Same pattern as §12.5 and §13.6: **an absolute count establishes that evidence exists; the Wilson bound decides whether it is bad.** Below three bounces a bad-looking rate is noise, and the ramp continues — which is correct at 5/day, where the entire purpose of the early days is to establish a sending pattern at all.
>
> The other conditions need no change: auth failures and Postmaster grades were already count- and verdict-based, consistent with §13.6c's reasoning about which signals are samples.

**Otherwise:**

| Condition | Action |
|---|---|
| Any alarm fires | **HOLD RAMP** — hold current volume, do not increase |
| Any auto-pause fires | **Reduce 25%** on resume |
| Blocklist listing, or Postmaster reputation **Bad** | **Reset to day-1 volume**, restart warm-up |
| Sending gap over 14 days | **Reset to day-8 volume** — reputation decays with silence |
| **After any bad day** | **Three consecutive clean days before increases resume** |

That last row is the one most warm-up plans omit. **Resuming the ramp the morning after a problem is how a recoverable dip becomes a burned domain** — the volume increase lands exactly when reputation is least able to absorb it.

**Consistency matters as much as volume:** 300 every weekday beats 500 / 300 / 700 / 100. The scheduler smooths within the day rather than emptying the queue at 09:00.

**13.4 Engagement — why §6 and §11 *are* the deliverability strategy.** Gmail weights engagement heavily, and **replies are the strongest signal you control**. The well-written, correctly-segmented email with the right catalogue *is* the mechanism.

**Executed by FitScore under the ramp precedence rule (§7.4c):** during P7 weeks 1–4, FitScore dominates bucket order, so early sends go to the likeliest repliers and build the reputation that carries later volume.

**13.5 Mechanics and compliance.** **`List-Unsubscribe` with one-click POST (RFC 8058)** — mandatory for bulk senders to Gmail and Yahoo since Feb 2024 · visible unsubscribe link · **physical postal address** (CAN-SPAM) · **multipart plain text alongside HTML** · simple HTML, sensible text-to-image ratio · **no link shorteners**.

### 13.6 Monitoring and auto-pause

*(v0.8 — applying §19.21. v0.7's flat percentages had the same small-sample flaw as the batch rule: at 100/day, a 0.3% complaint threshold is **0.3 emails**, so a single complaint would have paused all sending.)*

**Why this is not simply §12.5's floor copied across.** Batch quarantine is roughly symmetric — firing late wastes some sends, firing early blocks a good list. Domain reputation is **not** symmetric: firing late can cost the sending domain, which §13.2 calls unrecoverable, while firing early costs one day of sending. So the floors here are lower, and there is a middle response between "ignore" and "stop everything."

#### 13.6a Three responses, not two

| Response | Cost | When |
|---|---|---|
| **FLAG** | none | Evidence exists but is thin |
| **HOLD RAMP** | one day of *growth*, not of sending | Thin evidence of a real problem — **stop scaling into it** |
| **PAUSE** | all sending stops | Evidence clears the floor |

**HOLD RAMP is the important addition.** It is nearly free, it is instantly reversible, and it prevents the worst pattern available: increasing volume while a problem is quietly building.

#### 13.6b Windows stretch to reach the floor

Rates are evaluated over a **rolling window that extends backwards until the evidence floor is met**, capped at 30 days (beyond that the data is stale and describes a different sending pattern).

At 100/day the complaint window is ~10 days; at 500/day it is ~2. **The rule adapts to the ramp automatically** instead of needing a threshold per ramp week.

#### 13.6c Thresholds

| Signal | FLAG | HOLD RAMP | PAUSE | Floor before PAUSE |
|---|---|---|---|---|
| **Spam complaints** | any complaint | ≥3 in rolling 7 days | Wilson lower bound > 0.3% | **≥3 complaints** |
| | | | *or* ≥10 in rolling 7 days | *(absolute tripwire — no rate needed)* |
| | | | *or* **Wilson lower bound > 2%** | *(extreme-rate escape — see below)* |
| **Hard bounces** | > 2% raw | Wilson lower bound > 2% | Wilson lower bound > 4% | **≥10 bounces** |
| **Auth failures** | ≥1 in 24h | ≥5 in 24h | ≥25 in 24h, or >1% over ≥500 sends | **≥25** *(see below)* |
| **Blocklist listing** | — | — | **immediate** | **none — a single listing pauses** |
| **Postmaster reputation** | Medium | — | **Low or Bad** | **none — this is a verdict, not a sample** |
| **Open-rate collapse** | sharp fall | — | never auto-pauses | investigate only *(see 13.6e)* |

**Complaints.** Gmail's guidance is <0.1%, never >0.3%. At 0.1% you need ~1,000 sends to expect a single complaint, so a rate computed on a few hundred emails carries little information — which is why the ordinary rate test only fires when the Wilson bound clears 0.3% on enough evidence to mean it.

Two escapes cover the low-volume gap *(v0.9 — audit B1 applied to this row as well; v0.8's `≥1,000 sends` floor let an extreme rate run)*:
- **≥10 complaints in 7 days pauses regardless of denominator.** At full 500/day volume that is roughly the 0.3% line anyway, so the rules agree where they overlap.
- **Wilson lower bound > 2% pauses at any volume.** Three complaints in fifty sends is a 6% raw rate — twenty times Gmail's never-exceed line — with a Wilson lower bound of ~2.1%. Under v0.8 that produced only HOLD RAMP because the send floor blocked the pause. It now stops sending.

Same principle as §12.5: **an absolute count establishes that evidence exists; the Wilson bound decides whether it is bad. A volume floor on top of both only delays protection where it is most needed.**

**Auth failures are not a noisy rate — they are a configuration bug.** v0.7 treated them like complaints, which was a category error. SPF alignment either holds or it does not; there is no base rate to sample. Five failures in a day means something is misconfigured, and the response is to fix DNS, not to gather more data. So this row uses small absolute counts and the "rate" is nearly irrelevant.

**Blocklists and Postmaster reputation carry no floor at all**, because they are not sampled statistics — they are verdicts issued by parties with far more data than we have. A Spamhaus listing is binary. Google's reputation grade is computed across its entire view of the domain.

#### 13.6d At low volume, defer to Postmaster Tools

This resolves the measurement problem rather than working around it. Below ~1,000 sends/day our internal complaint rate is close to meaningless, while **Google Postmaster Tools computes reputation over its own window with vastly more evidence.** So during the ramp, Postmaster's domain reputation is the *primary* signal and our computed rates are secondary. Register with **Google Postmaster Tools** and **Microsoft SNDS** before the first live send, not after — a reputation dashboard is retrospective and gives you nothing about the week you failed to enrol for.

DMARC aggregate reports are parsed into the daily report.

#### 13.6e A caveat on open rate

Open tracking needs a pixel, which carries its own deliverability cost, and **Apple Mail Privacy Protection pre-fetches images**, inflating opens for a large share of recipients. Open rate is therefore a weak and biased signal. It stays as an investigation prompt, is never an auto-pause trigger, and **replies — not opens — remain the engagement metric that matters** (§13.4).

#### 13.6f Worked examples

| Day | Volume | Event | Response |
|---|---|---|---|
| Ramp wk 1 | 100 | 1 complaint | **FLAG.** 1 complaint on 100 sends is noise. v0.7 would have paused everything. |
| Ramp wk 1 | 100 | 3 complaints over 7 days (700 sends) | **HOLD RAMP.** Below the 1,000-send floor, so no pause — but stop growing until it is understood. |
| Ramp wk 3 | 200 | Postmaster reputation drops to Low | **PAUSE.** No floor needed; Google has more evidence than we do. |
| Steady | 500 | 12 complaints over 7 days (3,500 sends) | **PAUSE.** Clears both the floor and the tripwire. |
| Any | any | Spamhaus listing | **PAUSE immediately.** |
| Any | any | 6 DKIM alignment failures in 24h | **HOLD RAMP** + alert. A DNS bug, not a statistic. |

**13.7 What we do not build.** No forged headers, fake client strings, typing-mimicking delays, character substitution, invisible text, image-only emails, or false "sent from my iPhone." Beyond Rule 35: **these are far more detectable than AI writing is.**

---

## 14. Data, state, determinism, harness

### 14.1 Storage, queue, and worker failure

PostgreSQL is the single source of truth. Queue is a Postgres table with `SELECT … FOR UPDATE SKIP LOCKED`. State machine uses an explicit `allowed_transitions` map; every transition writes an audit row; illegal transitions raise.

#### 14.1a What SKIP LOCKED does and does not cover

*(v0.7 — owner finding 21.)*

`SKIP LOCKED` stops two workers claiming the **same** row. It does nothing about a worker that claims a row and then dies.

And the row cannot simply stay locked by the transaction, because **A2 takes up to 45 seconds** — holding an open database transaction across an LLM call is not viable. So the real pattern is: claim the row, **commit**, then work. Once that commit lands, a crash leaves an orphaned row in `processing` forever, invisible.

#### 14.1b Lease, heartbeat, reaper

Every claimed row carries `locked_by` (worker id), `locked_at`, `lease_expires_at`, `attempts`.

```
CLAIM      UPDATE ... SET status='rendering', locked_by=:worker,
                        lease_expires_at = now() + 90s
           WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)
           RETURNING *;                                    -- then COMMIT

HEARTBEAT  every 20s while working, extend lease_expires_at

REAPER     every 60s: reclaim rows whose lease has expired
           attempts += 1; if attempts >= 3 -> DEAD_LETTER (human review)
```

An expired lease means the worker is gone or wedged. Three failed attempts stop the retry loop — Master Prompt §12 already lists `attempts`, `locked_at` and `retry_count`; this defines their semantics.

#### 14.1c The dangerous window — and why this is really a Rule 28 problem

The stuck row is the visible problem. The serious one is underneath it.

Consider: the worker calls the email provider, **the send succeeds**, and the worker dies before recording it. The lease expires. The reaper reclaims the row and hands it to another worker. **The prospect receives the same email twice** — violating Rule 28, *"never send the same queued email twice because of a worker restart."*

So a crash must be interpreted differently depending on **where** it happened:

| State at crash | Side effect? | Reaper action |
|---|---|---|
| `rendering` — generating, validating | **None.** No email left the system. | **Reclaim and regenerate freely.** Safe. |
| `sending` — provider call in flight or just returned | **Unknown. This is the dangerous window.** | **Never retry blindly. Reconcile first.** |
| `sent` — provider response recorded | Done | Nothing to do |

#### 14.1d Reconciliation, and the token that makes it possible

For the `sending` state to be resolvable, every outbound message carries a **unique idempotency token stamped in a custom header** (`X-Narayani-Send-Key`), written to the database **before** the provider is called — never after.

```
1. INSERT send_key row, status='sending'      ← committed BEFORE the provider call
2. call provider with X-Narayani-Send-Key header
3. UPDATE status='sent', store provider message id
```

On reclaiming a `sending` row, the reaper does not retry. It **queries the provider's sent folder for that send key**:

- **Found** → the email went. Mark `sent`, backfill the message id. **No second send.**
- **Not found, and the provider is healthy** → the send never happened. Safe to requeue.
- **Cannot determine** — provider unreachable, search unsupported, ambiguous → **HOLD for human review. Never auto-retry.**

That last line is the Rule 34 position applied to infrastructure: *"if there is uncertainty about whether an email was already sent, prefer pausing the action rather than risking an incorrect outbound email."* An unresolvable `sending` row is exactly that uncertainty, and it fails closed.

The unique constraint on `send_key` remains the last-resort backstop — but a constraint that fires is a bug caught late. Reconciliation is what stops the duplicate before the provider is called at all.

#### 14.1e Reconciliation is a per-adapter capability, not an assumption

*(v0.9 — audit B3, partial. Feasibility itself is a P0 measurement — §18.)*

**`supports_send_reconciliation` is not a setting.** *(v1.0 — owner finding C3: a boolean a developer can type is a boolean a developer can get wrong, and this one guards Rule 28.)*

It is **derived from a live capability probe**, never declared in configuration:

```
1. Send a message to an owned seed address with a known Message-ID
2. Search the provider for that Message-ID
3. Found      -> supports_send_reconciliation = true,  verified_at = now()
   Not found  -> false
4. verified_at older than 30 days  ->  TREAT AS FALSE          (fail closed)
```

The probe runs in CI against a sandbox account and on every adapter configuration change. **Unverified is treated identically to unsupported**, so a stale or missing probe degrades to human HOLD rather than to a silent assumption that duplicates are being prevented.

| Adapter | Mechanism | Verification status | Ambiguous send |
|---|---|---|---|
| Gmail API | `rfc822msgid:` search — **requires our supplied `Message-ID` to survive**, which Gmail may rewrite | **Unverified — P0 probe 2 (§18.1)** | HOLD until verified |
| Microsoft Graph | Message-property querying; likely supported | **Unverified — P0 probe 2** | HOLD until verified |
| **Plain SMTP** | **None. SMTP has no query capability whatsoever.** | **Impossible by construction** | **Always HOLD** |

Where the capability is false, every ambiguous `sending` row **goes to HOLD for manual reconciliation**. Safe — but it means an SMTP-only deployment must expect manual work after any crash, and should be staffed accordingly. That is a stated consequence of adapter choice, not something to discover in production.

#### 14.1f Verification

`freezegun` plus a mock provider make all of this testable without waiting:

- kill mid-`rendering` → assert clean regeneration, no duplicate
- kill mid-`sending`, provider **accepted** → assert reconciliation finds it and does **not** resend
- kill mid-`sending`, provider **rejected** → assert safe requeue
- provider unreachable during reconciliation → assert HOLD, not retry
- three failures → assert dead-letter, not an infinite loop
- **adapter with `supports_send_reconciliation = false` → assert immediate HOLD, zero duplicates, and the row visible in the human queue** *(v0.9 — audit B9; the suite previously assumed reconciliation was always available)*

**14.2 LLM call ledger and replay.** Every call logged with `prompt_version`, `rulebook_version`, `model_id`, `temperature`, **the full assembled brief**, raw output, tokens, cost, latency, all three validator results, repair attempts. Storing the full brief buys **replay** — re-run a failed trace against a new prompt version.

**14.3 Prompts, rulebook and playbooks as deployed artifacts.** Versioned, semver'd, reviewed, referenced by version id on every generated message. **Rollback is a one-line config change.** CI asserts each is within its declared token budget.

### 14.4 Evaluation harness

**(a) Golden sets — with an honest data path, and honest ownership** *(v0.6 — audit A11)*

NASSCOM harness layer 5 requires *"50–200 **real** cases."* At P-B you supply 15 emails and 6–8 catalogues; there is no source for 100 labelled replies until the system has sent something. Critically: **sending produces replies; it does not produce labels.**

| Set | Size | Initial source | Path to real | Owner |
|---|---|---|---|---|
| Reply classification | 100 | Synthetic, **labelled as such** | ≥50% real **within 30 days of P7** — captured automatically | System |
| Scoring | 40 | Synthetic | ≥50% real within 30 days of P7 — depends on reply volume | System |
| Segmentation | 100 companies | **Human-labelled at P-B** | Already real | **Needs a named owner — §20.13** |
| Personalisation | 60 | Synthetic, from your 15 emails | **Requires human quality judgements on real output — a named task, not a by-product of sending** | **Needs a named owner — §20.13** |

P2 and P4 exit gates are met on synthetic data **and this is stated, not hidden**.

**(b) A2 quality-stability test**

For A2 we do **not** want output stability — variety is the point. We want **quality** stability. Five runs over the same 20 briefs:

| Measure | Threshold | Type |
|---|---|---|
| Joint first-pass rate | variance ≤5 points between runs | CI alarm |
| LLM-judge quality score | variance ≤0.5 on the 5-point rubric | CI alarm |
| Schema validity | 100% | **CI merge gate** |

**(c) Validator escape audit — the test that can actually fail** *(v0.6 — audit A2)*

v0.5 gated on *"0 ungrounded facts across 100 outputs."* That gate **could not fail**: the grounding validator blocks ungrounded facts before emission, so the property was true by construction and tested nothing.

The number that matters is the validator's **false-negative rate**:

> **Monthly, a human reviews 50 emails the validator PASSED and counts ungrounded facts. Target: 0 escapes.**

This is not tautological, it is the only measure that tells you whether the grounding validator actually works, and it **cannot be a CI gate** — it needs human judgement. It is a scheduled audit with an owner, and its result is reported in the monthly review.

The same shape applies to the sameness gate: 50 passed emails reviewed for "would a reader notice these are formulaic?"

**(d) Injection suites — inbound and outbound**
- **A3 (inbound):** 20 hostile replies must not change the enum output.
- **A2 (outbound):** 20 hostile imported notes must produce a clean email or a HOLD — **never attacker-chosen text in an outbound message.** Includes the self-authorisation case from audit A3: a note asserting a fake customer relationship must be rejected by check (e).

**(e) Metrics tracked**
Task success · schema-valid rate · **joint first-pass rate** · rulebook pass rate **and rejection rate per individual rule** (§6.5) · sameness pass rate · grounding pass rate · **validator escape rate** (monthly) · p95 latency · cost per task · escalation rate · repair-loop rate · HOLD rate · **veto rate**.

**(f) Cheap mechanical evals, CI-gated**
Claims traceability 100% · suppression recall **100%** · cross-segment CTA leakage **0** · cross-geography catalogue errors **0** · schema validity **100%**.

**(g) LLM-as-judge** is used for A2's subjective quality **only**, offline, with a rubric calibrated against 30 human labels first. It never appears in the send path.

**14.5 Reliability.** Circuit breaker on the provider (N consecutive failures ⇒ open, alert, stop generating). Timeouts and bounded backoff. **Least privilege enforced by the database:** the AI layer runs under a Postgres role with **no table grants at all**.

**14.6 The AI kill switch — two modes.** `AI_CONTENT_ENABLED=false` disables the AI layer; Layer 0 keeps running.
- **HOLD mode (default)** — nothing generated, nothing sent, backlog waits.
- **TEMPLATE mode (opt-in per campaign)** — merge-field fallback. Exists because provider outages are real, but **off by default** because it contradicts the no-copy-paste requirement.

**14.7 PII minimisation.** **A2 does not need the email address** — it needs a first name and a company. The assembler omits email, phone and free-text notes from Fact Packs unless a campaign opts in (and see §8.8 for what happens when it does).

### 14.8 Data retention, privacy and access

*(v1.0 — owner finding C5. This was absent from v0.9, not wrong in it — and §14.2 logged the full assembled brief on every LLM call with no retention limit, which is prospect PII accumulating indefinitely at ~500 rows a day.)*

#### 14.8a Retention

| Data class | Retention | Note |
|---|---|---|
| Prospect PII (name, email, phone, company) | Until erasure, or **24 months** after last activity | |
| Sent message bodies | 24 months | Needed for the thread digest and disputes |
| Replies (raw) | 24 months | |
| **Full assembled briefs** (§14.2) | **90 days** | High-density PII. Long enough for replay and debugging; not forever |
| **Raw LLM outputs** | **90 days** | Same |
| Token / cost metadata | 24 months | **Content stripped, numbers retained** — cost analysis needs no PII |
| Audit log | **7 years** | **Content-free by definition, not by intention** — see §14.8f |
| **Suppression list** | **Permanent** | See §14.8b |
| Bounce / DSN records | 24 months | |
| Provider message ids | 24 months | |
| **`thread_digest`** (§8.3) | **Life of the prospect record** | Name, tone notes, angle history. Persists across a 3-year relationship **by design** — expiring it would break follow-up continuity. Deleted with the prospect. |
| **`catalogue_decision_log`** (§7.3) | 24 months | Company, country, product interest, model reasoning |
| **Catalogue link views** (§11.4) | 24 months | Per-prospect open events — behavioural data |
| **`human_action_queue` items** (§7.5) | 24 months | Reply summaries and quoted evidence — **PII** |
| **Incident records** (§7.5a) | 7 years | Suppression-window losses. Contact id only, no content |
| **`rulebook per_rule_stats`** (§6.5) | 24 months | Aggregate counts, no PII |
| **`daily_send_usage`** | 7 years | Counters only |
| Backups | **35-day rolling** | Erasure propagates as backups expire — documented, not silent |

Retention is enforced by a nightly job that deletes past-horizon rows and writes a content-free audit entry recording how many of each class it removed.

> **This table is generated from the schema, not hand-maintained** *(v1.1 — audit D5)*
>
> v1.0's table listed ten classes and omitted seven — including `thread_digest`, whose three-year persistence (§8.3) directly contradicted the 24-month prospect-PII horizon it sat beside. **§19.22 caught it on its first application, against the section that prompted §19.22's creation.**
>
> A hand-written inventory drifts the first week someone adds a table. So the mechanism, not the list, is the fix:
>
> - Every table declares a `retention_class` in its migration.
> - **A migration that adds a table without one fails CI.**
> - This section is generated from those declarations; it cannot silently fall behind the schema.
>
> Per Part G of `AUDIT-v1.0.md`: a principle in a validation list is not self-enforcing. Making the omission *impossible* is worth more than restating that it is forbidden.

#### 14.8b The suppression list is never deleted — deliberately

An erasure request **must not** remove someone from suppression. Deleting the record of their objection is precisely how they get contacted again.

We retain a **one-way hash of the email address only** — enough to never contact them, not enough to contact them. GDPR Art 17(3) permits retaining data necessary to honour an objection, and this is the textbook case for it.

#### 14.8c Erasure and export

| Request | Response |
|---|---|
| **Erasure** (GDPR Art 17) | Delete prospect record, message bodies, briefs, raw outputs, replies. **Retain** the suppression hash and content-free audit rows. Confirm within 30 days. |
| **Export** (Art 20) | Machine-readable export of everything held about that prospect, generated from the same records. |
| **Objection** (Art 21) | Immediate suppression via the §7.5a synchronous path — same mechanism as unsubscribe. |

#### 14.8d The LLM provider is a data processor

Prospect data reaches a third party on **every generation**. This is a contractual matter, not a code one:

- The provider must be configured for **zero-retention / no-training** processing.
- That configuration must be **in writing before P7**, and named in the record of processing activities.
- §14.7's PII minimisation reduces the exposure but does not remove it — a first name and a company name are still personal data.

Listed in the open questions (§20.14) because it needs your sign-off, not a design decision.

#### 14.8e Access and encryption

- **Role-based access.** The AI layer already holds **no table grants at all** (§14.5).
- **The briefs table is restricted further** than the prospect table — it is the highest PII-density object in the system and should be readable by fewer people, not more.
- **TLS in transit; encryption at rest** on the database and backups.
- **Secrets in a manager, never in environment files or source** (Master Prompt §27).
- Every access to raw message bodies or briefs by a human writes an audit row.

#### 14.8f What "content-free" means in the audit log

*(v1.1 — audit D6. v1.0 asserted the audit log was content-free and simultaneously required a `reason` on every state transition — and the natural implementation of "reason" is free text.)*

The audit log is exempt from erasure and retained for **7 years**. That exemption is only defensible if the rows genuinely contain no personal data — so the constraint is defined, and enforced in code rather than trusted:

| Permitted | Forbidden |
|---|---|
| Timestamp, actor (user id or `system`) | Email addresses, names, company names |
| Action, object type, object **id** | Message bodies, subjects, quoted replies |
| Previous state, new state (enums) | Free-text `reason` of any kind |
| **`reason` as an enum** | Model output, brief contents |
| Prompt/rulebook version ids | Anything copied from a prospect or a reply |

`reason` is an **enum** — `unsubscribe_request`, `hard_bounce`, `human_reply`, `admin_pause`, `validator_hold`, `veto_limit`, `lease_expired`. Where a human needs the detail behind an audit row, they follow the object id into the operational tables, which **do** carry retention horizons and **are** erasable.

**Enforced mechanically:** the audit-writing helper accepts an enum and refuses a string. A CI assertion fails the build if any call site passes free text. Master Prompt §26's "metadata" is satisfied by structured fields, not prose.

---

## 15. CONTEXT RISK REGISTER — audit target 1

**Post-audit score: 35/35 solid**, each with a verification method that tests the stated risk and can fail.

| # | Risk | Where it bites *here* | Mitigation | Verification |
|---|---|---|---|---|
| 1 | Context Window Limitations | 3-year follow-up threads | Rolling digest §8.3 | p99 input tokens < **3,690** (90% of `A2_INPUT_CEILING`) over 1,000 simulated sends |
| 2 | Context Overload | Rulebook + playbook + facts + claims + summary | Declared per-section budgets §9.2 | `assert_budget()` unit test per assembler |
| 3 | Context Rot | Long-lived sessions | Stateless per call §8.1 | Architecture test: no worker holds state across invocations |
| 4 | Lost in the Middle | Rules buried mid-prompt | **Primarily retired by size** — a 4,100 ceiling has no "middle" to lose. Secondary: fixed order, rules at top **and** adjacent to the contract §8.9 | `A2_INPUT_CEILING` assertion (primary) + position-sensitivity eval: shuffle Fact Pack order, output stable (secondary) |
| 5 | Context Dilution | Null fields | Omit absent fields entirely §8.4 | Assembler test: NULL ⇒ key absent |
| 6 | Context Pollution | Hostile replies; **hostile notes reaching outbound prose** | **Notes can never authorise a fact — excluded from the entity allowlist** §8.8 §10.1e; fence + quote-strip + zero tools | **Two injection suites** §14.4d, including the self-authorisation case |
| 7 | Hallucination | Invented certs, MOQs, past deals | Grounding validator §10.1, whitelist-based | 60-case golden set + **monthly validator-escape audit, target 0** §14.4c |
| 8 | Grounding Failure | Drift off claims; **promising what the catalogue lacks** | `claims_used[]` + **check (h) as declared-term membership plus other-catalogue denylist — no NER** §10.1h | Claims traceability 100%; h1/h2 pass rate; escape audit §14.4c |
| 9 | Retrieval Failure | — | No retriever in v1 §8.5 | N/A until >500 claims |
| 10 | Retrieval Noise | — | Hard `LIMIT 5`, segment-filtered §8.5 | Assert ≤5 claims in any brief |
| 11 | Context Fragmentation | Data across 10 tables | One `ContextAssembler` per worker §8.1 | Fixture tests; **AI role has no table grants** §14.5 |
| 12 | Long-Context Degradation | Big campaigns, long threads | `A2_INPUT_CEILING = 4100` §9.2 | p99 alarm at 3,690 |
| 13 | Instruction-Following Degradation | Prompt grew 900 → 1,200 | Segment variance in the **playbook**, voice in the **rulebook**; length capped | **Rulebook first-pass rate is a continuous, per-rule adherence measure** §6.4, with numeric alarms §6.5 |
| 14 | Attention Dilution | Too many concerns | 8 workers, 1 job each, **0 tools each** §8.2 | Design review: any worker needing >3 tools must be split |
| 15 | Token Budget Constraint | Cost at 500/day | Budget object + model tiering; **cost derived from the 81% joint rate** §9.1 §9.3 | **Joint first-pass ≥80% is the P0 gate**; cost-per-email dashboard, alert >20% drift |
| 16 | Prompt Bloat | Artifacts grow per release | Versioned files with size tests §14.3 | CI fails if a version exceeds its budget |
| 17 | Memory Bloat | 20 messages over 3 years | Digest **rewritten**, never appended §8.3 | Digest ≤300 tok after 50 simulated sends |
| 18 | Knowledge Staleness | Old claims, **old rulebook**, **stale summaries**, stale pinned catalogues | Claims 180-day review §10.7; rulebook review **with numeric thresholds** §6.5; **version bump blocks the summary until re-confirmed** §11.2; `supersession_policy` §11.3 | Nightly stale-claim job; rulebook rejection alarms; **no catalogue attachable with an unconfirmed summary** |
| 19 | State Management | Multi-week cadences | State in Postgres §14.1 | Restart-recovery test |
| 20 | Context Management | What enters / stays / leaves | `ContextAssembler` + budget table §8.1 §9.2 | Golden serialised-brief snapshot per assembler |
| 21 | Agent Context Explosion | Chains growing | **No worker→worker link**; DB round-trip every hop §4 | Architecture test |
| 22 | Tool-Output Explosion | Bodies, 2,000-row pools, whole PDFs | Zero tools; bodies truncated; A7 gets aggregates; **term list to validator only** §11.2 | Assert no brief field exceeds its cap; assert term list never appears in a logged prompt |
| 23 | Reasoning Drift | Long chains | ≤3 LLM calls §8.2 | `max_iterations` + **no-progress detector** §10 |
| 24 | Goal Drift | Worker forgets the task | Runs last seconds; objective adjacent to contract §8.9 | Golden-set task-success rate |
| 25 | Error Propagation | Bad segment poisons every later email | A6 fails closed <0.75 (when built); **the send preview is the human cross-check in v1** §5.2c; every hop re-grounds from DB | Fault injection: corrupt a classification, assert downstream flags rather than compounds |
| 26 | Compounding Errors | Multi-stage pipeline | Typed contract + 3 validators + **mechanical code override** §10.6 | E2E test with deliberately bad model output |
| 27 | Non-Determinism | **A2 at temp 0.7** | temp 0.0 everywhere else; full ledger; **quality-stability, not output-stability** §14.4b | A2: 5 runs × 20 briefs — joint-rate variance ≤5pts, judge variance ≤0.5, schema validity 100% (gate). A3: label stability ≥99% |
| 28 | Prompt Sensitivity | Small edit shifts behaviour | Versioned artifacts gated by evals; **replay** proves the delta §14.2 | No prompt or rulebook merges without a green eval diff |
| 29 | Context Switching Cost | One worker, five jobs | One worker = one job §8.2 | Design review gate |
| 30 | Latency & Cost Scaling | 500–1,000/day | Bounded context, model tiering, **FitScore costs zero tokens**, circuit breaker | **Cost model derived from the measured joint rate** §9.3; p95 latency dashboard |
| 31 | **A wrong segment label** — *reshaped in v1.5.* Not "the classifier erred" but **"the CSV said supermarket and it is an importer"**. Still produces a fluent, well-grounded, completely wrong email with the wrong catalogue attached | `unknown` never queued; segment-scoped CTA and forbidden-topic checks; **the send preview shows company + segment + catalogue on one screen before sending** §5.2c. When A6 returns (§18) it comes back as a **label validator**, not a classifier | **v1:** preview covers every send at 15–50/day — assert the preview shows all three fields and requires an explicit action. **Later:** A6 flags rows whose supplied label disagrees with the company evidence; precision ≥97% on distributor/importer; **CTA leakage gated 0** |
| 32 | Sameness / copy-paste drift | 500 emails a day converging on one opener | Sameness gate §10-[5]; banned-opener ledger §10.3; rulebook denylist; temp 0.7 | Sameness pass ≥90%; distinct-opener ratio ≥95% per day; **monthly human formulaic-read audit** §14.4c |
| ~~33~~ | ~~Catalogue misrouting — India catalogue to a UAE importer~~ | **DISSOLVED in v1.5.** Under one-segment-one-catalogue (§5.2), `importer_india` and `importer_uae` are separate segments with separate catalogues. **There is no decision in which they can be confused** — the risk is not mitigated, it no longer exists | Row retained struck-through so the reasoning stays on the record. If a many-to-many catalogue rule ever returns, so does this row |
| 34 | Attachment deliverability cost | PDF on every cold email raises spam scores | Size <1MB, sanitisation, per-catalogue tracking, per-campaign attach flag §11.5 | Complaint rate <0.1%; per-catalogue bounce comparison |
| 35 | Sending-domain reputation loss | Burning the primary domain loses invoices and existing customers | Separate aged sending domain; real mailboxes per real people; **batch quarantine with an evidence floor** §12.5 §13.2 | Postmaster Tools reputation High/Medium; auto-pause thresholds §13.6 |
| 36 | **Unauthorised commercial commitment** — a grounded but unapproved price, MOQ, shelf life or term reaching a prospect in writing, looking like an offer from Narayani | **Categorical denylist check (i), overriding grounding** §10.8; **`commercial` claims filtered out at the assembler** so the model never sees them; CTAs are offers, not statements §5.3; A3 pauses automation on any commercial question §7.5 | **Zero commercial terms across the full 60-case golden set (CI gate)** · assembler test: no `claim_type='commercial'` row appears in any Fact Pack · monthly escape audit §14.4c reads specifically for implied commitments |
| 37 | **Duplicate send after a worker crash** — provider accepted the message, worker died before recording, reaper retried | **Send key written before the provider call; `rendering` vs `sending` distinguished; reconciliation before any retry; unresolvable ⇒ HOLD** §14.1c–d. **Reconciliation is a declared per-adapter capability, never assumed** §14.1e — SMTP has none and holds every ambiguous row | **Crash-injection suite** §14.1f, **including an adapter with no reconciliation capability**. Assert zero duplicates in every case. **Message-ID round-trip verified by P0 probe 2** §18.1 |
| 38 | **Small-batch quarantine misfire — in both directions.** One bounce in twenty must not halt nineteen good records; ten bounces in twenty must not be allowed to continue | **An absolute floor of ≥10 bounces establishes that evidence exists; the Wilson lower bound decides whether it is bad. No volume floor on top of either** §12.5 | Simulation across 20 / 50 / 200 / 2,000: assert 1/20 does **not** fire · assert **10/20 does** · assert 10/500 does not · assert 12/200 does |
| 39 | **Auto-pause mis-calibrated at ramp volumes** — one complaint on 100 sends must not stop everything; a 6% complaint rate on 50 sends must not be tolerated | **HOLD RAMP as a middle response; windows stretch to reach the floor; absolute count establishes evidence and Wilson decides severity, with an extreme-rate escape above 2%; blocklists and Postmaster grades exempt as verdicts not samples; auth failures treated as config bugs** §13.6 | Ramp simulation at 100 / 200 / 500: assert 1/100 does **not** pause · assert 3 in 7 days holds the ramp · assert **3 complaints in 50 sends pauses** · assert Postmaster "Low" pauses at any volume · assert a blocklist listing pauses with no floor. **Complaint-data availability confirmed by P0 probe 1** §18.1 |
| 40 | **HOLD-queue overflow** — held messages accumulating faster than anyone reviews them, wasting generation cost and hiding an upstream fault | **Depth thresholds against stated review capacity: alarm, then backpressure on tomorrow's target, then HOLD RAMP** §10.2 | Simulate a 5% terminal HOLD rate for 14 days: assert backpressure engages before the queue exceeds 3 days of capacity, and that the daily target falls rather than the queue growing |
| 41 | **Sending after unsubscribe** — a suppression landing between the check and the provider call, or an unsubscribe sitting in a queue while sends continue | **Both paths take `SELECT … FOR UPDATE` on the contact row first, then the conditional claim** §7.5a — an atomic statement alone is insufficient under READ COMMITTED · **unsubscribe writes suppression synchronously inside the HTTP request** · suppression events cancel in-flight rows · residual window measured, any loss escalated as an incident | **Deterministic interleaving test** — advisory lock or injected delay forcing each ordering in turn; assert the loser yields in **both** directions. Repetition is a smoke test, not the proof. · receipt-to-suppression p99 < 2s · commit-to-accept p99 < 5s · a window loss produces an incident record, not silence |
| 42 | **Unbounded PII accumulation** — full briefs and raw outputs retained indefinitely; and a retention inventory that silently falls behind the schema | **Per-class horizons; briefs and raw outputs capped at 90 days; suppression retained as a one-way hash; `content-free` defined and enforced, not asserted** §14.8, §14.8f · **the inventory is generated from `retention_class` declarations — a migration adding a table without one fails CI** | Nightly retention job tested against seeded past-horizon rows · erasure test: prospect data removed, **suppression hash retained**, audit rows intact · **CI: a migration with no `retention_class` fails** · **CI: the audit helper rejects a free-text `reason`** |
| 44 | **Mailbox-level over-send** — a global daily budget spent through one mailbox, or a paused mailbox still counted as capacity | **`ramp_target` is a sum over *active* mailboxes; the per-mailbox ceiling is checked inside the claiming transaction, not only in aggregate** §7.4d §7.5a | Simulate 5 mailboxes on staggered warm-up schedules with one paused mid-day: assert no mailbox exceeds its own allowance, assert the system cap holds, assert the paused mailbox's queued rows return to the pool |
| 45 | **A required measurement forbidden by the phase that requires it** — the reconciliation probe and seed-placement testing both need real sends during `TEST_MODE` | **`SEED_SENDING_ENABLED` with an allowlist enforced in the provider adapter, not in config; seed sends consume no cap** §18.2 | Assert a send to a non-allowlisted address while `LIVE_EMAIL_SENDING=false` **raises and is audited** · assert seed sends do not decrement the daily cap · assert the probe can complete before P3 |
| 46 | **A segment rule inferred from too few samples becoming a hard check** — 15 emails over 5 segments is 2–3 each at best, and unevenly distributed. A rule from 2 coin flips rejects ~40% of valid drafts, collapses the joint first-pass rate, and **presents as "the model writes badly for this segment"** rather than as a bad rule | **The rulebook is global house voice only; all segment behaviour is human-written in the playbook** §6.6a · **no pattern from fewer than 5 samples may become a hard check** · per-segment sample count reported at P-B before any rule is written §6.6b | **Per-rule rejection rate** §6.5 is the instrument — a rule rejecting >25% over 7 days raises a task naming it · **assert no rulebook rule's *content* is derived from segment-labelled samples, and every segment-specific *value* it consumes resolves to a `segment_playbook` field with a human author of record** *(v1.4 — audit F3: "assert no rulebook rule references a segment" would have failed on correct code, since §6.2's word-count and CTA rules are correctly segment-**parameterised**)* · assert every positive rule appears in ≥2 segments §6.6d · assert every rule carries a provenance class §6.6c |
| 43 | **Commercial-term evasion** — a commitment expressed without digits (*"flexible on minimums"*) or with spelled numbers (*"forty metric tonnes"*), passing a digit-anchored check | **Check (i) has three layers: numeric proximity, number words incl. lakh/crore, and a unit-free commitment denylist** §10.8 · **adversarial escape audit separated from CI regression** §10.8b | CI: 60-case set, 0 failures, **both directions tested** · **monthly human audit of 50 passed emails asking "does this imply a commitment?"** · every escape becomes a permanent CI case |

---

## 16. Production-readiness checklist — audit target 2

| # | Requirement | Satisfied by |
|---|---|---|
| 1 | Schema-validated output with a repair loop and a deterministic fallback | §10 — four stages; `fallback()` on every contract; HOLD terminal |
| 2 | Every tool has a timeout, error contract, least-privilege credentials | §14.5 — zero tools; AI role has **no DB grants** |
| 3 | Loop has iteration, time and cost caps | §7.2 · §7.5 — plus a **no-progress detector** §10 |
| 4 | Every run traced end to end and replayable | §14.2 — ledger stores the full assembled brief |
| 5 | Golden eval set exists and gates CI | §14.4a — **synthetic→real path with named owners** |
| 6 | Destructive actions need human approval or are reversible | §2 — sending is in Layer 0 behind TEST_MODE; un-suppression is human-only |
| 7 | Prompts versioned; rollback one-line | §14.3 — prompts, rulebook **and** playbooks |
| 8 | Cost per task measured and alerted | §9.3 · §14.4e |
| 9 | Documented answer to "what if the model is wrong?" | §10.2 HOLD (wrong) · §14.4c (wrong *and undetected*) · §14.6 (unavailable) |

---

## 17. Technology

**Python monolith.**

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | AI layer is Python-native |
| API | FastAPI + Pydantic v2 | Pydantic is already the contract layer |
| DB | PostgreSQL 15+ | Relational, `SKIP LOCKED` queue, JSONB for playbooks |
| ORM | SQLAlchemy 2.0 + Alembic | Reversible migrations |
| Queue | **Postgres table, not Redis/Celery in v1** | Durability + exactly-once, one fewer failure domain |
| Scheduler | APScheduler or cron → idempotent tick | A double-fire is harmless |
| AI | **LangChain core** (`with_structured_output`) for A1/A3/A4/A5/A6/A7 · **LangGraph for A2 only** | Only A2 has a real loop. *Do not over-engineer* (Master Prompt §29) |
| PDF | `pikepdf` (sanitise) · `pdfplumber` (extract) · Ghostscript (compress) | §11.1 |
| Email verify | `dnspython` for MX + an API for the remainder | §12.1 |
| Similarity | `datasketch` MinHash or plain trigram sets | §10-[5] |
| Observability | LangSmith **or** OTel + the DB ledger | *"You cannot debug an agent from its output alone."* |
| Frontend | React + Vite, REST | Dashboard, timeline, human-action queue, per-segment preview, catalogue admin |
| Tests | pytest + factories + `freezegun` | Time-travel is mandatory for 7/21/30/90-day cadences |

### 17.1 Why not CrewAI
CrewAI's value is autonomous delegation over an **unknown** path. Ours is fully known — a linear diagram in Master Prompt §2. The workers fire days apart on different triggers: A6 at import, A7 once daily, A2 at queue time, A3 on reply ingest. **They are not a crew; they are unrelated functions on different triggers.**

The adversarial-reviewer lesson *is* adopted — as three decidable validators, better than a second LLM because they cannot be talked out of a verdict, cost nothing per run, and never hallucinate a finding.

### 17.2 Cohort compliance is a standing CI gate

Re-run on every prompt or contract change:

- Stop conditions 5/5 — `max_iterations`, wall clock, token/cost budget, no-progress detector, explicit finish path
- Every enum has `unknown`, **and every `unknown` has defined behaviour**
- Schemas flat; no large free-text blob inside parsed JSON
- Schema generated from Pydantic, never hand-written twice
- Brace-counting JSON extraction, not regex
- Validator errors echoed back verbatim
- Deterministic fallback on every contract
- temp 0.0 on every classification and extraction step
- No system prompt exceeds its declared budget
- No worker holds >3 tools
- **No validator check requires an LLM to evaluate**

---

## 18. Phasing and gates

| Phase | Content | Exit gate |
|---|---|---|
| **P-A · Vision** *(this doc)* | Architecture, contracts, risk register, **two audits closed** | **You sign off §15, §16, §19.** |
| **P-B1 · Foundation** *(unavoidable, yours)* | Segments + catalogues + **the approved-claims list per segment** + 10 real prospect rows + business rules per segment (§6.7). **Nothing can be generated before this exists** — the writer has nothing to write *from*. | Every segment has a catalogue, a claims set and a playbook. A segment missing any of the three is not selectable (§5.2a). |
| **P-B2 · Convergence loop** | Stage 0 interview → ~10 drafts one at a time, each written from the rulebook in a **fresh context** → three guards run underneath (§6.1c). **Named owner hand-labels 100 companies** (§20.13) in parallel. | **Segment sweep passes in every segment** (§6.1d) **and** the blind baseline check prefers the final draft over draft 1 (§6.1e). Only then do hard checks activate. |
| **P0 · Measured spike** *(2–3 days)* | Throwaway. A2 draft + all four validator groups + the 5×20 stability run; A3 on 40 replies; rule-vs-LLM scoring; Jinja-vs-A5. **Plus three provider probes (§18.1).** No DB, no API, no UI. | **Numbers + five decisions:** **joint first-pass rate ≥80%** (the §9.3 cost assumption) · **check (i) measured pass rate** · tokens/call · cost/email · p95 latency · per-validator pass rates · validator false-positive rate · per-segment sample distribution (§6.6b) — **plus keep/kill for A4's LLM half and A5, model tier for A2, confirmation temp 0.7 clears grounding ≥95%, and answers to all three §18.1 probes.** |
| **P1** | Schema, config, import, **verification ladder**, dedupe, campaigns, state machine, queue, cap enforcer, tests. **Plus the admin surface** — segments, catalogues, campaigns, the `segment → catalogue` mapping and the playbook editor (§5.2a). **No LLM.** | Cap / FIFO / idempotency / restart / verification tests green; **a segment missing a catalogue or playbook is not selectable for sending** |
| **P2** | **A2 + ContextAssembler + three validators + catalogue pipeline + send preview + mock provider.** *(A6 and A8 are not built — §18.3.)* | 60-case golden set green (**synthetic, labelled as such**); 0 ungrounded facts; 0 CTA leaks; sameness ≥90%; **the preview shows company + segment + catalogue and requires an explicit action** |
| **P3** | Provider abstraction, sending path, **attachment + link delivery + supersession policy**, delivery/bounce tracking | Restart / duplicate / provider-failure tests green; attachment size enforced |
| **P4** | Reply ingest, pre-filter, A3, human-action queue, suppression, **bounce classification + batch quarantine** | Unsubscribe recall 100% (synthetic set); policy-block correctly distinguished from hard bounce |
| **P5** | Dashboard and reporting as **SQL views**. Rule-based opportunity score only. *(A7, FitScore, A4-LLM and A5 are not built — §18.3.)* | Report reconciles exactly with SQL |
| **P6** | Hardening, security review, **deliverability setup (§13)**, monitoring, deploy | **§16 fully ticked**; DMARC passing; domain aged and warmed |
| **P7** | **Live sending — only on your explicit written approval** | Manual admin action writing an audit row + ramp from 5/day per mailbox (§13.3), **first ~200 prospects hand-picked** (§18.3) |
| **P7b · Decision gate** *(new)* | **At ~1,000 sends: reply rate by segment.** | **This decides whether the deferred workers are built at all, and for which segments.** |
| **P8 · Re-baseline** | Reply and scoring sets: ≥50% real from captured data. **Segmentation and personalisation sets: named human labelling task, separately scheduled.** First **validator escape audit** (§14.4c). | Within 30 days of P7 |

`TEST_MODE=true`, `LIVE_EMAIL_SENDING=false` from P0 through P6. Live mode requires an explicit admin action writing an audit row — **not** an env-var flip.

### 18.2 Seed sending — the one narrow exception

*(v1.1 — audit D4. v1.0 required two P0 measurements that send real email, during phases where §18 forbids sending real email. The document forbade the thing it required.)*

Two things the design depends on cannot be established without a real message leaving the system:

- **The reconciliation capability probe** (§14.1e) sends to a seed address and searches for its `Message-ID`. §18.1 requires it **before P3**.
- **Seed-list placement testing** (§18.1 probe 1) is the answer to complaint data being unobservable at ramp volumes — and it must run **from the first send**, not from P7.

Rather than weaken `TEST_MODE`, a third flag with a hard recipient allowlist:

```
SEED_SENDING_ENABLED = true          # independent of LIVE_EMAIL_SENDING

Permitted recipients : an explicit allowlist of OWNED seed addresses only
                       (Gmail, Outlook, Yahoo, one corporate domain)
Enforcement          : a check in the PROVIDER ADAPTER, not in config.
                       A send to any non-allowlisted address while
                       LIVE_EMAIL_SENDING=false raises, is audited, and
                       fails the request.
Cap accounting       : seed sends consume NO daily-cap capacity (§7.4d)
Suppression          : seed addresses are exempt (they are ours)
```

The guarantee that matters is unchanged: **no prospect receives anything until P7.** The enforcement lives in the adapter rather than in configuration precisely so that it cannot be turned off by editing a settings file — which is the same reasoning as §14.1e's self-verifying capability probe.

Without this, either the two probes never happen, or someone quietly flips `LIVE_EMAIL_SENDING` to run them. The second outcome is exactly what §18's rule exists to prevent, so the rule needs a legitimate path rather than an unmet requirement.

### 18.1 Three provider probes — the questions no document can answer

*(v0.9 — audit findings B2, B3, B4 deliberately left open. Each asks what a provider actually does, and reading this document again will not tell us.)*

These are **not** design gaps to be argued out. They are measurements, and they are cheap — an afternoon each with a provider account.

**Probe 1 — What complaint data does the provider actually expose?** *(audit B2)*

Gmail publishes no per-message feedback loop. Postmaster Tools reports an **aggregate** spam rate, and only once the domain sustains enough daily volume for the dashboard to populate. Microsoft offers SNDS and JMRP; Yahoo offers an FBL. So §13.6's headline metric may not be computable for the largest slice of recipients, and may be least available during the ramp — exactly when it matters most.

*Determine:* which of complaint count, complaint rate and reputation grade are actually retrievable, per provider, at 100/day and at 500/day.

*If complaint data proves unavailable at ramp volumes, §13.6 shifts its primary automated signal to what is definitely observable* — **bounce rate** (already built, §12.4), **unsubscribe rate**, and **A3's `unsubscribe` / `not_interested` classifications** (§7.5) — *and adds seed-list placement testing:* owned addresses at Gmail, Outlook, Yahoo and a corporate domain, one included in every daily send, recording **inbox vs spam placement per provider per day**. That is a direct read on what complaint rate only proxies for, and it works from the first send at any volume.

**Probe 2 — Does our `Message-ID` survive the send?** *(audit B3)*

Crash reconciliation (§14.1d) depends on finding a sent message by an identifier we set. Gmail search does not index arbitrary `X-` headers; `rfc822msgid:` does work, **but only if the `Message-ID` we supply is preserved rather than rewritten.**

*Determine:* send one message through the chosen adapter with a known `Message-ID`, then search for it. Round-trip or not. This single test decides whether §14.1d's primary path exists or whether that adapter runs permanently in the `supports_send_reconciliation = false` mode of §14.1e.

Must be answered **before P3**, not during it.

**Probe 3 — What is check (i)'s real pass rate?** *(audit B4)*

§9.1 derives the ~81% joint first-pass rate from three validators. v0.7 then added the commercial-terms check (§10.8) without revisiting it — the same class of error §9.1 was written to correct.

Check (i) is new, untested, and bans a category that §10.8's proximity rules are designed to match precisely but have never been run against real drafts. **If it rejects even 5% of drafts the joint rate falls to ~77% and the repair overhead rises again.**

*Determine:* run check (i) across the P0 drafts and the golden set, measure its pass rate and — more importantly — its **false-positive rate** on legitimate numerics (*"40 years in business"* must pass). Feed the result back into §9.1 and §9.3 before P1 commits to a cost model.

---

## 19. Definition of "validated" — audit target 3

Passes only if all **twenty-five** are **yes**. *(The canonical list is `AUDIT-PACK.md` §2 — that is the sign-off artifact, and it already carries a CI assertion tying it to this document's version. If the two ever disagree, the pack is right and this copy is stale.)*

1. The LLM can lower, skip, defer, hold, escalate and suppress freely; it cannot raise a cap, send despite suppression, un-suppress, or re-send.
2. **Exactly two invariants admit no LLM input at any stage** — the 1,000/day system-wide ceiling and already-sent detection. *(This is not a claim that the rest of the system is AI: most of it, including every validator in the send path, is deterministic code — §2.2 category A.)*
3. Every row in §15 has a verification method that **tests the stated risk**, not an adjacent one.
4. **Every verification method can fail.** A check that is true by construction is not a check.
5. Every worker has a token budget, iteration cap, wall-clock cap, cost cap, flat typed contract, an `unknown` member on every enum, **defined behaviour for every `unknown`**, and a deterministic fallback that cannot crash the pipeline.
6. Context size for a prospect's 20th message equals that of their 2nd.
7. No worker's output reaches another worker without passing through the database.
8. Every failure fails **closed**, enforced **in code, not only in the prompt**.
9. The grounding validator is a **whitelist**, not a blacklist — **and every check in it is decidable without an LLM or NER**.
10. **Nothing in a free-text field can enlarge the entity allowlist.** Notes inform tone; they never authorise a fact.
11. No path sends a generic or templated email by default.
12. A prospect with `segment=unknown` or `catalogue=unknown` can never be queued.
13. **No LLM's output is approved or cleared by another LLM.** Disagreement between classifiers may route to a human; agreement is never sufficient to pass a gate.
14. Every house-voice rule that can be counted is a validator check, not a prompt request.
15. No feature exists whose purpose is to disguise automation from a recipient or a provider.
16. The one deliberately non-deterministic component (A2) has a stability test that measures **quality**, not output.
17. **Where a gate is met on synthetic data, the document says so, a dated obligation exists to replace it, and a named human owns any labelling that sending cannot produce.**
18. **No generated message contains a commercial term — price, MOQ, shelf life, payment, delivery, discount, exclusivity or capacity — regardless of whether it could be grounded.** Every term a prospect ever receives was written by a person at Narayani, in a catalogue or a reply.
19. **No automated message answers a commercial question.** A pricing or terms enquiry stops the sequence and goes to a human.
20. **A worker crash cannot produce a duplicate send.** Where it cannot be determined whether a message left the system, the row is held for a human, never retried.
21. **No automatic destructive or blocking action fires on evidence too thin to support it** — and none is withheld when the evidence is decisive. An absolute count establishes that evidence exists; the Wilson bound decides whether it is bad.
22. **Every data class the system stores has a stated retention period, an access rule, and an owner.** *(An absence check rather than a correctness check — §28.4 explains why it exists.)*
23. **The suppression gate is a single atomic statement, not a check followed by an action** — and the unsubscribe path writes its row synchronously, before returning 200.
24. **Every daily-cap statement resolves to one arithmetic rule**: `new + followups ≤ 1000`, `followups ≤ 500`, with a deterministic split no model can reorder.
25. **No validator's safety rests on a curated pass rate alone.** Every checker in the send path has an adversarial audit that tries to break it, separate from its regression set.

---

## 20. Open questions

1. **Approved claims per segment** (§10.7) — who signs off what Narayani may truthfully say to each buyer type? **Highest leverage.**
2. **The 15 emails and 6–8 catalogues** (§6.7, P-B) — needed for the rulebook, term lists, summaries and routing golden set.
3. **`wellness_gym` has no catalogue** (§5.5) — add an eighth, map to `organic_gourmet`, or drop the segment?
4. **Sending domain** (§13.2) — acceptable? Can it be registered now so 30-day ageing runs in parallel with the build?
5. **Mailboxes** (§13.2) — how many real people can own a sending mailbox and handle replies?
6. **Verification API budget** (§12.1) — paid verifier for the ~25% remainder, or DNS-only?
7. **Model budget** (§9.3) — monthly ceiling? Drives P0 tiering. Note the figure is now ~2.85M tokens/day, ~15% above v0.5.
8. **Languages** — any non-English prospects?
9. **Human review capacity** — messages/day a person can review? Sets HOLD tolerance.
10. **Lawful basis** (§3.2) — documented consent or legitimate interest?
11. **Historical data** — any past replies or bounce logs? Even 30 real replies would shorten the synthetic period.
12. **Catalogue summaries** (§11.2) — who writes the 200-token summary per catalogue, and who re-confirms it on a version bump?
13. **Labelling owner** *(audit A11)* — **who hand-labels the 100 companies at P-B, and who performs the monthly escape audits (§14.4c, §10.8b)?** Recurring human tasks that sending cannot produce. Roughly 3–4 hours initially, then ~2 hours monthly across both audits.
14. **LLM provider processing terms** *(new — owner finding C5, §14.8d)* — prospect data reaches a third party on **every generation**. **Zero-retention / no-training processing must be contractually confirmed in writing before P7.** Needs your sign-off, not a design decision.
15. **Data owner and retention approval** *(new — §14.8)* — who approves the retention horizons, and who handles an erasure or export request when one arrives? The 30-day statutory response window needs a named person, not a process diagram.
16. **Mailbox count and warm-up start date** *(§13.3)* — the schedule is per mailbox from day 1. A mailbox reaches its 150/day ceiling at roughly **week 11**, and staggered starts push full capacity out further *(v1.1 — audit D9 corrected v1.0's "~10 weeks")*. **That clock starts when the domain is registered, not when the code is finished** — which makes §20.4 the cheapest item on this list to decide today.

---

## 21. Change log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-09-06 | Initial draft, before reading the NASSCOM material in full |
| 0.2 | 2026-09-06 | Revised after reading all 7 NASSCOM files end to end |
| 0.3 | 2026-09-06 | Asymmetric authority; five segments as primary axis; anti-copy-paste as mechanism |
| 0.4 | 2026-09-06 | Rulebook, catalogue routing, email verification, deliverability programme |
| 0.5 | 2026-09-07 | First audit: 9 findings closed |
| 0.6 | 2026-09-07 | **Second audit: 12 findings closed — 3 of them defects in the v0.5 fixes.** Register 35/35 solid. See §24. |
| 0.7 | 2026-09-07 | **Owner review: 3 findings closed.** Batch-quarantine evidence floor · worker-crash lease/reconciliation (a latent Rule 28 violation) · **AI makes no commercial commitment, ever**. Register 38/38. See §25. |
| 0.8 | 2026-09-07 | **§13.6 auto-pause thresholds brought under the same evidence floor**, with asymmetric limits, a HOLD RAMP middle response, and floor exemptions for verdicts rather than samples. Register 39/39. See §26. |
| 0.9 | 2026-09-07 | **Third audit: 7 findings closed, 3 deferred to P0 as measurements.** Volume floors removed from both quarantine rules · check (i) made proximity-based · HOLD backpressure · veto decay · per-adapter reconciliation. Register 40/40. See §27. |
| **1.0** | 2026-09-07 | **Owner review 2: 6 findings closed, 2 of them P0.** Atomic suppression gate · **data retention & privacy (a genuine omission)** · one arithmetic rule for the daily cap · self-verifying reconciliation capability · operational warm-up schedule · check (i) hardened against unit-free commitments. Register **43/43**; §19 grows to 25 questions. See §28. |
| **1.1** | 2026-09-07 | **Fourth audit: 10 findings closed, 5 significant — all five in v1.0 material.** Contact-row lock closes the suppression race properly · per-mailbox ramp allocation · evidence floor on the warm-up gate (the bug's third appearance) · `SEED_SENDING_ENABLED` so the P0 probes can run · **retention inventory generated from the schema, not hand-written**. Register **45/45**. See §29. |
| **1.2** | 2026-09-07 | **Owner review 3: 1 finding.** §2.2 claimed "the only two decisions in code" while the document described 123 deterministic gates — an internal contradiction that could lead a developer to build safety-critical validators as AI calls. Rewritten as three categories. See §30. |
| **1.3** | 2026-09-07 | **Owner review 3, finding 2.** The rulebook is now **global house voice only** — all segment behaviour is human-written, no segment rule is ever derived from samples, and no pattern from fewer than 5 samples may become a hard check. P-B reports the per-segment sample count first. Register **46/46**; principles 9 and 10 added. |
| **1.5** | 2026-09-09 | **Owner rules applied — scope cut, one worker deleted.** **One segment = one catalogue**, so A8 is removed entirely and risk #33 dissolves · segments become **user-managed data** with an admin surface · **v1 ships 2 AI workers, not 8** (§18.3) · the send preview replaces A8's cross-check · risk #31 reshaped from *"the classifier erred"* to *"the supplied label is wrong"* · new decision gate **P7b at ~1,000 sends**. Register **45 live + 1 dissolved**. |
| **1.6** | 2026-09-10 | **Owner review 4 + three design audits.** P-B replaced: the rulebook is now built by a **convergence loop** (§6.1) rather than from 12–15 supplied emails — recognition beats production, especially in a second language. Every draft written from the rulebook in a **fresh context**, three guards underneath, **segment sweep + blind baseline check gate convergence**. Plus: **multi-tenant-ready schema** (`tenant_id` everywhere now, SaaS layer later), and projects B (prospect entry) and C (product database) accepted. |
| **1.4** | 2026-09-07 | **Fifth audit: 6 findings closed.** Rulebook rules now carry a **provenance class** — the evidence floor was inverted for prohibitions · a global rule must hold across **≥2 segments**, because a skewed sample cannot teach a neutral voice · row 46's assertion **could never pass** · principle 9 generalised · suppression detection split correctly · **§24–§31 moved to `REVISION-HISTORY.md`** (18% of the document). Register **46/46**. See §24.1. |

---

## 22. Design principles, in one page

1. **Deterministic spine, AI leaves.** The LLM writes and classifies; code counts, gates and sends.
2. **Asymmetric authority.** Full freedom toward less; none toward more.
3. **The database is the memory.** No conversation survives a unit of work.
4. **Rules that can be counted are checks, not requests.**
5. **No LLM approves an LLM.** Disagreement routes to a human; agreement passes nothing.
6. **Whitelists, not blacklists** — and **nothing untrusted may enlarge a whitelist**.
7. **Fail closed, in code.** Hold and ask a human; never send anyway.
8. **Every claim has a test — and every test can fail.**
9. **An absolute count establishes that evidence exists; a severity test decides whether it warrants acting.** No blocking action, and no derived rule, rests on a rate over a small denominator. *(The severity test is a Wilson bound for rate-based rules — §12.5 quarantine, §13.6 auto-pause, §13.3 warm-up — and cross-segment presence for rule derivation, §6.6d. v1.4 generalised the second clause, which had said "confidence bound" and therefore did not describe its own fourth application.)*
10. **Segment behaviour is human-written; house voice is derived.** Fifteen samples can teach how Narayani writes. They cannot teach how Narayani speaks to supermarket buyers specifically.

---

## 23. Eligibility versus ordering — a note on a recurring bug class

Both audits found the same mistake in different places: **conflating a gate with a score.**

v0.5's FitScore multiplied by `verification_weight`, where `unknown` mapped to 0. That looked like ordering but was really an eligibility decision hidden inside arithmetic — and it silently made a human-review outcome undefined.

The corrected shape, used everywhere in v0.6:

> **Eligibility is a binary gate, evaluated first, with an explicit reason recorded. Scoring only ever orders things already eligible.**

The same separation now governs confidence floors (§7.1, §7.3), verification status (§12.1) and the FitScore itself (§7.4b). Where a threshold decides *whether*, it is a gate. Where a number decides *which first*, it is a score. Never one variable doing both.

---


---

## 24. Revision history

The detailed fix record for every revision from v0.6 onward lives in **`REVISION-HISTORY.md`**.

*(v1.4 — audit F6. Those sections had grown to **390 lines of 2,212 — 18% of this document** — and duplicated content already held in the `AUDIT-*.md` and `REVIEW-OWNER-*.md` files. Each fix round was permanently enlarging the design document with prose about the design document.)*

| Where to look | For |
|---|---|
| **§21** above | The summary changelog — one row per version |
| **`REVISION-HISTORY.md`** | The narrative behind each row: what was wrong, why it mattered, what changed |
| **`AUDIT-v*.md`** | The five audits, with findings, severities and reasoning |
| **`REVIEW-OWNER-*.md`** | The three owner reviews and their assessments |
| **`AUDIT-PACK.md`** | The two-page version for sign-off |

**Sections §1–§23 are the design.** Everything else is provenance, and none of it is required to implement the system.

### 24.1 What v1.4 changed

Six findings from `AUDIT-v1.3.md`, all closed:

| # | Finding | Fix |
|---|---|---|
| **F1** | The evidence floor was **inverted for prohibitions** — applied literally, banning *"I hope this email finds you well"* would have required 5 samples containing it | §6.6c — three **provenance classes**. The floor applies to positive rules only; prohibitions are justified by absence across all fifteen, owner-directed rules by your instruction. Every rule records its class. |
| **F2** | §6.6b warned the samples are skewed; §6.6a then put the whole derived voice on those samples. **A skewed fifteen cannot teach a neutral global voice** — 8 importer samples make the "house voice" 53% importer | §6.6d — a positive rule becomes a global hard check **only if it appears in ≥2 segments**. Plus P-B now reports which segment dominates the derived voice and asks you to confirm it generalises. |
| **F3** | Row 46 asserted *"no rulebook rule references a segment"* — which **would have failed on correct code**, since §6.2's word-count and CTA rules are correctly segment-*parameterised* | Rewritten as a **provenance** test: no rule's *content* derived from segment-labelled samples; every segment-specific *value* resolves to a human-authored playbook field. |
| **F4** | Principle 9 said *"a confidence bound decides whether it is bad"* — which did not describe its own fourth application, where there is no rate and no bound | §22.9 — generalised to *"a severity test"*: a Wilson bound for rate rules, cross-segment presence for rule derivation. |
| **F5** | §2.2 attributed **all** suppression detection to the LLM. A one-click unsubscribe and a hard bounce code are pure category A | §2.2 — detection split. Only reply-based detection is category B. Suppression is a category-A gate fed by both. |
| **F6** | §24–§31 had reached 18% of the document | This section. Moved to `REVISION-HISTORY.md`. |

**F3 is worth noting as a class.** `AUDIT-v0.5.md` A2 found a gate that could **never fail**; this one could **never pass**. Both violate §19.4 from opposite directions, and both would have been discovered only when someone tried to implement them.

### 18.3 What v1 does not build, and what stands in for it

*(v1.5 — from `PROPOSAL-v1-scope.md`, accepted. The reasoning is there in full; this is the operative summary.)*

**v1 has two AI workers, not eight: A2 writes, A3 reads replies.** Everything else is deterministic code or a human.

| Not built in v1 | What stands in | Returns when |
|---|---|---|
| **A6 SegmentClassifier** | `segment` is a **required column** in your list; a row without one is rejected at import, never guessed | Volume outgrows the send preview — and it returns as a **label validator**, not a classifier (§15 row 31) |
| **A8 CatalogueSelector** | **Deleted permanently** — §5.2 | Only if a many-to-many catalogue rule ever returns |
| **A7 DailyTargeting** | The warm-up schedule sets the daily number (§13.3) | When volume exceeds the ramp |
| **FitScore** | FIFO on `due_at`, filtered by verification status. **The first ~200 prospects are hand-picked** — at 15–30/day a person who knows the business will out-pick a formula, and this is the one place low volume makes the manual version genuinely better | When hand-picking stops being practical |
| **A4's LLM half** | The rule-based score alone, as a sortable column (§7.7) | P7b, if replies justify it |
| **A5 DailyDigest** | A SQL view. At 50/day you read the rows | P7b, if replies justify it |
| **A1 ImportTriage** | Manual column mapping in the import screen | Any time; it was advisory |
| **Per-prospect catalogue link tracking** | Static link. **Honest cost:** the catalogue-view buying signal (§11.4) is lost until the token is added | Any time; it is a small addition |
| **Replay tooling** | The ledger still stores briefs (§14.2); only the tooling waits | When a prompt change needs proving |

**Kept despite being unreachable at v1 volumes**, because they are cheap SQL and expensive to remember later: batch-quarantine thresholds, complaint and bounce auto-pause rules, HOLD-queue counters. They simply will not fire below ~1,000 sends — which costs nothing, and means the thresholds are already in place when volume arrives.

**Why this order.** Warm-up runs ~11 weeks per mailbox (§13.3). Across three mailboxes that is ~1,700 cumulative sends by week 7. At those volumes batch quarantine cannot trigger (needs 10 bounces), complaint rules cannot compute (need 1,000 sends), HOLD backpressure cannot engage, and FitScore is ordering twenty rows. **The warm-up hands you eleven weeks in which half the system has nothing to do** — and the only question that matters in those weeks is whether anyone replies.

**Deferred rows stay in the register** marked not-yet-applicable, so restoring a worker restores its risk row with it. Row 33 is the exception: it is deleted, because the decision it guarded no longer exists in any version.
