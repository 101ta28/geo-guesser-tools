# geo-guesser-tools

町域や小地域の Shapefile 一式を `input` フォルダに置き、属性ごとにまとめて GeoJSON 化するツールです。

主な用途は、行政区域データを `S_NAME` などの地名単位で分割し、GeoGuessr 用や地図処理用の小区画データを作ることです。

## 作業フロー

このリポジトリでは、次の流れで地図を作る想定です。

1. 境界データを取得する

   e-Stat の地図境界データから対象地域の Shapefile を取得します。  
   https://www.e-stat.go.jp/gis/statmap-search?page=1&type=2&aggregateUnitForBoundary=A&toukeiCode=00200521&toukeiYear=2020&serveyId=A002005212020&coordsys=1&format=shape&datum=2000

2. 小地域ごとに分割する

   取得した `xxxx.shp`, `xxxx.dbf`, `xxxx.shx`, `xxxx.prj` を `input` フォルダに置き、このツールで `S_NAME` などの単位に分割します。

3. スポーン地点を自動生成する

   分割後のデータを使って、スポーン地点を自動生成します。  
   https://map-g3nerator.vercel.app

4. スポーツ地点をチェックする

   生成した地点を確認・検証します。  
   https://mapcheckr.vercel.app

5. 地図を作成する

   最終的な地図作成は次のツールで行います。  
   https://map-making.app

6. GeoGuessr にインポートする

   作成した JSON を GeoGuessr Creator Hub からマップにインポートします。  
   https://www.geoguessr.com/ja/creator-hub

## 想定する入力構成

たとえば `input` フォルダに次のように置きます。

    input/
      xxxx.shp
      xxxx.dbf
      xxxx.shx
      xxxx.prj

`shp`, `dbf`, `shx`, `prj` の 4 ファイルが必須です。

## 使い方

作業ディレクトリ:

    C:\Users\Owner\workspace\geoguesser_maker

### 小地域ごとに分割する

    uv run python main.py input --output-dir output/xxxx_areas --group-field S_NAME

出力例:

    Generated 94 area file(s) in output\xxxx_areas
    Combined JSON: output\xxxx_areas\xxxx_areas_by_S_NAME.json
    Grouped by: S_NAME

`S_NAME` の代わりに他の属性列でまとめたい場合は `--group-field` を変えます。

    uv run python main.py input --output-dir output/by_keycode --group-field KEY_CODE

## 出力

小地域分割では、たとえば `output/xxxx_areas` に次のようなファイルができます。

- `xxx町_areas_by_S_NAME.json`
  - GeoJSON 形式の `FeatureCollection` を `.json` 拡張子で保存したもの
- `xxx町_areas_by_S_NAME__area_0001_xxx町.json` など
  - 各地名を 1 ファイルずつ保存
- `xxx町_areas_by_S_NAME_manifest.json`
  - `area_id` と `group_value` の対応表

各 feature には次のような属性が付きます。

- `area_id`
- `source_name`
- `group_field`
- `group_value`
- `bbox`
- `record_count`
- `city_name`
- `pref_name`
- `key_codes`

## オプション

- `--stem`
  - フォルダ内に複数の `.shp` がある場合にベース名を指定
- `--prefix`
  - 出力ファイル名の接頭辞を変更。未指定なら `CITY_NAME` などから自動で決めます

## テスト

    uv run python -m unittest discover -s tests -v
