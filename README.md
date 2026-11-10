# sdf-reader

Python 3.12+ library for CASPEX SWIR image frames and the RAMSES SDF sample in this repository. Reading and PNG export use only the standard library.

From the repository root:

```sh
uv sync
uv run python -m examples.simple_read input/2026-11-10T08-37-16.270423Z.sdf
```

This prints every decoded camera header field, reserved bits and raw header/envelope bytes, and saves a PNG beside the input. The example scales the observed pixel range by default, like Matplotlib. Use `--raw` to preserve 8/12-bit pixel values exactly. A 12-bit image uses a 16-bit PNG and may appear dark in ordinary viewers. Normalization stretches the observed range to 16 bits; 24-bit images require normalization for PNG export.

To display the image with a histogram alongside it:

```sh
uv sync --extra plot
uv run --extra plot python -m examples.plot_image input/2026-11-10T08-37-16.270423Z.sdf
```

Add `--output figure.png` to save the Matplotlib figure instead of showing it.

```python
from sdf_reader import read_sdf

image = read_sdf('frame.sdf')
print(image.header.to_dict())
print(image.metadata())
print(image.shape)               # (height, width)
print(image.pixels[0])           # unsigned original pixel value
image.save_png('frame.png')
```

## Specification and scope

Camera fields follow `docs/3DDS-0848-2 - 3DIPCC0848 CASPEX SWIR Standard Flight Code - User Manual and ICD - Draft.pdf`, section 9.1.3, Table 25 (pages 27–28) and Table 26 (page 29). The reader supports little-endian 8/24-bit pixels and packed 12-bit pixel pairs. It returns frame length, format, EOS, both binning codes and factors, frame ID/tag, integration time (ticks and seconds at 128 MHz), gain (raw and dB), dimensions, optical black mean/min/max, 37-bit OBT, all reserved bits and the original header. OBT stays in raw ticks: no UTC epoch is specified by this header table.

`docs/RAMSES-PNP-CHA-MAN-001_i4_1_SwUm_Signed.pdf`, sections 5.3 and 6.5.11, describes the camera interface and dummy SDFs, but delegates detailed SDF definitions to the separate SwReq document, which is not supplied. Consequently the library preserves outer envelope bytes without assigning undocumented semantics. `envelope_words_be` is only a raw grouping, not a specification of fields.

Auto-detection supports bare 36-byte camera headers and the supplied sample's observed layout: 24 outer bytes followed by an extra byte, then the camera header at offset 25. Detection checks the observed 24-bit big-endian sizes at offsets 2 and 5 and the little-endian camera size at 25. These envelope details are inferred from the sample, not established by the supplied manuals. For another known container use `read_sdf(path, header_offset=...)`. SpaceWire transport packets, compressed SDFs and dummy patterns are not decoded.

Under the detected layout, the sample describes a 1280 × 1036, 12-bit image, 0.5-second integration, frame ID 3, with one missing payload byte. Strict library reading raises `SDFError`. Both example scripts enable padding by default so they work with this sample; pass `--strict` to reject incomplete data. `allow_truncated=True` explicitly pads missing bytes with zero and warns; metadata records the missing count. This affects the final pixel and does not recover its original value. No missing bytes are silently repaired. Unexpected trailing bytes and inconsistent lengths are rejected.

## Verification

```sh
uv run python -m unittest discover -s tests -v
```

Tests cover all pixel formats, odd 12-bit counts, header units/flags, truncation, trailing data, PNG content/CRC and the supplied sample. Matplotlib is an optional dependency and is needed to run the plotting example.

## Plotting in a devcontainer / PyCharm

The container has no desktop display, so Matplotlib normally selects `Agg`.
The example then saves `<input>.histogram.png` and prints its path; open it in PyCharm.

An interactive backend supplied by PyCharm continues to use `plt.show()` normally.

## PNG data selection

The simple example exports only image rows by default. Choose the output with:

```sh
uv run python examples/simple_read.py input/2026-11-10T08-37-16.270423Z.sdf --data image -o image.png
uv run python examples/simple_read.py input/2026-11-10T08-37-16.270423Z.sdf --data both -o full.png
uv run python examples/simple_read.py input/2026-11-10T08-37-16.270423Z.sdf --data dark -o dark.png
```

`both` preserves all rows in one PNG. For this sample, the first 12 rows are dark
and the remaining 1024 rows are image data. This split is inferred from the sample's
row intensities and 1036-row height. The manual (§11.1.6) documents four optional
leading optical-black rows, but does not specify this sample's additional eight rows.
The reader also recognizes 1028-row frames as four dark rows plus 1024 image rows.
The supplied 1040-row acquisition also has 12 leading dark rows, leaving 1028
rows (which may include sensor margins). Recognition requires unbinned width 1280.
The header has no explicit dark-row count or KEEP_OPTICAL_BLACK flag; total height
alone cannot distinguish active data, margins and optical-black lines. Metadata
reports `inferred_dark_row_count` separately from the actual header fields.
Other layouts default to no removal; use `--dark-row-count N` to specify the number
of leading dark rows for your acquisition. This is a layout selection, not an
intensity-based detector. Requesting dark-only output without identified dark rows
raises an error. Normalization uses only the selected rows.

Library usage: `image.save_png('image.png', data='image', normalize=True)`;
`data='both'` and `data='dark'` select the other outputs. `dark_row_count=N` overrides
the inferred layout. Original pixels and header dimensions remain unchanged.
