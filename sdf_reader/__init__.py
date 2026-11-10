"""CASPEX SWIR frame reader. See README for supported SDF envelopes."""
from array import array
from dataclasses import dataclass, asdict
from pathlib import Path
import struct
import warnings
import zlib


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
        return {1: 12, 2: 8, 3: 24}[self.data_format]

    def to_dict(self):
        return {**asdict(self), 'bits_per_pixel': self.bits_per_pixel,
                'binning_x': 1 << self.binning_x_code,
                'binning_y': self.binning_y_code + 1,
                'integration_time_seconds': self.integration_time_ticks / 128_000_000,
                'gain_db': self.gain_raw / 10}


@dataclass
class SDFImage:
    header: Header
    pixels: array
    envelope: bytes
    header_offset: int
    missing_payload_bytes: int = 0

    @property
    def shape(self):
        return self.header.height, self.header.width

    @property
    def inferred_dark_row_count(self):
        """Leading dark rows for recognized unbinned full-width layouts.

        This is an acquisition-layout inference, not a field in the header.
        """
        h = self.header
        if h.width != 1280 or h.binning_x_code or h.binning_y_code:
            return 0
        return {1028: 4, 1036: 12, 1040: 12}.get(h.height, 0)

    def metadata(self):
        return {'header': self.header.to_dict(), 'header_offset': self.header_offset,
                'inferred_dark_row_count': self.inferred_dark_row_count,
                'envelope_hex': self.envelope.hex(),
                'envelope_words_be': [int.from_bytes(self.envelope[i:i+4], 'big')
                                      for i in range(0, len(self.envelope), 4)],
                'missing_payload_bytes': self.missing_payload_bytes}

    def select_data(self, data="image", *, dark_row_count=None):
        """Return (pixels, height) for image, both, or leading dark rows.

        Auto layout: 1036 rows -> 12 dark + 1024 image (supplied sample);
        1040 rows -> 12 dark + 1028 remaining rows (observed second layout);
        1028 rows -> 4 optical black + 1024 image. Requires unbinned width 1280.
        Other layouts keep all rows. Remaining rows may include sensor margins.
        Use dark_row_count when acquisition configuration differs.
        """
        if data not in {"image", "both", "dark"}:
            raise SDFError('data must be image, both, or dark')
        if dark_row_count is None:
            dark_row_count = self.inferred_dark_row_count
        if not isinstance(dark_row_count, int) or not 0 <= dark_row_count < self.header.height:
            raise SDFError('dark_row_count must be an integer between 0 and height-1')
        cut = dark_row_count * self.header.width
        if data == 'both':
            return self.pixels[:], self.header.height
        if data == 'dark':
            if not dark_row_count:
                raise SDFError('No dark rows identified; specify dark_row_count explicitly')
            return self.pixels[:cut], dark_row_count
        return self.pixels[cut:], self.header.height-dark_row_count

    def save_png(self, path, *, normalize=False, data="image", dark_row_count=None):
        """Save grayscale PNG. 8/12-bit values are lossless; 24-bit needs normalize=True.

        data selects image (default), both, or dark; see select_data.
        Normalization maps the selected rows' observed range to 0..65535 (constant images to zero).
        """
        bits = self.header.bits_per_pixel
        if bits == 24 and not normalize:
            raise SDFError('24-bit grayscale needs normalize=True for a 16-bit PNG')
        depth = 8 if bits == 8 and not normalize else 16
        values, height = self.select_data(data, dark_row_count=dark_row_count)
        low, high = min(values), max(values)
        if normalize:
            values = [round((v-low)*65535/(high-low)) if high > low else 0 for v in values]
        width = self.header.width
        rows = bytearray()
        for y in range(height):
            rows.append(0)  # PNG filter: None
            row = values[y*width:(y+1)*width]
            rows.extend(bytes(row) if depth == 8 else struct.pack(f'>{width}H', *row))
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
        Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, 0, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def _header(b):
    if len(b) != 36:
        raise SDFError('Camera header must contain 36 bytes')
    def u(start, size):
        return int.from_bytes(b[start:start+size], 'little')
    fmt = b[4] & 3
    if fmt not in (1, 2, 3):
        raise SDFError(f'Unsupported data format {fmt}')
    h = Header(u(0,4), fmt, bool(b[5] & 32), (b[5] >> 3) & 3, b[5] & 7,
               b[6], u(7,8), u(15,4), u(19,2) & 511, u(21,2) & 8191,
               u(23,2) & 4095, u(25,2) & 4095, u(27,2) & 4095,
               u(29,2) & 4095, u(31,5) & ((1 << 37)-1),
               {'byte_5': b[4] >> 2, 'byte_6': b[5] >> 6,
                'byte_21': b[20] >> 1, 'byte_23': b[22] >> 5,
                'byte_25': b[24] >> 4, 'byte_27': b[26] >> 4,
                'byte_29': b[28] >> 4, 'byte_31': b[30] >> 4,
                'byte_36': b[35] >> 5}, b.hex())
    if not h.width or not h.height:
        raise SDFError('Zero image dimension')
    return h


def read_sdf(path, *, header_offset=None, allow_truncated=False):
    """Read a bare frame or the observed 24-byte RAMSES SDF envelope.

    Unknown containers require an explicit header_offset. Strict by default;
    allow_truncated pads missing payload bytes with zero and emits a warning.
    """
    b = Path(path).read_bytes()
    if header_offset is None:
        # Observed envelope: total size and camera size are big-endian words.
        wrapped = (len(b) >= 61 and int.from_bytes(b[2:5], 'big') == len(b)
                   and int.from_bytes(b[5:8], 'big') == len(b)-24
                   and int.from_bytes(b[25:29], 'little') == len(b)-24)
        header_offset = 25 if wrapped else 0
    if header_offset < 0:
        raise SDFError('header_offset must be nonnegative')
    h = _header(b[header_offset:header_offset+36])
    count = h.width*h.height
    expected = (count*h.bits_per_pixel + 7)//8
    if h.packet_length != 36 + expected:
        raise SDFError(f'Packet length {h.packet_length} disagrees with dimensions/format ({36+expected})')
    payload = b[header_offset+36:]
    missing = expected-len(payload)
    if missing > 0:
        if not allow_truncated:
            raise SDFError(f'Pixel payload is {missing} byte(s) short; use allow_truncated=True to pad explicitly')
        warnings.warn(f'Padding {missing} missing payload byte(s) with zero', RuntimeWarning, stacklevel=2)
        payload += bytes(missing)
    elif missing < 0:
        raise SDFError(f'Unexpected trailing data: {-missing} bytes')
    pixels = array('I')
    if h.bits_per_pixel == 8:
        pixels.extend(payload)
    elif h.bits_per_pixel == 24:
        pixels.extend(int.from_bytes(payload[i:i+3], 'little') for i in range(0,len(payload),3))
    else:
        for i in range(0,len(payload)-2,3):
            a,c,d = payload[i:i+3]
            pixels.extend((a | ((c & 15) << 8), (c >> 4) | (d << 4)))
        if count % 2:
            pixels.append(payload[-2] | ((payload[-1] & 15) << 8))
    return SDFImage(h, pixels, b[:header_offset], header_offset, max(0,missing))


__all__ = ['read_sdf', 'SDFImage', 'Header', 'SDFError']
