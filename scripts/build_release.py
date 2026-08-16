#!/usr/bin/env python3
"""Build a PCM package zip and regenerate pcm/packages.json + pcm/repository.json.

Usage: scripts/build_release.py <version> [--repo-url BASE_RAW_URL] [--release-url-base URL]

Reads metadata.json for package identity/tags/license, adds/replaces the
entry for <version> in its "versions" array, zips the package contents,
and writes pcm/packages.json + pcm/repository.json pointing at the GitHub
Release asset for that version.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE_PATHS = ["symbols", "footprints", "3dmodels", "resources", "metadata.json"]


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def build_zip(version: str, out_dir: pathlib.Path) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"sao-kicad-lib-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in PACKAGE_PATHS:
            src = ROOT / rel
            if src.is_dir():
                for f in sorted(src.rglob("*")):
                    if f.is_file():
                        zf.write(f, f.relative_to(ROOT))
            elif src.is_file():
                zf.write(src, src.relative_to(ROOT))
            else:
                sys.exit(f"missing required package path: {rel}")
    return zip_path


def install_size(zip_path: pathlib.Path) -> int:
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            total += info.file_size
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--kicad-version", default="9.0")
    ap.add_argument("--status", default="stable")
    ap.add_argument(
        "--download-url-base",
        default=None,
        help="e.g. https://github.com/aerospace-venoms/SAO-kicad-lib/releases/download/v{version}",
    )
    args = ap.parse_args()

    metadata_path = ROOT / "metadata.json"
    metadata = json.loads(metadata_path.read_text())

    build_dir = ROOT / "build"
    zip_path = build_zip(args.version, build_dir)
    download_size = zip_path.stat().st_size
    size_installed = install_size(zip_path)
    sha256 = sha256_of(zip_path)

    version_entry = {
        "version": args.version,
        "status": args.status,
        "kicad_version": args.kicad_version,
        "download_size": download_size,
        "install_size": size_installed,
    }

    # Keep local metadata.json version history free of URLs (those only
    # matter for the hosted repository index), but do track size/hash so
    # re-running this script is idempotent per version.
    versions = [v for v in metadata.get("versions", []) if v["version"] != args.version]
    versions.append(version_entry)
    versions.sort(key=lambda v: v["version"])
    metadata["versions"] = versions
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

    # Build pcm/packages.json (repository-hosted, includes download_url + sha256)
    pcm_dir = ROOT / "pcm"
    pcm_dir.mkdir(exist_ok=True)

    download_url_base = args.download_url_base or (
        "https://github.com/aerospace-venoms/SAO-kicad-lib/releases/download/v{version}"
    )
    download_url = f"{download_url_base.format(version=args.version)}/{zip_path.name}"

    packages_json_path = pcm_dir / "packages.json"
    if packages_json_path.exists():
        packages_data = json.loads(packages_json_path.read_text())
    else:
        packages_data = {"packages": []}

    pkg = None
    for p in packages_data["packages"]:
        if p["identifier"] == metadata["identifier"]:
            pkg = p
            break
    if pkg is None:
        pkg = {k: v for k, v in metadata.items() if k != "versions"}
        pkg["versions"] = []
        packages_data["packages"].append(pkg)
    else:
        for k, v in metadata.items():
            if k != "versions":
                pkg[k] = v

    pkg["versions"] = [v for v in pkg["versions"] if v["version"] != args.version]
    pkg["versions"].append(
        {
            **version_entry,
            "download_url": download_url,
            "download_sha256": sha256,
        }
    )
    pkg["versions"].sort(key=lambda v: v["version"])

    packages_json_path.write_text(json.dumps(packages_data, indent=2) + "\n")

    # resources.zip: <identifier>/icon.png, per PCM repository convention
    resources_zip_path = pcm_dir / "resources.zip"
    with zipfile.ZipFile(resources_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        icon = ROOT / "resources" / "icon.png"
        if icon.exists():
            zf.write(icon, f"{metadata['identifier']}/icon.png")
    resources_sha256 = sha256_of(resources_zip_path)
    resources_size = resources_zip_path.stat().st_size

    packages_sha256 = sha256_of(packages_json_path)
    packages_size = packages_json_path.stat().st_size

    repo_url_base = "https://raw.githubusercontent.com/aerospace-venoms/SAO-kicad-lib/main/pcm"
    repository = {
        "$schema": "https://go.kicad.org/pcm/schemas/v1",
        "name": "SAO Connector Library Repository",
        "packages": {
            "url": f"{repo_url_base}/packages.json",
            "sha256": packages_sha256,
            "update_time_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "update_timestamp": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        },
        "resources": {
            "url": f"{repo_url_base}/resources.zip",
            "sha256": resources_sha256,
            "update_time_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "update_timestamp": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        },
    }
    (pcm_dir / "repository.json").write_text(json.dumps(repository, indent=2) + "\n")

    print(f"built {zip_path} ({download_size} bytes, sha256={sha256})")
    print(f"updated {packages_json_path} ({packages_size} bytes, sha256={packages_sha256})")
    print(f"updated {resources_zip_path} ({resources_size} bytes, sha256={resources_sha256})")
    print(f"updated {pcm_dir / 'repository.json'}")


if __name__ == "__main__":
    main()
