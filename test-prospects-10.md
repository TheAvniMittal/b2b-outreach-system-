# Test Prospects — 10 rows
Narayani Distributors B2B Outreach System · supports VISION.md §18.3, §7.6, §12.1

Ten synthetic-but-realistic prospect rows for Stage 1 (CSV import + verification
ladder) and Stage 5 (seed sending). `segment` is a **required column** — this is
v1's deliberate stand-in for the deferred A6 SegmentClassifier (§18.3): a row
missing `segment` is rejected at import, never guessed.

**Email addresses match the Stage 5 seed-sending allowlist exactly**
(`avnimittal900+prospect1@gmail.com` through `+prospect9@gmail.com`, plus the
bare `avnimittal900@gmail.com`). Do not change these addresses without also
updating the allowlist in the Stage 5 prompt — the provider adapter will
reject anything not on it while `LIVE_EMAIL_SENDING=false` (§18.2), which is
correct behaviour, not a bug, if you edit one file and not the other.

Company names, domains and notes are invented for testing only.

---

## CSV (import directly via app/pipeline/import_csv.py)

```csv
row_id,company_name,segment,company_domain,contact_name,designation,country,email,product_interest,notes
1,Al Faisal Import Trading,importer_india,alfaisalimport.example,Rohan Mehta,Import Manager,India,avnimittal900+prospect1@gmail.com,spices and rice,Looking to diversify spice suppliers ahead of Q1
2,Gulf Horizon Foods LLC,importer_uae,gulfhorizonfoods.example,Fatima Al Suwaidi,Compliance Officer,United Arab Emirates,avnimittal900+prospect2@gmail.com,dry fruits and spice blends,Requires HALAL documentation upfront
3,Shree Balaji Distributors,distributor_wholesale,shreebalajidist.example,Vikram Rao,Owner,India,avnimittal900+prospect3@gmail.com,packaged snacks wholesale,Covers three states, looking to add a new supplier line
4,Metro FMCG Traders,fmcg_distributor,metrofmcgtraders.example,Anjali Desai,Purchase Head,India,avnimittal900+prospect4@gmail.com,ready-to-cook mixes and pickles,High SKU turnover retailer network
5,Coastal Bakery Supplies,horeca_bakery,coastalbakerysupplies.example,Thomas Fernandes,Purchase Manager,India,avnimittal900+prospect5@gmail.com,bulk bakery ingredients,Supplies five hotel kitchens in the region
6,Pure Roots Organic Store,organic_gourmet,purerootsorganic.example,Meera Iyer,Owner,India,avnimittal900+prospect6@gmail.com,organic oils and preserves,Small gourmet retail chain, three outlets
7,Greenfield Supermarkets,supermarket_retail,greenfieldsuper.example,Karan Shah,Category Buyer,India,avnimittal900+prospect7@gmail.com,packaged foods category,Evaluating new vendors for Q2 category reset
8,FitLife Wellness Studio,wellness_gym,fitlifewellness.example,Priya Nair,Nutrition Lead,India,avnimittal900+prospect8@gmail.com,protein snacks and clean-label bars,Small MOQ required, single-location gym
9,Noor Al Madina Trading,importer_india,noormadinatrading.example,Ahmed Qureshi,Import Manager,India,avnimittal900+prospect9@gmail.com,rice and spices,Second contact at an existing importer segment for test coverage
10,Sunrise Wholesale Mart,distributor_wholesale,sunrisewholesale.example,Deepak Kulkarni,Purchase Head,India,avnimittal900@gmail.com,general grocery wholesale,Bare seed address — used as the primary owned test mailbox
```

---

## Notes for Stage 1 / Stage 5 test authors

- All 10 rows use `gmail.com` as the email host (via `+` addressing), so the
  verification ladder's MX-lookup stage (§12.1 stage 1) will pass for all 10
  regardless of the invented `company_domain` values — `company_domain` is
  metadata only in v1, not the address the system emails.
- Segment coverage: `importer_india` ×2, `importer_uae` ×1,
  `distributor_wholesale` ×2, `fmcg_distributor` ×1, `horeca_bakery` ×1,
  `organic_gourmet` ×1, `supermarket_retail` ×1, `wellness_gym` ×1 — every
  segment from `segments-catalogues-rulebook-v0.md` appears at least once,
  which matters for Stage 4's per-segment draft test and the eventual
  segment-sweep convergence check (§6.1d).
- Row 10 uses the bare seed address deliberately — treat it as "yourself" when
  testing reply ingestion end to end in Stage 5 step 4.
- None of these are real companies. If you later import a real prospect list,
  keep the CSV header identical so `import_csv.py` doesn't need changes.
