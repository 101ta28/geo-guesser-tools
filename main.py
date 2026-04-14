from __future__ import annotations

import argparse
import json
import struct
from collections import defaultdict
from pathlib import Path
from typing import Iterable


Point = tuple[float, float]
Ring = list[Point]
Polygon = list[Ring]
MultiPolygon = list[Polygon]

EPSILON = 1e-12


def close_ring(ring: Iterable[Point]) -> Ring:
    points = list(ring)
    if not points:
        return []
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def signed_ring_area(ring: Ring) -> float:
    if len(ring) < 4:
        return 0.0
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        area += x1 * y2 - x2 * y1
    return area / 2.0


def ring_area(ring: Ring) -> float:
    return abs(signed_ring_area(ring))


def polygon_area(polygon: Polygon) -> float:
    if not polygon:
        return 0.0
    outer = ring_area(polygon[0])
    holes = sum(ring_area(ring) for ring in polygon[1:])
    return max(0.0, outer - holes)


def geometry_to_multipolygon(geometry: dict) -> MultiPolygon:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        return [[close_ring(tuple(point) for point in ring) for ring in coordinates]]
    if geometry_type == "MultiPolygon":
        return [
            [close_ring(tuple(point) for point in ring) for ring in polygon]
            for polygon in coordinates
        ]
    raise ValueError(f"Unsupported geometry type: {geometry_type}")


def multipolygon_bounds(multipolygon: MultiPolygon) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for polygon in multipolygon:
        for ring in polygon:
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    if not xs or not ys:
        raise ValueError("Geometry has no coordinates.")
    return min(xs), min(ys), max(xs), max(ys)


def point_in_ring(point: Point, ring: Ring) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        intersects = ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / ((y2 - y1) or EPSILON) + x1
        )
        if intersects:
            inside = not inside
    return inside


def clip_ring_against_edge(
    ring: Ring,
    inside,
    intersect,
) -> Ring:
    if not ring:
        return []

    result: Ring = []
    previous = ring[-1]
    previous_inside = inside(previous)

    for current in ring:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                result.append(intersect(previous, current))
            result.append(current)
        elif previous_inside:
            result.append(intersect(previous, current))
        previous = current
        previous_inside = current_inside

    if result and result[0] != result[-1]:
        result.append(result[0])
    return result


def clip_ring_to_rect(ring: Ring, min_x: float, min_y: float, max_x: float, max_y: float) -> Ring:
    clipped = close_ring(ring)

    def intersect_vertical(p1: Point, p2: Point, x_edge: float) -> Point:
        x1, y1 = p1
        x2, y2 = p2
        if abs(x2 - x1) < EPSILON:
            return (x_edge, y1)
        ratio = (x_edge - x1) / (x2 - x1)
        return (x_edge, y1 + ratio * (y2 - y1))

    def intersect_horizontal(p1: Point, p2: Point, y_edge: float) -> Point:
        x1, y1 = p1
        x2, y2 = p2
        if abs(y2 - y1) < EPSILON:
            return (x1, y_edge)
        ratio = (y_edge - y1) / (y2 - y1)
        return (x1 + ratio * (x2 - x1), y_edge)

    clipped = clip_ring_against_edge(
        clipped,
        lambda point: point[0] >= min_x - EPSILON,
        lambda p1, p2: intersect_vertical(p1, p2, min_x),
    )
    clipped = clip_ring_against_edge(
        clipped,
        lambda point: point[0] <= max_x + EPSILON,
        lambda p1, p2: intersect_vertical(p1, p2, max_x),
    )
    clipped = clip_ring_against_edge(
        clipped,
        lambda point: point[1] >= min_y - EPSILON,
        lambda p1, p2: intersect_horizontal(p1, p2, min_y),
    )
    clipped = clip_ring_against_edge(
        clipped,
        lambda point: point[1] <= max_y + EPSILON,
        lambda p1, p2: intersect_horizontal(p1, p2, max_y),
    )

    if len(clipped) < 4 or ring_area(clipped) <= EPSILON:
        return []
    return clipped


def clip_polygon_to_rect(polygon: Polygon, min_x: float, min_y: float, max_x: float, max_y: float) -> Polygon:
    clipped_rings: Polygon = []
    for ring in polygon:
        clipped = clip_ring_to_rect(ring, min_x, min_y, max_x, max_y)
        if clipped:
            clipped_rings.append(clipped)
    if not clipped_rings or polygon_area(clipped_rings) <= EPSILON:
        return []
    return clipped_rings


def make_area_feature(
    polygons: MultiPolygon,
    group_field: str,
    group_value: str,
    source_properties: dict,
    source_name: str,
    index: int,
) -> dict:
    geometry_type = "Polygon" if len(polygons) == 1 else "MultiPolygon"
    coordinates = polygons[0] if geometry_type == "Polygon" else polygons
    bbox = multipolygon_bounds(polygons)
    properties = dict(source_properties)
    properties.update(
        {
            "area_id": f"area_{index:04d}",
            "source_name": source_name,
            "group_field": group_field,
            "group_value": group_value,
            "bbox": list(bbox),
        }
    )
    return {
        "type": "Feature",
        "geometry": {"type": geometry_type, "coordinates": coordinates},
        "properties": properties,
    }


def sanitize_token(value: str) -> str:
    cleaned = []
    for char in str(value):
        if char in '<>:"/\\|?*':
            cleaned.append("_")
        elif ord(char) < 32:
            continue
        elif char.isspace():
            cleaned.append("_")
        else:
            cleaned.append(char)
    sanitized = "".join(cleaned).strip(" ._")
    return sanitized or "area"


def infer_area_prefix(
    source_name: str | None,
    features: list[dict],
    input_dir: Path,
) -> str:
    if source_name:
        return source_name
    for feature in features:
        props = feature.get("properties", {})
        for key in ("CITY_NAME", "S_NAME", "PREF_NAME"):
            value = props.get(key)
            if value:
                return str(value)
    return input_dir.name


def read_dbf_records(path: Path) -> list[dict]:
    data = path.read_bytes()
    record_count = struct.unpack("<I", data[4:8])[0]
    header_length = struct.unpack("<H", data[8:10])[0]
    record_length = struct.unpack("<H", data[10:12])[0]

    fields: list[tuple[str, str, int, int]] = []
    offset = 32
    while data[offset] != 0x0D:
        name = data[offset : offset + 11].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        field_type = chr(data[offset + 11])
        field_length = data[offset + 16]
        decimals = data[offset + 17]
        fields.append((name, field_type, field_length, decimals))
        offset += 32

    records: list[dict] = []
    for index in range(record_count):
        start = header_length + index * record_length
        record = data[start : start + record_length]
        if record[:1] == b"*":
            continue
        position = 1
        values: dict[str, object] = {}
        for name, field_type, field_length, decimals in fields:
            raw = record[position : position + field_length]
            position += field_length
            text = raw.decode("cp932", errors="ignore").strip()
            if field_type == "N":
                if not text:
                    values[name] = None
                elif decimals:
                    values[name] = float(text)
                else:
                    values[name] = int(text)
            else:
                values[name] = text or None
        records.append(values)
    return records


def split_shapefile_rings(parts: list[int], points: list[Point]) -> list[Ring]:
    rings: list[Ring] = []
    for idx, start in enumerate(parts):
        end = parts[idx + 1] if idx + 1 < len(parts) else len(points)
        rings.append(close_ring(points[start:end]))
    return rings


def rings_to_polygons(rings: list[Ring]) -> MultiPolygon:
    polygons: MultiPolygon = []
    for ring in rings:
        if len(ring) < 4 or ring_area(ring) <= EPSILON:
            continue
        signed = signed_ring_area(ring)
        if signed < 0 or not polygons:
            polygons.append([ring])
            continue
        assigned = False
        test_point = ring[0]
        for polygon in polygons:
            if point_in_ring(test_point, polygon[0]):
                polygon.append(ring)
                assigned = True
                break
        if not assigned:
            polygons.append([ring])
    return polygons


def read_shp_records(path: Path) -> list[MultiPolygon]:
    data = path.read_bytes()
    if len(data) < 100:
        raise ValueError(f"Invalid shapefile header: {path}")
    position = 100
    records: list[MultiPolygon] = []

    while position + 8 <= len(data):
        _, content_length_words = struct.unpack(">2i", data[position : position + 8])
        position += 8
        content_length = content_length_words * 2
        content = data[position : position + content_length]
        position += content_length
        if len(content) < 4:
            continue
        shape_type = struct.unpack("<i", content[:4])[0]
        if shape_type == 0:
            records.append([])
            continue
        if shape_type != 5:
            raise ValueError(f"Unsupported shapefile shape type: {shape_type}")

        num_parts, num_points = struct.unpack("<2i", content[36:44])
        parts = list(struct.unpack(f"<{num_parts}i", content[44 : 44 + 4 * num_parts]))
        points_offset = 44 + 4 * num_parts
        point_values = struct.unpack(f"<{num_points * 2}d", content[points_offset : points_offset + 16 * num_points])
        points = list(zip(point_values[::2], point_values[1::2]))
        rings = split_shapefile_rings(parts, points)
        records.append(rings_to_polygons(rings))

    return records


def load_shapefile_features(shp_path: Path, dbf_path: Path) -> list[dict]:
    geometries = read_shp_records(shp_path)
    records = read_dbf_records(dbf_path)
    if len(geometries) != len(records):
        raise ValueError(
            f"Shapefile geometry count ({len(geometries)}) does not match DBF record count ({len(records)})."
        )

    features = []
    for geometry, properties in zip(geometries, records):
        if not geometry:
            continue
        geometry_type = "Polygon" if len(geometry) == 1 else "MultiPolygon"
        coordinates = geometry[0] if geometry_type == "Polygon" else geometry
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": geometry_type, "coordinates": coordinates},
                "properties": properties,
            }
        )
    return features


def discover_input_files(input_dir: Path, stem: str | None = None) -> tuple[Path, Path, Path, Path]:
    if stem:
        shp_path = input_dir / f"{stem}.shp"
    else:
        shp_files = sorted(input_dir.glob("*.shp"))
        if len(shp_files) != 1:
            raise ValueError("Input directory must contain exactly one .shp file unless --stem is provided.")
        shp_path = shp_files[0]

    dbf_path = shp_path.with_suffix(".dbf")
    shx_path = shp_path.with_suffix(".shx")
    prj_path = shp_path.with_suffix(".prj")
    if not shp_path.exists() or not dbf_path.exists() or not shx_path.exists() or not prj_path.exists():
        raise ValueError(f"Missing required shapefile components for stem: {shp_path.stem}")
    return shp_path, dbf_path, shx_path, prj_path


def dissolve_features_by_field(
    features: list[dict],
    group_field: str,
    source_name: str,
) -> list[dict]:
    grouped_polygons: dict[str, MultiPolygon] = defaultdict(list)
    grouped_properties: dict[str, dict] = {}

    for feature in features:
        value = feature["properties"].get(group_field)
        if value is None:
            continue
        group_value = str(value)
        polygons = geometry_to_multipolygon(feature["geometry"])
        grouped_polygons[group_value].extend(polygons)
        grouped_properties.setdefault(
            group_value,
            {
                "record_count": 0,
                "city_name": feature["properties"].get("CITY_NAME"),
                "pref_name": feature["properties"].get("PREF_NAME"),
                "key_codes": [],
            },
        )
        grouped_properties[group_value]["record_count"] += 1
        key_code = feature["properties"].get("KEY_CODE")
        if key_code and key_code not in grouped_properties[group_value]["key_codes"]:
            grouped_properties[group_value]["key_codes"].append(key_code)

    area_features: list[dict] = []
    for index, group_value in enumerate(sorted(grouped_polygons), start=1):
        source_properties = grouped_properties[group_value]
        area_features.append(
            make_area_feature(
                polygons=grouped_polygons[group_value],
                group_field=group_field,
                group_value=group_value,
                source_properties=source_properties,
                source_name=source_name,
                index=index,
            )
        )
    return area_features


def write_area_outputs(features: list[dict], output_dir: Path, prefix: str, group_field: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    group_label = sanitize_token(group_field)
    collection_path = output_dir / f"{prefix}_areas_by_{group_label}.json"
    collection = {"type": "FeatureCollection", "features": features}
    collection_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = []
    for feature in features:
        area_id = feature["properties"]["area_id"]
        group_value = str(feature["properties"]["group_value"])
        group_token = sanitize_token(group_value)
        filename = f"{prefix}_areas_by_{group_label}__{area_id}_{group_token}.json"
        area_path = output_dir / filename
        area_path.write_text(json.dumps(feature, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.append(
            {
                "area_id": area_id,
                "group_value": group_value,
                "file": filename,
            }
        )

    manifest_path = output_dir / f"{prefix}_areas_by_{group_label}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return collection_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read a Shapefile bundle in an input directory and dissolve it by an attribute such as S_NAME."
    )
    parser.add_argument("input", help="Input directory containing xxxx.shp, xxxx.dbf, xxxx.shx, and xxxx.prj.")
    parser.add_argument("--output-dir", default="output", help="Directory for generated GeoJSON files.")
    parser.add_argument("--prefix", help="Output filename prefix. Defaults to the municipality name.")
    parser.add_argument("--group-field", default="S_NAME", help="Attribute field used to group areas.")
    parser.add_argument("--stem", help="Base filename stem for the shapefile bundle inside the input directory.")
    return parser


def run_area_mode(args: argparse.Namespace, input_dir: Path) -> int:
    shp_path, dbf_path, _, _ = discover_input_files(input_dir, args.stem)
    features = load_shapefile_features(shp_path, dbf_path)
    prefix = infer_area_prefix(args.prefix, features, input_dir)
    dissolved = dissolve_features_by_field(
        features=features,
        group_field=args.group_field,
        source_name=prefix,
    )
    collection_path = write_area_outputs(dissolved, Path(args.output_dir), prefix, args.group_field)
    print(f"Generated {len(dissolved)} area file(s) in {Path(args.output_dir)}")
    print(f"Combined GeoJSON: {collection_path}")
    print(f"Grouped by: {args.group_field}")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    input_path = Path(args.input)
    if not input_path.is_dir():
        raise ValueError("input must be a directory containing xxxx.shp, xxxx.dbf, xxxx.shx, and xxxx.prj.")
    return run_area_mode(args, input_path)


if __name__ == "__main__":
    raise SystemExit(main())
