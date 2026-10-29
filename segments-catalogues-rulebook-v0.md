# Segments, Catalogues, and Rulebook v0 (Archived Specification)
> **NOTE**: This file is an archived baseline specification. Live configuration for all 8 segment playbooks and the global house rulebook is maintained exclusively in PostgreSQL via the Web Dashboard (`/app#config`). Runtime agents and validators read directly from the database and do not reference this file.

Narayani Distributors B2B Outreach System · supports VISION.md §5 and §6

This is P-B1/P-B2 input data: the segment↔catalogue mapping (§5.2), the per-segment
playbooks (§5.3), and Rulebook v0 — the guidance-only starting rulebook that Stage 2
of the convergence loop (§6.1) will harden into Rulebook v1 through real corrections.

**Nothing in this file is final.** The rulebook section is explicitly guidance,
not hard checks, until the segment sweep and baseline check both pass (§6.1f).
Word-count targets and forbidden topics per segment are human-set placeholders —
edit them to match how Narayani actually sells before running Stage 1.

---

## 1. Segment → Catalogue mapping (§5.2, §5.2d)

One segment, exactly one catalogue. `unknown` is never a real segment and is
never seeded a catalogue — a company that can't be confidently classified is
never queued for sending.

| segment (PK) | catalogue_file | catalogue_name | note |
|---|---|---|---|
| `importer_india` | `catalogue_importer_india.pdf` | Indian Importer Catalogue | HS codes, FSSAI, domestic compliance docs |
| `importer_uae` | `catalogue_importer_uae.pdf` | UAE Importer Catalogue | HALAL cert, GCC compliance docs, different Incoterms set |
| `distributor_wholesale` | `catalogue_distributor.pdf` | Distributor / Wholesaler Catalogue | margin bands, territory language (no numbers in email — catalogue only) |
| `fmcg_distributor` | `catalogue_fmcg.pdf` | FMCG Distributor Catalogue | SKU velocity, shelf-ready formats |
| `horeca_bakery` | `catalogue_horeca.pdf` | HoReCa / Bakery Catalogue | bulk formats, batch consistency, cost-per-portion framing |
| `organic_gourmet` | `catalogue_organic_gourmet.pdf` | Organic & Gourmet Catalogue | clean-label, premium framing |
| `supermarket_retail` | `catalogue_supermarket.pdf` | Supermarket / Retail Chain Catalogue | EAN, category fit, listing terms |
| `wellness_gym` | `catalogue_organic_gourmet.pdf` *(copied, §5.2b)* | Wellness & Gym Catalogue | duplicate of organic_gourmet under a new name until a dedicated file exists |
| `unknown` | — | — | **Never queued. Human review only.** |

A segment is not selectable for sending until it has: a name, an attached
catalogue file, and a completed playbook below (§5.2a — "incomplete = invisible").

---

## 2. Per-segment playbooks (§5.3)

Every field below is human-written. None of it is derived from sample emails —
that boundary is absolute (§6.6a). If a pattern looks segment-specific while
building the rulebook in Stage 2, it belongs here, not in the global rulebook.

### `importer_india`
```yaml
segment: importer_india
allowed_angles: [compliance_first, certification_led, shelf_life_reliability]
allowed_ctas: [spec_sheet, certifications_pack, quotation_fob_cif, sample_shipment, call]
approved_claim_ids: []   # filled from approved-claims-synthetic.md at import
forbidden_topics: [retail_shelf_placement, gym_nutrition_language, consumer_pack_sizes]
target_words_min: 80
target_words_max: 150
register_spec: >
  Formal, document-forward. Assume the reader is compliance/import staff, not
  a retail buyer. Lead with certification and documentation readiness, not price.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.3, reply: 0.5, spec_request: 0.2}
```

### `importer_uae`
```yaml
segment: importer_uae
allowed_angles: [compliance_first, halal_certification, gcc_documentation]
allowed_ctas: [spec_sheet, certifications_pack, quotation_fob_cif, sample_shipment, call]
approved_claim_ids: []
forbidden_topics: [retail_shelf_placement, gym_nutrition_language, indian_domestic_compliance]
target_words_min: 80
target_words_max: 150
register_spec: >
  Formal, document-forward, same register as importer_india but never conflate
  Indian domestic certifications with GCC/HALAL requirements — this is a
  different compliance regime, not the same one restated.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.3, reply: 0.5, spec_request: 0.2}
```

### `distributor_wholesale`
```yaml
segment: distributor_wholesale
allowed_angles: [margin_opportunity, territory_fit, product_range_breadth]
allowed_ctas: [price_list, territory_discussion, catalogue, call]
approved_claim_ids: []
forbidden_topics: [FCL/container, single_unit_retail_pricing]
target_words_min: 90
target_words_max: 130
register_spec: >
  Direct and business-to-business. Owner or purchase-head reader who thinks in
  margin and territory, not in individual SKUs. Confident but not pushy.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.35, reply: 0.45, spec_request: 0.2}
```

### `fmcg_distributor`
```yaml
segment: fmcg_distributor
allowed_angles: [sku_velocity, shelf_ready_format, range_breadth]
allowed_ctas: [price_list, catalogue, sample_pack, call]
approved_claim_ids: []
forbidden_topics: [FCL/container, halal_certification_uae_specific]
target_words_min: 90
target_words_max: 130
register_spec: >
  Practical, numbers-aware without stating numbers. Reader evaluates range fit
  and turnover potential, not novelty.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.35, reply: 0.45, spec_request: 0.2}
```

### `horeca_bakery`
```yaml
segment: horeca_bakery
allowed_angles: [batch_consistency, bulk_format_fit, cost_per_portion_framing]
allowed_ctas: [sample_pack, bulk_pricing, spec_sheet, call]
approved_claim_ids: []
forbidden_topics: [consumer_pack_sizes, retail_shelf_ean]
target_words_min: 80
target_words_max: 120
register_spec: >
  Practical, chef-to-supplier tone. Reader cares about consistency batch to
  batch and whether the format fits their kitchen, not brand story.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.3, reply: 0.5, spec_request: 0.2}
```

### `organic_gourmet`
```yaml
segment: organic_gourmet
allowed_angles: [clean_label, provenance, premium_positioning]
allowed_ctas: [catalogue, sample_pack, call]
approved_claim_ids: []
forbidden_topics: [FCL/container, bulk_commodity_pricing]
target_words_min: 80
target_words_max: 120
register_spec: >
  Warmer and more descriptive than the compliance-heavy segments, but still
  never states a price or a claim not in approved_claims. Story-forward,
  fact-anchored.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.4, reply: 0.4, spec_request: 0.2}
```

### `supermarket_retail`
```yaml
segment: supermarket_retail
allowed_angles: [category_fit, shelf_ready_packaging, promo_support]
allowed_ctas: [catalogue, listing_proposal, category_deck, sample_pack, meeting]
approved_claim_ids: []
forbidden_topics: [FCL/container, gym_nutrition_language]
target_words_min: 90
target_words_max: 130
register_spec: >
  Category-buyer register: shelf fit, EAN-readiness, and category logic first.
  Never bulk-commodity pricing language — this reader thinks in listings, not tonnage.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.35, reply: 0.45, spec_request: 0.2}
```

### `wellness_gym`
```yaml
segment: wellness_gym
allowed_angles: [nutrition_profile, clean_label, small_moq_fit]
allowed_ctas: [sample_pack, nutrition_sheet, retail_pricing, call]
approved_claim_ids: []
forbidden_topics: [FCL/container, import_documentation_language, bulk_commodity_pricing]
target_words_min: 80
target_words_max: 110
register_spec: >
  Warm, health-forward, small-buyer register. Never mention containers, FCL/LCL,
  or import paperwork — this reader buys retail-ready packs, not freight.
cadence_override: {day_0: true, day_7: true, day_21: true, day_30: true, day_90: true}
opportunity_weights: {catalogue_view: 0.4, reply: 0.4, spec_request: 0.2}
```

---

## 3. Rulebook v0 — guidance only, no hard checks yet (§6.1, §6.2)

This is the seed rulebook the first drafts in Stage 2 (P-B2) are written from.
Nothing here is a validator check until the segment sweep (§6.1d) and baseline
check (§6.1e) both pass — see VISION.md §6.1f. Treat every line below as a
starting hypothesis, not a fact about how Narayani writes.

### 3.1 Global structure (candidate positive rules — needs ≥2-segment evidence, §6.6d)

```yaml
greeting_form: "Dear <First Name>,"     # VISION.md §6.2's canonical form.
                                          # NOTE: if your Stage-0 interview or
                                          # sample emails favor "Hi <First Name>,"
                                          # instead, change this line AND the
                                          # matching regex in Stage 3's build —
                                          # keep this file and the Stage 3 prompt
                                          # in sync.
word_count_target: "segment-specific — see segment_playbook.target_words_min/max"
paragraph_structure: "max 3 paragraphs, 2-3 sentences each"
cta_position: "exactly one CTA, in the final paragraph, from the segment's allowed_ctas"
sign_off_form: |
  Best regards,
  <Sender First Name>
  Narayani Distributors
```

### 3.2 Global prohibitions (candidate hard denylists — no sample-count floor, §6.6c)

```yaml
banned_openers:
  - "I hope this email finds you well"
  - "I hope you are doing well"
  - "I trust this email finds you in good health"

banned_phrases:
  - "reaching out"
  - "leverage"
  - "synergy"
  - "circle back"

character_rules:
  - "no exclamation marks"
  - "no ALL-CAPS words"

past_relationship_verbs_denylist:   # grounding check (f), not rulebook, but
  - "as discussed"                   # listed here for visibility while writing
  - "your order"                     # the first drafts — a cold prospect has
  - "following our call"             # no prior relationship with Narayani.
  - "last time"
  - "we previously"
```

### 3.3 Owner-directed rules (no floor — recorded with owner as source, §6.6c)

```yaml
- rule: "No commercial term of any kind may appear in generated prose"
  source: "owner directive, VISION.md §10.8"
  enforced_by: "commercial_term_gate (Stage 3), not the rulebook checker"

- rule: "Every CTA is an offer to send something, never a statement of terms"
  source: "owner directive, VISION.md §5.3"
  enforced_by: "grounding check (g) + commercial_term_gate"
```

### 3.4 Stage 0 interview — seven questions to ask before drafting (§6.1a)

Answers here are guidance only; they harden only if the convergence loop
confirms them independently.

1. Greeting form? (default assumed above: "Dear <First Name>,")
2. Target length in words, roughly?
3. Should the email mention the recipient's city/country?
4. Sign-off shape? (default assumed above)
5. Anything that must always be said?
6. Anything that must never be said?
7. Paste one email you like the sound of (structure extracted only — greeting
   form, length, where the ask sits — never phrases; the source text is
   discarded after extraction per §6.1a).

### 3.5 What still needs owner input before Stage 2 can run (§6.7)

- [ ] The 15 real sample emails (or fewer — even 1 real one beats none, §6.7)
- [ ] Answers to the seven Stage-0 questions above
- [ ] Confirmation or correction of `target_words_min/max` per segment (§2 above)
- [ ] Confirmation or correction of `forbidden_topics` per segment (§2 above)
- [ ] `approved_claim_ids` populated per segment (see `approved-claims-synthetic.md`)
