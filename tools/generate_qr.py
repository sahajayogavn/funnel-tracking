#!/usr/bin/env python3
"""
# code:tool-qr-001
Generate QR code PNG images from URLs.

Uses a safe_filename convention so the QR image can be matched back to its
source URL at runtime:
    URL  →  re.sub(r'[^a-zA-Z0-9]', '_', url)  →  collapse underscores  →  .png

Output directory default: memory/agent_memory/qr_codes/

Usage examples
--------------
# Single URL
.venv/bin/python tools/generate_qr.py --url "https://zalo.me/g/dxknkh602"

# Multiple URLs
.venv/bin/python tools/generate_qr.py \
    --url "https://zalo.me/g/dxknkh602" \
    --url "https://zalo.me/g/msfjzzpxz5oo2mvwtqks"

# Custom output directory
.venv/bin/python tools/generate_qr.py \
    --url "https://zalo.me/g/dxknkh602" \
    --outdir /tmp/qr

# List existing QR codes and their URLs (reverse lookup)
.venv/bin/python tools/generate_qr.py --list

Dependencies: pip install "qrcode[pil]"
"""
# code:tool-qr-001:generate-qr

import argparse
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

DEFAULT_OUTDIR = os.path.join(
    os.path.dirname(__file__), "..", "memory", "agent_memory", "qr_codes"
)


# code:tool-qr-001:safe-filename
def url_to_safe_filename(url: str) -> str:
    """Convert a URL to a filesystem-safe filename (without extension).

    >>> url_to_safe_filename("https://zalo.me/g/abc123")
    'https_zalo_me_g_abc123'
    """
    safe = re.sub(r"[^a-zA-Z0-9]", "_", url)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe


# code:tool-qr-001:safe-filename-reverse
def safe_filename_to_url_hint(filename: str) -> str:
    """Best-effort reverse of safe_filename — replaces underscores back.

    Not perfectly reversible but useful for display/debugging.
    """
    name = os.path.splitext(filename)[0]  # strip .png
    # Known pattern: https_zalo_me_g_XXXX → https://zalo.me/g/XXXX
    if name.startswith("https_zalo_me_g_"):
        group_id = name[len("https_zalo_me_g_"):]
        return f"https://zalo.me/g/{group_id}"
    return name.replace("_", "/", 2).replace("_", ".", 1)


# code:tool-qr-001:generate
def generate_qr(url: str, outdir: str) -> str:
    """Generate a QR code PNG for the given URL.

    Returns the absolute path to the saved PNG file.
    """
    try:
        import qrcode  # type: ignore
    except ImportError:
        logger.error("Missing dependency. Run: pip install 'qrcode[pil]'")
        sys.exit(1)

    os.makedirs(outdir, exist_ok=True)
    safe_name = url_to_safe_filename(url)
    filepath = os.path.join(outdir, f"{safe_name}.png")

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(filepath)
    logger.info("Created: %s → %s", filepath, url)
    return os.path.abspath(filepath)


# code:tool-qr-001:list
def list_qr_codes(outdir: str) -> list[dict]:
    """Return a list of existing QR code files with their inferred URLs."""
    results = []
    if not os.path.isdir(outdir):
        return results
    for f in sorted(os.listdir(outdir)):
        if f.endswith(".png"):
            url_hint = safe_filename_to_url_hint(f)
            results.append({"file": f, "url": url_hint})
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Generate QR code PNGs from URLs (safe_filename convention)."
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        help="URL(s) to generate QR codes for. Can be repeated.",
    )
    parser.add_argument(
        "--outdir",
        default=DEFAULT_OUTDIR,
        help=f"Output directory (default: {DEFAULT_OUTDIR})",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_mode",
        help="List existing QR codes and their inferred URLs.",
    )
    args = parser.parse_args()

    if args.list_mode:
        codes = list_qr_codes(args.outdir)
        if not codes:
            print("No QR codes found.")
        else:
            print(f"{'File':<55} URL")
            print("-" * 90)
            for c in codes:
                print(f"{c['file']:<55} {c['url']}")
        return

    if not args.urls:
        parser.error("Provide at least one --url or use --list.")

    for url in args.urls:
        generate_qr(url, args.outdir)

    print(f"\nDone. {len(args.urls)} QR code(s) saved to: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()
