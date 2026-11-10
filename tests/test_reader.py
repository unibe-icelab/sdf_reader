import struct
from array import array
from dataclasses import replace
import tempfile
import unittest
import warnings
import zlib
from pathlib import Path
from sdf_reader import read_sdf, SDFError


class ReaderTests(unittest.TestCase):
    def read(self, fmt, payload, count=2, **kwargs):
        h = bytearray(36)
        struct.pack_into('<I', h, 0, 36 + (count*{1:12,2:8,3:24}[fmt]+7)//8)
        h[4] = fmt
        h[5] = 32 | (2 << 3) | 3
        h[6] = 9
        h[7:15] = (123456).to_bytes(8, 'little')
        struct.pack_into('<IHHH', h, 15, 128000000, 123, count, 1)
        h[31:36] = (123456789).to_bytes(5, 'little')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'test.sdf'
            p.write_bytes(h + payload)
            return read_sdf(p, **kwargs)

    def test_formats(self):
        for fmt, payload, expected in [(2,b'\x00\xff',[0,255]),
                (1,bytes.fromhex('bc3a12'),[0xabc,0x123]),
                (3,bytes.fromhex('563412efcdab'),[0x123456,0xabcdef])]:
            with self.subTest(fmt=fmt):
                image = self.read(fmt,payload)
                self.assertEqual(list(image.pixels),expected)
                self.assertEqual(image.shape,(1,2))

    def test_header(self):
        h = self.read(2,b'\x00\xff').header.to_dict()
        for k,v in dict(end_of_sequence=True, binning_x=4, binning_y=4,
                        frame_id=9,frame_tag=123456,integration_time_seconds=1,
                        gain_db=12.3,obt_ticks=123456789).items():
            self.assertEqual(h[k],v)

    def test_odd_12bit(self):
        self.assertEqual(list(self.read(1,bytes.fromhex('bc0a'),count=1).pixels),[0xabc])

    def test_truncated(self):
        with self.assertRaises(SDFError):
            self.read(2,b'\x01')
        with warnings.catch_warnings(record=True) as caught:
            image = self.read(2,b'\x01',allow_truncated=True)
        self.assertEqual(list(image.pixels),[1,0])
        self.assertEqual(image.missing_payload_bytes,1)
        self.assertTrue(caught)

    def test_trailing(self):
        with self.assertRaises(SDFError):
            self.read(2,b'\x00\x01\x02')

    def test_png(self):
        image = self.read(1,bytes.fromhex('bc3a12'))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'out.png'
            image.save_png(p)
            b = p.read_bytes()
        self.assertEqual(b[:8],b'\x89PNG\r\n\x1a\n')
        offset = 8
        while offset < len(b):
            n = int.from_bytes(b[offset:offset+4],'big')
            kind = b[offset+4:offset+8]
            data = b[offset+8:offset+8+n]
            self.assertEqual(zlib.crc32(kind+data),int.from_bytes(b[offset+8+n:offset+12+n],'big'))
            if kind == b'IDAT':
                self.assertEqual(zlib.decompress(data),b'\x00\x0a\xbc\x01\x23')
            offset += 12+n

    def test_row_selection(self):
        image = self.read(2, b'\x00\x01')
        image.header = replace(image.header, height=4)
        image.pixels = array('I', [10, 11, 20, 21, 100, 101, 200, 201])
        for mode, expected, height in [('image', [100,101,200,201], 2),
                                       ('dark', [10,11,20,21], 2),
                                       ('both', list(image.pixels), 4)]:
            pixels, actual_height = image.select_data(mode, dark_row_count=2)
            self.assertEqual(list(pixels), expected)
            self.assertEqual(actual_height, height)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'image.png'
            image.save_png(p, data='image', dark_row_count=2, normalize=True)
            b = p.read_bytes()
            self.assertEqual(struct.unpack('>II', b[16:24]), (2,2))
            n = int.from_bytes(b[33:37], 'big')
            raw = zlib.decompress(b[41:41+n])
            self.assertEqual(raw[:3], b'\x00\x00\x00')
            self.assertEqual(raw[-2:], b'\xff\xff')
        self.assertEqual(len(image.pixels), 8)
        for count in (-1, 4):
            with self.assertRaises(SDFError):
                image.select_data(dark_row_count=count)
        with self.assertRaises(SDFError):
            image.select_data('dark', dark_row_count=0)

    def test_1040_layout(self):
        image = self.read(2, b'\x00\x01')
        image.header = replace(image.header, width=1280, height=1040,
                               binning_x_code=0, binning_y_code=0)
        image.pixels = array('I', [1]) * (1280*1040)
        self.assertEqual(image.inferred_dark_row_count, 12)
        self.assertEqual(image.select_data('dark')[1], 12)
        self.assertEqual(image.select_data('image')[1], 1028)
        self.assertEqual(image.select_data('both')[1], 1040)
        image.header = replace(image.header, binning_y_code=1)
        self.assertEqual(image.inferred_dark_row_count, 0)

    def test_sample(self):
        p = Path(__file__).resolve().parents[1]/'input/Image_0x0_0x40000000.png'
        if not p.exists():
            self.skipTest('Sample not available')
        with self.assertRaises(SDFError):
            read_sdf(p)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            image = read_sdf(p,allow_truncated=True)
        self.assertEqual(image.shape,(1036,1280))
        self.assertEqual(len(image.pixels),1036*1280)
        self.assertEqual(image.missing_payload_bytes,1)
        self.assertEqual(image.select_data('image')[1], 1024)
        self.assertEqual(image.select_data('dark')[1], 12)
        self.assertEqual(image.select_data('both')[1], 1036)


if __name__ == '__main__':
    unittest.main()
