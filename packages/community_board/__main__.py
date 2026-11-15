"""Print the explicitly selected synthetic workspace, never real provider data."""
import argparse
import json

from .demo import demo_board


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", choices=("product-alpha", "product-beta", "commons"))
    args = parser.parse_args()
    print(json.dumps(demo_board().list_items(args.workspace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
