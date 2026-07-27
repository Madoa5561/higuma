# コントリビュート

higumaへのIssue、修正、ドキュメント改善を歓迎します。

## 開発環境

```bash
git clone https://github.com/Madoa5561/higuma.git
cd higuma
python -m pip install -e ".[dev,docs]"
```

## 検証

```bash
cargo fmt --all -- --check
cargo check
cargo test
python -m ruff check python tests examples bench
python -m pytest
mkdocs build --strict
```

## Pull Request

1. 変更を小さく保ち、理由を説明します。
2. 振る舞いを変える場合はtestを追加します。
3. 公開APIを変える場合はREADMEとdocsを更新します。
4. `CHANGELOG.md` の該当sectionへ追記します。

詳しい方針は
[CONTRIBUTING.md](https://github.com/Madoa5561/higuma/blob/main/CONTRIBUTING.md)、
脆弱性報告は
[SECURITY.md](https://github.com/Madoa5561/higuma/blob/main/SECURITY.md)
を参照してください。
