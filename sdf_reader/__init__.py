"""CASPEX SWIR frame reader with lossless grayscale PNG and metadata."""

import re
import struct
import warnings
import zlib

from array import array
from dataclasses import dataclass, asdict
from datetime import datetime
from math import gcd
from pathlib import Path


class SDFError(ValueError):
    """Invalid or unsupported SDF/frame."""


@dataclass(frozen=True)
class Header:
    packet_length: int
    data_format: int
    end_of_sequence: bool
    binning_x_code: int
    binning_y_code: int
    frame_id: int
    frame_tag: int
    integration_time_ticks: int
    gain_raw: int
    width: int
    height: int
    optical_black_mean: int
    optical_black_min: int
    optical_black_max: int
    obt_ticks: int
    reserved: dict
    raw_hex: str

    @property
    def bits_per_pixel(self):
        return {
            1: 12,
            2: 8,
            3: 24,
        }[self.data_format]

    def to_dict(self):
        return {
            **asdict(self),
            "bits_per_pixel": self.bits_per_pixel,
            "binning_x": 1 << self.binning_x_code,
            "binning_y": self.binning_y_code + 1,
            "integration_time_seconds":
                self.integration_time_ticks / 128_000_000,
            "gain_db": self.gain_raw / 10,
        }


# ============================================================
# Filename timestamp
# ============================================================

def _filename_datetime(path):
    """Extract acquisition timestamp from an SDF filename.

    Supported examples:

        2026-11-10T09-04-30.038751Z.sdf
        20261110_090430.sdf
        2026-11-10_09-04-30.sdf

    Returns:
        (EXIF datetime, subseconds, UTC offset)

    If no valid timestamp exists:
        (None, None, None)
    """

    if path is None:
        return None, None, None

    name = Path(path).stem

    pattern = (
        r"(?<!\d)"
        r"(\d{4})-?(\d{2})-?(\d{2})"
        r"[T_ -]"
        r"(\d{2})[:-]?(\d{2})[:-]?(\d{2})"
        r"(?:\.(\d+))?"
        r"(Z|[+-]\d{2}:?\d{2})?"
        r"(?!\d)"
    )

    match = re.search(pattern, name)

    if not match:
        return None, None, None

    year, month, day, hour, minute, second, subsec, tz = (
        match.groups()
    )

    try:
        dt = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
        )
    except ValueError:
        return None, None, None

    offset = None

    if tz == "Z":
        offset = "+00:00"

    elif tz:
        digits = tz[1:].replace(":", "")

        if int(digits[:2]) > 23 or int(digits[2:]) > 59:
            return None, None, None

        offset = (
            tz[0]
            + digits[:2]
            + ":"
            + digits[2:]
        )

    return (
        dt.strftime("%Y:%m:%d %H:%M:%S"),
        subsec,
        offset,
    )


# ============================================================
# EXIF encoder
# ============================================================

def _build_exif(header, *, source_path=None):
    """Build standard TIFF/EXIF metadata for a PNG eXIf chunk.

    Only standardized EXIF tags are used.

    CASPEX-specific metadata is stored separately in PNG iTXt
    chunks to avoid collisions with existing EXIF/DNG tags.
    """

    # TIFF types
    ASCII = 2
    SHORT = 3
    LONG = 4
    RATIONAL = 5

    def ascii_tag(value):
        return (
            ASCII,
            str(value).encode("ascii") + b"\x00",
        )

    # --------------------------------------------------------
    # Standard TIFF IFD0
    # --------------------------------------------------------

    ifd0 = {
        0x010E: ascii_tag("CASPEX SWIR SDF frame"),
        0x010F: ascii_tag("CASPEX"),
        0x0110: ascii_tag("CASPEX SWIR"),
        0x0112: (
            SHORT,
            struct.pack("<H", 1),
        ),
        0x0131: ascii_tag("sdf_reader"),
    }

    # --------------------------------------------------------
    # Standard EXIF sub-IFD
    # --------------------------------------------------------

    exif = {}

    timestamp, subsec, offset = _filename_datetime(
        source_path
    )

    if timestamp:

        # TIFF DateTime
        ifd0[0x0132] = ascii_tag(timestamp)

        # DateTimeOriginal
        exif[0x9003] = ascii_tag(timestamp)

        # DateTimeDigitized
        exif[0x9004] = ascii_tag(timestamp)

        if subsec:

            # SubSecTimeOriginal
            exif[0x9291] = ascii_tag(subsec)

            # SubSecTimeDigitized
            exif[0x9292] = ascii_tag(subsec)

        if offset:

            # OffsetTime
            exif[0x9010] = ascii_tag(offset)

            # OffsetTimeOriginal
            exif[0x9011] = ascii_tag(offset)

            # OffsetTimeDigitized
            exif[0x9012] = ascii_tag(offset)

    # --------------------------------------------------------
    # Exposure time
    # --------------------------------------------------------

    numerator = header.integration_time_ticks
    denominator = 128_000_000

    divisor = gcd(numerator, denominator)

    numerator //= divisor
    denominator //= divisor

    if (
        numerator <= 0xFFFFFFFF
        and denominator <= 0xFFFFFFFF
    ):
        exif[0x829A] = (
            RATIONAL,
            struct.pack(
                "<II",
                numerator,
                denominator,
            ),
        )

    # --------------------------------------------------------
    # TIFF IFD encoder
    # --------------------------------------------------------

    def encode_ifd(entries, start):
        """Encode an IFD with external data offsets."""

        items = sorted(entries.items())

        table_size = (
            2
            + 12 * len(items)
            + 4
        )

        payload = bytearray()

        out = bytearray(
            struct.pack("<H", len(items))
        )

        type_sizes = {
            ASCII: 1,
            SHORT: 2,
            LONG: 4,
            RATIONAL: 8,
        }

        for tag, (typ, raw) in items:

            unit = type_sizes[typ]

            if len(raw) % unit:
                raise ValueError(
                    "Invalid TIFF value length"
                )

            count = len(raw) // unit

            if len(raw) <= 4:

                value = raw.ljust(
                    4,
                    b"\x00",
                )

            else:

                value = struct.pack(
                    "<I",
                    start
                    + table_size
                    + len(payload),
                )

                payload.extend(raw)

            out.extend(
                struct.pack(
                    "<HHI",
                    tag,
                    typ,
                    count,
                )
            )

            out.extend(value)

        # No next IFD
        out.extend(
            struct.pack("<I", 0)
        )

        out.extend(payload)

        return bytes(out)

    # --------------------------------------------------------
    # Link EXIF sub-IFD
    # --------------------------------------------------------

    ifd0[0x8769] = (
        LONG,
        struct.pack("<I", 0),
    )

    exif_start = (
        8
        + len(encode_ifd(ifd0, 8))
    )

    ifd0[0x8769] = (
        LONG,
        struct.pack("<I", exif_start),
    )

    return (
        b"II*\x00\x08\x00\x00\x00"
        + encode_ifd(ifd0, 8)
        + encode_ifd(exif, exif_start)
    )


# ============================================================
# PNG helpers
# ============================================================

def _png_chunk(kind, payload):
    """Create a PNG chunk with CRC."""

    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(
            ">I",
            zlib.crc32(kind + payload),
        )
    )


def _png_text_chunk(key, value):
    """Create an uncompressed PNG iTXt metadata chunk."""

    keyword = key.encode("latin-1")

    if (
        not 1 <= len(keyword) <= 79
        or b"\x00" in keyword
    ):
        raise ValueError(
            f"Invalid PNG metadata keyword: {key!r}"
        )

    payload = (
        keyword
        + b"\x00"  # Keyword terminator
        + b"\x00"  # Compression flag
        + b"\x00"  # Compression method
        + b"\x00"  # Language tag
        + b"\x00"  # Translated keyword
        + str(value).encode("utf-8")
    )

    return _png_chunk(
        b"iTXt",
        payload,
    )


# ============================================================
# SDF image
# ============================================================

@dataclass
class SDFImage:

    header: Header
    pixels: array
    envelope: bytes
    header_offset: int

    missing_payload_bytes: int = 0
    source_path: str | None = None

    @property
    def shape(self):
        return (
            self.header.height,
            self.header.width,
        )

    @property
    def inferred_dark_row_count(self):
        """Infer leading dark rows for known layouts."""

        h = self.header

        if (
            h.width != 1280
            or h.binning_x_code
            or h.binning_y_code
        ):
            return 0

        return {
            1028: 4,
            1036: 12,
            1040: 12,
        }.get(h.height, 0)

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    def metadata(self):

        return {
            "header": self.header.to_dict(),
            "header_offset": self.header_offset,
            "inferred_dark_row_count":
                self.inferred_dark_row_count,
            "envelope_hex": self.envelope.hex(),
            "envelope_words_be": [
                int.from_bytes(
                    self.envelope[i:i + 4],
                    "big",
                )
                for i in range(
                    0,
                    len(self.envelope),
                    4,
                )
            ],
            "missing_payload_bytes":
                self.missing_payload_bytes,
        }

    # --------------------------------------------------------
    # Select image data
    # --------------------------------------------------------

    def select_data(
        self,
        data="image",
        *,
        dark_row_count=None,
    ):

        if data not in {
            "image",
            "both",
            "dark",
        }:
            raise SDFError(
                "data must be image, both, or dark"
            )

        if dark_row_count is None:
            dark_row_count = (
                self.inferred_dark_row_count
            )

        if (
            not isinstance(dark_row_count, int)
            or not 0 <= dark_row_count < self.header.height
        ):
            raise SDFError(
                "dark_row_count must be an integer "
                "between 0 and height-1"
            )

        cut = (
            dark_row_count
            * self.header.width
        )

        if data == "both":
            return (
                self.pixels[:],
                self.header.height,
            )

        if data == "dark":

            if not dark_row_count:
                raise SDFError(
                    "No dark rows identified; "
                    "specify dark_row_count explicitly"
                )

            return (
                self.pixels[:cut],
                dark_row_count,
            )

        return (
            self.pixels[cut:],
            self.header.height - dark_row_count,
        )

    # ========================================================
    # PNG export
    # ========================================================

    def save_png(
        self,
        path,
        *,
        normalize=False,
        data="image",
        dark_row_count=None,
        rotate=0,
        mirror_x=False,
        mirror_y=False,
    ):
        """Save grayscale PNG with EXIF and SDF metadata.

        Supports:
            - 8-bit grayscale
            - 16-bit grayscale
            - Normalization
            - Rotation: 0, 90, 180, 270
            - Horizontal mirroring
            - Vertical mirroring
            - Standard EXIF metadata
            - Individual SDF metadata in PNG iTXt chunks

        Transformations:
            1. Rotation clockwise
            2. Mirror X
            3. Mirror Y
        """

        if rotate not in (0, 90, 180, 270):
            raise ValueError(
                "rotate must be 0, 90, 180, or 270"
            )

        bits = self.header.bits_per_pixel

        if bits == 24 and not normalize:
            raise SDFError(
                "24-bit grayscale needs normalize=True "
                "for a 16-bit PNG"
            )

        depth = (
            8
            if bits == 8 and not normalize
            else 16
        )

        values, height = self.select_data(
            data,
            dark_row_count=dark_row_count,
        )

        width = self.header.width

        # ----------------------------------------------------
        # Normalize
        # ----------------------------------------------------

        if normalize:

            low = min(values)
            high = max(values)

            values = [
                round(
                    (v - low)
                    * 65535
                    / (high - low)
                )
                if high > low
                else 0
                for v in values
            ]

        # ----------------------------------------------------
        # Rotate
        # ----------------------------------------------------

        if rotate == 90:

            values = [
                values[
                    (height - 1 - x) * width + y
                ]
                for y in range(width)
                for x in range(height)
            ]

            width, height = height, width

        elif rotate == 180:

            values = values[::-1]

        elif rotate == 270:

            values = [
                values[
                    x * width + (width - 1 - y)
                ]
                for y in range(width)
                for x in range(height)
            ]

            width, height = height, width

        # ----------------------------------------------------
        # Mirror X
        # ----------------------------------------------------

        if mirror_x:

            values = [
                values[
                    y * width + (width - 1 - x)
                ]
                for y in range(height)
                for x in range(width)
            ]

        # ----------------------------------------------------
        # Mirror Y
        # ----------------------------------------------------

        if mirror_y:

            values = [
                values[
                    (height - 1 - y) * width + x
                ]
                for y in range(height)
                for x in range(width)
            ]

        # ----------------------------------------------------
        # Encode PNG rows
        # ----------------------------------------------------

        rows = bytearray()

        for y in range(height):

            # PNG filter: None
            rows.append(0)

            row = values[
                y * width:
                (y + 1) * width
            ]

            if depth == 8:

                rows.extend(
                    bytes(row)
                )

            else:

                rows.extend(
                    struct.pack(
                        f">{width}H",
                        *row,
                    )
                )

        # ----------------------------------------------------
        # Standard EXIF
        # ----------------------------------------------------

        exif = _build_exif(
            self.header,
            source_path=self.source_path,
        )

        # ----------------------------------------------------
        # SDF metadata
        # ----------------------------------------------------

        metadata = self.metadata()

        header_metadata = metadata["header"]

        # ----------------------------------------------------
        # Build PNG
        # ----------------------------------------------------

        png = bytearray(
            b"\x89PNG\r\n\x1a\n"
        )

        # Image header
        png += _png_chunk(
            b"IHDR",
            struct.pack(
                ">IIBBBBB",
                width,
                height,
                depth,
                0,
                0,
                0,
                0,
            ),
        )

        # ----------------------------------------------------
        # EXIF chunk
        # ----------------------------------------------------

        png += _png_chunk(
            b"eXIf",
            exif,
        )

        # ----------------------------------------------------
        # Individual SDF header fields
        # ----------------------------------------------------

        for key, value in header_metadata.items():

            # Already present in PNG IHDR
            if key in (
                "width",
                "height",
            ):
                continue

            # Already stored as standard EXIF exposure
            if key == "integration_time_seconds":
                continue

            if isinstance(value, dict):

                for subkey, subvalue in value.items():

                    png += _png_text_chunk(
                        f"SDF_{key}_{subkey}",
                        subvalue,
                    )

            else:

                png += _png_text_chunk(
                    f"SDF_{key}",
                    value,
                )

        # ----------------------------------------------------
        # Additional SDF metadata
        # ----------------------------------------------------

        additional_fields = {
            "header_offset":
                self.header_offset,

            "inferred_dark_row_count":
                self.inferred_dark_row_count,

            "missing_payload_bytes":
                self.missing_payload_bytes,

            "envelope_hex":
                self.envelope.hex(),
        }

        for key, value in additional_fields.items():

            png += _png_text_chunk(
                f"SDF_{key}",
                value,
            )

        # ----------------------------------------------------
        # Export settings
        # ----------------------------------------------------

        export_fields = {
            "data_selection": data,

            "dark_row_count": (
                self.inferred_dark_row_count
                if dark_row_count is None
                else dark_row_count
            ),

            "normalized": int(normalize),
            "rotate": rotate,
            "mirror_x": int(mirror_x),
            "mirror_y": int(mirror_y),
        }

        for key, value in export_fields.items():

            png += _png_text_chunk(
                f"SDF_export_{key}",
                value,
            )

        # ----------------------------------------------------
        # Image data
        # ----------------------------------------------------

        png += _png_chunk(
            b"IDAT",
            zlib.compress(rows),
        )

        # End of PNG
        png += _png_chunk(
            b"IEND",
            b"",
        )

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        Path(path).write_bytes(png)


# ============================================================
# SDF header decoder
# ============================================================

def _header(b):

    if len(b) != 36:
        raise SDFError(
            "Camera header must contain 36 bytes"
        )

    def u(start, size):
        return int.from_bytes(
            b[start:start + size],
            "little",
        )

    fmt = b[4] & 3

    if fmt not in (1, 2, 3):
        raise SDFError(
            f"Unsupported data format {fmt}"
        )

    h = Header(
        u(0, 4),
        fmt,
        bool(b[5] & 32),
        (b[5] >> 3) & 3,
        b[5] & 7,
        b[6],
        u(7, 8),
        u(15, 4),
        u(19, 2) & 511,
        u(21, 2) & 8191,
        u(23, 2) & 4095,
        u(25, 2) & 4095,
        u(27, 2) & 4095,
        u(29, 2) & 4095,
        u(31, 5) & ((1 << 37) - 1),

        {
            "byte_5": b[4] >> 2,
            "byte_6": b[5] >> 6,
            "byte_21": b[20] >> 1,
            "byte_23": b[22] >> 5,
            "byte_25": b[24] >> 4,
            "byte_27": b[26] >> 4,
            "byte_29": b[28] >> 4,
            "byte_31": b[30] >> 4,
            "byte_36": b[35] >> 5,
        },

        b.hex(),
    )

    if not h.width or not h.height:
        raise SDFError(
            "Zero image dimension"
        )

    return h


# ============================================================
# SDF reader
# ============================================================

def read_sdf(
    path,
    *,
    header_offset=None,
    allow_truncated=False,
):
    """Read a CASPEX SDF frame.

    Supports:
        - Bare frames
        - Recognized RAMSES SDF envelope
        - 8-bit pixels
        - 12-bit packed pixels
        - 24-bit pixels
        - Optional truncated payload padding
    """

    b = Path(path).read_bytes()

    # --------------------------------------------------------
    # Detect envelope
    # --------------------------------------------------------

    if header_offset is None:

        wrapped = (
            len(b) >= 61
            and int.from_bytes(
                b[2:5],
                "big",
            ) == len(b)
            and int.from_bytes(
                b[5:8],
                "big",
            ) == len(b) - 24
            and int.from_bytes(
                b[25:29],
                "little",
            ) == len(b) - 24
        )

        header_offset = (
            25 if wrapped else 0
        )

    if header_offset < 0:
        raise SDFError(
            "header_offset must be nonnegative"
        )

    # --------------------------------------------------------
    # Decode header
    # --------------------------------------------------------

    h = _header(
        b[
            header_offset:
            header_offset + 36
        ]
    )

    count = (
        h.width
        * h.height
    )

    expected = (
        count * h.bits_per_pixel + 7
    ) // 8

    if h.packet_length != 36 + expected:

        raise SDFError(
            f"Packet length {h.packet_length} "
            f"disagrees with dimensions/format "
            f"({36 + expected})"
        )

    # --------------------------------------------------------
    # Payload
    # --------------------------------------------------------

    payload = b[
        header_offset + 36:
    ]

    missing = (
        expected
        - len(payload)
    )

    if missing > 0:

        if not allow_truncated:

            raise SDFError(
                f"Pixel payload is {missing} byte(s) "
                "short; use allow_truncated=True "
                "to pad explicitly"
            )

        warnings.warn(
            f"Padding {missing} missing payload "
            "byte(s) with zero",
            RuntimeWarning,
            stacklevel=2,
        )

        payload += bytes(missing)

    elif missing < 0:

        raise SDFError(
            f"Unexpected trailing data: {-missing} bytes"
        )

    # --------------------------------------------------------
    # Decode pixels
    # --------------------------------------------------------

    pixels = array("I")

    if h.bits_per_pixel == 8:

        pixels.extend(payload)

    elif h.bits_per_pixel == 24:

        pixels.extend(
            int.from_bytes(
                payload[i:i + 3],
                "little",
            )
            for i in range(
                0,
                len(payload),
                3,
            )
        )

    else:

        # Packed 12-bit pixels
        for i in range(
            0,
            len(payload) - 2,
            3,
        ):

            a, c, d = payload[i:i + 3]

            pixels.extend(
                (
                    a | ((c & 15) << 8),
                    (c >> 4) | (d << 4),
                )
            )

        if count % 2:

            pixels.append(
                payload[-2]
                | ((payload[-1] & 15) << 8)
            )

    # --------------------------------------------------------
    # Return image
    # --------------------------------------------------------

    return SDFImage(
        header=h,
        pixels=pixels,
        envelope=b[:header_offset],
        header_offset=header_offset,
        missing_payload_bytes=max(0, missing),
        source_path=str(path),
    )


__all__ = [
    "read_sdf",
    "SDFImage",
    "Header",
    "SDFError",
]