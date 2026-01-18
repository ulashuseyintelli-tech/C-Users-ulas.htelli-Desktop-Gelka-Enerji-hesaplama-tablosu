from __future__ import annotations
import argparse, json
from .pipeline import parse_invoice

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    inv = parse_invoice(args.pdf)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(inv.to_dict(), f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
