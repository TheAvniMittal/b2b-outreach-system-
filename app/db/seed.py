import datetime
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.db.session import get_db_session
from app.db.models import Catalogue, Segment, ApprovedClaim

CATALOGUES_DATA = [
    {
        "id": 1,
        "name": "Indian Importer Catalogue",
        "catalogue_file": "catalogue_importer_india.pdf",
        "summary": "Full compliance catalogue with HS codes, FSSAI licensing, and domestic export specs.",
        "registered_term_list": ["FSSAI", "ISO 22000", "APEDA", "spices", "turmeric", "rice", "Basmati"],
    },
    {
        "id": 2,
        "name": "UAE Importer Catalogue",
        "catalogue_file": "catalogue_importer_uae.pdf",
        "summary": "HALAL certified product range and GCC compliance documentation for UAE trade corridor.",
        "registered_term_list": ["HALAL", "GCC", "GMP", "spices", "dry fruits", "dates", "almonds", "cashews"],
    },
    {
        "id": 3,
        "name": "Distributor / Wholesaler Catalogue",
        "catalogue_file": "catalogue_distributor.pdf",
        "summary": "Wholesale distributor catalogue covering margin structures, bulk packing, and multi-state distribution.",
        "registered_term_list": ["namkeen", "papad", "ready-to-eat", "snacks", "wholesale", "territory"],
    },
    {
        "id": 4,
        "name": "FMCG Distributor Catalogue",
        "catalogue_file": "catalogue_fmcg.pdf",
        "summary": "Fast-moving packaged foods engineered for high shelf turnover and rapid retail reorder cycles.",
        "registered_term_list": ["FSSAI", "mixes", "pickles", "sauces", "shelf rotation", "FMCG"],
    },
    {
        "id": 5,
        "name": "HoReCa / Bakery Catalogue",
        "catalogue_file": "catalogue_horeca.pdf",
        "summary": "Commercial foodservice and bakery bulk ingredients offering batch-to-batch consistency for institutional kitchens.",
        "registered_term_list": ["flours", "sugars", "fillings", "bakery", "commercial kitchens", "foodservice"],
    },
    {
        "id": 6,
        "name": "Organic & Gourmet Catalogue",
        "catalogue_file": "catalogue_organic_gourmet.pdf",
        "summary": "Clean-label, NPOP certified organic foods and artisanal gourmet preserves in premium packaging.",
        "registered_term_list": ["India Organic", "NPOP", "cold-pressed oils", "organic honey", "artisanal preserves"],
    },
    {
        "id": 7,
        "name": "Supermarket / Retail Chain Catalogue",
        "catalogue_file": "catalogue_supermarket.pdf",
        "summary": "Shelf-ready retail packaged foods with EAN barcoding and listing compliance.",
        "registered_term_list": ["EAN", "barcode", "FSSAI", "shelf-ready", "listing terms"],
    },
    {
        "id": 8,
        "name": "Wellness & Gym Catalogue",
        # Duplicate of organic_gourmet under new name until dedicated file exists (§5.2b)
        "catalogue_file": "catalogue_organic_gourmet.pdf",
        "summary": "High-protein snacks, seed mixes, and clean-label bars in compact retail formats for fitness centers.",
        "registered_term_list": ["protein snacks", "clean-label", "seed mixes", "small MOQ", "India Organic", "NPOP"],
    },
]

SEGMENTS_DATA = [
    {
        "segment": "importer_india",
        "name": "Indian Importer",
        "catalogue_id": 1,
        "allowed_angles": ["compliance_first", "certification_led", "shelf_life_reliability"],
        "allowed_ctas": ["spec_sheet", "certifications_pack", "quotation_fob_cif", "sample_shipment", "call"],
        "forbidden_topics": ["retail_shelf_placement", "gym_nutrition_language", "consumer_pack_sizes"],
        "target_words_min": 100,
        "target_words_max": 150,
        "register_spec": (
            "Formal, document-forward. Assume the reader is compliance/import staff, not a retail buyer. "
            "Lead with certification and documentation readiness, not price."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.3, "reply": 0.5, "spec_request": 0.2},
    },
    {
        "segment": "importer_uae",
        "name": "UAE Importer",
        "catalogue_id": 2,
        "allowed_angles": ["compliance_first", "halal_certification", "gcc_documentation"],
        "allowed_ctas": ["spec_sheet", "certifications_pack", "quotation_fob_cif", "sample_shipment", "call"],
        "forbidden_topics": ["retail_shelf_placement", "gym_nutrition_language", "indian_domestic_compliance"],
        "target_words_min": 100,
        "target_words_max": 150,
        "register_spec": (
            "Formal, document-forward, same register as importer_india but never conflate Indian domestic "
            "certifications with GCC/HALAL requirements — this is a different compliance regime, not the same one restated."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.3, "reply": 0.5, "spec_request": 0.2},
    },
    {
        "segment": "distributor_wholesale",
        "name": "Distributor / Wholesale",
        "catalogue_id": 3,
        "allowed_angles": ["margin_opportunity", "territory_fit", "product_range_breadth"],
        "allowed_ctas": ["price_list", "territory_discussion", "catalogue", "call"],
        "forbidden_topics": ["FCL/container", "single_unit_retail_pricing"],
        "target_words_min": 90,
        "target_words_max": 130,
        "register_spec": (
            "Direct and business-to-business. Owner or purchase-head reader who thinks in margin and territory, "
            "not in individual SKUs. Confident but not pushy."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.35, "reply": 0.45, "spec_request": 0.2},
    },
    {
        "segment": "fmcg_distributor",
        "name": "FMCG Distributor",
        "catalogue_id": 4,
        "allowed_angles": ["sku_velocity", "shelf_ready_format", "range_breadth"],
        "allowed_ctas": ["price_list", "catalogue", "sample_pack", "call"],
        "forbidden_topics": ["FCL/container", "halal_certification_uae_specific"],
        "target_words_min": 90,
        "target_words_max": 130,
        "register_spec": (
            "Practical, numbers-aware without stating numbers. Reader evaluates range fit and turnover potential, not novelty."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.35, "reply": 0.45, "spec_request": 0.2},
    },
    {
        "segment": "horeca_bakery",
        "name": "HoReCa / Bakery",
        "catalogue_id": 5,
        "allowed_angles": ["batch_consistency", "bulk_format_fit", "cost_per_portion_framing"],
        "allowed_ctas": ["sample_pack", "bulk_pricing", "spec_sheet", "call"],
        "forbidden_topics": ["consumer_pack_sizes", "retail_shelf_ean"],
        "target_words_min": 80,
        "target_words_max": 120,
        "register_spec": (
            "Practical, chef-to-supplier tone. Reader cares about consistency batch to batch and whether the "
            "format fits their kitchen, not brand story."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.3, "reply": 0.5, "spec_request": 0.2},
    },
    {
        "segment": "organic_gourmet",
        "name": "Organic & Gourmet",
        "catalogue_id": 6,
        "allowed_angles": ["clean_label", "provenance", "premium_positioning"],
        "allowed_ctas": ["catalogue", "sample_pack", "call"],
        "forbidden_topics": ["FCL/container", "bulk_commodity_pricing"],
        "target_words_min": 80,
        "target_words_max": 120,
        "register_spec": (
            "Warmer and more descriptive than the compliance-heavy segments, but still never states a price or "
            "a claim not in approved_claims. Story-forward, fact-anchored."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.4, "reply": 0.4, "spec_request": 0.2},
    },
    {
        "segment": "supermarket_retail",
        "name": "Supermarket / Retail",
        "catalogue_id": 7,
        "allowed_angles": ["category_fit", "shelf_ready_packaging", "promo_support"],
        "allowed_ctas": ["catalogue", "listing_proposal", "category_deck", "sample_pack", "meeting"],
        "forbidden_topics": ["FCL/container", "gym_nutrition_language"],
        "target_words_min": 90,
        "target_words_max": 130,
        "register_spec": (
            "Category-buyer register: shelf fit, EAN-readiness, and category logic first. "
            "Never bulk-commodity pricing language — this reader thinks in listings, not tonnage."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.35, "reply": 0.45, "spec_request": 0.2},
    },
    {
        "segment": "wellness_gym",
        "name": "Wellness & Gym",
        "catalogue_id": 8,
        "allowed_angles": ["nutrition_profile", "clean_label", "small_moq_fit"],
        "allowed_ctas": ["sample_pack", "nutrition_sheet", "retail_pricing", "call"],
        "forbidden_topics": ["FCL/container", "import_documentation_language", "bulk_commodity_pricing"],
        "target_words_min": 80,
        "target_words_max": 110,
        "register_spec": (
            "Warm, health-forward, small-buyer register. Never mention containers, FCL/LCL, or import paperwork — "
            "this reader buys retail-ready packs, not freight."
        ),
        "cadence_override": {"day_0": True, "day_7": True, "day_21": True, "day_30": True, "day_90": True},
        "opportunity_weights": {"catalogue_view": 0.4, "reply": 0.4, "spec_request": 0.2},
    },
    {
        "segment": "unknown",
        "name": "Unknown Segment",
        "catalogue_id": None,
        "allowed_angles": [],
        "allowed_ctas": [],
        "forbidden_topics": [],
        "target_words_min": 0,
        "target_words_max": 0,
        "register_spec": "Unknown segment. Never queued. Human review only.",
        "cadence_override": {},
        "opportunity_weights": {},
    },
]

APPROVED_CLAIMS_DATA = [
    (1, "importer_india", "spices", "certification", "Our spice processing facility holds FSSAI certification."),
    (2, "importer_india", "spices", "certification", "Our facility is ISO 22000 certified for food safety management."),
    (3, "importer_india", "spices", "capability", "We supply whole and ground spices in bulk export packaging."),
    (4, "importer_india", "spices", "product_descriptive", "Our turmeric is sourced from farms in Maharashtra and Telangana."),
    (5, "importer_india", "rice", "capability", "We export Basmati and non-Basmati rice varieties for the import trade."),
    (6, "importer_india", "rice", "certification", "Our rice packing unit is APEDA registered."),
    (7, "importer_india", "general", "capability", "We prepare full export documentation including certificate of origin and phytosanitary certificates."),
    (8, "importer_india", "general", "commercial", "Standard FOB terms are available on request for confirmed orders."),
    (9, "importer_uae", "spices", "certification", "Our spice range is HALAL certified for GCC markets."),
    (10, "importer_uae", "spices", "capability", "We supply spice blends packed to GCC labelling requirements."),
    (11, "importer_uae", "dry_fruits", "product_descriptive", "Our dry fruit range includes almonds, cashews, and dates sourced for the UAE trade."),
    (12, "importer_uae", "general", "certification", "Our export facility holds GMP certification."),
    (13, "importer_uae", "general", "commercial", "CIF and FOB quotations for the UAE corridor are prepared on a per-order basis."),
    (14, "distributor_wholesale", "snacks", "capability", "We supply a full range of packaged Indian snacks for wholesale distribution."),
    (15, "distributor_wholesale", "snacks", "product_descriptive", "Our snack range includes namkeen, papad, and ready-to-eat mixes."),
    (16, "distributor_wholesale", "general", "capability", "We offer territory-based distribution arrangements for our full product range."),
    (17, "distributor_wholesale", "general", "commercial", "Margin structures and territory terms are discussed directly with qualified distributors."),
    (18, "fmcg_distributor", "packaged_foods", "capability", "Our packaged food range is designed for FMCG shelf rotation and reordering cycles."),
    (19, "fmcg_distributor", "packaged_foods", "product_descriptive", "Our range includes ready-to-cook mixes, pickles, and packaged sauces."),
    (20, "fmcg_distributor", "general", "certification", "Our packaging facility is FSSAI licensed."),
    (21, "horeca_bakery", "bakery_ingredients", "capability", "We supply bulk bakery ingredients including flours, sugars, and fillings in food-service formats."),
    (22, "horeca_bakery", "bakery_ingredients", "product_descriptive", "Our bakery ingredient range is formulated for consistent batch performance in commercial kitchens."),
    (23, "horeca_bakery", "general", "capability", "We supply in bulk formats sized for HoReCa and institutional kitchens."),
    (24, "horeca_bakery", "general", "commercial", "Bulk pricing for kitchen-scale orders is shared directly with the buyer."),
    (25, "organic_gourmet", "organic_foods", "certification", "Our organic range carries India Organic (NPOP) certification."),
    (26, "organic_gourmet", "organic_foods", "product_descriptive", "Our gourmet range includes cold-pressed oils, organic honey, and artisanal preserves."),
    (27, "organic_gourmet", "organic_foods", "capability", "We pack our organic range in retail-ready gourmet formats."),
    (28, "supermarket_retail", "packaged_foods", "capability", "Our packaged range is available in shelf-ready, EAN-coded retail packaging."),
    (29, "supermarket_retail", "packaged_foods", "certification", "Our retail packaging line is FSSAI licensed."),
    (30, "supermarket_retail", "general", "commercial", "Listing terms and promotional support are discussed with category buyers directly."),
    (31, "wellness_gym", "health_foods", "product_descriptive", "Our wellness range includes protein-rich snacks, seed mixes, and clean-label bars."),
    (32, "wellness_gym", "health_foods", "capability", "Our wellness range is packed in small-MOQ retail formats suited to gyms and health stores."),
    (33, "wellness_gym", "health_foods", "certification", "Our wellness range carries India Organic (NPOP) certification on eligible SKUs."),
]


def seed_all(session: Session) -> None:
    # 1. Catalogues (insert-only-if-missing, no overwriting)
    for cat_dict in CATALOGUES_DATA:
        existing = session.query(Catalogue).filter_by(id=cat_dict["id"]).first()
        if not existing:
            cat = Catalogue(**cat_dict)
            session.add(cat)
    session.flush()

    # 2. Segments (insert-only-if-missing, no overwriting)
    for seg_dict in SEGMENTS_DATA:
        existing = session.query(Segment).filter_by(segment=seg_dict["segment"]).first()
        if not existing:
            seg = Segment(**seg_dict)
            session.add(seg)
    session.flush()

    # 3. Approved Claims (insert-only-if-missing, no overwriting)
    v_from = datetime.date(2026, 1, 1)
    reviewed = datetime.date(2026, 9, 7)
    claim_ids_by_seg: Dict[str, List[int]] = {}

    for claim_id, seg, cat, ctype, text in APPROVED_CLAIMS_DATA:
        existing = session.query(ApprovedClaim).filter_by(id=claim_id).first()
        if not existing:
            ac = ApprovedClaim(
                id=claim_id,
                segment=seg,
                product_category=cat,
                claim_type=ctype,
                text=text,
                valid_from=v_from,
                valid_to=None,
                last_reviewed_at=reviewed,
                retention_class="operational_config",
            )
            session.add(ac)

        claim_ids_by_seg.setdefault(seg, []).append(claim_id)

    session.flush()

    # Update segments with their approved_claim_ids if not already set
    for seg_slug, ids in claim_ids_by_seg.items():
        seg_obj = session.query(Segment).filter_by(segment=seg_slug).first()
        if seg_obj and not seg_obj.approved_claim_ids:
            seg_obj.approved_claim_ids = ids

    session.commit()


if __name__ == "__main__":
    with get_db_session() as session:
        seed_all(session)
        print("Seed data successfully inserted.")
