#!/usr/bin/env python3
"""
Dataset Registry Scanner
Scans a directory of datasets and builds a JSON registry with auto-detected metadata.
Priority order for each field:
  1. dataset_info.yaml in the dataset folder (author-provided, highest trust)
  2. README.md / LICENSE heuristics
  3. File extension / directory structure heuristics
  4. Papers with Code API
  5. Hugging Face datasets API
  6. Filesystem metadata (owner, size, date)
"""

import os
import json
import pwd
import stat
import subprocess
import hashlib
import re
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import requests
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DATASETS_ROOT = os.environ.get("DATASETS_ROOT", "/home/janus/iwi5-datasets")
OUTPUT_JSON   = os.environ.get("OUTPUT_JSON",   os.path.join(DATASETS_ROOT, ".registry", "datasets.json"))
TEMPLATE_DIR  = Path(__file__).parent / "templates"

# ---------------------------------------------------------------------------
# File-type heuristics
# ---------------------------------------------------------------------------

EXT_3D = {".nii", ".nii.gz", ".dcm", ".mha", ".nrrd", ".mgz", ".mnc", ".hdr", ".img"}
EXT_2D = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
EXT_TEXT = {".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html"}
EXT_AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
EXT_VIDEO = {".mp4", ".avi", ".mov", ".mkv"}
EXT_MESH  = {".obj", ".ply", ".stl", ".off", ".glb", ".gltf"}
EXT_POINTCLOUD = {".pcd", ".las", ".laz"}

ANNOTATION_SIGNALS = {
    "segmentation_masks": ["mask", "segmentation", "seg", "label_map"],
    "bounding_boxes":     ["bbox", "bboxes", "annotations.json", "labels.json", "coco", "pascal_voc"],
    "class_labels":       ["labels", "classes", "label", "class"],
    "captions":           ["caption", "captions", "text", "report", "description"],
    "keypoints":          ["keypoint", "keypoints", "pose", "landmark"],
    "depth_maps":         ["depth", "disparity"],
    "optical_flow":       ["flow", "optical_flow"],
    "point_clouds":       [".pcd", ".las", ".laz", "pointcloud", "point_cloud"],
}

DOMAIN_SIGNALS = {
    "medical_imaging":  ["dcm", "dicom", "nii", "mri", "ct", "xray", "x-ray", "cxr", "mimic", "adni", "retina", "fundus", "pathology"],
    "art":              ["painting", "artwork", "museum", "deart", "herculaneum", "sniffyart"],
    "remote_sensing":   ["satellite", "aerial", "drone", "lidar", "sar"],
    "natural_images":   ["imagenet", "places", "coco", "openimages", "objects365"],
    "text":             ["text", "nlp", "script", "document", "ocr"],
    "multimodal":       ["multimodal", "multi-modal", "multi_modal"],
    "olfactory":        ["odor", "smell", "fragrant", "olfact"],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_owner(path: str) -> str:
    try:
        uid = os.stat(path).st_uid
        return pwd.getpwuid(uid).pw_name
    except Exception:
        return str(os.stat(path).st_uid)


def get_size_gb(path: str) -> float:
    try:
        result = subprocess.run(
            ["du", "-sb", path], capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            bytes_ = int(result.stdout.split()[0])
            return round(bytes_ / (1024**3), 2)
    except Exception:
        pass
    return 0.0


def get_added_date(path: str) -> str:
    try:
        ts = os.stat(path).st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def collect_extensions(path: str, max_files: int = 5000) -> dict[str, int]:
    """Walk up to max_files files and count extensions."""
    counts: dict[str, int] = {}
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            ext = Path(f).suffix.lower()
            # handle .nii.gz
            if f.endswith(".nii.gz"):
                ext = ".nii.gz"
            counts[ext] = counts.get(ext, 0) + 1
            n += 1
            if n >= max_files:
                return counts
    return counts


def collect_dir_names(path: str, depth: int = 3) -> list[str]:
    """Collect all directory names up to depth levels deep (lowercased)."""
    names = []
    for root, dirs, _ in os.walk(path):
        level = root.replace(path, "").count(os.sep)
        if level >= depth:
            dirs[:] = []
            continue
        names.extend(d.lower() for d in dirs)
    return names


def read_readme(path: str) -> str:
    for fname in ["README.md", "readme.md", "README.txt", "readme.txt", "README"]:
        fpath = os.path.join(path, fname)
        if os.path.isfile(fpath):
            try:
                return Path(fpath).read_text(errors="replace")[:4000]
            except Exception:
                pass
    return ""


def read_license(path: str) -> str:
    for fname in ["LICENSE", "LICENSE.md", "LICENSE.txt", "license.txt"]:
        fpath = os.path.join(path, fname)
        if os.path.isfile(fpath):
            try:
                return Path(fpath).read_text(errors="replace")[:500]
            except Exception:
                pass
    return ""


def read_yaml_info(path: str) -> dict:
    fpath = os.path.join(path, "dataset_info.yaml")
    if not os.path.isfile(fpath):
        return {}
    try:
        with open(fpath) as f:
            data = yaml.safe_load(f)
        return data or {}
    except Exception as e:
        log.warning("Could not parse dataset_info.yaml in %s: %s", path, e)
        return {}


def extract_yaml_frontmatter(text: str) -> dict:
    """Extract YAML front matter from README (--- ... ---)."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if m:
        try:
            return yaml.safe_load(m.group(1)) or {}
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Heuristic inference
# ---------------------------------------------------------------------------

def infer_modality(ext_counts: dict, dir_names: list[str], readme: str) -> str:
    readme_low = readme.lower()
    # explicit keywords
    if any(k in readme_low for k in ["3d", "volumetric", "voxel", "dicom", "nifti", ".dcm"]):
        return "3D"
    if any(k in readme_low for k in ["2d", "image", "photograph"]):
        return "2D"
    # extension counts
    count_3d = sum(ext_counts.get(e, 0) for e in EXT_3D)
    count_2d = sum(ext_counts.get(e, 0) for e in EXT_2D)
    count_mesh = sum(ext_counts.get(e, 0) for e in EXT_MESH)
    count_pc   = sum(ext_counts.get(e, 0) for e in EXT_POINTCLOUD)
    if count_3d > 0:
        return "3D"
    if count_mesh + count_pc > 0:
        return "3D"
    if count_2d > 0:
        return "2D"
    # directory names
    for d in dir_names:
        if any(k in d for k in ["volume", "dicom", "nifti", "3d"]):
            return "3D"
    return "unknown"


def infer_domain(name: str, readme: str, ext_counts: dict) -> str:
    combined = (name + " " + readme).lower()
    for domain, signals in DOMAIN_SIGNALS.items():
        for sig in signals:
            if sig in combined:
                return domain
    # fallback from extensions
    if sum(ext_counts.get(e, 0) for e in EXT_3D) > 0:
        return "medical_imaging"
    if sum(ext_counts.get(e, 0) for e in EXT_AUDIO) > 0:
        return "audio"
    if sum(ext_counts.get(e, 0) for e in EXT_VIDEO) > 0:
        return "video"
    return "unknown"


def infer_annotations(ext_counts: dict, dir_names: list[str], readme: str) -> list[str]:
    found = []
    combined = " ".join(dir_names) + " " + readme.lower()
    for ann_type, signals in ANNOTATION_SIGNALS.items():
        for sig in signals:
            if sig in combined:
                found.append(ann_type)
                break
    # check extensions too
    if any(ext_counts.get(e, 0) > 0 for e in EXT_MESH):
        if "point_clouds" not in found:
            found.append("3d_mesh")
    if any(ext_counts.get(e, 0) > 0 for e in EXT_POINTCLOUD):
        if "point_clouds" not in found:
            found.append("point_clouds")
    return list(dict.fromkeys(found))  # deduplicate preserving order


def infer_tasks(annotations: list[str], readme: str, domain: str) -> list[str]:
    tasks = []
    readme_low = readme.lower()
    task_keywords = {
        "classification":       ["classification", "class label", "category"],
        "segmentation":         ["segmentation", "segment", "mask"],
        "detection":            ["detection", "bounding box", "bbox", "object detection"],
        "retrieval":            ["retrieval", "search", "image-text", "cross-modal"],
        "generation":           ["generation", "synthesis", "gan", "diffusion"],
        "report_generation":    ["report generation", "radiology report", "captioning"],
        "depth_estimation":     ["depth estimation", "depth map"],
        "pose_estimation":      ["pose estimation", "keypoint"],
        "reconstruction":       ["reconstruction", "3d reconstruction"],
        "registration":         ["registration", "alignment"],
    }
    for task, signals in task_keywords.items():
        if any(s in readme_low for s in signals):
            tasks.append(task)
    # infer from annotations
    ann_task_map = {
        "segmentation_masks": "segmentation",
        "bounding_boxes":     "detection",
        "class_labels":       "classification",
        "captions":           "retrieval",
        "keypoints":          "pose_estimation",
        "depth_maps":         "depth_estimation",
    }
    for ann, task in ann_task_map.items():
        if ann in annotations and task not in tasks:
            tasks.append(task)
    return tasks


def infer_num_samples(ext_counts: dict, modality: str) -> int | None:
    if modality == "2D":
        count = sum(ext_counts.get(e, 0) for e in EXT_2D)
        return count if count > 0 else None
    if modality == "3D":
        count = sum(ext_counts.get(e, 0) for e in EXT_3D)
        return count if count > 0 else None
    return None


def infer_license(readme: str, license_text: str, frontmatter: dict) -> str:
    if frontmatter.get("license"):
        return str(frontmatter["license"])
    combined = (readme + " " + license_text).lower()
    license_map = {
        "mit":           "MIT",
        "apache 2.0":    "Apache-2.0",
        "apache-2.0":    "Apache-2.0",
        "gpl":           "GPL",
        "cc by 4.0":     "CC-BY-4.0",
        "cc by-nc":      "CC-BY-NC",
        "cc0":           "CC0",
        "physionet":     "PhysioNet Credentialed",
        "non-commercial":"Non-Commercial",
    }
    for key, val in license_map.items():
        if key in combined:
            return val
    return "unknown"


def infer_is_public(license_str: str, readme: str) -> bool | None:
    readme_low = readme.lower()
    private_signals = ["credential", "non-commercial", "restricted", "agreement", "request access",
                       "physionet", "sign up", "login required", "permission"]
    if any(s in readme_low for s in private_signals):
        return False
    if any(s in license_str.lower() for s in ["cc0", "mit", "apache", "cc-by-4", "cc by 4"]):
        return True
    return None


# ---------------------------------------------------------------------------
# External API lookups
# ---------------------------------------------------------------------------

_pwc_cache: dict[str, dict] = {}


def lookup_papers_with_code(name: str) -> dict:
    if name in _pwc_cache:
        return _pwc_cache[name]
    result = {}
    try:
        resp = requests.get(
            "https://paperswithcode.com/api/v1/datasets/",
            params={"name": name},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                r = results[0]
                result = {
                    "description": r.get("full_name") or r.get("name"),
                    "pwc_url":     r.get("url"),
                    "paper_url":   r.get("paper", {}).get("url") if r.get("paper") else None,
                }
    except Exception as e:
        log.debug("PwC lookup failed for %s: %s", name, e)
    _pwc_cache[name] = result
    return result


_hf_cache: dict[str, dict] = {}


def lookup_huggingface(name: str) -> dict:
    if name in _hf_cache:
        return _hf_cache[name]
    result = {}
    try:
        resp = requests.get(
            "https://huggingface.co/api/datasets",
            params={"search": name, "limit": 3},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                # pick the closest match by name
                match = next(
                    (d for d in data if name.lower() in d.get("id", "").lower()), data[0]
                )
                tags = match.get("tags", [])
                tasks_hf = [t.replace("task_categories:", "") for t in tags if t.startswith("task_categories:")]
                result = {
                    "hf_id":       match.get("id"),
                    "description": match.get("description"),
                    "tasks_hf":    tasks_hf,
                    "license_hf":  next((t.replace("license:", "") for t in tags if t.startswith("license:")), None),
                }
    except Exception as e:
        log.debug("HF lookup failed for %s: %s", name, e)
    _hf_cache[name] = result
    return result


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------

def generate_template(dataset_path: str, partial: dict) -> None:
    """Write a pre-filled dataset_info.yaml template for missing fields."""
    fpath = os.path.join(dataset_path, "dataset_info.yaml")
    if os.path.exists(fpath):
        return  # already has one, don't overwrite

    missing = partial.get("missing_fields", [])
    if not missing:
        return

    lines = [
        "# Dataset information — please fill in fields marked with ???",
        "# Then re-run the scanner: scanner.py",
        "",
        f"name: {partial.get('name', '???')}",
        f"description: {partial.get('description') or '???'}",
        f"modality: {partial.get('modality') or '???'}  # 2D or 3D",
        "",
        "tasks:  # e.g. classification, segmentation, detection, retrieval, generation",
    ]
    tasks = partial.get("tasks") or []
    if tasks:
        for t in tasks:
            lines.append(f"  - {t}")
    else:
        lines.append("  - ???")

    lines += [
        "",
        "annotations:  # e.g. class_labels, segmentation_masks, bounding_boxes, captions, keypoints",
    ]
    anns = partial.get("annotations") or []
    if anns:
        for a in anns:
            lines.append(f"  - {a}")
    else:
        lines.append("  - ???")

    lines += [
        "",
        f"classes:  # list the class names, or null if not applicable",
    ]
    classes = partial.get("classes")
    if classes:
        for c in classes:
            lines.append(f"  - {c}")
    else:
        lines.append("  - ???")

    lines += [
        "",
        f"num_samples: {partial.get('num_samples') or '???'}",
        f"preprocessing: {partial.get('preprocessing') or 'none'}  # describe any preprocessing done",
        "",
        "citation: |",
        f"  {partial.get('citation') or '???'}",
        "",
        f"is_public: {partial.get('is_public') if partial.get('is_public') is not None else '???'}  # true = freely downloadable, false = requires login/agreement",
        f"license: {partial.get('license') or '???'}",
    ]

    try:
        Path(fpath).write_text("\n".join(lines) + "\n")
        log.info("  Wrote template → %s", fpath)
    except PermissionError:
        log.warning("  Cannot write template to %s (no write permission)", dataset_path)


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan_dataset(path: str, name: str) -> dict:
    log.info("Scanning %s ...", name)

    # --- 1. Author-provided YAML (highest trust) ---
    yaml_info = read_yaml_info(path)

    # --- 2. README + frontmatter ---
    readme = read_readme(path)
    license_text = read_license(path)
    frontmatter = extract_yaml_frontmatter(readme)

    # --- 3. Filesystem heuristics ---
    log.info("  Collecting file extensions (may take a moment for large datasets)...")
    ext_counts = collect_extensions(path)
    dir_names  = collect_dir_names(path)

    modality    = yaml_info.get("modality")  or infer_modality(ext_counts, dir_names, readme)
    domain      = yaml_info.get("domain")    or infer_domain(name, readme, ext_counts)
    annotations = yaml_info.get("annotations") or infer_annotations(ext_counts, dir_names, readme)
    tasks       = yaml_info.get("tasks")     or infer_tasks(annotations, readme, domain)
    num_samples = yaml_info.get("num_samples") or infer_num_samples(ext_counts, modality)
    license_str = yaml_info.get("license")   or infer_license(readme, license_text, frontmatter)
    is_public   = yaml_info.get("is_public") if "is_public" in yaml_info else infer_is_public(license_str, readme)

    # --- 4. External API lookups (only if description or citation still missing) ---
    pwc = {}
    hf  = {}
    description = yaml_info.get("description") or frontmatter.get("description") or ""
    citation    = yaml_info.get("citation")    or ""

    if not description or not citation:
        pwc = lookup_papers_with_code(name)
        time.sleep(0.3)   # polite rate limiting
        hf  = lookup_huggingface(name)
        time.sleep(0.3)

    description = description or pwc.get("description") or hf.get("description") or ""
    citation    = citation    or pwc.get("paper_url") or ""
    if not license_str or license_str == "unknown":
        license_str = hf.get("license_hf") or license_str

    # Merge tasks from HF if we found nothing
    if not tasks and hf.get("tasks_hf"):
        tasks = hf["tasks_hf"]

    # --- 5. Filesystem metadata ---
    owner      = get_owner(path)
    size_gb    = get_size_gb(path)
    added_date = get_added_date(path)

    # --- 6. Identify missing fields ---
    record = {
        "name":         yaml_info.get("name")        or name,
        "path":         path,
        "owner":        yaml_info.get("owner")        or owner,
        "size_gb":      size_gb,
        "added_date":   added_date,
        "modality":     modality,
        "domain":       domain,
        "tasks":        tasks,
        "annotations":  annotations,
        "classes":      yaml_info.get("classes")      or None,
        "num_samples":  num_samples,
        "preprocessing":yaml_info.get("preprocessing") or "",
        "description":  description,
        "citation":     citation,
        "is_public":    is_public,
        "license":      license_str,
        "pwc_url":      pwc.get("pwc_url"),
        "hf_id":        hf.get("hf_id"),
        "source":       "yaml" if yaml_info else "auto",
        "last_updated": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
    }

    # Compute missing fields
    important = ["description", "citation", "classes", "tasks", "annotations",
                 "is_public", "license", "preprocessing"]
    missing = [f for f in important if not record[f] and record[f] is not False]
    record["missing_fields"] = missing

    # Generate template if needed
    generate_template(path, record)

    return record


def scan_all(datasets_root: str, output_json: str) -> None:
    root = Path(datasets_root)
    if not root.exists():
        log.error("DATASETS_ROOT does not exist: %s", datasets_root)
        return

    # Ensure output directory exists
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)

    # Load existing registry to preserve manual overrides
    existing: dict[str, dict] = {}
    if Path(output_json).exists():
        try:
            with open(output_json) as f:
                old = json.load(f)
            existing = {d["name"]: d for d in old.get("datasets", [])}
            log.info("Loaded %d existing records", len(existing))
        except Exception as e:
            log.warning("Could not load existing registry: %s", e)

    datasets = []
    skip = {".registry", ".git"}

    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or entry.name in skip:
            continue
        if not entry.is_dir():
            # top-level .tar / .tar.gz files — include as stubs
            if entry.suffix in {".tar", ".gz", ".zip"}:
                stub = {
                    "name":         entry.stem.split(".")[0],
                    "path":         str(entry),
                    "owner":        get_owner(str(entry)),
                    "size_gb":      round(entry.stat().st_size / (1024**3), 2),
                    "added_date":   datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "modality":     "unknown",
                    "domain":       "unknown",
                    "tasks":        [],
                    "annotations":  [],
                    "classes":      None,
                    "num_samples":  None,
                    "preprocessing":"",
                    "description":  "",
                    "citation":     "",
                    "is_public":    None,
                    "license":      "unknown",
                    "pwc_url":      None,
                    "hf_id":        None,
                    "source":       "stub",
                    "last_updated": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
                    "missing_fields": ["description", "citation", "classes", "tasks",
                                       "annotations", "is_public", "license"],
                }
                # Preserve existing manual data if present
                if stub["name"] in existing and existing[stub["name"]].get("source") == "yaml":
                    stub.update({k: v for k, v in existing[stub["name"]].items()
                                 if k not in {"size_gb", "added_date", "last_updated"}})
                datasets.append(stub)
            continue

        record = scan_dataset(str(entry), entry.name)

        # If the existing entry was manually edited (source == yaml), preserve its fields
        # but still update filesystem metadata
        if entry.name in existing and existing[entry.name].get("source") == "yaml":
            merged = existing[entry.name].copy()
            merged["size_gb"]     = record["size_gb"]
            merged["last_updated"] = record["last_updated"]
            merged["missing_fields"] = record["missing_fields"]
            datasets.append(merged)
        else:
            datasets.append(record)

    registry = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "datasets_root": datasets_root,
        "total": len(datasets),
        "datasets": datasets,
    }

    with open(output_json, "w") as f:
        json.dump(registry, f, indent=2, default=str)

    log.info("Registry saved → %s  (%d datasets)", output_json, len(datasets))

    # Print summary of missing fields
    needs_input = [d["name"] for d in datasets if d.get("missing_fields")]
    if needs_input:
        log.info("\nDatasets needing author input (%d):", len(needs_input))
        for n in needs_input:
            d = next(x for x in datasets if x["name"] == n)
            log.info("  %-35s missing: %s", n, ", ".join(d["missing_fields"]))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scan datasets and build JSON registry")
    parser.add_argument("--root",   default=DATASETS_ROOT, help="Path to datasets folder")
    parser.add_argument("--output", default=OUTPUT_JSON,   help="Output JSON path")
    parser.add_argument("--dataset", default=None,         help="Rescan a single dataset by name")
    args = parser.parse_args()

    if args.dataset:
        path = os.path.join(args.root, args.dataset)
        record = scan_dataset(path, args.dataset)
        print(json.dumps(record, indent=2, default=str))
    else:
        scan_all(args.root, args.output)
