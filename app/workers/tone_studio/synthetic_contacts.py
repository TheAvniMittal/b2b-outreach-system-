"""
Synthetic sample contacts for Tone Studio (§A).
STRICT REQUIREMENT: Real prospect data never goes to the Tone Studio.
These synthetic contacts are mock entities used solely for drafting sample emails
and calibrating tone within the Studio sandbox. Nothing is ever sent to them.
"""

from typing import Dict, Any

SYNTHETIC_CONTACTS: Dict[str, Dict[str, Any]] = {
    "distributor_wholesale": {
        "company_name": "Apex Wholesale Distribution Ltd",
        "contact_name": "Priya Sharma",
        "designation": "Head of Sourcing & Distribution",
        "segment": "distributor_wholesale",
        "country": "India",
        "verified_signal": "Expanding FMCG regional distribution network across Western India",
        "is_synthetic": True,
    },
    "fmcg_distributor": {
        "company_name": "Metro FMCG Network LLP",
        "contact_name": "Rajesh Patel",
        "designation": "Director of Category Procurement",
        "segment": "fmcg_distributor",
        "country": "India",
        "verified_signal": "Seeking high-velocity branded packaged food and staples for urban grocers",
        "is_synthetic": True,
    },
    "horeca_bakery": {
        "company_name": "Baker's Choice Commercial Kitchens",
        "contact_name": "Chef Vikram Rao",
        "designation": "Executive Pastry & Procurement Head",
        "segment": "horeca_bakery",
        "country": "India",
        "verified_signal": "Sourcing batch-consistent bulk flour, baking essentials, and specialty ingredients",
        "is_synthetic": True,
    },
    "importer_india": {
        "company_name": "Indo-Global Trade Consortium",
        "contact_name": "Arun Nair",
        "designation": "VP of Global Sourcing & Imports",
        "segment": "importer_india",
        "country": "India",
        "verified_signal": "Importing bulk certified food commodities and spice products",
        "is_synthetic": True,
    },
    "importer_uae": {
        "company_name": "Emirates Trade Link LLC",
        "contact_name": "Tariq Al-Mansoor",
        "designation": "Director of Food Services & Imports",
        "segment": "importer_uae",
        "country": "United Arab Emirates",
        "verified_signal": "Distributing to premium GCC hospitality and food retail chains",
        "is_synthetic": True,
    },
    "organic_gourmet": {
        "company_name": "Pure Earth Gourmet Provisions",
        "contact_name": "Ananya Deshmukh",
        "designation": "Lead Category Curator",
        "segment": "organic_gourmet",
        "country": "India",
        "verified_signal": "Expanding specialty clean-label, cold-pressed, and certified organic lines",
        "is_synthetic": True,
    },
    "supermarket_retail": {
        "company_name": "Prime Mart Supermarkets Ltd",
        "contact_name": "Sanjay Verma",
        "designation": "Senior Category Manager - Packaged Foods",
        "segment": "supermarket_retail",
        "country": "India",
        "verified_signal": "Looking for shelf-ready packaged goods with strong inventory turns",
        "is_synthetic": True,
    },
    "wellness_gym": {
        "company_name": "ProFit Wellness & Fitness Centers",
        "contact_name": "Rohan Sen",
        "designation": "Operations & Wellness Director",
        "segment": "wellness_gym",
        "country": "India",
        "verified_signal": "Stocking clean-label high-protein snacks and seed mixes for fitness clubs",
        "is_synthetic": True,
    },
    "unknown": {
        "company_name": "Sample B2B Partner Ltd",
        "contact_name": "Demo Contact",
        "designation": "Commercial Procurement Manager",
        "segment": "unknown",
        "country": "India",
        "verified_signal": "Exploring regional procurement opportunities",
        "is_synthetic": True,
    },
}


def get_synthetic_contact(segment: str) -> Dict[str, Any]:
    """
    Returns synthetic contact dictionary for a given segment.
    If segment is not in the predefined map, generates a safe synthetic mock.
    Never queries or accesses real prospect data.
    """
    if segment in SYNTHETIC_CONTACTS:
        return SYNTHETIC_CONTACTS[segment].copy()

    # Dynamic fallback for newly added segments
    human_name = segment.replace("_", " ").title()
    return {
        "company_name": f"Sample {human_name} Enterprises",
        "contact_name": "Demo Procurement Lead",
        "designation": "Sourcing Manager",
        "segment": segment,
        "country": "India",
        "verified_signal": f"Evaluating wholesale suppliers for {human_name} operations",
        "is_synthetic": True,
    }
