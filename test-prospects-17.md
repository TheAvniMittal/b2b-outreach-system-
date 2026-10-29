# Test Prospects — 17 rows
Narayani Distributors B2B Outreach System · supports VISION.md §18.3, §7.6, §12.1

Seventeen synthetic-but-realistic prospect rows for Stage 1 (CSV import +
verification ladder) and Stage 5 (seed sending). `segment` is a **required
column** — this is v1's deliberate stand-in for the deferred A6
SegmentClassifier (§18.3): a row missing `segment` is rejected at import,
never guessed.

**Rows 1–10** match the original Stage 5 seed-sending allowlist exactly
(`avnimittal900+prospect1@gmail.com` through `+prospect9@gmail.com`, plus the
bare `avnimittal900@gmail.com`).

**Rows 11–17 are NEW** — seven additional real, separate recipient addresses
added for a wider test round:
`avnimittal175@gmail.com`, `bhoomi.thakur168@gmail.com`,
`abhinavusesthis@gmail.com`, `ayushofficialuse@gmail.com`,
`abhinavarcade70@gmail.com`, `mittalavni019@gmail.com`,
`mittalavni89@gmail.com`.

> ⚠️ **Action required before sending to rows 11–17:** these are separate
> inboxes, not `+alias` addresses on one account. The Stage 5 prompt's
> hard-coded allowlist (in the sending code, not just this CSV) must be
> updated to include all seven, or the provider adapter will correctly
> *reject* them as "not on the allowlist" — that's the safety feature working
> as designed, not a bug. Tell Antigravity explicitly: *"Add these 7 addresses
> to the test allowlist in the sending code."*
>
> Also confirm each of these seven people knows a test email is coming and is
> okay receiving it — several are outside your own mailbox, so this is real
> outreach to real inboxes, even if the content is a test.

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
11,Avni Test Imports,importer_india,avnitestimports.example,Avni Mittal,Import Manager,India,avnimittal175@gmail.com,spices and pulses,Additional real-inbox test recipient — round 2
12,Bhoomi Retail Ventures,supermarket_retail,bhoomiretail.example,Bhoomi Thakur,Category Buyer,India,bhoomi.thakur168@gmail.com,packaged foods category,Additional real-inbox test recipient — round 2
13,Abhinav Foods Trading,fmcg_distributor,abhinavfoodstrading.example,Abhinav Sharma,Purchase Head,India,abhinavusesthis@gmail.com,ready-to-cook mixes,Additional real-inbox test recipient — round 2
14,Ayush Organic Mart,organic_gourmet,ayushorganicmart.example,Ayush Verma,Owner,India,ayushofficialuse@gmail.com,organic oils and preserves,Additional real-inbox test recipient — round 2
15,Abhinav Arcade Distributors,distributor_wholesale,abhinavarcadedist.example,Abhinav Rane,Owner,India,abhinavarcade70@gmail.com,packaged snacks wholesale,Additional real-inbox test recipient — round 2
16,Mittal Bakery Supplies,horeca_bakery,mittalbakerysupplies.example,Avni Mittal,Purchase Manager,India,mittalavni019@gmail.com,bulk bakery ingredients,Additional real-inbox test recipient — round 2
17,Mittal Wellness Studio,wellness_gym,mittalwellness.example,Avni Mittal,Nutrition Lead,India,mittalavni89@gmail.com,protein snacks and clean-label bars,Additional real-inbox test recipient — round 2
```

---

## Notes for Stage 1 / Stage 5 test authors

- Rows 1–10 all use `gmail.com` as the email host (via `+` addressing on one
  account), so the verification ladder's MX-lookup stage (§12.1 stage 1)
  passes for all 10 regardless of the invented `company_domain` values —
  `company_domain` is metadata only in v1, not the address the system emails.
- Rows 11–17 also use `gmail.com`-hosted addresses, so they'll pass the same
  MX-lookup stage, but they are **separate real inboxes** — treat replies
  from them as genuine test replies, not synthetic data.
- Segment coverage across all 17 rows: `importer_india` ×3,
  `importer_uae` ×1, `distributor_wholesale` ×4, `fmcg_distributor` ×2,
  `horeca_bakery` ×2, `organic_gourmet` ×2, `supermarket_retail` ×2,
  `wellness_gym` ×2 — every segment from `segments-catalogues-rulebook-v0.md`
  still appears at least once, now with extra coverage on several segments.
- Row 10 remains the bare seed address, "yourself," for reply-ingestion
  testing end to end.
- Before running Stage 5 seed sending against rows 11–17: update the
  allowlist in the sending code to include all seven new addresses.
- None of these are real companies. If you later import a real prospect
  list, keep the CSV header identical so `import_csv.py` doesn't need
  changes.
