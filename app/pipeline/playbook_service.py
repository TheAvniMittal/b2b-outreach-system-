import re
import yaml
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.db.models import Segment, RulebookConfig, Catalogue, AuditReason, record_audit_log
from app.pipeline.config_service import update_rulebook, get_active_rulebook

logger = logging.getLogger("playbook_service")


def parse_playbook_markdown(md_text: str) -> Dict[str, Any]:
    """
    Parses a playbook markdown document (e.g. segments-catalogues-rulebook-v0.md).
    Extracts:
    1. Per-segment playbooks (Section 2)
    2. Global rulebook configuration (Section 3)
    Enforces validation and rejects malformed documents.
    """
    if not md_text or not md_text.strip():
        raise ValueError("Playbook markdown content is empty.")

    # 1. Parse Segment YAML blocks
    # Match: ### `segment_name` or ### segment_name followed by ```yaml ... ```
    seg_pattern = re.compile(
        r"###\s+[`']?([a-zA-Z0-9_]+)[`']?\s*\n+```ya?ml\s*\n(.*?)\n```",
        re.DOTALL | re.IGNORECASE,
    )
    matches = seg_pattern.findall(md_text)
    
    parsed_segments: Dict[str, Dict[str, Any]] = {}
    for raw_seg_id, yaml_content in matches:
        seg_id = raw_seg_id.strip().lower()
        if seg_id == "unknown":
            continue
        try:
            data = yaml.safe_load(yaml_content)
            if not isinstance(data, dict):
                continue
            
            # Extract and validate fields
            t_min = data.get("target_words_min")
            t_max = data.get("target_words_max")
            
            # Validation bounds
            if t_min is not None and t_max is not None:
                try:
                    t_min = int(t_min)
                    t_max = int(t_max)
                    if t_min <= 0 or t_max < t_min:
                        raise ValueError(f"Invalid word count bounds for segment '{seg_id}': [{t_min}, {t_max}].")
                except ValueError as ve:
                    raise ValueError(f"Word count limits must be integers: {ve}")

            parsed_segments[seg_id] = {
                "target_words_min": t_min,
                "target_words_max": t_max,
                "allowed_angles": [str(a).strip() for a in data.get("allowed_angles", []) if str(a).strip()],
                "allowed_ctas": [str(c).strip() for c in data.get("allowed_ctas", []) if str(c).strip()],
                "forbidden_topics": [str(t).strip() for t in data.get("forbidden_topics", []) if str(t).strip()],
                "register_spec": str(data.get("register_spec", "")).strip(),
                "cadence_override": data.get("cadence_override"),
                "opportunity_weights": data.get("opportunity_weights"),
            }
        except Exception as err:
            logger.warning(f"Failed parsing segment block for '{seg_id}': {err}")
            raise ValueError(f"Error parsing YAML block for segment '{seg_id}': {err}")

    # 2. Parse Rulebook configuration blocks
    # Look for banned_openers, banned_phrases, max_paragraphs, max_sentences
    rulebook_updates: Dict[str, Any] = {}
    yaml_blocks = re.findall(r"```ya?ml\s*\n(.*?)\n```", md_text, re.DOTALL)
    for y_block in yaml_blocks:
        try:
            y_data = yaml.safe_load(y_block)
            if not isinstance(y_data, dict):
                continue
            if "banned_openers" in y_data:
                rulebook_updates["banned_openers"] = [
                    str(o).strip().lower() for o in y_data["banned_openers"] if str(o).strip()
                ]
            if "banned_phrases" in y_data:
                rulebook_updates["banned_phrases"] = [
                    str(p).strip().lower() for p in y_data["banned_phrases"] if str(p).strip()
                ]
            if "paragraph_structure" in y_data:
                # e.g. "max 3 paragraphs, 2-3 sentences each"
                p_text = str(y_data["paragraph_structure"]).lower()
                p_match = re.search(r"max\s+(\d+)\s+paragraphs?", p_text)
                if p_match:
                    rulebook_updates["max_paragraphs"] = int(p_match.group(1))
                s_match = re.search(r"(\d+)-(\d+)\s+sentences?", p_text)
                if s_match:
                    rulebook_updates["max_sentences_per_paragraph"] = int(s_match.group(2))
            if "max_paragraphs" in y_data:
                rulebook_updates["max_paragraphs"] = int(y_data["max_paragraphs"])
            if "max_sentences_per_paragraph" in y_data:
                rulebook_updates["max_sentences_per_paragraph"] = int(y_data["max_sentences_per_paragraph"])
        except Exception:
            pass

    if not parsed_segments and not rulebook_updates:
        raise ValueError(
            "Could not find any valid segment playbooks or rulebook YAML blocks in the markdown document. "
            "Please ensure headers match '### `segment_name`' with standard ```yaml blocks."
        )

    return {
        "segments": parsed_segments,
        "rulebook": rulebook_updates,
    }


def import_playbook_markdown(
    session: Session,
    md_text: str,
    operator_name: str = "operator",
) -> Dict[str, Any]:
    """
    Parses and imports playbook markdown, synchronizing PostgreSQL tables:
    1. Updates segment playbooks in `segments` table.
    2. Updates dynamic house rulebook in `rulebook_config` table.
    3. Records audit log for traceability.
    """
    parsed = parse_playbook_markdown(md_text)
    segments_dict = parsed["segments"]
    rb_dict = parsed["rulebook"]

    updated_segments = []
    # 1. Update Segment rows
    for seg_id, cfg in segments_dict.items():
        seg_obj = session.query(Segment).filter(Segment.segment == seg_id).first()
        if not seg_obj:
            logger.info(f"Segment '{seg_id}' not found in database, skipping.")
            continue

        prev_state = {
            "target_words_min": seg_obj.target_words_min,
            "target_words_max": seg_obj.target_words_max,
            "allowed_angles": seg_obj.allowed_angles,
            "allowed_ctas": seg_obj.allowed_ctas,
            "forbidden_topics": seg_obj.forbidden_topics,
        }

        if cfg["target_words_min"] is not None:
            seg_obj.target_words_min = cfg["target_words_min"]
        if cfg["target_words_max"] is not None:
            seg_obj.target_words_max = cfg["target_words_max"]
        if cfg["allowed_angles"]:
            seg_obj.allowed_angles = cfg["allowed_angles"]
        if cfg["allowed_ctas"]:
            seg_obj.allowed_ctas = cfg["allowed_ctas"]
        if cfg["forbidden_topics"] is not None:
            seg_obj.forbidden_topics = cfg["forbidden_topics"]
        if cfg["register_spec"]:
            seg_obj.register_spec = cfg["register_spec"]
        if cfg.get("cadence_override"):
            seg_obj.cadence_override = cfg["cadence_override"]
        if cfg.get("opportunity_weights"):
            seg_obj.opportunity_weights = cfg["opportunity_weights"]

        seg_obj.updated_at = datetime.now(timezone.utc)
        session.flush()

        prev_summary = f"w:{prev_state['target_words_min']}-{prev_state['target_words_max']}"[:50]
        new_summary = f"w:{seg_obj.target_words_min}-{seg_obj.target_words_max}"[:50]

        record_audit_log(
            session=session,
            actor=operator_name or "operator",
            action="update_segment_playbook",
            object_type="segment",
            object_id=seg_id,
            reason=AuditReason.RULEBOOK_UPDATED,
            prev_state=prev_summary,
            new_state=new_summary,
        )
        updated_segments.append(seg_id)

    # 2. Update Rulebook if present
    rb_version = None
    if rb_dict:
        active_rb = get_active_rulebook(session)
        openers = rb_dict.get("banned_openers", active_rb.banned_openers or [])
        phrases = rb_dict.get("banned_phrases", active_rb.banned_phrases or [])
        max_p = rb_dict.get("max_paragraphs", active_rb.max_paragraphs or 3)
        max_s = rb_dict.get("max_sentences_per_paragraph", active_rb.max_sentences_per_paragraph or 3)

        new_rb = update_rulebook(
            session=session,
            name=f"Rulebook from Markdown Import by {operator_name}",
            banned_openers=openers,
            banned_phrases=phrases,
            max_paragraphs=max_p,
            max_sentences_per_paragraph=max_s,
            operator_name=operator_name,
        )
        rb_version = new_rb.version

    session.commit()
    return {
        "success": True,
        "updated_segments_count": len(updated_segments),
        "updated_segments": updated_segments,
        "rulebook_version": rb_version,
    }


def update_single_segment_playbook(
    session: Session,
    segment_id: str,
    updates: Dict[str, Any],
    operator_name: str = "operator",
) -> Segment:
    """
    Guarded update for a single segment's configurable parameters:
    Allows editing target_words_min, target_words_max, allowed_angles,
    allowed_ctas, forbidden_topics, and register_spec.
    Locked segment keys cannot be renamed or deleted.
    """
    seg = session.query(Segment).filter(Segment.segment == segment_id).first()
    if not seg:
        raise ValueError(f"Segment '{segment_id}' not found.")

    prev_state = {
        "target_words_min": seg.target_words_min,
        "target_words_max": seg.target_words_max,
        "allowed_angles": seg.allowed_angles,
        "allowed_ctas": seg.allowed_ctas,
        "forbidden_topics": seg.forbidden_topics,
    }

    t_min = updates.get("target_words_min", seg.target_words_min)
    t_max = updates.get("target_words_max", seg.target_words_max)

    if t_min is not None and t_max is not None:
        if int(t_min) <= 0 or int(t_max) < int(t_min):
            raise ValueError(f"Target word minimum ({t_min}) must be positive and <= maximum ({t_max}).")
        seg.target_words_min = int(t_min)
        seg.target_words_max = int(t_max)

    if "allowed_angles" in updates and updates["allowed_angles"] is not None:
        seg.allowed_angles = [str(a).strip() for a in updates["allowed_angles"] if str(a).strip()]
    if "allowed_ctas" in updates and updates["allowed_ctas"] is not None:
        seg.allowed_ctas = [str(c).strip() for c in updates["allowed_ctas"] if str(c).strip()]
    if "forbidden_topics" in updates and updates["forbidden_topics"] is not None:
        seg.forbidden_topics = [str(t).strip() for t in updates["forbidden_topics"] if str(t).strip()]
    if "register_spec" in updates and updates["register_spec"] is not None:
        seg.register_spec = str(updates["register_spec"]).strip()

    seg.updated_at = datetime.now(timezone.utc)
    session.flush()

    prev_summary = f"w:{prev_state['target_words_min']}-{prev_state['target_words_max']}"[:50]
    new_summary = f"w:{seg.target_words_min}-{seg.target_words_max}"[:50]

    record_audit_log(
        session=session,
        actor=operator_name or "operator",
        action="update_segment_playbook",
        object_type="segment",
        object_id=segment_id,
        reason=AuditReason.RULEBOOK_UPDATED,
        prev_state=prev_summary,
        new_state=new_summary,
    )
    session.commit()
    return seg


def export_playbook_markdown(session: Session) -> str:
    """
    Exports the current active PostgreSQL segment playbooks and rulebook configuration
    as a clean, fully formatted Markdown document.
    """
    segments = session.query(Segment).order_by(Segment.segment).all()
    rb = get_active_rulebook(session)

    lines = [
        "# Segments, Catalogues, and Rulebook Active Configuration",
        "Narayani Distributors B2B Outreach System",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "---",
        "",
        "## 1. Segment → Catalogue mapping",
        "",
        "| segment (PK) | catalogue_file | catalogue_name |",
        "|---|---|---|",
    ]

    for s in segments:
        cat_file = "—"
        cat_name = "—"
        if s.catalogue_id:
            cat = session.query(Catalogue).filter(Catalogue.id == s.catalogue_id).first()
            if cat:
                cat_file = cat.catalogue_file or "—"
                cat_name = cat.name or "—"
        lines.append(f"| `{s.segment}` | `{cat_file}` | {cat_name} |")

    lines.extend([
        "",
        "---",
        "",
        "## 2. Per-segment playbooks",
        "",
    ])

    for s in segments:
        lines.append(f"### `{s.segment}`")
        lines.append("```yaml")
        lines.append(f"segment: {s.segment}")
        angles_str = ", ".join(s.allowed_angles or [])
        lines.append(f"allowed_angles: [{angles_str}]")
        ctas_str = ", ".join(s.allowed_ctas or [])
        lines.append(f"allowed_ctas: [{ctas_str}]")
        topics_str = ", ".join(s.forbidden_topics or [])
        lines.append(f"forbidden_topics: [{topics_str}]")
        lines.append(f"target_words_min: {s.target_words_min or 80}")
        lines.append(f"target_words_max: {s.target_words_max or 120}")
        if s.register_spec:
            lines.append("register_spec: >")
            for r_line in s.register_spec.split("\n"):
                lines.append(f"  {r_line.strip()}")
        else:
            lines.append("register_spec: >\n  B2B professional register.")
        lines.append("```")
        lines.append("")

    lines.extend([
        "---",
        "",
        f"## 3. Dynamic Rulebook (Version {rb.version} - Active)",
        "",
        "### 3.1 Global structure",
        "```yaml",
        'greeting_form: "Hi <First Name>,"',
        f'max_paragraphs: {rb.max_paragraphs or 3}',
        f'max_sentences_per_paragraph: {rb.max_sentences_per_paragraph or 3}',
        "```",
        "",
        "### 3.2 Global prohibitions",
        "```yaml",
        "banned_openers:",
    ])

    for o in rb.banned_openers or []:
        lines.append(f'  - "{o}"')

    lines.append("banned_phrases:")
    for p in rb.banned_phrases or []:
        lines.append(f'  - "{p}"')

    lines.extend([
        "character_rules:",
        '  - "no exclamation marks"',
        '  - "no ALL-CAPS words (except allowed: FSSAI, ISO, HACCP, APEDA, HALAL, GMP, GCC, EAN, SKU, MOQ, LLC, FMCG, B2B, NPOP)"',
        "```",
        "",
    ])

    return "\n".join(lines)
