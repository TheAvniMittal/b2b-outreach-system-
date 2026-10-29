# Synthetic approved_claims — 33 rows
Narayani Distributors B2B Outreach System · supports VISION.md §10.7, §5.3

**This table is synthetic placeholder data**, built so P0/Stage 1/Stage 3 have
something real to seed and test against before the real approved-claims list
(owner's highest-leverage open item, VISION.md §20.1) exists. Replace it with
Narayani's real, signed-off claims before P7 — nothing in this file should
ever reach a real prospect.

Schema (VISION.md §10.7): `id, segment, product_category, claim_type, text,
valid_from, valid_to, last_reviewed_at`.

`claim_type` is one of `capability | product_descriptive | certification |
commercial`. **Rows typed `commercial` are never eligible for a Fact Pack** —
the assembler filters them out before A2 ever sees them (§10.8). They exist
here only so Stage 3's grounding tests have a real commercial-claim row to
confirm gets excluded.

`valid_to: null` means currently active. `last_reviewed_at` uses the document
date (2026-09-07) as a stand-in "today."

---

## CSV (import directly into the `approved_claims` table)

```csv
id,segment,product_category,claim_type,text,valid_from,valid_to,last_reviewed_at
1,importer_india,spices,certification,Our spice processing facility holds FSSAI certification.,2026-01-01,,2026-09-07
2,importer_india,spices,certification,Our facility is ISO 22000 certified for food safety management.,2026-01-01,,2026-09-07
3,importer_india,spices,capability,We supply whole and ground spices in bulk export packaging.,2026-01-01,,2026-09-07
4,importer_india,spices,product_descriptive,Our turmeric is sourced from farms in Maharashtra and Telangana.,2026-01-01,,2026-09-07
5,importer_india,rice,capability,We export Basmati and non-Basmati rice varieties for the import trade.,2026-01-01,,2026-09-07
6,importer_india,rice,certification,Our rice packing unit is APEDA registered.,2026-01-01,,2026-09-07
7,importer_india,general,capability,We prepare full export documentation including certificate of origin and phytosanitary certificates.,2026-01-01,,2026-09-07
8,importer_india,general,commercial,Standard FOB terms are available on request for confirmed orders.,2026-01-01,,2026-09-07
9,importer_uae,spices,certification,Our spice range is HALAL certified for GCC markets.,2026-01-01,,2026-09-07
10,importer_uae,spices,capability,We supply spice blends packed to GCC labelling requirements.,2026-01-01,,2026-09-07
11,importer_uae,dry_fruits,product_descriptive,Our dry fruit range includes almonds, cashews, and dates sourced for the UAE trade.,2026-01-01,,2026-09-07
12,importer_uae,general,certification,Our export facility holds GMP certification.,2026-01-01,,2026-09-07
13,importer_uae,general,commercial,CIF and FOB quotations for the UAE corridor are prepared on a per-order basis.,2026-01-01,,2026-09-07
14,distributor_wholesale,snacks,capability,We supply a full range of packaged Indian snacks for wholesale distribution.,2026-01-01,,2026-09-07
15,distributor_wholesale,snacks,product_descriptive,Our snack range includes namkeen, papad, and ready-to-eat mixes.,2026-01-01,,2026-09-07
16,distributor_wholesale,general,capability,We offer territory-based distribution arrangements for our full product range.,2026-01-01,,2026-09-07
17,distributor_wholesale,general,commercial,Margin structures and territory terms are discussed directly with qualified distributors.,2026-01-01,,2026-09-07
18,fmcg_distributor,packaged_foods,capability,Our packaged food range is designed for FMCG shelf rotation and reordering cycles.,2026-01-01,,2026-09-07
19,fmcg_distributor,packaged_foods,product_descriptive,Our range includes ready-to-cook mixes, pickles, and packaged sauces.,2026-01-01,,2026-09-07
20,fmcg_distributor,general,certification,Our packaging facility is FSSAI licensed.,2026-01-01,,2026-09-07
21,horeca_bakery,bakery_ingredients,capability,We supply bulk bakery ingredients including flours, sugars, and fillings in food-service formats.,2026-01-01,,2026-09-07
22,horeca_bakery,bakery_ingredients,product_descriptive,Our bakery ingredient range is formulated for consistent batch performance in commercial kitchens.,2026-01-01,,2026-09-07
23,horeca_bakery,general,capability,We supply in bulk formats sized for HoReCa and institutional kitchens.,2026-01-01,,2026-09-07
24,horeca_bakery,general,commercial,Bulk pricing for kitchen-scale orders is shared directly with the buyer.,2026-01-01,,2026-09-07
25,organic_gourmet,organic_foods,certification,Our organic range carries India Organic (NPOP) certification.,2026-01-01,,2026-09-07
26,organic_gourmet,organic_foods,product_descriptive,Our gourmet range includes cold-pressed oils, organic honey, and artisanal preserves.,2026-01-01,,2026-09-07
27,organic_gourmet,organic_foods,capability,We pack our organic range in retail-ready gourmet formats.,2026-01-01,,2026-09-07
28,supermarket_retail,packaged_foods,capability,Our packaged range is available in shelf-ready, EAN-coded retail packaging.,2026-01-01,,2026-09-07
29,supermarket_retail,packaged_foods,certification,Our retail packaging line is FSSAI licensed.,2026-01-01,,2026-09-07
30,supermarket_retail,general,commercial,Listing terms and promotional support are discussed with category buyers directly.,2026-01-01,,2026-09-07
31,wellness_gym,health_foods,product_descriptive,Our wellness range includes protein-rich snacks, seed mixes, and clean-label bars.,2026-01-01,,2026-09-07
32,wellness_gym,health_foods,capability,Our wellness range is packed in small-MOQ retail formats suited to gyms and health stores.,2026-01-01,,2026-09-07
33,wellness_gym,health_foods,certification,Our wellness range carries India Organic (NPOP) certification on eligible SKUs.,2026-01-01,,2026-09-07
```

---

## Notes for Stage 1 / Stage 3 test authors

- **Cross-segment certification test (Stage 3 golden set):** row 33 gives
  `wellness_gym` an NPOP certification claim — but there is **no FSSAI, HALAL,
  ISO, HACCP, BRC, GMP, or APEDA claim seeded for `wellness_gym`**. Use any of
  those certification tokens in a `wellness_gym` draft to trigger grounding
  check (c)'s intended failure (a certification token with no matching
  approved_claim for that segment).
- **Commercial-claim exclusion test:** rows 8, 13, 17, 24, and 30 are
  `claim_type: commercial`. Confirm your ContextAssembler filters all five out
  of every Fact Pack, regardless of segment match — assert the brief assembled
  for e.g. `importer_india` never contains row 8's text.
- **Two certifications for one segment:** `importer_india` (rows 1, 2) and
  `importer_uae` (rows 9, 12) each have two different certification claims —
  useful for testing that the grounding allowlist is additive, not
  single-value.
