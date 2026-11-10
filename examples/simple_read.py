"""Export a frame as PNG and print all metadata."""
import argparse
import json
from pathlib import Path
from sdf_reader import read_sdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('-o', '--output', type=Path)
    truncation = parser.add_mutually_exclusive_group()
    truncation.add_argument('--strict', dest='allow_truncated', action='store_false',
                            help='Reject incomplete pixel data instead of padding with a warning')
    truncation.add_argument('--allow-truncated', dest='allow_truncated', action='store_true',
                            help='Pad missing pixel bytes with zero (default; emits a warning)')
    parser.set_defaults(allow_truncated=True)
    scaling = parser.add_mutually_exclusive_group()
    scaling.add_argument('--normalize', dest='normalize', action='store_true',
                         help='Scale the observed range for display (default)')
    scaling.add_argument('--raw', dest='normalize', action='store_false',
                         help='Preserve original pixel values; may appear dark')
    parser.set_defaults(normalize=True)
    parser.add_argument('--data', choices=('image', 'both', 'dark'), default='image',
                        help='Data to export (default: image only)')
    parser.add_argument('--dark-row-count', type=int,
                        help='Override the number of leading dark rows')
    args = parser.parse_args()
    image = read_sdf(args.path, allow_truncated=args.allow_truncated)
    print(json.dumps(image.metadata(), indent=2))
    output = args.output or args.path.with_suffix('.png')
    image.save_png(output, normalize=args.normalize, data=args.data,
                   dark_row_count=args.dark_row_count)
    print(f'Saved {output}')


if __name__ == '__main__':
    main()
