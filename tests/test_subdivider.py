import struct
import tempfile
import unittest
from pathlib import Path

import main


def write_test_dbf(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        ("KEY_CODE", "C", 11, 0),
        ("PREF_NAME", "C", 12, 0),
        ("CITY_NAME", "C", 16, 0),
        ("S_NAME", "C", 24, 0),
    ]
    record_length = 1 + sum(field[2] for field in fields)
    header_length = 32 + 32 * len(fields) + 1

    header = bytearray(32)
    header[0] = 0x03
    header[4:8] = struct.pack("<I", len(rows))
    header[8:10] = struct.pack("<H", header_length)
    header[10:12] = struct.pack("<H", record_length)

    descriptors = bytearray()
    for name, field_type, length, decimals in fields:
        field = bytearray(32)
        encoded_name = name.encode("ascii")
        field[: len(encoded_name)] = encoded_name
        field[11] = ord(field_type)
        field[16] = length
        field[17] = decimals
        descriptors.extend(field)

    records = bytearray()
    for row in rows:
        records.append(0x20)
        for name, field_type, length, _ in fields:
            value = row[name]
            if field_type == "C":
                encoded = str(value).encode("cp932")
                records.extend(encoded.ljust(length, b" ")[:length])

    path.write_bytes(bytes(header) + bytes(descriptors) + b"\x0D" + bytes(records) + b"\x1A")


def write_test_shapefile(path: Path, polygons: list[list[tuple[float, float]]]) -> None:
    shp_records = bytearray()
    shx_records = bytearray()
    offset_words = 50

    for index, ring in enumerate(polygons, start=1):
        closed = ring if ring[0] == ring[-1] else ring + [ring[0]]
        xs = [point[0] for point in closed]
        ys = [point[1] for point in closed]
        num_parts = 1
        num_points = len(closed)
        parts = struct.pack("<i", 0)
        point_values = b"".join(struct.pack("<2d", x, y) for x, y in closed)
        content = b"".join(
            [
                struct.pack("<i", 5),
                struct.pack("<4d", min(xs), min(ys), max(xs), max(ys)),
                struct.pack("<2i", num_parts, num_points),
                parts,
                point_values,
            ]
        )
        content_length_words = len(content) // 2
        shp_records.extend(struct.pack(">2i", index, content_length_words))
        shp_records.extend(content)
        shx_records.extend(struct.pack(">2i", offset_words, content_length_words))
        offset_words += 4 + content_length_words

    file_length_words = (100 + len(shp_records)) // 2
    index_length_words = (100 + len(shx_records)) // 2

    def build_header(file_length: int) -> bytes:
        header = bytearray(100)
        header[:4] = struct.pack(">i", 9994)
        header[24:28] = struct.pack(">i", file_length)
        header[28:32] = struct.pack("<i", 1000)
        header[32:36] = struct.pack("<i", 5)
        all_x = [point[0] for ring in polygons for point in ring]
        all_y = [point[1] for ring in polygons for point in ring]
        header[36:68] = struct.pack("<4d", min(all_x), min(all_y), max(all_x), max(all_y))
        return bytes(header)

    path.write_bytes(build_header(file_length_words) + bytes(shp_records))
    path.with_suffix(".shx").write_bytes(build_header(index_length_words) + bytes(shx_records))
    path.with_suffix(".prj").write_text(
        'GEOGCS["GCS_JGD_2000",DATUM["D_JGD_2000",SPHEROID["GRS_1980",6378137.0,298.257222101]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]',
        encoding="utf-8",
    )


def write_test_bundle(input_dir: Path) -> None:
    stem = input_dir / "sample"
    polygons = [
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        [(2.0, 0.0), (3.0, 0.0), (3.0, 1.0), (2.0, 1.0)],
        [(0.0, 2.0), (1.0, 2.0), (1.0, 3.0), (0.0, 3.0)],
    ]
    rows = [
        {"KEY_CODE": "001", "PREF_NAME": "富山県", "CITY_NAME": "上市町", "S_NAME": "東町"},
        {"KEY_CODE": "002", "PREF_NAME": "富山県", "CITY_NAME": "上市町", "S_NAME": "東町"},
        {"KEY_CODE": "003", "PREF_NAME": "富山県", "CITY_NAME": "上市町", "S_NAME": "西町"},
    ]
    write_test_shapefile(stem.with_suffix(".shp"), polygons)
    write_test_dbf(stem.with_suffix(".dbf"), rows)
class SubdividerTests(unittest.TestCase):
    def test_load_shapefile_and_dissolve_by_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_dir = Path(tmp_dir)
            write_test_bundle(input_dir)
            shp_path, dbf_path, shx_path, prj_path = main.discover_input_files(input_dir)
            self.assertTrue(shx_path.exists())
            self.assertTrue(prj_path.exists())
            features = main.load_shapefile_features(shp_path, dbf_path)
            dissolved = main.dissolve_features_by_field(
                features=features,
                group_field="S_NAME",
                source_name="sample",
            )

            self.assertEqual(len(dissolved), 2)
            east = next(item for item in dissolved if item["properties"]["group_value"] == "東町")
            self.assertEqual(east["geometry"]["type"], "MultiPolygon")
            self.assertEqual(east["properties"]["record_count"], 2)

    def test_write_area_outputs_writes_collection_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_dir = Path(tmp_dir) / "input"
            output_dir = Path(tmp_dir) / "output"
            input_dir.mkdir()
            write_test_bundle(input_dir)

            shp_path, dbf_path, _, _ = main.discover_input_files(input_dir)
            dissolved = main.dissolve_features_by_field(
                features=main.load_shapefile_features(shp_path, dbf_path),
                group_field="S_NAME",
                source_name="sample",
            )
            output_path = main.write_area_outputs(dissolved, output_dir, "sample", "S_NAME")

            self.assertTrue(output_path.exists())
            manifest = output_dir / "sample_areas_by_S_NAME_manifest.json"
            self.assertTrue(manifest.exists())
            per_area = sorted(output_dir.glob("sample_areas_by_S_NAME__area_*.json"))
            self.assertEqual(len(per_area), 2)
            self.assertEqual(output_path.name, "sample_areas_by_S_NAME.json")


if __name__ == "__main__":
    unittest.main()
