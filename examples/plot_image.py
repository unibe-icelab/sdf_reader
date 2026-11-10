"""Show an image beside a histogram of its original pixel values."""
import argparse
import json
from pathlib import Path
from sdf_reader import read_sdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    truncation = parser.add_mutually_exclusive_group()
    truncation.add_argument('--strict', dest='allow_truncated', action='store_false',
                            help='Reject incomplete pixel data instead of padding with a warning')
    truncation.add_argument('--allow-truncated', dest='allow_truncated', action='store_true',
                            help='Pad missing pixel bytes with zero (default; emits a warning)')
    parser.set_defaults(allow_truncated=True)
    parser.add_argument('--output', help='Save the figure instead of opening a window')
    args = parser.parse_args()
    import matplotlib
    import matplotlib.pyplot as plt
    import numpy as np
    image = read_sdf(args.path, allow_truncated=args.allow_truncated)
    print(json.dumps(image.metadata(), indent=2))
    data = np.asarray(image.pixels).reshape(image.shape)
    fig, (ax, hist) = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    fig.colorbar(ax.imshow(data, cmap='gray', interpolation='nearest'), ax=ax, label='Pixel value (DN)')
    ax.set(title='SDF image', xlabel='Column', ylabel='Row')
    hist.hist(data.ravel(), bins=256)
    hist.set(title='Pixel histogram', xlabel='Pixel value (DN)', ylabel='Count')
    if args.output:
        fig.savefig(args.output, dpi=150)
    elif str(matplotlib.get_backend()).lower() in {'agg', 'pdf', 'ps', 'svg', 'pgf', 'cairo', 'template'}:
        output = Path(args.path).with_suffix('.histogram.png')
        fig.savefig(output, dpi=150)
        print(f'No interactive display (backend: {matplotlib.get_backend()}). Saved {output}')
        print('Open this PNG in your IDE.')
    else:
        plt.show()


if __name__ == '__main__':
    main()
